from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence, List

from concurrent.futures import ThreadPoolExecutor, as_completed

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import (
    connection,
    get_open_auctions_ending_before,
    get_recent_max_price,
)
from infrastructure.ebay.api import (
    fetch_live_snapshot,
    EbayApiRateLimited,
    EbayApiTempError,
)

logger = get_logger(__name__)

# ===========================
# Tunables / env flags
# ===========================
GRACE_WINDOW = timedelta(minutes=5)            # how long after scheduled end we "trust it's ended"
CLOSE_DELAY  = timedelta(seconds=90)           # used for prefetch window, then filtered
MAX_PER_HEARTBEAT = 30                         # safety cap

STALE_UNENDED_FALLBACK = timedelta(hours=12)   # force-finalize zombies >12h overdue

PASS_BUDGET_SECONDS = float(os.getenv("CLOSE_PASS_BUDGET_S", "25"))
CLOSE_DISABLE       = os.getenv("CLOSE_DISABLE", "0").lower() in ("1", "true", "yes")

# polite pacing even though it's API backed
MAX_WORKERS        = int(os.getenv("CLOSE_MAX_WORKERS", "1"))
MIN_GAP_SECONDS    = float(os.getenv("CLOSE_MIN_GAP_S", "5.8"))
GLOBAL_COOLDOWN_S  = float(os.getenv("CLOSE_COOLDOWN_S", "34"))

# ===========================
# Types
# ===========================
@dataclass
class Auction:
    id: int
    external_id: Optional[str]
    end_time: Optional[datetime] = None
    finalized: Optional[bool] = None


def _as_auction(row: Any) -> Auction:
    if isinstance(row, Auction):
        return row

    if isinstance(row, dict):
        return Auction(
            id=row.get("id") or row.get("auction_id"),
            external_id=(
                row.get("external_id")
                or row.get("item_id")
                or row.get("ebay_id")
            ),
            end_time=row.get("end_time"),
            finalized=row.get("finalized") or row.get("is_finalized"),
        )

    return Auction(
        id=getattr(row, "id", None) or getattr(row, "auction_id", None),
        external_id=(
            getattr(row, "external_id", None)
            or getattr(row, "item_id", None)
            or getattr(row, "ebay_id", None)
        ),
        end_time=getattr(row, "end_time", None),
        finalized=getattr(row, "finalized", None) or getattr(row, "is_finalized", None),
    )


@dataclass
class CloseResult:
    id: int
    action: str              # "FINALIZE" or "MARK"
    status: Optional[str]    # "sold" | "unsold" | "live" | "retry_soon"
    final_price: Optional[int]
    confidence: Optional[str] = None


# ===========================
# Global pacing state
# ===========================
_last_fetch_ts: float = 0.0
_cooldown_until: float = 0.0


def _pacing_sleep() -> None:
    global _last_fetch_ts, _cooldown_until
    now = time.perf_counter()

    # cooldown after rate limit
    if now < _cooldown_until:
        time.sleep(_cooldown_until - now)
        now = time.perf_counter()

    # min gap between calls
    since_last = now - _last_fetch_ts
    gap = max(0.0, MIN_GAP_SECONDS - since_last)
    if gap > 0:
        time.sleep(gap)

    _last_fetch_ts = time.perf_counter()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ===========================
# DB bulk operations
# ===========================
def _mark_ending_soon_bulk(cutoff: datetime) -> int:
    """
    Bookkeeping only: mark rows as 'ending_soon' if end_time is close.
    This does NOT hit eBay.
    """
    with connection, connection.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '3000ms'")
        cur.execute(
            """
            UPDATE auction_listings
               SET status = 'ending_soon'
             WHERE end_time <= %s
               AND status IN (
                    'open',
                    'ending_soon',
                    'live',
                    'active',
                    'retry_soon',
                    'api_active'
               )
            """,
            (cutoff,),
        )
        return cur.rowcount


