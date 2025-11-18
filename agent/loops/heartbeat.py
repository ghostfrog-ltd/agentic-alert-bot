from __future__ import annotations

import os, time, random, threading
from time import perf_counter
from dotenv import load_dotenv
from infrastructure.utils.logger import get_logger
from agent.reminders import check_and_send_reem_reminders

load_dotenv()

logger = get_logger(__name__)

# -------------------------------------------------
# eBay Auth (new dependency for API-backed scraping)
# -------------------------------------------------
try:
    from infrastructure.ebay.auth import get_auth, EbayAuthError
except Exception as e:
    get_auth = None
    EbayAuthError = Exception
    logger.error(f"[Heartbeat] import get_auth failed: {e}")

# ---------- Optional imports guarded (loose coupling)
try:
    from agent.actions.scrape.sources import run as run_scrape
except Exception as e:
    run_scrape = None
    logger.error(f"[Heartbeat] import run_scrape failed: {e}")

try:
    from agent.actions.close.ended import run as close_ended
except Exception as e:
    close_ended = None
    logger.error(f"[Heartbeat] import close_ended failed: {e}")

try:
    from agent.actions.alert.hot_listings import run as hot_listings
except Exception as e:
    hot_listings = None
    logger.error(f"[Heartbeat] import hot_listings failed: {e}")

try:
    from agent.actions.alert.new_listings import run as alert_new_listings
except Exception as e:
    alert_new_listings = None
    logger.error(f"[Heartbeat] import alert_new_listings failed: {e}")

try:
    from agent.actions.alert.roi_listings import run as roi
except Exception as e:
    roi = None
    logger.error(f"[Heartbeat] import roi failed: {e}")

try:
    from agent.actions.process.comps import run as run_comps
except Exception as e:
    run_comps = None
    logger.error(f"[Heartbeat] import run_comps failed: {e}")

# NEW: attributes backfill
try:
    from agent.actions.maintenance.attributes import run as backfill_attrs
except Exception as e:
    backfill_attrs = None
    logger.error(f"[Heartbeat] import backfill_attrs failed: {e}")

try:
    from infrastructure.utils.usage_tracker import get_api_usage_today
except Exception as e:
    get_api_usage_today = None
    logger.error(f"[Heartbeat] import schema helpers failed: {e}")


# -----------------------------
# ENV + RUNTIME FLAGS
# -----------------------------
def env_flag(name: str, default: str = "0") -> bool:
    val = os.getenv(name, default)
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


SLEEP_BASE_S = float(os.getenv("GF_HEARTBEAT_SLEEP_SECONDS", "5"))
SLEEP_JITTER = float(os.getenv("GF_HEARTBEAT_JITTER_S", "0.7"))

FEAT_ROI = env_flag("GF_HEARTBEAT_ENABLE_ROI")
FEAT_CLOSE = env_flag("GF_HEARTBEAT_ENABLE_CLOSE")
FEAT_SCRAPE = env_flag("GF_HEARTBEAT_ENABLE_SCRAPE")
FEAT_COMPS = env_flag("GF_HEARTBEAT_ENABLE_COMPS")
FEAT_HOT = env_flag("GF_HEARTBEAT_ENABLE_HOT")
FEAT_ALERTS = env_flag("GF_HEARTBEAT_ENABLE_ALERTS")
FEAT_ATTRS = env_flag("GF_HEARTBEAT_ENABLE_ATTRS")  # NEW

HEARTBEAT_BUDGET_S = float(os.getenv("GF_HEARTBEAT_BUDGET_S", "240"))
PHASE_HOT_BUDGET_S = float(os.getenv("GF_PHASE_HOT_BUDGET_S", "80"))
PHASE_FINALIZE_BUDGET_S = float(os.getenv("GF_PHASE_FINALIZE_BUDGET_S", "80"))
PHASE_CLOSE_BUDGET_S = float(os.getenv("GF_PHASE_CLOSE_BUDGET_S", "80"))

MAX_API_CALLS = int(os.getenv("GF_MAX_API_CALLS", "6000"))

