from __future__ import annotations

import json
import os

import requests
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()  # load TELEGRAM_BOT_TOKEN from .env

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in env")

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(url, timeout=10)

    print(resp.status_code)
    print(json.dumps(resp.json(), indent=2))


if __name__ == "__main__":
    main()