def _apply_results_bulk(results: list[CloseResult]) -> None:
    """
    - mark sold/unsold with final_price
    - mark live / retry_soon
    - HARD FINALIZE anything action == FINALIZE so we stop touching it forever
    """
    if not results:
        return

    to_finalize_sold = [
        (r.final_price, r.id)
        for r in results
        if r.action == "FINALIZE"
        and r.status == "sold"
        and r.final_price is not None
    ]

    to_finalize_unsold = [
        (r.id,)
        for r in results
        if r.action == "FINALIZE"
        and r.status == "unsold"
    ]

    to_mark_live = [
        (r.id,)
        for r in results
        if r.action == "MARK"
        and r.status == "live"
    ]

    to_mark_retry = [
        (r.id,)
        for r in results
        if r.action == "MARK"
        and r.status == "retry_soon"
    ]

    to_hard_finalize = [
        (r.id,)
        for r in results
        if r.action == "FINALIZE"
    ]

    with connection, connection.cursor() as cur:
        if to_finalize_sold:
            cur.executemany(
                """
                UPDATE auction_listings
                   SET status='sold',
                       final_price=%s,
                       last_seen=(now() AT TIME ZONE 'utc')
                 WHERE id=%s
                """,
                to_finalize_sold,
            )

        if to_finalize_unsold:
            cur.executemany(
                """
                UPDATE auction_listings
                   SET status='unsold',
                       final_price=NULL,
                       last_seen=(now() AT TIME ZONE 'utc')
                 WHERE id=%s
                """,
                to_finalize_unsold,
            )

        if to_mark_live:
            cur.executemany(
                """
                UPDATE auction_listings
                   SET status='live',
                       last_seen=(now() AT TIME ZONE 'utc')
                 WHERE id=%s
                """,
                to_mark_live,
            )

        if to_mark_retry:
            cur.executemany(
                """
                UPDATE auction_listings
                   SET status='retry_soon',
                       last_seen=(now() AT TIME ZONE 'utc')
                 WHERE id=%s
                """,
                to_mark_retry,
            )

        if to_hard_finalize:
            # retire finalized auctions so they never come back
            cur.executemany(
                """
                UPDATE auction_listings
                   SET finalized = TRUE,
                       watch = FALSE,
                       closed_at = (now() AT TIME ZONE 'utc'),
                       last_seen = (now() AT TIME ZONE 'utc')
                 WHERE id = %s
                """,
                to_hard_finalize,
            )

    logger.info(
        "[close] batch done: finalized_sold=%d, finalized_unsold=%d, live=%d, retry=%d",
        len(to_finalize_sold),
        len(to_finalize_unsold),
        len(to_mark_live),
        len(to_mark_retry),
    )


# ===========================
# eBay snapshot interpretation
# ===========================
def _snapshot_to_result(a: Auction, snap: dict) -> CloseResult:
    ended = bool(snap.get("ended"))
    sold = bool(snap.get("sold"))
    live = bool(snap.get("live"))

    if ended:
        fp = snap.get("final_price")
        fp_int: Optional[int] = None

        if fp is not None:
            try:
                fp_int = int(round(float(fp)))
            except Exception:
                fp_int = None

        if fp_int is None:
            recent = get_recent_max_price(a.id, window_minutes=360)
            if recent is not None:
                try:
                    fp_int = int(round(float(recent)))
                except Exception:
                    fp_int = None

        if sold and fp_int is not None:
            return CloseResult(
                id=a.id,
                action="FINALIZE",
                status="sold",
                final_price=fp_int,
                confidence="api" if snap.get("final_price") is not None else "history",
            )

        return CloseResult(
            id=a.id,
            action="FINALIZE",
            status="unsold",
            final_price=None,
            confidence=None,
        )

    if live:
        return CloseResult(
            id=a.id,
            action="MARK",
            status="live",
            final_price=None,
            confidence=None,
        )

    return CloseResult(
        id=a.id,
        action="MARK",
        status="retry_soon",
        final_price=None,
        confidence=None,
    )


