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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "unminuto/unminuto.github.io"
SITE = "https://unminuto.github.io"


def run(*args: str, check: bool = True, cwd: Path = ROOT, capture: bool = True) -> str:
    p = subprocess.run(args, cwd=cwd, text=True, capture_output=capture)
    if check and p.returncode:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(args)}\n{p.stdout}\n{p.stderr}")
    return (p.stdout or "").strip()


def ensure_only_publication_paths(allowed: set[str]) -> None:
    status = subprocess.run(
        ("git", "status", "--short", "--untracked-files=all"),
        cwd=ROOT,
        text=True,
        capture_output=True,
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--commit-message", required=True)
    ap.add_argument("--social-assets", help="Optional rendered Instagram asset directory under ig")
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
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
    if (ROOT / "_site").exists():
        stale = Path("/tmp") / f"prj-news-mattino-cron-stale-{int(time.time())}"
        (ROOT / "_site").rename(stale)
    destination = Path("/tmp") / f"prj-news-mattino-cron-build-{int(time.time())}"
    run("bundle", "exec", "jekyll", "build", "--trace", "--destination", str(destination))
    run("git", "diff", "--check")
    stage = ["git", "add", args.edition, args.image, "_data/start_here.yml"]
    if social_assets:
        stage.append(args.social_assets)
    run(*stage)
    status = run("git", "status", "--short")
    if not status:
        raise RuntimeError("nothing staged for publication")
    run("git", "commit", "-m", args.commit_message)
    run("git", "push", "origin", "master")
    runs = json.loads(run("gh", "run", "list", "--repo", REPO, "--limit", "5", "--json", "databaseId,headSha,status,conclusion"))
    sha = run("git", "rev-parse", "HEAD")
    current = next((r for r in runs if r.get("headSha") == sha), None)
    if not current:
        raise RuntimeError(f"no Pages workflow found for commit {sha}")
    run("gh", "run", "watch", str(current["databaseId"]), "--repo", REPO, "--exit-status", capture=True)
    code = subprocess.run(["curl", "-L", "--fail", "--silent", "--show-error", "--max-time", "30", args.url], cwd=ROOT, capture_output=True, text=True)
    if code.returncode:
        raise RuntimeError(f"live URL failed: {args.url}\n{code.stderr}")
    final_status = run("git", "status", "--short", "--branch")
    if "\n" in final_status or (final_status and not final_status.startswith("## ")):
        raise RuntimeError(f"repository not clean after publication: {final_status}")
    print(json.dumps({"ok": True, "commit": sha, "url": args.url, "workflow": current["databaseId"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
