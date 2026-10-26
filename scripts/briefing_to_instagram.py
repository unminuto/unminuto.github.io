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
from datetime import UTC, datetime, timedelta
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VISIBLE_SITE = "unminuto.news"
DIRECT_INSTAGRAM_API = "https://graph.instagram.com/v24.0"
DEFAULT_CREDENTIALS = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "secrets" / "unminuto-instagram.env"
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
    return f'''<rect width="1080" height="1080" fill="{background}"/>{bars(0, 0, 1080, 16)}
{text("un minuto", 64, 94, 46, foreground, weight=900, letter_spacing="-3")}{bars(64, 111, 272, 7)}
{text("GERMANIA, IN BREVE", 64, 168, 19, "#C7B9FF" if dark else VIOLET, weight=900, letter_spacing="2")}
{text(section.upper(), 64, 218, 18, secondary, weight=800, letter_spacing="1.5")}
{text(step, 1016, 218, 18, "#C7B9FF" if dark else VIOLET, weight=900, letter_spacing="1.5", anchor="end")}
<rect x="64" y="962" width="952" height="1" fill="#4E3976"/>{text(url, 64, 1018, 22, secondary if dark else INK, weight=900, letter_spacing="-.55")}'''


def svg(content: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="0 0 1080 1080">{content}</svg>'


def topic_tags(title: str) -> list[str]:
    low = title.lower()
    tags = ["#Germania", "#NotizieGermania", "#GermaniaInBreve", "#unminuto"]
    if any(word in low for word in ("energia", "elettricità", "carburant")):
        tags.extend(["#Energia", "#PoliticaEnergetica"])
    if any(word in low for word in ("länder", "land")):
        tags.append("#Laender")
    if any(word in low for word in ("fiscal", "finanza", "bilancio")):
        tags.extend(["#FinanzaPubblica", "#Fisco"])
    if any(word in low for word in ("qiagen", "investimenti", "auto")):
        tags.append("#EconomiaTedesca")
    if any(word in low for word in ("php", "sicurezza", "digitale", "app")):
        tags.extend(["#Digitale", "#Cybersicurezza"])
    if any(word in low for word in ("armi", "estremist")):
        tags.extend(["#Armi", "#Estremismo"])
    if "trasporto" in low:
        tags.extend(["#Trasporti", "#Deutschlandticket"])
    if "ue" in low or "europa" in low:
        tags.append("#Europa")
    return list(dict.fromkeys(tags))


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
    # Keep the opening hierarchy bold but reserve a protected area for the deck.
    # Longer headlines receive fewer, wider lines rather than overlapping content.
    title_size, title_width = 80, 19
    title_lines = wrap(title.upper(), title_width)
    while len(title_lines) > 4 and title_size > 64:
        title_size -= 2
        title_width += 1
        title_lines = wrap(title.upper(), title_width)
    return svg(chrome("01 / 03", "Briefing quotidiano", url, dark=True) + f'''{text(f"{index:02d}", 1016, 555, 385, "#5A4580", weight=900, letter_spacing="-30", anchor="end", opacity=".9")}
{text(title_lines, 64, 350, title_size, "#FFF9FF", weight=900, leading=.91, letter_spacing="-4.2")}
<rect x="64" y="680" width="800" height="3" fill="{SUN}"/>
{text(wrap(deck, 38), 64, 750, 35, "#D9D0E4", weight=500, leading=1.27, letter_spacing="-.65")}{bars(64, 895, 952, 12)}''')


def article_slide(title: str, body: str, url: str) -> str:
    # The reading slide intentionally omits the headline: it has already been read on slide one.
    # Scale down only as needed while retaining a large, legible default size.
    size, width = 36, 39
    lines = wrap(body, width)
    while len(lines) > 14 and size > 24:
        size -= 1
        width += 3
        lines = wrap(body, width)
    if len(lines) > 16:
        raise ValueError(f"article too long for a single Instagram reading slide: {title}")
    return svg(chrome("02 / 03", "La notizia", url) + f'''{text("02", 1016, 480, 300, LILAC, weight=900, letter_spacing="-25", anchor="end", opacity=".75")}
<rect x="64" y="285" width="240" height="8" fill="{PINK}"/>
{text(lines, 64, 365, size, "#4E4358", weight=500, leading=1.30, letter_spacing="-.45")}''')


def cta_slide(url: str) -> str:
    return svg(chrome("03 / 03", "Il briefing quotidiano", url, dark=True) + f'''{text("→", 1016, 460, 290, SUN, weight=900, letter_spacing="-20", anchor="end", opacity=".9")}
{text(["TUTTE LE NEWS.", "TUTTE LE FONTI."], 64, 370, 80, "#FFF9FF", weight=900, leading=.93, letter_spacing="-4.5")}
<rect x="64" y="545" width="800" height="3" fill="#6D5B93"/>
{text(["Il briefing giornaliero", "con fonti e contesto."], 64, 625, 38, "#D9D0E4", weight=500, leading=1.25, letter_spacing="-.8")}
{text("unminuto.news", 64, 820, 67, SUN, weight=900, letter_spacing="-3")}
{text("APRI IL SITO  →", 64, 885, 24, "#FFF9FF", weight=900, letter_spacing="1.2")}{bars(64, 915, 952, 12)}''')


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
            subprocess.run(("rsvg-convert", "-w", "1080", "-h", "1080", str(svg_path), "-o", str(png_path)), check=True)
            pngs.append(png_path)
        caption = f"{title}\n\n{body}\n\nLeggi tutte le notizie, le fonti e il contesto nel briefing giornaliero di un minuto: https://{url}\n\n{' '.join(topic_tags(title))}"
        (folder / "caption.txt").write_text(caption + "\n", encoding="utf-8")
        result.append({"title": title, "caption": caption, "folder": folder, "slides": pngs, "url": url})
    return result


def request_json(url: str, data: dict[str, str]) -> dict:
    encoded = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=encoded, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("error", {}).get("message", body)
        except json.JSONDecodeError:
            detail = body
        raise RuntimeError(f"Instagram Graph API {exc.code}: {detail}") from exc


def get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Instagram Graph API {exc.code}: {body}") from exc


def wait_for_container(container_id: str, token: str, timeout_seconds: int = 180) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = get_json(
            f"{DIRECT_INSTAGRAM_API}/{container_id}?" + urllib.parse.urlencode({"fields": "status_code,status"}),
            token,
        )
        state = status.get("status_code") or status.get("status")
        if state == "FINISHED":
            return
        if state in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Instagram media container {container_id} failed: {status}")
        time.sleep(2)
    raise RuntimeError(f"Instagram media container {container_id} was not ready within {timeout_seconds} seconds")


def load_credentials(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"missing Instagram credentials file: {path}")
    credentials = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            credentials[key] = value
    if not credentials.get("INSTAGRAM_ACCESS_TOKEN"):
        raise RuntimeError(f"INSTAGRAM_ACCESS_TOKEN is missing from {path}")
    return credentials


def save_credentials(path: Path, credentials: dict[str, str]) -> None:
    """Atomically persist a refreshed access token without exposing it in logs."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        "# Managed locally. Never commit or paste these values into chat.\n"
        + "".join(f"{key}={value}\n" for key, value in credentials.items()),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def refresh_access_token(credentials_path: Path, credentials: dict[str, str]) -> dict[str, str]:
    """Refresh a valid long-lived Instagram token before each daily publish.

    Meta permits refreshes after 24 hours and resets the token lifetime to 60
    days. An already expired or revoked token cannot be recovered here and
    requires the one-time Business Login flow again.
    """
    token = credentials["INSTAGRAM_ACCESS_TOKEN"]
    endpoint = DIRECT_INSTAGRAM_API.rsplit("/v", 1)[0] + "/refresh_access_token"
    url = endpoint + "?" + urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": token})
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            refreshed = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "Instagram token refresh failed; it is expired, revoked, or missing the required permission. "
            "Complete Business Login once to issue a new long-lived token."
        ) from exc
    new_token = refreshed.get("access_token")
    if not new_token:
        raise RuntimeError("Instagram token refresh returned no access token")
    credentials["INSTAGRAM_ACCESS_TOKEN"] = str(new_token)
    if refreshed.get("expires_in"):
        expires_at = datetime.now(UTC) + timedelta(seconds=int(refreshed["expires_in"]))
        credentials["INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT"] = expires_at.isoformat()
    save_credentials(credentials_path, credentials)
    return credentials


def direct_account_id(token: str) -> str:
    request = urllib.request.Request(
        f"{DIRECT_INSTAGRAM_API}/me?" + urllib.parse.urlencode({"fields": "id,username"}),
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            account = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"could not read the direct Instagram account: {body}") from exc
    if not account.get("id") or not account.get("username"):
        raise RuntimeError("Instagram API did not return an account id and username")
    return str(account["id"])


def read_state(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid Instagram publication state: {path}") from exc
    return data if isinstance(data, dict) else {}


def write_state(path: Path, state: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def publication_key(carousel: dict[str, object]) -> str:
    return f"{carousel['url']}\n{carousel['title']}"


def publish(carousel: dict[str, object], image_base_url: str, account_id: str, token: str) -> dict:
    base = f"{DIRECT_INSTAGRAM_API}/{account_id}"
    children = []
    folder_name = Path(str(carousel["folder"])).name
    for number in range(1, 4):
        image_url = f"{image_base_url.rstrip('/')}/{folder_name}/{number:02d}.png"
        created = request_json(f"{base}/media", {"image_url": image_url, "is_carousel_item": "true", "access_token": token})
        children.append(created["id"])
        wait_for_container(created["id"], token)
    parent = request_json(f"{base}/media", {"media_type": "CAROUSEL", "children": ",".join(children), "caption": str(carousel["caption"]), "access_token": token})
    wait_for_container(parent["id"], token)
    published = request_json(f"{base}/media_publish", {"creation_id": parent["id"], "access_token": token})
    return {"title": carousel["title"], "media_id": published.get("id"), "container_id": parent["id"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image-base-url", help="Public base URL corresponding to --output folders")
    parser.add_argument("--limit", type=int, help="Render and publish only the first N stories (for controlled tests)")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--credentials-file", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "state" / "unminuto-instagram-published.json",
        help="Local idempotency record used only when publishing",
    )
    args = parser.parse_args(argv)
    carousels = render(args.edition, args.output)
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        carousels = carousels[:args.limit]
    if args.publish:
        if not args.image_base_url:
            parser.error("--image-base-url is required with --publish")
        credentials = refresh_access_token(args.credentials_file, load_credentials(args.credentials_file))
        token = credentials["INSTAGRAM_ACCESS_TOKEN"]
        account_id = direct_account_id(token)
        state = read_state(args.state_file)
        published = []
        for item in carousels:
            key = publication_key(item)
            if key in state:
                continue
            result = publish(item, args.image_base_url, account_id, token)
            published.append(result)
            state[key] = {"media_id": str(result["media_id"]), "title": str(result["title"])}
            write_state(args.state_file, state)
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