# NEW: how many listings per tick to backfill attrs for
ATTR_BACKFILL_LIMIT = int(os.getenv("GF_ATTR_BACKFILL_LIMIT", "1"))

_lock = threading.Lock()


def _sleep_with_jitter():
    time.sleep(SLEEP_BASE_S + random.uniform(0, SLEEP_JITTER))


def tick():
    if not _lock.acquire(blocking=False):
        logger.warning("[Heartbeat] skip: previous run still active")
        return

    start_wall = perf_counter()
    logger.info("\n\n==================== 🫀 HEARTBEAT START ====================\n")

    try:
        check_and_send_reem_reminders()
    except Exception as e:
        logger.exception("[Reem] reminder check failed: %s", e)

    # -------------------------------------------------
    # take snapshot of feature toggles for THIS run
    # (we do NOT mutate the module-level FEAT_* globals)
    # -------------------------------------------------
    feat_close = FEAT_CLOSE
    feat_scrape = FEAT_SCRAPE
    feat_roi = FEAT_ROI
    feat_comps = FEAT_COMPS
    feat_hot = FEAT_HOT
    feat_alerts = FEAT_ALERTS
    feat_attrs = FEAT_ATTRS  # NEW

    # -------------------------------------------------
    # eBay auth preflight
    # -------------------------------------------------
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

    # -------------------------------------------------
    # Daily API usage guard
    # if we're over budget, kill the eBay-heavy phases
    # -------------------------------------------------
    if get_api_usage_today:
        usage = get_api_usage_today("ebay")
        if usage >= MAX_API_CALLS:
            logger.warning(
                f"[Heartbeat] Daily eBay API usage {usage} >= {MAX_API_CALLS}, "
                f"skipping eBay phases"
            )
            feat_close = False
            feat_scrape = False
            feat_roi = False
            feat_comps = False
            feat_hot = False
            feat_alerts = False
            feat_attrs = False  # NEW: also disable attrs backfill

    spent_total = 0.0

    try:
        # Phase 1: CLOSE ENDED
        if feat_close and close_ended and spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                close_ended()
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] close_ended OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] close_ended FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        # Phase 2: SCRAPE SOURCES (ingest fresh listings via eBay API)
        if feat_scrape and auth_ok and run_scrape:
            t0 = perf_counter()
            try:
                run_scrape(ebay_token=ebay_token)
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] scrape_sources OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] scrape_sources FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt
        elif feat_scrape and not auth_ok:
            logger.warning("[Heartbeat] scrape_sources skipped (no valid eBay token)")

        # Phase 3: COMPS REFRESH (recompute rolling medians/means)
        if feat_comps and spent_total < HEARTBEAT_BUDGET_S and run_comps:
            t0 = perf_counter()
            try:
                run_comps(force=False)
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] process.comps OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] process.comps FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        # Phase 3.5: ATTRIBUTES BACKFILL (Trading GetItem → raw_attrs + typed fields)
        if feat_attrs and backfill_attrs and spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                backfill_attrs(limit=ATTR_BACKFILL_LIMIT, enable_api=True)
                phase_dt = perf_counter() - t0
                logger.info(
                    f"[Heartbeat] attrs_backfill (limit={ATTR_BACKFILL_LIMIT}) OK in {phase_dt:.2f}s"
                )
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(
                    f"[Heartbeat] attrs_backfill FAILED in {phase_dt:.2f}s: {e}"
                )
            spent_total += phase_dt

        # Phase 4: SCAN HOT LISTINGS (look for flips about to finish)
        if feat_hot and hot_listings and spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                hot_listings()
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] hot_listings OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] hot_listings FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        # Phase 5: FLIPS + ALERT DIGEST
        if feat_roi and roi and spent_total < HEARTBEAT_BUDGET_S:
            t0 = perf_counter()
            try:
                roi(limit_output=10)
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] roi OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] roi FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        # Phase 6: NEW LISTING ALERTS
        if feat_alerts and alert_new_listings and spent_total < HEARTBEAT_BUDGET_S:
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


