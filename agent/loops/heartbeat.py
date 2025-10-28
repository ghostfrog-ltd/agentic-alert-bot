# agent/loops/heartbeat.py
from __future__ import annotations

import os, time, random, threading
from time import perf_counter
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from infrastructure.utils.logger import get_logger
logger = get_logger(__name__)

# ---------- Optional imports guarded (keep loose coupling)
try:
    from agent.actions.scrape_sources import run as run_scrape
except Exception as e:
    run_scrape = None
    logger.error(f"[Heartbeat] import run_scrape failed: {e}")

try:
    from agent.actions.close_auctions import tick as close_tick
except Exception as e:
    close_tick = None
    logger.error(f"[Heartbeat] import close_tick failed: {e}")

try:
    from agent.actions.scan_ending_soon import run as run_scan
except Exception as e:
    run_scan = None
    logger.error(f"[Heartbeat] import run_scan failed: {e}")

try:
    from agent.actions.alert_new_listings import run as alert_new_listings
except Exception as e:
    alert_new_listings = None
    logger.error(f"[Heartbeat] import alert_new_listings failed: {e}")

try:
    from agent.actions.scan_flips import run as scan_flips
except Exception:
    scan_flips = None  # optional

try:
    from agent.state import should_scrape_now
except Exception:
    should_scrape_now = None  # optional gate

# compute_daily_comps may not exist on some branches — make optional
try:
    from infrastructure.db.schema import compute_daily_comps
except Exception:
    compute_daily_comps = None

# -----------------------------
# ENV KNOBS (safe defaults)
# -----------------------------
SLEEP_BASE_S  = float(os.getenv("GF_HEARTBEAT_SLEEP_SECONDS", "5"))
SLEEP_JITTER  = float(os.getenv("GF_HEARTBEAT_JITTER_S", "0.7"))
REFRESH_HRS   = float(os.getenv("GF_COMPS_REFRESH_HOURS", "6"))

# Feature toggles (easy runtime control)

FEAT_FLIPS    = os.getenv("GF_HEARTBEAT_ENABLE_FLIPS", "0")
FEAT_CLOSE    = os.getenv("GF_HEARTBEAT_ENABLE_CLOSE", "0")
FEAT_SCRAPE   = os.getenv("GF_HEARTBEAT_ENABLE_SCRAPE", "0")
FEAT_COMPS    = os.getenv("GF_HEARTBEAT_ENABLE_COMPS", "0")
FEAT_SCAN     = os.getenv("GF_HEARTBEAT_ENABLE_SCAN_ENDING", "0")
FEAT_ALERTS   = os.getenv("GF_HEARTBEAT_ENABLE_ALERTS", "0")

# Protection against overlapping heartbeats in the same process
_lock = threading.Lock()
_last_comps_at: datetime | None = None


def _sleep_with_jitter():
    time.sleep(SLEEP_BASE_S + random.uniform(0, SLEEP_JITTER))


def _maybe_refresh_comps():
    global _last_comps_at
    if not (FEAT_COMPS and compute_daily_comps):
        return
    now = datetime.now(timezone.utc)
    if _last_comps_at is None or (now - _last_comps_at) > timedelta(hours=REFRESH_HRS):
        t0 = perf_counter()
        try:
            compute_daily_comps()
            _last_comps_at = now
            logger.info(f"[Heartbeat] comps refreshed in {perf_counter()-t0:.2f}s")
        except Exception as e:
            logger.error(f"[Heartbeat] comps refresh failed: {e}")


def _should_scrape_safe() -> bool:
    if not FEAT_SCRAPE or not run_scrape:
        return False
    if should_scrape_now is None:
        # No gate provided; default to True
        return True
    try:
        return bool(should_scrape_now())
    except Exception as e:
        logger.error(f"[Heartbeat] should_scrape_now failed: {e}")
        return False


def _time_step(name: str, fn, *args, **kwargs):
    """Run a step with timing + error isolation."""
    t0 = perf_counter()
    try:
        result = fn(*args, **kwargs)
        dt = perf_counter() - t0
        logger.info(f"[Heartbeat] {name} OK in {dt:.2f}s")
        return result, dt, None
    except Exception as e:
        dt = perf_counter() - t0
        logger.error(f"[Heartbeat] {name} FAILED in {dt:.2f}s: {e}")
        return None, dt, e


def tick():
    # Prevent overlapping runs if the outer scheduler misfires
    if not _lock.acquire(blocking=False):
        logger.warning("[Heartbeat] skip: previous run still active")
        return

    start_wall = perf_counter()
    logger.info("\n\n==================== 🫀 HEARTBEAT START ====================\n")

    try:
        # 1) Close auctions first (final_price + status)
        if FEAT_CLOSE and close_tick:
            _time_step("close_tick", close_tick)

        # 2) Scrape new/updated listings
        if _should_scrape_safe():
            _time_step("scrape_sources", run_scrape)
        else:
            logger.info("[Heartbeat] scrape_sources skipped (gate off or not due)")

        # 3) Recompute comps (uses new finals)
        _maybe_refresh_comps()

        # 4) (Optional) ending-soon alerts
        if FEAT_SCAN and run_scan:
            _time_step("scan_ending_soon", run_scan)

        # 5) NOW run flips — after comps are fresh
        if FEAT_FLIPS and scan_flips:
            _time_step("scan_flips", scan_flips, limit_output=10)

        # 6) Digest email
        if FEAT_ALERTS and alert_new_listings:
            _time_step("alert_new_listings", alert_new_listings)

        total_dt = perf_counter() - start_wall
        logger.info(f"\n[Heartbeat] TOTAL {total_dt:.2f}s")
    finally:
        logger.info("\n===================== 🫀 HEARTBEAT END =====================\n")
        _lock.release()
        _sleep_with_jitter()
