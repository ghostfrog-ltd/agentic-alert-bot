from __future__ import annotations

from dotenv import load_dotenv

from infrastructure.utils.telegram import send_telegram_message


def main() -> None:
    # Load .env so TELEGRAM_* vars are available
    load_dotenv()

    ok = send_telegram_message(
        "🐸 GhostFrog test message.\n\n"
        "If you can read this on your phone, Telegram wiring works. 🚀"
    )
    print("Sent:", ok)


if __name__ == "__main__":
    main()
