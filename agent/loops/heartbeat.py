# agent/loops/heartbeat.py
from __future__ import annotations

import os, time, random
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()  # ensure all modules see .env values

from agent.actions.scrape_sources import run as run_scrape
from agent.actions.close_auctions import tick as close_tick
from agent.actions.scan_ending_soon import run as run_scan
from agent.actions.alert_new_listings import run as alert_new_listings
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

# compute_daily_comps may not exist on some branches — make optional
try:
    from infrastructure.db.schema import compute_daily_comps
except Exception:
    compute_daily_comps = None

# -----------------------------
# ENV KNOBS (safe defaults)
# -----------------------------
SLEEP_BASE_S = float(os.getenv("GF_HEARTBEAT_SLEEP_SECONDS", "5"))
SLEEP_JITTER = float(os.getenv("GF_HEARTBEAT_JITTER_S", "0.7"))
REFRESH_HRS = float(os.getenv("GF_COMPS_REFRESH_HOURS", "6"))

_last_comps_at = None


def _sleep_with_jitter():
    time.sleep(SLEEP_BASE_S + random.uniform(0, SLEEP_JITTER))


def _maybe_refresh_comps():
    if not compute_daily_comps:
        return

    global _last_comps_at
    now = datetime.now(timezone.utc)

    if _last_comps_at is None or (now - _last_comps_at) > timedelta(hours=REFRESH_HRS):
        try:
            compute_daily_comps()
            _last_comps_at = now
            #logger.info("[Heartbeat] comps refreshed")
        except Exception as e:
            logger.error(f"[Heartbeat] comps refresh failed: {e}")


def _should_scrape_safe() -> bool:
    try:
        from agent.state import should_scrape_now
        return bool(should_scrape_now())
    except Exception as e:
        logger.error(f"[Heartbeat] should_scrape_now failed: {e}")
        return False


def tick():
    # 1) Scrape (respect your should_scrape_now gate)
    if _should_scrape_safe():
        try:
            run_scrape()
        except Exception as e:
            logger.error(f"[Heartbeat] scrape run failed: {e}")

    # 2) Close auctions (mark sold/ended + final_price)
    try:
        close_tick()
    except Exception as e:
        logger.error(f"[Heartbeat] close_tick failed: {e}")

    # 3) Ensure comps are reasonably fresh
    _maybe_refresh_comps()

    # 4) Scan ending soon & raise alerts
    try:
        run_scan()
    except Exception as e:
        logger.error(f"[Heartbeat] scan run failed: {e}")

    # 5) new listing email
    try:
        alert_new_listings()
    except Exception as e:
        logger.error(f"[Heartbeat] new listing email failed: {e}")

    _sleep_with_jitter()
