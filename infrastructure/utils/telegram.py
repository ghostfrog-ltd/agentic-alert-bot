# infrastructure/utils/telegram.py
from __future__ import annotations

from typing import Optional

from infrastructure.adapters.telegram import TelegramAdapter
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

_adapter: Optional[TelegramAdapter] = None


def _get_adapter() -> Optional[TelegramAdapter]:
    global _adapter
    if _adapter is None:
        try:
            _adapter = TelegramAdapter.from_env()
        except Exception:
            logger.exception("Failed to initialise TelegramAdapter from env")
            _adapter = None
    return _adapter


def send_telegram_message(
    text: str,
    *,
    parse_mode: str | None = None,
    disable_web_page_preview: bool = True,
) -> bool:
    """
    Simple convenience wrapper used across the codebase.
    """
    adapter = _get_adapter()
    if not adapter:
        logger.warning("Telegram not configured; message not sent.")
        return False

    return adapter.send_message(
        text,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview,
    )
