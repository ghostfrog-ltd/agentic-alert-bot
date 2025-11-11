from __future__ import annotations

import time

from dotenv import load_dotenv

from infrastructure.adapters.telegram import TelegramAdapter
from infrastructure.utils.logger import get_logger
from agent.actions.telegram.roi_summary import build_roi_message

logger = get_logger(__name__)


def main() -> None:
    load_dotenv()
    adapter = TelegramAdapter.from_env()

    logger.info("Starting Telegram listener loop…")
    offset: int | None = None

    while True:
        updates = adapter.fetch_updates(offset=offset, timeout_s=10)  # long poll

        for upd in updates:
            offset = upd.update_id + 1  # advance cursor
            text = upd.text.strip().lower()

            logger.info("Telegram command from %s: %s", upd.chat_id, text)

            if text == "/ping":
                adapter.send_message("🐸 Pong from GhostFrog HQ!")


            elif text.startswith("/roi"):

                # Allow `/roi` or `/roi 5`

                parts = text.split()

                limit = 3

                if len(parts) > 1:

                    try:

                        limit = max(1, min(10, int(parts[1])))

                    except ValueError:

                        pass

                msg = build_roi_message(limit=limit)

                adapter.send_message(msg)

            elif text == "/stats":
                # TODO: hook into DB / ROI stats here
                adapter.send_message("📊 Stats command received (todo wire DB).")
            else:
                adapter.send_message(
                    "🤔 Unknown command. Try /ping or /stats for now."
                )

        # small safety sleep – most of the time will be spent in long-poll
        time.sleep(1)


if __name__ == "__main__":
    main()
