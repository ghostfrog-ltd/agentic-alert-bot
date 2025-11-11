# main.py
from __future__ import annotations

import argparse
import threading

from agent.loops.heartbeat import tick
from agent.loops.telegram_listener import main as telegram_tick
from agent.actions.scrape.sources import run as run_scrape


def run_heartbeat_and_telegram() -> None:
    """
    Run the heartbeat loop and Telegram listener together.
    Each runs in its own thread so they don't block each other.
    """
    t1 = threading.Thread(target=tick, name="heartbeat", daemon=True)
    t2 = threading.Thread(target=telegram_tick, name="telegram", daemon=True)

    t1.start()
    t2.start()

    # keep the main thread alive
    t1.join()
    t2.join()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["heartbeat", "scrape"])
    args = parser.parse_args()

    if args.cmd == "heartbeat":
        run_heartbeat_and_telegram()
    elif args.cmd == "scrape":
        run_scrape()


if __name__ == "__main__":
    main()
