# main.py
from __future__ import annotations

import argparse
import os
import threading
import time
import traceback

from agent.loops.heartbeat import tick as heartbeat_tick
from agent.loops.heartbeat import tick_forever as  heartbeat_tick
from agent.loops.telegram_listener import main as telegram_tick
from agent.actions.scrape.sources import run as run_scrape

HEARTBEAT_HEALTH_LOG_SECS = int(os.getenv("GF_HEARTBEAT_HEALTH_LOG_SECS", "60"))
RESTART_BACKOFF_SECS = int(os.getenv("GF_THREAD_RESTART_BACKOFF_SECS", "3"))

def _run_supervised(target, name: str) -> None:
    """
    Run a target callable in a crash-restarting loop with noisy logging.
    If the target returns (no loop inside) we treat that like a crash.
    """
    print(f"[supervisor] starting '{name}' thread")
    while True:
        try:
            print(f"[{name}] starting")
            target()
            # If we ever get here, the target returned (not expected). Log loudly.
            print(f"[{name}] WARNING: target returned (no loop?). Restarting in {RESTART_BACKOFF_SECS}s.")
        except Exception as e:
            print(f"[{name}] ERROR: crashed with {e.__class__.__name__}: {e}")
            traceback.print_exc()
            print(f"[{name}] restarting in {RESTART_BACKOFF_SECS}s...")
        time.sleep(RESTART_BACKOFF_SECS)

def _heartbeat_wrapper() -> None:
    """
    Wrap heartbeat to add periodic 'still alive' logs even if heartbeat is quiet.
    Assumes heartbeat_tick() either loops internally OR runs once quickly.
    We call it in a small driver loop so a one-shot tick still repeats.
    """
    last_health = 0.0
    while True:
        # Call the real heartbeat work (once). If heartbeat_tick() already loops,
        # it should return quickly or do a unit of work and return.
        heartbeat_tick()

        now = time.time()
        if now - last_health >= HEARTBEAT_HEALTH_LOG_SECS:
            print(f"[heartbeat] alive @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
            last_health = now

        # Prevent tight loop; tweak if your tick already sleeps internally
        time.sleep(1)

def run_heartbeat_and_telegram() -> None:
    t1 = threading.Thread(target=lambda: _run_supervised(_heartbeat_wrapper, "heartbeat"),
                          name="heartbeat", daemon=True)
    t2 = threading.Thread(target=lambda: _run_supervised(telegram_tick, "telegram"),
                          name="telegram", daemon=True)

    t1.start()
    t2.start()

    # keep the main thread alive and surface if one ever terminates (it shouldn't)
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
    # Thread exception hook (Python 3.8+) to ensure crashes are visible
    def _thread_excepthook(args):
        print(f"[thread-excepthook] {args.exc_type.__name__}: {args.exc_value}")
        traceback.print_tb(args.exc_traceback)
    try:
        threading.excepthook = _thread_excepthook  # type: ignore[attr-defined]
    except Exception:
        pass

    main()
