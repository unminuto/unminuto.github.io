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


def build_broadcast_payload(item: FeedItem, send_at: str | None = None) -> dict:
    title = html.escape(item.title)
    link = html.escape(item.link, quote=True)
    summary = html.escape(_email_summary(item.content))
    body = f"<h1>{title}</h1>\n<p>{summary}</p>\n<p><a href=\"{link}\">Leggi l'edizione completa su unminuto.news</a></p>"
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
