#!/usr/bin/env python3
"""Install direct Instagram Business Login credentials without exposing secrets."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

DEFAULT_APP_ID = "1707668857359495"
DEFAULT_PATH = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "secrets" / "unminuto-instagram.env"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--app-id", default=DEFAULT_APP_ID)
    args = parser.parse_args()

    token = getpass.getpass("Paste the Instagram user access token (input is hidden): ").strip()
    secret = getpass.getpass("Paste the Instagram app secret (input is hidden): ").strip()
    if not token or not secret:
        raise SystemExit("Both the access token and app secret are required; no file was written.")

    args.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = (
        "# Managed locally. Never commit or paste these values into chat.\n"
        f"INSTAGRAM_APP_ID={args.app_id}\n"
        f"INSTAGRAM_APP_SECRET={secret}\n"
        f"INSTAGRAM_ACCESS_TOKEN={token}\n"
    )
    fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    finally:
        try:
            os.chmod(args.path, 0o600)
        except FileNotFoundError:
            pass
    print(f"Instagram credentials saved with mode 0600 at {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
