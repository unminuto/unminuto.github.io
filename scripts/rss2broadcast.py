"""RSS-to-Kit broadcast integration helpers and CLI."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FeedItem:
    guid: str
    title: str
    link: str
    content: str


def _text(element: ET.Element | None) -> str:
    return "" if element is None else "".join(element.itertext()).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(element: ET.Element, names: set[str]) -> ET.Element | None:
    for child in element.iter():
        if _local(child.tag) in names:
            return child
    return None


def parse_feed(xml: str) -> list[FeedItem]:
    root = ET.fromstring(xml)
    entries = [e for e in root.iter() if _local(e.tag) in {"item", "entry"}]
    items: list[FeedItem] = []
    for entry in entries:
        title = _text(_first(entry, {"title"}))
        guid = _text(_first(entry, {"guid", "id"})) or title
        link_element = _first(entry, {"link"})
        link = ""
        if link_element is not None:
            link = link_element.attrib.get("href", "") or _text(link_element)
        content_element = _first(entry, {"encoded", "content", "description", "summary"})
        content = _text(content_element)
        if title and guid and link:
            items.append(FeedItem(guid=guid, title=title, link=link, content=content))
    return items


def select_new_item(items: Iterable[FeedItem], processed_guids: set[str]) -> FeedItem | None:
    return next((item for item in items if item.guid not in processed_guids), None)


def _email_summary(content: str) -> str:
    match = re.search(r'<div class="briefing-summary">\s*<p>(.*?)</p>', content, re.DOTALL)
    fragment = match.group(1) if match else content
    text = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(html.unescape(text).split())


def _story_titles(content: str) -> list[str]:
    headings = re.findall(r"<article[^>]*>.*?<h2[^>]*>(.*?)</h2>", content, re.DOTALL)
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", heading)).split()) for heading in headings]


def build_broadcast_payload(item: FeedItem, send_at: str | None = None) -> dict:
    title = html.escape(item.title)
    link = html.escape(item.link, quote=True)
    summary = html.escape(_email_summary(item.content))
    story_links = "".join(
        "<li style=\"padding:12px 0;border-top:1px solid #d6cadf;line-height:1.2;\">"
        f"<a href=\"{link}\" style=\"color:#21172f;text-decoration:none;font-weight:800;\">{index:02d} — {html.escape(story)}</a>"
        "</li>"
        for index, story in enumerate(_story_titles(item.content), start=1)
    )
    stories = (
        "<h2 style=\"margin:24px 0 8px;color:#6240d5;font:800 13px Arial,sans-serif;"
        "letter-spacing:.1em;text-transform:uppercase;\">Oggi</h2>"
        f"<ul style=\"margin:0;padding:0;list-style:none;\">{story_links}</ul>"
        if story_links
        else ""
    )
    body = f'''<div style="margin:0 auto;padding:0;max-width:640px;background:#fff9ff;color:#21172f;font-family:Arial,sans-serif;">
<div style="padding:22px 28px 18px;border-bottom:6px solid #7c4dff;"><span style="font-size:23px;font-weight:900;letter-spacing:-.07em;text-transform:uppercase;">un minuto</span><br><span style="font-size:11px;font-weight:800;letter-spacing:.12em;">GERMANIA, IN BREVE</span></div>
<div style="padding:0 28px 30px;"><p style="margin:24px 0 8px;color:#6240d5;font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;">BRIEFING QUOTIDIANO</p>
<h1 style="margin:0;font-size:36px;line-height:1;letter-spacing:-.06em;text-transform:uppercase;"><a href="{link}" style="color:#21172f;text-decoration:none;">{title}</a></h1>
<div style="margin:18px 0 0;padding:17px;border-left:8px solid #ff4f87;background:#f1e8f5;font:18px/1.45 Georgia,serif;"><strong>TL;DR — </strong>{summary}</div>
{stories}
<p style="margin:24px 0 0;"><a href="{link}" style="display:inline-block;background:#7c4dff;color:#ffffff;padding:13px 16px;font-size:13px;font-weight:800;letter-spacing:.04em;text-decoration:none;">APRI IL BRIEFING COMPLETO →</a></p>
<p style="margin:28px 0 0;padding-top:14px;border-top:1px solid #d6cadf;color:#6f6378;font-size:11px;line-height:1.4;">unminuto.news · Ricevi questo briefing perché sei iscritto.</p></div></div>'''
    payload = {
        "subject": item.title,
        "preview_text": _email_summary(item.content),
        "content": body,
        "public": True,
    }
    if send_at:
        payload["send_at"] = send_at
    return payload


def fetch_feed(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Germania-in-breve-rss2broadcast/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def create_broadcast(payload: dict, api_key: str, api_url: str) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Kit-Api-Key": api_key},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()).get("processed_guids", []))


def _save_state(path: Path, guid: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"processed_guids": [guid]}, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Kit broadcast from the latest Jekyll RSS item")
    parser.add_argument("--feed-url", default=os.environ.get("RSS_FEED_URL", "https://unminuto.news/feed.xml"))
    parser.add_argument("--send-at", default=os.environ.get("KIT_SEND_AT"))
    parser.add_argument("--state-file", default=os.environ.get("RSS2BROADCAST_STATE", ".newsletter-state/kit-rss2broadcast.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    items = parse_feed(fetch_feed(args.feed_url))
    item = select_new_item(items, _load_state(Path(args.state_file)))
    if item is None:
        print("No unprocessed RSS item.")
        return 0
    payload = build_broadcast_payload(item, args.send_at)
    if args.dry_run:
        print(json.dumps({"item": item.__dict__, "payload": payload}, ensure_ascii=False, indent=2))
        return 0

    api_key = os.environ.get("KIT_API_KEY")
    if not api_key:
        print("KIT_API_KEY is required unless --dry-run is used.", file=sys.stderr)
        return 2
    response = create_broadcast(payload, api_key, os.environ.get("KIT_API_URL", "https://api.kit.com/v4/broadcasts"))
    _save_state(Path(args.state_file), item.guid)
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
