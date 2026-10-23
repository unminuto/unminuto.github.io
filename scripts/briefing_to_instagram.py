#!/usr/bin/env python3
"""Render and publish one three-slide Instagram carousel per briefing item."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VISIBLE_SITE = "unminuto.news"
PAPER, INK, PLUM, VIOLET, PINK, SUN, LILAC, MUTED, LINE = (
    "#F4EFEA", "#21172F", "#251848", "#7C4DFF", "#FF4F87", "#FFDE02", "#F1E8F5", "#6F6378", "#D6CADF"
)


def clean(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def wrap(value: str, width: int) -> list[str]:
    return textwrap.wrap(value, width=width, break_long_words=False, break_on_hyphens=False)


def text(lines: list[str] | str, x: int, y: int, size: int, color: str, *, weight: int = 700, leading: float = 1.08, letter_spacing: str = "-1.2", anchor: str = "start", opacity: str | None = None) -> str:
    lines = lines if isinstance(lines, list) else str(lines).split("\n")
    opacity_attr = f' opacity="{opacity}"' if opacity else ""
    spans = "".join(f'<tspan x="{x}" dy="{0 if i == 0 else int(size * leading)}">{html.escape(line)}</tspan>' for i, line in enumerate(lines))
    return f'<text x="{x}" y="{y}" text-anchor="{anchor}" fill="{color}"{opacity_attr} font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{weight}" letter-spacing="{letter_spacing}">{spans}</text>'


def bars(x: int, y: int, width: int, height: int) -> str:
    a, b, c = round(width * .34), round(width * .33), width - round(width * .34) - round(width * .33)
    return f'<rect x="{x}" y="{y}" width="{a}" height="{height}" fill="{PINK}"/><rect x="{x+a}" y="{y}" width="{b}" height="{height}" fill="{VIOLET}"/><rect x="{x+a+b}" y="{y}" width="{c}" height="{height}" fill="{SUN}"/>'


def chrome(step: str, section: str, url: str, *, dark: bool = False) -> str:
    background, foreground, secondary = (PLUM, "#FFF9FF", "#D9D0E4") if dark else (PAPER, INK, MUTED)
    return f'''<rect width="1080" height="1350" fill="{background}"/>{bars(0, 0, 1080, 14)}
{text("un minuto", 70, 104, 43, foreground, weight=900, letter_spacing="-3")}{bars(70, 121, 252, 6)}
{text("GERMANIA, IN BREVE", 70, 178, 18, "#C7B9FF" if dark else VIOLET, weight=900, letter_spacing="2")}
{text(section.upper(), 70, 232, 17, secondary, weight=800, letter_spacing="1.5")}
{text(step, 1010, 232, 17, "#C7B9FF" if dark else VIOLET, weight=900, letter_spacing="1.5", anchor="end")}
<rect x="70" y="1160" width="940" height="1" fill="#4E3976"/>{text(url, 70, 1220, 17, secondary if dark else INK, weight=800, letter_spacing="-.35")}'''


def svg(content: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350" viewBox="0 0 1080 1350">{content}</svg>'


def topic_tags(title: str) -> list[str]:
    low = title.lower()
    tags = ["#Germania", "#NotizieGermania", "#GermaniaInBreve", "#unminuto"]
    if any(word in low for word in ("qiagen", "investimenti", "fiscali", "auto")):
        tags.append("#EconomiaTedesca")
    elif any(word in low for word in ("php", "sicurezza")):
        tags.append("#Cybersicurezza")
    elif "trasporto" in low:
        tags.extend(["#Trasporti", "#Deutschlandticket"])
    elif "ue" in low:
        tags.append("#Europa")
    return tags


def parse_edition(path: Path) -> tuple[str, list[tuple[str, str]]]:
    raw = path.read_text(encoding="utf-8")
    permalink = re.search(r"^permalink:\s*(\S+)\s*$", raw, re.MULTILINE)
    if not permalink:
        raise ValueError("edition has no permalink")
    articles = re.findall(r'<article class="briefing-item">\s*<h2>(.*?)</h2>\s*<p>(.*?)</p>', raw, re.S)
    if not articles:
        raise ValueError("edition contains no briefing-item articles")
    return permalink.group(1), [(clean(title), clean(body)) for title, body in articles]


def title_slide(index: int, title: str, body: str, url: str) -> str:
    deck = " ".join(body.split()[:18]) + "…"
    return svg(chrome("01 / 03", "Briefing quotidiano", url) + f'''{text(f"{index:02d}", 1010, 550, 420, LILAC, weight=900, letter_spacing="-30", anchor="end", opacity=".9")}
{text(wrap(title.upper(), 15), 70, 400, 82, INK, weight=900, leading=.94, letter_spacing="-4")}
<rect x="70" y="730" width="760" height="2" fill="{VIOLET}"/>
{text(wrap(deck, 42), 70, 795, 30, "#4E4358", weight=500, leading=1.34, letter_spacing="-.5")}{bars(70, 960, 940, 10)}''')


def article_slide(title: str, body: str, url: str) -> str:
    # Scale text down only as needed: every article must fit on this single requested slide.
    size, width = 29, 45
    lines = wrap(body, width)
    while len(lines) > 16 and size > 18:
        size -= 1
        width += 3
        lines = wrap(body, width)
    if len(lines) > 22:
        raise ValueError(f"article too long for a single Instagram reading slide: {title}")
    return svg(chrome("02 / 03", "La notizia", url) + f'''{text("02", 1010, 510, 340, LILAC, weight=900, letter_spacing="-25", anchor="end", opacity=".82")}
{text(wrap(title.upper(), 24), 70, 370, 45, INK, weight=900, leading=.98, letter_spacing="-2")}
<rect x="70" y="500" width="760" height="2" fill="{PINK}"/>
{text(lines, 70, 560, size, "#4E4358", weight=500, leading=1.36, letter_spacing="-.35")}{bars(70, 1080, 620, 8)}''')


def cta_slide(url: str) -> str:
    return svg(chrome("03 / 03", "Il briefing quotidiano", url, dark=True) + f'''{text("→", 1010, 540, 330, SUN, weight=900, letter_spacing="-20", anchor="end", opacity=".9")}
{text(["TUTTE LE NEWS.", "TUTTE LE FONTI."], 70, 405, 74, "#FFF9FF", weight=900, leading=.95, letter_spacing="-4")}
<rect x="70" y="600" width="760" height="2" fill="#6D5B93"/>
{text(["Leggi il briefing giornaliero", "e iscriviti alla newsletter."], 70, 680, 36, "#D9D0E4", weight=500, leading=1.32, letter_spacing="-.7")}
{text("APRI IL SITO  →", 70, 870, 26, SUN, weight=900, letter_spacing=".4")}{bars(70, 970, 760, 10)}''')


def render(edition: Path, output: Path) -> list[dict[str, object]]:
    permalink, articles = parse_edition(edition)
    url = f"{VISIBLE_SITE}{permalink}"
    output.mkdir(parents=True, exist_ok=True)
    result = []
    for index, (title, body) in enumerate(articles, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:55]
        folder = output / f"{index:02d}-{slug}"
        folder.mkdir(exist_ok=True)
        slides = [title_slide(index, title, body, url), article_slide(title, body, url), cta_slide(url)]
        pngs = []
        for number, source in enumerate(slides, 1):
            svg_path, png_path = folder / f"{number:02d}.svg", folder / f"{number:02d}.png"
            svg_path.write_text(source, encoding="utf-8")
            subprocess.run(("rsvg-convert", "-w", "1080", "-h", "1350", str(svg_path), "-o", str(png_path)), check=True)
            pngs.append(png_path)
        caption = f"{title}\n\n{body}\n\nLeggi tutte le notizie, le fonti e il contesto nel briefing giornaliero di un minuto: https://{url}\n\n{' '.join(topic_tags(title))}"
        (folder / "caption.txt").write_text(caption + "\n", encoding="utf-8")
        result.append({"title": title, "caption": caption, "folder": folder, "slides": pngs, "url": url})
    return result


def request_json(url: str, data: dict[str, str]) -> dict:
    encoded = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=encoded, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def publish(carousel: dict[str, object], image_base_url: str, account_id: str, token: str) -> dict:
    base = f"https://graph.facebook.com/v21.0/{account_id}"
    children = []
    folder_name = Path(str(carousel["folder"])).name
    for number in range(1, 4):
        image_url = f"{image_base_url.rstrip('/')}/{folder_name}/{number:02d}.png"
        created = request_json(f"{base}/media", {"image_url": image_url, "is_carousel_item": "true", "access_token": token})
        children.append(created["id"])
    parent = request_json(f"{base}/media", {"media_type": "CAROUSEL", "children": ",".join(children), "caption": str(carousel["caption"]), "access_token": token})
    published = request_json(f"{base}/media_publish", {"creation_id": parent["id"], "access_token": token})
    return {"title": carousel["title"], "media_id": published.get("id"), "container_id": parent["id"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image-base-url", help="Public base URL corresponding to --output folders")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    carousels = render(args.edition, args.output)
    if args.publish:
        if not args.image_base_url:
            parser.error("--image-base-url is required with --publish")
        token, account_id = os.environ.get("INSTAGRAM_ACCESS_TOKEN"), os.environ.get("INSTAGRAM_ACCOUNT_ID")
        if not token or not account_id:
            raise RuntimeError("INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID are required")
        published = [publish(item, args.image_base_url, account_id, token) for item in carousels]
    else:
        published = []
    print(json.dumps({"carousels": len(carousels), "slides": len(carousels) * 3, "output": str(args.output), "published": published}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
