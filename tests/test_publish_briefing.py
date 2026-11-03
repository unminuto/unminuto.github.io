"""Publisher regression tests: no real commands, network, sleeps or fixture writes."""
import contextlib
import importlib.util
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from email.message import Message

spec = importlib.util.spec_from_file_location(
    "publish_briefing", Path(__file__).resolve().parents[1] / "scripts/publish_briefing.py")
assert spec is not None and spec.loader is not None
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

SHA = "a" * 40
EDITION = "collections/_newsletter/2026-11-03-briefing.md"
IMAGE = "assets/images/briefing/daily/2026-11-03.webp"
URL = "https://unminuto.github.io/2026/11/03/briefing/"
TEXT = '''---
permalink: /2026/11/03/briefing/
---
<div class="briefing-summary"><p>Exact daily summary.</p></div>
<figure class="briefing-image briefing-image--daily"><img src="/assets/images/briefing/daily/2026-11-03.webp"></figure>
<article class="briefing-item"><h2>Unique headline</h2><p>Exact edition facts &amp; context.</p>
<p class="source-line"><span class="source-access F">F</span><span class="source-lang">D</span> Source</p></article>
'''


def workflow(**updates):
    result = dict(databaseId=123, headSha=SHA, workflowName=publisher.WORKFLOW_NAME,
                  status="completed", conclusion="success")
    result.update(updates)
    return result


