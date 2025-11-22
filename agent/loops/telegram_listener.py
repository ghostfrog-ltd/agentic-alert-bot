from __future__ import annotations

import os
import time

from dotenv import load_dotenv

from agent.actions.telegram.hot_listings import build_hot_listings_message
from agent.actions.telegram.roi_summary import build_roi_message
from infrastructure.adapters.telegram import TelegramAdapter
from infrastructure.utils.logger import get_logger
from infrastructure.db import schema

logger = get_logger(__name__)


def env_flag(name: str, default: str = "0") -> bool:
    """
    Simple env helper: "1/true/yes/on" => True, everything else => False.
    """
    val = os.getenv(name, default)
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


# Per-env toggle: should this process actually run the Telegram listener?
TELEGRAM_BOT_ENABLED = env_flag("GF_ENABLE_TELEGRAM_BOT", "1")


# ---------------------------------------------------------
# Handlers
# ---------------------------------------------------------

def mark_time_expired_as_ended() -> int:
    """
    Anything beyond its scheduled end time is no longer live.
    """
    conn = schema.get_fresh_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auction_listings
               SET status = 'ended'
             WHERE status = 'live'
               AND end_time <= NOW();
            """
        )
        return cur.rowcount


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

    if not TELEGRAM_BOT_ENABLED:
        logger.info(
            "[telegram] GF_ENABLE_TELEGRAM_BOT=0 – Telegram listener disabled for this env"
        )
        return

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
                mark_time_expired_as_ended()
                handle_hot_command(adapter, chat_id)

            elif text.startswith("/roi"):
                mark_time_expired_as_ended()
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
