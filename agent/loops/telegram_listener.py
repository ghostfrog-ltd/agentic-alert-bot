from __future__ import annotations

import os
import time

from dotenv import load_dotenv

from agent.actions.telegram.hot_listings import build_hot_listings_message
from agent.actions.telegram.roi_summary import build_roi_message

from infrastructure.adapters.telegram import TelegramAdapter
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------
# Handlers
# ---------------------------------------------------------

def handle_hot_command(adapter: TelegramAdapter, chat_id: int) -> None:
    """
    Respond to /hot for THIS user/chat only.
    Never broadcasts.
    """
    try:
        msg = build_hot_listings_message(limit=3)
        adapter.send_message(msg, chat_id=chat_id)
    except Exception as e:
        logger.error("[telegram] /hot failed: %s", e)
        adapter.send_message("⚠️ Unable to fetch hot listings.", chat_id=chat_id)


def handle_roi_command(adapter: TelegramAdapter, chat_id: int, text: str) -> None:
    """
    Respond to /roi or /roi N for THIS user/chat only.
    Never broadcasts.
    """
    parts = text.split()
    limit = 3

    # Allow: /roi 5
    if len(parts) > 1:
        try:
            limit = max(1, min(10, int(parts[1])))
        except ValueError:
            pass

    try:
        msg = build_roi_message(limit=limit)
        adapter.send_message(msg, chat_id=chat_id)
    except Exception as e:
        logger.error("[telegram] /roi failed: %s", e)
        adapter.send_message("⚠️ Unable to fetch ROI data.", chat_id=chat_id)


# ---------------------------------------------------------
# Main listener loop
# ---------------------------------------------------------

def main() -> None:
    load_dotenv()

    adapter = TelegramAdapter.from_env()
    logger.info("🐸 Starting Telegram listener loop…")

    offset: int | None = None

    while True:
        updates = adapter.fetch_updates(offset=offset, timeout_s=10)

        for upd in updates:
            offset = upd.update_id + 1

            text = (upd.text or "").strip().lower()
            chat_id = upd.chat_id

            logger.info("[telegram] Command from %s: %s", chat_id, text)

            # -------------------------------------
            # Commands
            # -------------------------------------

            if text == "/ping":
                adapter.send_message("🐸 Pong from GhostFrog HQ!", chat_id=chat_id)

            elif text == "/hot":
                handle_hot_command(adapter, chat_id)

            elif text.startswith("/roi"):
                handle_roi_command(adapter, chat_id, text)

            elif text == "/stats":
                adapter.send_message(
                    "📊 Stats command received (TODO: wire DB).",
                    chat_id=chat_id,
                )

            else:
                adapter.send_message(
                    "🤔 Unknown command. Try /ping, /hot, /roi or /stats.",
                    chat_id=chat_id,
                )

        # Prevent tight-loop hammering (long-poll already slows it)
        time.sleep(1)


if __name__ == "__main__":
    main()