def tick_once():
    """
    Do one full heartbeat cycle. No while True here.
    """
    if not _lock.acquire(blocking=False):
        logger.warning("[Heartbeat] skip: previous run still active")
        return

    start_wall = perf_counter()
    logger.info("\n\n==================== 🫀 HEARTBEAT START ====================\n")

    try:
        try:
            check_and_send_reem_reminders()
        except Exception as e:
            logger.exception("[Reem] reminder check failed: %s", e)

        # snapshot feature flags
        feat_close = FEAT_CLOSE
        feat_scrape = FEAT_SCRAPE
        feat_roi = FEAT_ROI
        feat_comps = FEAT_COMPS
        feat_hot = FEAT_HOT
        feat_alerts = FEAT_ALERTS
        feat_attrs = FEAT_ATTRS

        # eBay auth preflight
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

        # daily API usage guard
        if get_api_usage_today:
            usage = get_api_usage_today("ebay")
            if usage >= MAX_API_CALLS:
                logger.warning(
                    f"[Heartbeat] Daily eBay API usage {usage} >= {MAX_API_CALLS}, skipping eBay phases"
                )
                feat_close = False
                feat_scrape = False
                feat_roi = False
                feat_comps = False
                feat_hot = False
                feat_alerts = False
                feat_attrs = False

        spent_total = 0.0

        # Phase 1: CLOSE ENDED
        # if feat_close and close_ended and spent_total < HEARTBEAT_BUDGET_S:
        if feat_close and close_ended :
            t0 = perf_counter()
            try:
                close_ended()
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] close_ended OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] close_ended FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        # Phase 2: SCRAPE
        if feat_scrape and auth_ok and run_scrape:
            t0 = perf_counter()
            try:
                run_scrape(ebay_token=ebay_token)
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] scrape_sources OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] scrape_sources FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt
        elif feat_scrape and not auth_ok:
            logger.warning("[Heartbeat] scrape_sources skipped (no valid eBay token)")

        # Phase 3: COMPS
        #if feat_comps and spent_total < HEARTBEAT_BUDGET_S and run_comps:
        if feat_comps and run_comps:
            t0 = perf_counter()
            try:
                run_comps(force=False)
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] process.comps OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] process.comps FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        # Phase 3.5: ATTRIBUTES BACKFILL
        #if feat_attrs and backfill_attrs and spent_total < HEARTBEAT_BUDGET_S:
        if feat_attrs and backfill_attrs:
            t0 = perf_counter()
            try:
                backfill_attrs(limit=ATTR_BACKFILL_LIMIT, enable_api=True)
                phase_dt = perf_counter() - t0
                logger.info(
                    f"[Heartbeat] attrs_backfill (limit={ATTR_BACKFILL_LIMIT}) OK in {phase_dt:.2f}s"
                )
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(
                    f"[Heartbeat] attrs_backfill FAILED in {phase_dt:.2f}s: {e}"
                )
            spent_total += phase_dt

        '''
        # Phase 4: HOT LISTINGS
        #if feat_hot and hot_listings and spent_total < HEARTBEAT_BUDGET_S:
        if feat_hot and hot_listings :
            t0 = perf_counter()
            try:
                hot_listings()
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] hot_listings OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] hot_listings FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt
        '''

        # Phase 5: ROI
        # if feat_roi and roi and spent_total < HEARTBEAT_BUDGET_S:
        # if feat_roi and roi and spent_total:
        if feat_roi and roi:
            t0 = perf_counter()
            try:
                roi(limit_output=10)
                phase_dt = perf_counter() - t0
                logger.info(f"[Heartbeat] roi OK in {phase_dt:.2f}s")
            except Exception as e:
                phase_dt = perf_counter() - t0
                logger.error(f"[Heartbeat] roi FAILED in {phase_dt:.2f}s: {e}")
            spent_total += phase_dt

        # Phase 6: NEW LISTING ALERTS
        #if feat_alerts and alert_new_listings and spent_total < HEARTBEAT_BUDGET_S:
        if feat_alerts and alert_new_listings :

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


def tick_forever():
    logger.info("[Heartbeat] loop starting…")
    while True:
        try:
            tick_once()
        except Exception as e:
            logger.exception("[Heartbeat] top-level crash in tick_once: %s", e)
        _sleep_with_jitter()
