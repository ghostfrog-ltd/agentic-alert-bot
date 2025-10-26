# agent/loops/heartbeat.py
from datetime import datetime, timedelta, timezone
import time

from agent.actions.scrape_sources import run as run_scrape
from agent.actions.close_auctions import tick as close_tick
from agent.actions.scan_ending_soon import run as run_scan
from agent.state import should_scrape_now
from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import compute_daily_comps  # <- add this if you implemented it

logger = get_logger(__name__)

_last_comps_at = None

def _maybe_refresh_comps():
    global _last_comps_at
    now = datetime.now(timezone.utc)
    # refresh at most every 6h (cheap) — or schedule nightly if you prefer
    if _last_comps_at is None or (now - _last_comps_at) > timedelta(hours=6):
        try:
            compute_daily_comps()
            _last_comps_at = now
            logger.info("[Heartbeat] comps refreshed")
        except Exception as e:
            logger.error(f"[Heartbeat] comps refresh failed: {e}")

def tick():
    # 1) Scrape (respect your should_scrape_now gate)
    if should_scrape_now():
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

    time.sleep(5)
