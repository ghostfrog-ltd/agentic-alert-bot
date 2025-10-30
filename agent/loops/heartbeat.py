# agent/loops/heartbeat.py
from __future__ import annotations

import os, time, random, threading
from time import perf_counter
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from infrastructure.utils.logger import get_logger
logger = get_logger(__name__)

# -------------------------------------------------
# eBay Auth (new dependency for API-backed scraping)
# -------------------------------------------------
try:
    from infrastructure.ebay.auth import get_auth, EbayAuthError
except Exception as e:
    get_auth = None
    EbayAuthError = Exception  # fallback so except still works
    logger.error(f"[Heartbeat] import get_auth failed: {e}")

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
# Deal with env always being true
# -----------------------------
def env_flag(name: str, default: str = "0") -> bool:
    """
    Read an environment variable and convert it into a proper boolean.

    Recognised truthy values (case-insensitive):
        "1", "true", "yes", "on"
    Recognised falsy values:
        "0", "false", "no", "off", "", or not set at all.

    Examples:
        GF_HEARTBEAT_ENABLE_SCRAPE=1      → True
        GF_HEARTBEAT_ENABLE_CLOSE=false    → False
        GF_HEARTBEAT_ENABLE_ALERTS=off     → False
    """
    val = os.getenv(name, default)
    if val is None:
        return False
    val = val.strip().lower()
    return val in ("1", "true", "yes", "on")

# -----------------------------
# ENV KNOBS (safe defaults)
# -----------------------------
SLEEP_BASE_S  = float(os.getenv("GF_HEARTBEAT_SLEEP_SECONDS", "5"))
SLEEP_JITTER  = float(os.getenv("GF_HEARTBEAT_JITTER_S", "0.7"))
REFRESH_HRS   = float(os.getenv("GF_COMPS_REFRESH_HOURS", "6"))

# Feature toggles (easy runtime control)
FEAT_FLIPS  = env_flag("GF_HEARTBEAT_ENABLE_FLIPS")
FEAT_CLOSE  = env_flag("GF_HEARTBEAT_ENABLE_CLOSE")
FEAT_SCRAPE = env_flag("GF_HEARTBEAT_ENABLE_SCRAPE")
FEAT_COMPS  = env_flag("GF_HEARTBEAT_ENABLE_COMPS")
FEAT_SCAN   = env_flag("GF_HEARTBEAT_ENABLE_SCAN_ENDING")
FEAT_ALERTS = env_flag("GF_HEARTBEAT_ENABLE_ALERTS")

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

    # -------------------------------------------------
    # eBay auth pre-flight
    # -------------------------------------------------
    ebay_token = None
    auth_ok = False
    if get_auth:
        try:
            ebay_token = get_auth().get_token()  # will refresh if needed
            auth_ok = True
            logger.info("[Heartbeat] eBay auth OK (token acquired)")
        except EbayAuthError as e:
            logger.error(f"[Heartbeat] eBay auth failed: {e}")
        except Exception as e:
            logger.error(f"[Heartbeat] eBay auth unexpected error: {e}")
    else:
        logger.error("[Heartbeat] eBay auth helper not available")

    try:
        # 1) Close auctions first (final_price + status)
        if FEAT_CLOSE and close_tick:
            _time_step("close_tick", close_tick)

        # 2) Scrape new/updated listings (now API-backed)
        #    Only run if:
        #       - scrape feature is on
        #       - scrape function is imported
        #       - we passed auth_ok (so we can talk to eBay API)
        if _should_scrape_safe() and auth_ok:
            _time_step("scrape_sources", run_scrape, ebay_token=ebay_token)
        elif _should_scrape_safe() and not auth_ok:
            logger.warning("[Heartbeat] scrape_sources skipped (no valid eBay token)")
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
        logger.info(f"[Heartbeat] TOTAL {total_dt:.2f}s")
    finally:
        logger.info("\n\n===================== 🫀 HEARTBEAT END =====================\n")
        _lock.release()
        _sleep_with_jitter()
