# infrastructure/adapters/telegram.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Iterable, Iterator, Optional

import requests

from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TelegramUpdate:
    update_id: int
    chat_id: int
    text: str
    raw: Dict[str, Any]


class TelegramAdapter:
    """
    Thin wrapper around the Telegram Bot API.

    - Handles base URL + token
    - Normalises updates into TelegramUpdate objects
    - Provides send_message() + fetch_updates()
    """

    def __init__(
        self,
        bot_token: str,
        default_chat_id: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.bot_token = bot_token
        self.default_chat_id = default_chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.session = session or requests.Session()

    # --------- factory ---------

    @classmethod
    def from_env(cls) -> "TelegramAdapter":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment")

        chat_id_raw = os.getenv("TELEGRAM_CHAT_ID")
        default_chat_id: Optional[int] = int(chat_id_raw) if chat_id_raw else None

        return cls(bot_token=token, default_chat_id=default_chat_id)

    # --------- core calls ---------

    def send_message(
        self,
        text: str,
        *,
        chat_id: Optional[int] = None,
        parse_mode: Optional[str] = None,
        disable_web_page_preview: bool = True,
    ) -> bool:
        """
        Send a message to Telegram.

        If chat_id is not given, uses default_chat_id from env.
        """
        target_chat_id = chat_id or self.default_chat_id
        if not target_chat_id:
            logger.warning(
                "TelegramAdapter.send_message called without chat_id "
                "and no TELEGRAM_CHAT_ID configured"
            )
            return False

        url = f"{self.base_url}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": target_chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = self.session.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning(
                    "Telegram send failed (%s): %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return False
            return True
        except Exception:
            logger.exception("Telegram send error")
            return False

    def fetch_updates(
        self,
        *,
        offset: Optional[int] = None,
        timeout_s: int = 0,
    ) -> List[TelegramUpdate]:
        """
        Fetch updates via getUpdates.

        - offset: pass last_update_id + 1 to avoid re-processing
        - timeout_s: if > 0, uses Telegram long-polling (server holds
                     connection open until something happens or timeout)
        """
        params: Dict[str, Any] = {}
        if offset is not None:
            params["offset"] = offset
        if timeout_s > 0:
            params["timeout"] = timeout_s  # long polling

        url = f"{self.base_url}/getUpdates"

        try:
            resp = self.session.get(url, params=params, timeout=timeout_s + 5)
            data = resp.json()
        except Exception:
            logger.exception("Telegram fetch_updates error")
            return []

        if not data.get("ok"):
            logger.warning("Telegram getUpdates not ok: %s", data)
            return []

        updates: List[TelegramUpdate] = []
        for raw in data.get("result", []):
            msg = raw.get("message") or raw.get("edited_message")
            if not msg:
                continue
            text = msg.get("text")
            if not text:
                continue

            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None:
                continue

            updates.append(
                TelegramUpdate(
                    update_id=raw["update_id"],
                    chat_id=int(chat_id),
                    text=text,
                    raw=raw,
                )
            )

        return updates
