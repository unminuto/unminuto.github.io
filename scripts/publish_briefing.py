#!/usr/bin/env python3
"""Deterministic final publication gate for the daily Jekyll briefing."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tempfile
import hashlib
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urljoin

CONTEXT = {"stage": "preflight", "commit": None, "workflow": None}
COMMAND_TIMEOUT = 60.0
WORKFLOW_NAME = "Deploy Jekyll site to GitHub Pages"


def progress(stage: str, **fields) -> None:
    CONTEXT["stage"] = stage
    print(json.dumps({"event": "progress", **CONTEXT, **fields}), file=sys.stderr, flush=True)

ROOT = Path(__file__).resolve().parents[1]
REPO = "unminuto/unminuto.github.io"
SITE = "https://unminuto.github.io"


def run(*args: str, check: bool = True, cwd: Path = ROOT, capture: bool = True,
        timeout: float | None = None) -> str:
    p = subprocess.run(args, cwd=cwd, text=True, capture_output=capture,
                       timeout=COMMAND_TIMEOUT if timeout is None else timeout)
    if check and p.returncode:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(args)}\n{p.stdout}\n{p.stderr}")
    return (p.stdout or "").strip()


def ensure_only_publication_paths(allowed: set[str]) -> None:
    status = subprocess.run(
        ("git", "status", "--short", "--untracked-files=all"),
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=COMMAND_TIMEOUT,
    )
    if status.returncode:
        raise RuntimeError(f"could not inspect publication changes: {status.stderr}")
    changed = []
    for line in status.stdout.splitlines():
        path = line[3:]
        if path and path not in allowed:
            changed.append(path)
    if changed:
        raise RuntimeError(
            "publication may modify only the edition, its image, and _data/start_here.yml; "
            "template or site files changed: " + ", ".join(changed)
        )


def wait_for_pages(sha: str, wait: float, poll: float) -> dict:
    """Discovery and completion share one deadline; never dispatch or repush."""
    deadline = time.monotonic() + wait
    current = None
    last_error = "no matching Pages workflow discovered"
    while time.monotonic() < deadline:
        stage = "workflow-completion" if current else "workflow-discovery"
        progress(stage)
        try:
            timeout = min(COMMAND_TIMEOUT, deadline - time.monotonic())
            if timeout <= 0:
                break
            if current:
                candidate = json.loads(run(
                    "gh", "run", "view", str(current["databaseId"]), "--repo", REPO,
                    "--json", "databaseId,headSha,workflowName,status,conclusion", timeout=timeout))
            else:
                runs = json.loads(run(
                    "gh", "run", "list", "--repo", REPO, "--workflow", "pages.yml",
                    "--commit", sha, "--limit", "20", "--json",
                    "databaseId,headSha,workflowName,status,conclusion", timeout=timeout))
                candidate = next((r for r in runs if r.get("headSha") == sha
                                  and r.get("workflowName") == WORKFLOW_NAME), None)
            if candidate:
                if candidate.get("headSha") != sha or candidate.get("workflowName") != WORKFLOW_NAME:
                    raise ValueError("workflow identity does not match exact commit and Pages workflow")
                current = candidate
                CONTEXT["workflow"] = current["databaseId"]
                last_error = f"workflow {current['databaseId']} is {current.get('status')}"
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError) as exc:
            last_error = str(exc)
            progress(stage, retry_error=last_error)
        else:
            if current and current.get("status") == "completed":
                if current.get("conclusion") != "success":
                    raise RuntimeError(f"Pages deployment failed: {current.get('conclusion')}")
                return current
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll, remaining))
    raise RuntimeError(f"Pages workflow timed out after {wait:g}s: {last_error}")


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(markup: str) -> str:
    parser = PlainText()
    parser.feed(markup)
    return " ".join(" ".join(parser.parts).split())


def verify_live(url: str, text: str, image: Path, social_assets: Path | None,
                wait: float, poll: float) -> None:
    """Retry stale HTML and assets under a single propagation deadline."""
    origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}/"
    blocks = re.findall(r'<div class="briefing-summary">(.*?)</div>|'
                        r'<article class="briefing-item">(.*?)</article>', text, re.DOTALL)
    expected = [plain_text(a or b) for a, b in blocks]
    if not expected or any(not block for block in expected):
        raise RuntimeError("edition has no verifiable summary/article content")
    assets = [image]
    if social_assets:
        pngs = sorted(p for p in social_assets.rglob("*") if p.is_file() and p.suffix.lower() == ".png")
        if not pngs:
            raise RuntimeError("social asset directory contains no PNGs")
        assets.extend(pngs)
    pending: dict[str, bytes | None] = {url: None}
    for asset in assets:
        pending[urljoin(origin, asset.relative_to(ROOT).as_posix())] = hashlib.sha256(asset.read_bytes()).digest()
    image_path = "/" + image.relative_to(ROOT).as_posix()
    deadline = time.monotonic() + wait
    errors = {}
    while pending and time.monotonic() < deadline:
        for target, digest in list(pending.items()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                request = Request(target, headers={"Cache-Control": "no-cache", "User-Agent": "unminuto-publisher"})
                # Each response is bounded too, not just connection establishment.
                with urlopen(request, timeout=min(COMMAND_TIMEOUT, remaining)) as response:
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}")
                    chunks = []
                    while True:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("live verification deadline reached")
                        chunk = response.read(65536)
                        if time.monotonic() >= deadline:
                            raise TimeoutError("live verification deadline reached")
                        if not chunk:
                            break
                        chunks.append(chunk)
                    body = b"".join(chunks)
                if digest is None:
                    html = body.decode("utf-8")
                    actual = plain_text(html)
                    if any(block not in actual for block in expected) or image_path not in html:
                        raise RuntimeError("live HTML does not contain exact edition content and image")
                elif hashlib.sha256(body).digest() != digest:
                    raise RuntimeError("live asset differs from local asset")
                del pending[target]
                errors.pop(target, None)
                progress("live", verified=target, pending=len(pending))
            except (OSError, ValueError, RuntimeError, HTTPException) as exc:
                errors[target] = str(exc)
                progress("live", url=target, retry_error=str(exc))
        if pending:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll, remaining))
    if pending:
        raise RuntimeError(f"live verification timed out after {wait:g}s: " + json.dumps(
            {target: errors.get(target, "not checked before deadline") for target in pending}))


def verify_remote(sha: str) -> None:
    progress("remote")
    remote = run("git", "ls-remote", "origin", "refs/heads/master").split()
    if not remote or remote[0] != sha:
        raise RuntimeError(f"remote master does not match HEAD {sha}: {' '.join(remote)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--commit-message")
    ap.add_argument("--social-assets", help="Optional rendered Instagram asset directory under ig")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--verify-only", action="store_true", help="Read-only recovery of the current remote HEAD deployment")
    ap.add_argument("--workflow-wait", type=float, default=300, help="Total discovery/deploy budget in seconds")
    ap.add_argument("--live-wait", type=float, default=120, help="Total public URL propagation budget in seconds")
    ap.add_argument("--poll-interval", type=float, default=5, help="Retry interval in seconds")
    ap.add_argument("--command-timeout", type=float, default=60, help="Per-command/network timeout in seconds")
    args = ap.parse_args()
    global COMMAND_TIMEOUT
    for name in ("workflow_wait", "live_wait", "poll_interval", "command_timeout"):
        value = getattr(args, name)
        if not 0 < value < float("inf"):
            ap.error(f"--{name.replace('_', '-')} must be positive and finite")
    COMMAND_TIMEOUT = args.command_timeout
    CONTEXT.update(stage="preflight", commit=None, workflow=None)
    progress("preflight", verify_only=args.verify_only)
    if not args.verify_only and not args.check_only and not args.commit_message:
        ap.error("--commit-message is required for publication")
    parsed_url = urlsplit(args.url)
    if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.query or parsed_url.fragment:
        raise RuntimeError("publication URL must be an absolute HTTPS URL without query or fragment")
    edition = ROOT / args.edition
    image = ROOT / args.image
    edition_dir = ROOT / "collections" / "_newsletter"
    if not edition.is_file():
        raise RuntimeError(f"missing edition: {edition}")
    if edition.resolve().parent != edition_dir.resolve():
        raise RuntimeError(
            "edition must be stored in collections/_newsletter; "
            "root-level _newsletter is not a configured Jekyll collection"
        )
    if not image.is_file():
        raise RuntimeError(f"missing image: {image}")
    social_assets = None
    if args.social_assets:
        social_assets = ROOT / args.social_assets
        social_root = ROOT / "ig"
        if not social_assets.is_dir() or social_root.resolve() not in social_assets.resolve().parents:
            raise RuntimeError("social assets must be a populated directory under ig")
        if not any(p.is_file() and p.suffix.lower() == ".png" for p in social_assets.rglob("*")):
            raise RuntimeError("social asset directory contains no PNGs")
    date_match = re.match(r"(\d{4}-\d{2}-\d{2})-", edition.name)
    if not date_match:
        raise RuntimeError(f"edition filename must start with YYYY-MM-DD: {edition.name}")
    edition_day = date_match.group(1)
    duplicates = sorted(
        candidate for candidate in (ROOT / "collections" / "_newsletter").glob(f"{edition_day}-*.md")
        if candidate.resolve() != edition.resolve()
    )
    if duplicates:
        raise RuntimeError(
            f"an edition already exists for {edition_day}: "
            + ", ".join(candidate.name for candidate in duplicates)
        )
    text = edition.read_text(encoding="utf-8")
    permalink_match = re.search(r"^permalink:\s*(\S+)\s*$", text, re.MULTILINE)
    if not permalink_match:
        raise RuntimeError("edition front matter must declare a permalink")
    permalink = permalink_match.group(1)
    expected_path = re.sub(r"^https?://[^/]+", "", args.url).rstrip("/") + "/"
    if permalink != expected_path:
        raise RuntimeError(
            f"edition permalink {permalink!r} does not match publication URL {expected_path!r}"
        )
    start_here = ROOT / "_data" / "start_here.yml"
    if not start_here.is_file() or permalink not in start_here.read_text(encoding="utf-8"):
        raise RuntimeError("_data/start_here.yml must include the edition permalink")
    allowed = {
        str(edition.relative_to(ROOT)),
        str(image.relative_to(ROOT)),
        str(start_here.relative_to(ROOT)),
    }
    if social_assets:
        allowed.update(str(item.relative_to(ROOT)) for item in social_assets.rglob("*") if item.is_file())
    ensure_only_publication_paths(allowed)
    if 'class="source-badge"' in text or 'class="language-badge"' in text or 'class="source-row"' in text:
        raise RuntimeError("edition uses legacy source markup; use source-line, source-access, and source-lang")
    if 'class="source-access ' not in text or 'class="source-lang"' not in text:
        raise RuntimeError("edition must include source-access and source-lang badges for every source")
    if '<article class="briefing-item">' not in text:
        raise RuntimeError("edition articles must use the briefing-item content class")
    marker = '<figure class="briefing-image briefing-image--daily">'
    summary = text.find('<div class="briefing-summary">')
    figure = text.find(marker)
    article = text.find("<article")
    if summary < 0 or figure < 0 or article < 0 or not (summary < figure < article):
        raise RuntimeError("daily image is not immediately after the briefing summary")
    if args.check_only:
        print(json.dumps({"ok": True, "edition": str(edition), "image": str(image)}))
        return 0
    dirty = run("git", "status", "--short", "--untracked-files=all")
    if args.verify_only and dirty:
        raise RuntimeError("verify-only requires a clean working tree and index")
    if not dirty:
        # A retry after a successful commit/push must only verify, never create
        # a duplicate commit or automatically repush an unconfirmed remote.
        run("git", "ls-files", "--error-unmatch", *sorted(allowed))
        progress("recovery", reason="clean existing publication; skipping build/commit/push")
    else:
        progress("build")
        scratch = Path(os.environ.get("TMPDIR") or (Path.home() / ".hermes" / "cache" / "scratch"))
        scratch.mkdir(parents=True, exist_ok=True)
        if (ROOT / "_site").exists():
            stale = Path(tempfile.mkdtemp(prefix="briefing-stale-", dir=scratch))
            (ROOT / "_site").rename(stale / "_site")
        with tempfile.TemporaryDirectory(prefix="briefing-build-", dir=scratch) as destination:
            run("bundle", "exec", "jekyll", "build", "--trace", "--destination", destination)
        ensure_only_publication_paths(allowed)
        run("git", "diff", "--check")
        progress("commit")
        run("git", "add", *sorted(allowed))
        run("git", "commit", "-m", args.commit_message)
        CONTEXT["commit"] = run("git", "rev-parse", "HEAD")
        progress("push")
        run("git", "push", "origin", "master")
    sha = run("git", "rev-parse", "HEAD")
    CONTEXT["commit"] = sha
    verify_remote(sha)
    current = wait_for_pages(sha, args.workflow_wait, args.poll_interval)
    progress("live")
    verify_live(args.url, text, image, social_assets, args.live_wait, args.poll_interval)
    progress("final-check")
    verify_remote(sha)
    if run("git", "rev-parse", "HEAD") != sha:
        raise RuntimeError("HEAD changed during publication verification")
    if run("git", "status", "--short", "--untracked-files=all"):
        raise RuntimeError("repository not clean after publication verification")
    print(json.dumps({"ok": True, "commit": sha, "url": args.url,
                      "workflow": current["databaseId"], "verify_only": args.verify_only}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, **CONTEXT, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