# ===========================
# Worker
# ===========================
def _close_one(a: Auction, idx: int, total: int, deadline: float) -> CloseResult:
    """
    Called ONLY for stuff that should already be over (past GRACE_WINDOW),
    not-yet-finalized.
    """
    if time.perf_counter() > deadline:
        return CloseResult(a.id, "MARK", "retry_soon", None)

    # polite pacing + jitter
    time.sleep(random.uniform(0.4, 0.9))
    _pacing_sleep()

    row_for_api = {
        "id": a.id,
        "item_id": a.external_id,
        "external_id": a.external_id,
        "ebay_id": a.external_id,
        "current_price": None,
        "market_price": None,
        "bid_count": None,
    }

    try:
        snap = fetch_live_snapshot(row_for_api)
    except EbayApiRateLimited:
        global _cooldown_until
        _cooldown_until = time.perf_counter() + GLOBAL_COOLDOWN_S + random.uniform(1.0, 3.0)
        logger.warning(
            "[close] rate-limited by eBay — cooling down for ~%.1fs",
            GLOBAL_COOLDOWN_S,
        )
        return CloseResult(a.id, "MARK", "retry_soon", None)
    except EbayApiTempError as e:
        logger.warning(
            "[close] temp eBay API error for id=%s ext=%s: %s",
            a.id,
            a.external_id,
            e,
        )
        return CloseResult(a.id, "MARK", "retry_soon", None)
    except Exception as e:
        logger.warning(
            "[close] unexpected eBay API error for id=%s ext=%s: %s",
            a.id,
            a.external_id,
            e,
        )
        return CloseResult(a.id, "MARK", "retry_soon", None)

    # HARD OVERRIDE:
    # If our DB says this auction should have ended ages ago (older than GRACE_WINDOW),
    # we don't care if Browse claims it's still "live". It's dead to us.
    force_dead_cutoff = datetime.now(timezone.utc) - GRACE_WINDOW
    if a.end_time and a.end_time <= force_dead_cutoff:
        snap = dict(snap)
        snap["ended"] = True
        snap["live"] = False

    # per-item spam stays at debug
    logger.debug(
        "[close] snapshot for auction %s → ended=%s live=%s sold=%s final=%s",
        a.id,
        snap.get("ended"),
        snap.get("live"),
        snap.get("sold"),
        snap.get("final_price"),
    )

    return _snapshot_to_result(a, snap)


# ===========================
# choose which auctions to finalize
# ===========================
def _filter_finalizables(rows: Sequence[Any], now: datetime) -> list[Auction]:
    """
    Input: rows from get_open_auctions_ending_before().
    Output: only auctions that:
      - have actually ended (end_time <= now - GRACE_WINDOW)
      - are NOT finalized yet
    """
    cutoff_must_be_over = now - GRACE_WINDOW
    finalizables: list[Auction] = []

    for r in rows:
        a = _as_auction(r)
        if not a.id:
            continue
        if getattr(a, "finalized", False):
            continue
        if not a.end_time:
            continue
        if a.end_time <= cutoff_must_be_over:
            finalizables.append(a)

    return finalizables


# ===========================
# Batch processor
# ===========================
def _process_due_auctions(due: Sequence[Auction], budget_seconds: float) -> None:
    """
    Only called with definitely-ended auctions.
    Gets final price once, then finalizes them so they leave the pool.
    """
    if not due:
        logger.info("[close] no due auctions to finalize")
        return

    # oldest-ended first
    try:
        due = sorted(
            due,
            key=lambda a: a.end_time or datetime.max.replace(tzinfo=timezone.utc),
        )
    except Exception:
        pass

    total = len(due)
    if total > MAX_PER_HEARTBEAT:
        logger.info(
            "[close] Limiting finalize batch to %d/%d auctions this pass",
            MAX_PER_HEARTBEAT,
            total,
        )
        due = due[:MAX_PER_HEARTBEAT]
        total = len(due)
    else:
        logger.info(f"[close] Finalizing {total} ended auctions")

    deadline = time.perf_counter() + budget_seconds
    results: list[CloseResult] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [
            ex.submit(_close_one, a, i + 1, total, deadline)
            for i, a in enumerate(due)
        ]
        for fut in as_completed(futs):
            try:
                r = fut.result()
                results.append(r)
            except Exception as e:
                logger.warning(f"[close] worker error: {e}")

    # apply finalize / mark retry
    _apply_results_bulk(results)

    # final safety net:
    # if something *still* isn't marked finalized but is older than GRACE_WINDOW,
    # force finalize it as unsold so we stop polling forever.
    with connection, connection.cursor() as cur:
        ids_still_not_final = [a.id for a in due]
        if ids_still_not_final:
            cur.execute(
                """
                UPDATE auction_listings
                   SET status     = COALESCE(status, 'unsold'),
                       finalized  = TRUE,
                       watch      = FALSE,
                       closed_at  = (now() AT TIME ZONE 'utc'),
                       last_seen  = (now() AT TIME ZONE 'utc')
                 WHERE id = ANY(%s)
                   AND end_time <= (now() AT TIME ZONE 'utc') - INTERVAL '5 minutes'
                   AND finalized = FALSE
                """,
                (ids_still_not_final,),
            )