def response(body, status=200):
    mock = MagicMock()
    mock.__enter__.return_value = mock
    mock.status = status
    mock.read.side_effect = [body, b""]
    return mock


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stderr = self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.stdout = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.clock = 0.0
        self.stack.enter_context(patch.object(publisher.time, "monotonic", side_effect=lambda: self.clock))
        self.stack.enter_context(patch.object(publisher.time, "sleep", side_effect=self.sleep))
        self.stack.enter_context(patch.object(publisher, "ROOT", Path("/repo")))
        self.stack.enter_context(patch.object(publisher, "COMMAND_TIMEOUT", 60))
        publisher.CONTEXT.update(stage="preflight", commit=None, workflow=None)
        # Fail closed if any test accidentally reaches a real external interface.
        self.process = self.stack.enter_context(patch.object(
            publisher.subprocess, "run", side_effect=AssertionError("unexpected subprocess")))
        self.network = self.stack.enter_context(patch.object(
            publisher, "urlopen", side_effect=AssertionError("unexpected network")))

    def sleep(self, seconds):
        self.clock += seconds

    def runs(self, *values):
        return self.stack.enter_context(patch.object(publisher, "run", side_effect=values))

    def test_delayed_discovery_then_completion(self):
        command = self.runs("[]", "[]", json.dumps([workflow(status="queued", conclusion="")]),
                            json.dumps(workflow(status="in_progress", conclusion="")),
                            json.dumps(workflow()))
        self.assertEqual(publisher.wait_for_pages(SHA, 30, 2)["databaseId"], 123)
        self.assertEqual(command.call_count, 5)
        self.assertEqual(self.clock, 8)
        self.assertIn("--workflow", command.call_args_list[0].args)
        self.assertIn("pages.yml", command.call_args_list[0].args)
        self.assertIn(SHA, command.call_args_list[0].args)

    def test_wrong_sha_and_wrong_workflow_ignored(self):
        command = self.runs(json.dumps([workflow(headSha="b" * 40), workflow(workflowName="Tests")]),
                            json.dumps([workflow()]))
        self.assertEqual(publisher.wait_for_pages(SHA, 10, 2)["databaseId"], 123)
        self.assertEqual(command.call_count, 2)

    def test_discovery_timeout_is_bounded(self):
        with patch.object(publisher, "run", return_value="[]") as command:
            with self.assertRaisesRegex(RuntimeError, "timed out.*no matching"):
                publisher.wait_for_pages(SHA, 5, 2)
        self.assertEqual(self.clock, 5)
        self.assertEqual(command.call_count, 3)
        self.assertEqual(command.call_args.kwargs["timeout"], 1)

    def test_completion_shares_discovery_deadline(self):
        command = self.runs("[]", json.dumps([workflow(status="queued", conclusion="")]),
                            json.dumps(workflow(status="in_progress", conclusion="")))
        with self.assertRaisesRegex(RuntimeError, "timed out.*in_progress"):
            publisher.wait_for_pages(SHA, 5, 2)
        self.assertEqual(self.clock, 5)
        self.assertEqual(command.call_count, 3)
        self.assertEqual(publisher.CONTEXT["workflow"], 123)

    def test_transient_gh_error_retried(self):
        self.runs(RuntimeError("gh HTTP 502"), json.dumps([workflow()]))
        self.assertEqual(publisher.wait_for_pages(SHA, 5, 1)["conclusion"], "success")
        self.assertIn("gh HTTP 502", self.stderr.getvalue())

    def test_command_timeout_retried(self):
        self.runs(subprocess.TimeoutExpired("gh", 1), json.dumps([workflow()]))
        self.assertEqual(publisher.wait_for_pages(SHA, 5, 1)["conclusion"], "success")

    def test_transient_completion_error_retried(self):
        self.runs(json.dumps([workflow(status="in_progress", conclusion="")]),
                  RuntimeError("network"), json.dumps(workflow()))
        self.assertEqual(publisher.wait_for_pages(SHA, 8, 1)["conclusion"], "success")

    def test_all_non_success_deploy_conclusions_fail_immediately(self):
        for conclusion in ("failure", "cancelled", "timed_out", "skipped", "neutral", ""):
            with self.subTest(conclusion=conclusion):
                with patch.object(publisher, "run", return_value=json.dumps([workflow(conclusion=conclusion)])):
                    with self.assertRaisesRegex(RuntimeError, "deployment failed"):
                        publisher.wait_for_pages(SHA, 10, 2)
        self.assertEqual(self.clock, 0)

    def test_wrong_identity_on_view_never_accepted(self):
        self.runs(json.dumps([workflow(status="queued", conclusion="")]),
                  json.dumps(workflow(headSha="b" * 40)),
                  json.dumps(workflow(workflowName="Tests")))
        with self.assertRaisesRegex(RuntimeError, "timed out.*identity"):
            publisher.wait_for_pages(SHA, 3, 1)

    def mock_assets(self, social=False):
        self.stack.enter_context(patch.object(Path, "read_bytes", return_value=b"image-data"))
        self.stack.enter_context(patch.object(Path, "is_file", return_value=True))
        pngs = [Path("/repo/ig/day/story/01.png"), Path("/repo/ig/day/story/02.PNG")]
        self.stack.enter_context(patch.object(Path, "rglob", return_value=pngs if social else []))
        return pngs

    def verify_live(self, social=False, wait=5):
        publisher.verify_live(URL, TEXT, Path("/repo") / IMAGE,
                              Path("/repo/ig/day") if social else None, wait, 1)

    def test_live_404_then_exact_content_200(self):
        self.mock_assets()
        self.network.side_effect = [HTTPError(URL, 404, "not propagated", Message(), None),
                                    response(b"image-data"), response(TEXT.encode())]
        self.verify_live()
        self.assertEqual(self.network.call_count, 3)
        self.assertEqual(self.clock, 1)
        self.assertTrue(all(c.kwargs["timeout"] <= 5 for c in self.network.call_args_list))

    def test_stale_content_retried_even_when_http_200(self):
        self.mock_assets()
        self.network.side_effect = [response(TEXT.replace("Exact edition facts", "Old facts").encode()),
                                    response(b"image-data"), response(TEXT.encode())]
        self.verify_live()
        self.assertEqual(self.clock, 1)

    def test_transient_incomplete_http_read_retried(self):
        self.mock_assets()
        broken = response(b"unused")
        broken.read.side_effect = publisher.HTTPException("incomplete response")
        self.network.side_effect = [broken, response(b"image-data"), response(TEXT.encode())]
        self.verify_live()
        self.assertEqual(self.clock, 1)

    def test_slow_read_cannot_succeed_after_deadline(self):
        self.mock_assets()
        slow = response(b"unused")

        def read(size):
            self.clock += 6
            return TEXT.encode()

        slow.read.side_effect = read
        self.network.return_value = slow
        self.network.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "deadline reached"):
            self.verify_live(wait=5)
        self.assertEqual(self.network.call_count, 1)

    def test_wrong_image_content_fails(self):
        self.mock_assets()
        self.network.side_effect = lambda request, **kw: response(
            TEXT.encode() if request.full_url == URL else b"old-image")
        with self.assertRaisesRegex(RuntimeError, "live asset differs"):
            self.verify_live(wait=3)
        self.assertEqual(self.clock, 3)

    def test_all_social_pngs_verified(self):
        self.mock_assets(social=True)
        self.network.side_effect = [response(TEXT.encode())] + [response(b"image-data") for _ in range(3)]
        self.verify_live(social=True)
        urls = [call.args[0].full_url for call in self.network.call_args_list]
        self.assertEqual(len(urls), 4)
        self.assertIn("https://unminuto.github.io/ig/day/story/02.PNG", urls)

    def test_missing_social_png_delayed_then_verified(self):
        self.mock_assets(social=True)
        self.network.side_effect = [response(TEXT.encode()), response(b"image-data"),
                                    response(b"image-data"), HTTPError(URL, 404, "missing", Message(), None),
                                    response(b"image-data")]
        self.verify_live(social=True)
        self.assertEqual(self.network.call_count, 5)
        self.assertEqual(self.clock, 1)

    def test_empty_social_directory_rejected(self):
        self.mock_assets()
        with self.assertRaisesRegex(RuntimeError, "no PNGs"):
            self.verify_live(social=True)
        self.network.assert_not_called()

    def test_live_timeout_stops_network_retries(self):
        self.mock_assets()
        self.network.side_effect = HTTPError(URL, 404, "missing", Message(), None)
        with self.assertRaisesRegex(RuntimeError, "live verification timed out"):
            self.verify_live(wait=3)
        self.assertEqual(self.clock, 3)
        self.assertEqual(self.network.call_count, 6)

    def main_fixture(self, extra=(), dirty="", remote=SHA):
        self.stack.enter_context(patch.object(publisher.sys, "argv", ["publish_briefing.py",
            "--edition", EDITION, "--image", IMAGE, "--url", URL, *extra]))
        self.stack.enter_context(patch.object(Path, "is_file", return_value=True))
        self.stack.enter_context(patch.object(Path, "read_text", return_value=TEXT))
        self.stack.enter_context(patch.object(Path, "read_bytes", return_value=b"image-data"))
        self.stack.enter_context(patch.object(Path, "glob", return_value=[]))

        def execute(args, **kwargs):
            self.assertGreater(kwargs["timeout"], 0)
            if args[:3] == ("git", "status", "--short"):
                output = dirty
            elif args[:3] == ("git", "rev-parse", "HEAD"):
                output = SHA
            elif args[:2] == ("git", "ls-remote"):
                output = f"{remote}\trefs/heads/master"
            elif args[:3] == ("gh", "run", "list"):
                output = json.dumps([workflow()])
            elif args[:3] == ("git", "ls-files", "--error-unmatch"):
                output = EDITION
            else:
                raise AssertionError(f"unexpected or mutating command: {args}")
            return subprocess.CompletedProcess(args, 0, output, "")

        self.process.side_effect = execute
        self.network.side_effect = lambda request, **kwargs: response(
            TEXT.encode() if request.full_url == URL else b"image-data")
        # Filesystem mutations must not occur in read-only/recovery paths.
        self.stack.enter_context(patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")))
        self.stack.enter_context(patch.object(Path, "rename", side_effect=AssertionError("rename")))
        self.stack.enter_context(patch.object(publisher.tempfile, "TemporaryDirectory", side_effect=AssertionError("build directory")))

    def test_verify_only_no_build_stage_commit_or_push(self):
        self.main_fixture(["--verify-only"])
        self.assertEqual(publisher.main(), 0)
        result = json.loads(self.stdout.getvalue())
        self.assertTrue(result["verify_only"])
        self.assertEqual(result["commit"], SHA)
        commands = [c.args[0] for c in self.process.call_args_list]
        self.assertTrue(any(c[:2] == ("git", "ls-remote") for c in commands))
        self.assertTrue(any(c[:3] == ("gh", "run", "list") for c in commands))
        self.assertEqual(self.network.call_count, 2)
        for line in self.stderr.getvalue().splitlines():
            self.assertEqual(json.loads(line)["event"], "progress")

    def test_clean_existing_publication_auto_recovers_without_mutation(self):
        self.main_fixture(["--commit-message", "daily edition"])
        self.assertEqual(publisher.main(), 0)
        self.assertIn("clean existing publication", self.stderr.getvalue())

    def test_recovery_never_repushes_remote_mismatch(self):
        self.main_fixture(["--verify-only"], remote="b" * 40)
        with self.assertRaisesRegex(RuntimeError, "remote master does not match"):
            publisher.main()
        self.network.assert_not_called()
        self.assertEqual(publisher.CONTEXT["commit"], SHA)
        self.assertEqual(publisher.CONTEXT["stage"], "remote")

    def test_verify_only_rejects_dirty_inputs_without_mutation(self):
        self.main_fixture(["--verify-only"], dirty=" M " + EDITION)
        with self.assertRaisesRegex(RuntimeError, "requires a clean"):
            publisher.main()
        self.network.assert_not_called()

    def test_allowlist_still_blocks_template_changes(self):
        self.main_fixture(["--verify-only"], dirty=" M _layouts/newsletter.html")
        with self.assertRaisesRegex(RuntimeError, "template or site files changed"):
            publisher.main()
        self.network.assert_not_called()

    def test_check_only_keeps_preflight_and_no_network_or_mutation(self):
        self.main_fixture(["--check-only", "--commit-message", "edition"])
        self.assertEqual(publisher.main(), 0)
        self.network.assert_not_called()
        self.assertEqual(self.process.call_count, 1)

    def test_duplicate_same_day_rejected(self):
        self.main_fixture(["--check-only"])
        with patch.object(Path, "glob", return_value=[Path("/repo/collections/_newsletter/2026-11-03-other.md")]):
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                publisher.main()
        self.process.assert_not_called()

    def publication_fixture(self, push_error=None):
        self.main_fixture(["--commit-message", "daily edition"], dirty=" M " + EDITION)
        read_command = self.process.side_effect
        committed = False

        def execute(args, **kwargs):
            nonlocal committed
            if args[:2] == ("bundle", "exec") or args[:2] in (
                    ("git", "diff"), ("git", "add"), ("git", "push"), ("git", "commit")):
                if args[:2] == ("git", "commit"):
                    committed = True
                if args[:2] == ("git", "push") and push_error:
                    raise push_error
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:3] == ("git", "status", "--short") and committed:
                return subprocess.CompletedProcess(args, 0, "", "")
            return read_command(args, **kwargs)

        self.process.side_effect = execute
        self.stack.enter_context(patch.object(Path, "mkdir"))
        self.stack.enter_context(patch.object(Path, "exists", return_value=False))
        self.stack.enter_context(patch.dict(publisher.os.environ, {"TMPDIR": "/scratch"}))
        temporary = self.stack.enter_context(patch.object(publisher.tempfile, "TemporaryDirectory"))
        temporary.return_value.__enter__.return_value = "/scratch/briefing-build-test"
        return temporary

    def test_new_publication_builds_in_scratch_and_pushes_once(self):
        temporary = self.publication_fixture()
        self.assertEqual(publisher.main(), 0)
        temporary.assert_called_once_with(prefix="briefing-build-", dir=Path("/scratch"))
        commands = [c.args[0] for c in self.process.call_args_list]
        for verb in ("add", "commit", "push"):
            self.assertEqual(sum(c[:2] == ("git", verb) for c in commands), 1)
        build = next(c for c in commands if c[0] == "bundle")
        self.assertEqual(build[-2:], ("--destination", "/scratch/briefing-build-test"))
        stage = next(c for c in commands if c[:2] == ("git", "add"))
        self.assertEqual(set(stage[2:]), {EDITION, IMAGE, "_data/start_here.yml"})

    def test_push_timeout_is_not_retried_and_reports_commit_context(self):
        self.publication_fixture(subprocess.TimeoutExpired("git push", 60))
        with self.assertRaises(subprocess.TimeoutExpired):
            publisher.main()
        commands = [c.args[0] for c in self.process.call_args_list]
        self.assertEqual(sum(c[:2] == ("git", "push") for c in commands), 1)
        self.assertEqual(publisher.CONTEXT["commit"], SHA)
        self.assertEqual(publisher.CONTEXT["stage"], "push")
        self.network.assert_not_called()

    def test_subprocess_timeout_passed_and_propagated(self):
        self.process.side_effect = subprocess.TimeoutExpired("git", 60)
        with self.assertRaises(subprocess.TimeoutExpired):
            publisher.run("git", "status")
        self.assertEqual(self.process.call_args.kwargs["timeout"], 60)


if __name__ == "__main__":
    unittest.main()
