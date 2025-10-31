from __future__ import annotations

import os, time, random, threading
from time import perf_counter
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from infrastructure.utils.logger import get_logger
from infrastructure.watchlist import (
    poll_hot_and_alert,
    finalize_hot_batch,
)
from infrastructure.ebay.api import fetch_live_snapshot  # must exist

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
    val = os.getenv(name, default)
    if val is None:
        return False
    val = val.strip().lower()
    return val in ("1", "true", "yes", "on")

# -----------------------------
# ENV KNOBS (safe defaults)
# -----------------------------
SLEEP_BASE_S = float(os.getenv("GF_HEARTBEAT_SLEEP_SECONDS", "5"))
SLEEP_JITTER = float(os.getenv("GF_HEARTBEAT_JITTER_S", "0.7"))
REFRESH_HRS  = float(os.getenv("GF_COMPS_REFRESH_HOURS", "6"))

# Feature toggles (easy runtime control)
FEAT_FLIPS  = env_flag("GF_HEARTBEAT_ENABLE_FLIPS")
FEAT_CLOSE  = env_flag("GF_HEARTBEAT_ENABLE_CLOSE")
FEAT_SCRAPE = env_flag("GF_HEARTBEAT_ENABLE_SCRAPE")
FEAT_COMPS  = env_flag("GF_HEARTBEAT_ENABLE_COMPS")
FEAT_SCAN   = env_flag("GF_HEARTBEAT_ENABLE_SCAN_ENDING")
FEAT_ALERTS = env_flag("GF_HEARTBEAT_ENABLE_ALERTS")

HEARTBEAT_BUDGET_S      = float(os.getenv("GF_HEARTBEAT_BUDGET_S",      "30"))
PHASE_HOT_BUDGET_S      = float(os.getenv("GF_PHASE_HOT_BUDGET_S",      "5"))
PHASE_FINALIZE_BUDGET_S = float(os.getenv("GF_PHASE_FINALIZE_BUDGET_S", "5"))
PHASE_CLOSE_BUDGET_S    = float(os.getenv("GF_PHASE_CLOSE_BUDGET_S",    "10"))
# scrape / scan / flips / alerts just eat whatever is left after that

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
        return True
    try:
        return bool(should_scrape_now())
    except Exception as e:
        logger.error(f"[Heartbeat] should_scrape_now failed: {e}")
        return False


def tick():
    if not _lock.acquire(blocking=False):
        logger.warning("[Heartbeat] skip: previous run still active")
        return

    start_wall = perf_counter()
    logger.info("\n\n==================== 🫀 HEARTBEAT START ====================\n")

    # Auth preflight
    ebay_token = None
    auth_ok = False
    if get_auth:
        try:
            ebay_token = get_auth().get_token()
            auth_ok = True
            logger.info("[Heartbeat] eBay auth OK (token acquired)")
        except EbayAuthError as e:
            logger.error(f"[Heartbeat] eBay auth failed: {e}")
        except Exception as e:
            logger.error(f"[Heartbeat] eBay auth unexpected error: {e}")
    else:
        logger.error("[Heartbeat] eBay auth helper not available")

    spent_total = 0.0

    try:
        # Phase 1: HOT WATCHLIST (Tier 2 live tracking + alerts)
        if spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                poll_hot_and_alert(fetch_live_snapshot)
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] poll_hot_and_alert OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] poll_hot_and_alert FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

            if phase_dt > PHASE_HOT_BUDGET_S:
                logger.warning(
                    "[Heartbeat] poll_hot_and_alert exceeded phase budget (%.2fs > %.2fs)",
                    phase_dt, PHASE_HOT_BUDGET_S
                )

        if spent_total >= HEARTBEAT_BUDGET_S:
            logger.info("[Heartbeat] budget exhausted after poll_hot_and_alert")
            return

        # Phase 2: FINALIZE HOT (hammer price capture for watched items)
        if spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                finalize_hot_batch()
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] finalize_hot_batch OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] finalize_hot_batch FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

            if phase_dt > PHASE_FINALIZE_BUDGET_S:
                logger.warning(
                    "[Heartbeat] finalize_hot_batch exceeded phase budget (%.2fs > %.2fs)",
                    phase_dt, PHASE_FINALIZE_BUDGET_S
                )

        if spent_total >= HEARTBEAT_BUDGET_S:
            logger.info("[Heartbeat] budget exhausted after finalize_hot_batch")
            return

        # Phase 3: CLOSE AUCTIONS (status cleanup / legacy close flow)
        if FEAT_CLOSE and close_tick and spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                close_tick()
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] close_tick OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] close_tick FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

            if phase_dt > PHASE_CLOSE_BUDGET_S:
                logger.warning(
                    "[Heartbeat] close_tick exceeded phase budget (%.2fs > %.2fs)",
                    phase_dt, PHASE_CLOSE_BUDGET_S
                )

        if spent_total >= HEARTBEAT_BUDGET_S:
            logger.info("[Heartbeat] budget exhausted after close_tick")
            return

        # Phase 4: SCRAPE SOURCES (broad discovery / Tier0+1)
        if spent_total < HEARTBEAT_BUDGET_S:
            if _should_scrape_safe() and auth_ok and run_scrape:
                t0 = perf_counter()
                try:
                    run_scrape(ebay_token=ebay_token)
                    phase_dt = perf_counter() - t0
                    logger.info(f"[Heartbeat] scrape_sources OK in {phase_dt:.2f}s")
                except Exception as e:
                    phase_dt = perf_counter() - t0
                    logger.error(f"[Heartbeat] scrape_sources FAILED in {phase_dt:.2f}s: {e}")
                spent_total += phase_dt
            elif _should_scrape_safe() and not auth_ok:
                logger.warning("[Heartbeat] scrape_sources skipped (no valid eBay token)")
            else:
                logger.info("[Heartbeat] scrape_sources skipped (gate off or not due)")

        if spent_total >= HEARTBEAT_BUDGET_S:
            logger.info("[Heartbeat] budget exhausted after scrape_sources")
            return

        # Phase 5: COMPS REFRESH
        _maybe_refresh_comps()

        if spent_total >= HEARTBEAT_BUDGET_S:
            logger.info("[Heartbeat] budget exhausted after comps")
            return

        # Phase 6: SCAN ENDING SOON
        if FEAT_SCAN and run_scan and spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                run_scan()
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] scan_ending_soon OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] scan_ending_soon FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        if spent_total >= HEARTBEAT_BUDGET_S:
            logger.info("[Heartbeat] budget exhausted after scan_ending_soon")
            return

        # Phase 7: FLIPS + DIGEST
        if FEAT_FLIPS and scan_flips and spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                scan_flips(limit_output=10)
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] scan_flips OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] scan_flips FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        if FEAT_ALERTS and alert_new_listings and spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                alert_new_listings()
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] alert_new_listings OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] alert_new_listings FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        total_dt = perf_counter() - start_wall
        logger.info(f"[Heartbeat] TOTAL {total_dt:.2f}s (spent={spent_total:.2f}s)")

    finally:
        logger.info("\n\n===================== 🫀 HEARTBEAT END =====================\n")
        _lock.release()
        _sleep_with_jitter()