# ===========================
# Stale cleanup
# ===========================
def _finalize_stale(now_utc: datetime) -> None:
    """
    After STALE_UNENDED_FALLBACK, anything still open gets force-closed as unsold.
    """
    cutoff = now_utc - STALE_UNENDED_FALLBACK
    stale_rows = get_open_auctions_ending_before(cutoff) or []
    stale = [_as_auction(r) for r in stale_rows]

    if not stale:
        logger.info("[close] Stale candidates: 0")
        logger.info("[close] Stale pass finalized: 0")
        return

    logger.info(f"[close] Stale candidates: {len(stale)}")

    to_finalize_unsold = [(a.id,) for a in stale]

    with connection, connection.cursor() as cur:
        # mark unsold
        cur.executemany(
            """
            UPDATE auction_listings
               SET status='unsold',
                   final_price=NULL,
                   last_seen=(now() AT TIME ZONE 'utc')
             WHERE id=%s
            """,
            to_finalize_unsold,
        )
        # HARD FINALIZE so we never touch them again
        cur.executemany(
            """
            UPDATE auction_listings
               SET finalized = TRUE,
                   watch = FALSE,
                   closed_at = (now() AT TIME ZONE 'utc')
             WHERE id=%s
            """,
            to_finalize_unsold,
        )

    logger.info(f"[close] Stale pass finalized: {len(to_finalize_unsold)}")


# ===========================
# Public entry point
# ===========================
def tick() -> None:
    try:
        t0 = time.perf_counter()
        now = _now_utc()

        if CLOSE_DISABLE:
            logger.info("[close] CLOSE_DISABLE=1 — skipping close pass")
            return

        # Stage 1: bookkeeping
        try:
            cutoff = now + GRACE_WINDOW
            rows = get_open_auctions_ending_before(cutoff) or []
            logger.info(
                "[close] ending_soon candidates: %d (cutoff=%s)",
                len(rows),
                cutoff.isoformat(),
            )
            updated = _mark_ending_soon_bulk(cutoff)
            logger.info("[close] ending_soon bulk updated: %d", updated)
        except Exception as e:
            logger.error("[close] ending_soon pass failed: %s", e)

        # Stage 2: finalize actually-dead stuff only
        try:
            raw_rows = get_open_auctions_ending_before(now + CLOSE_DELAY) or []
            finalizables = _filter_finalizables(raw_rows, now)

            logger.info(
                "[close] Finalizable candidates: %d (cutoff=%s)",
                len(finalizables),
                (now + CLOSE_DELAY).isoformat(),
            )

            _process_due_auctions(finalizables, PASS_BUDGET_SECONDS)
        except Exception as e:
            logger.error("[close] finalize pass failed: %s", e)

        # Stage 3: stale >12h
        try:
            _finalize_stale(now)
        except Exception as e:
            logger.error("[close] stale fallback pass failed: %s", e)

        logger.info("[close] PASS DONE in %.2fs", time.perf_counter() - t0)
    except Exception as e:
        logger.error("[close] FATAL in tick(): %s", e)
