from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence, List, Dict

from concurrent.futures import ThreadPoolExecutor, as_completed

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import (
    connection,                         # single-threaded main-thread writes only
    get_open_auctions_ending_before,    # read helper: should return id, external_id/item_id, end_time, etc.
    get_recent_max_price,               # still kept as a fallback if API gives no price
)
from infrastructure.ebay.api import (
    fetch_live_snapshot,                # the hybrid Browse/Trading function we just wrote
    EbayApiRateLimited,
    EbayApiTempError,
)

logger = get_logger(__name__)

# ===========================
# Knobs & env flags
# ===========================
GRACE_WINDOW = timedelta(minutes=5)            # mark ending_soon for stuff ending in next 5m
CLOSE_DELAY  = timedelta(seconds=90)           # treat auctions ending within 90s as "due"
MAX_PER_HEARTBEAT = 30                         # don't process more than this in one tick

STALE_UNENDED_FALLBACK = timedelta(hours=12)   # after 12h past end_time, force finalize unsold

PASS_BUDGET_SECONDS = float(os.getenv("CLOSE_PASS_BUDGET_S", "25"))
CLOSE_DISABLE       = os.getenv("CLOSE_DISABLE", "0") in ("1", "true", "True")

# polite pacing even though it's API backed
MAX_WORKERS        = int(os.getenv("CLOSE_MAX_WORKERS", "1"))   # keep 1 by default
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


def _as_auction(row: Any) -> Auction:
    """
    Normalise DB row into Auction.
    We assume DB row has:
      - id (our PK)
      - external_id / item_id / ebay_id (eBay itemId string)
      - end_time (UTC timestamptz)
    """
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
        )

    return Auction(
        id=getattr(row, "id", None) or getattr(row, "auction_id", None),
        external_id=(
            getattr(row, "external_id", None)
            or getattr(row, "item_id", None)
            or getattr(row, "ebay_id", None)
        ),
        end_time=getattr(row, "end_time", None),
    )


@dataclass
class CloseResult:
    id: int
    action: str              # "FINALIZE" or "MARK"
    status: Optional[str]    # "sold" | "unsold" | "live" | "retry_soon"
    final_price: Optional[int]
    confidence: Optional[str] = None  # "api" | "history" | None


# ===========================
# Global pacing state
# ===========================
_last_fetch_ts: float = 0.0
_cooldown_until: float = 0.0


def _pacing_sleep() -> None:
    """
    Respect global cooldown and minimum gap, to be polite with eBay API usage.
    """
    global _last_fetch_ts, _cooldown_until
    now = time.perf_counter()

    # global cooldown after rate limit
    if now < _cooldown_until:
        time.sleep(_cooldown_until - now)
        now = time.perf_counter()

    # min gap between requests
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
    Promote anything ending soon into 'ending_soon'. We include api_active so
    rows created by the ingestion API path get watched too.
    """
    with connection, connection.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '3000ms'")
        cur.execute(
            """
            UPDATE auction_listings
               SET status = 'ending_soon'
             WHERE end_time <= %s
               AND status IN (
                    'OPEN',
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
            # 👇 bump last_seen so it's not treated as stale
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
            # 👇 bump last_seen here too
            cur.executemany(
                """
                UPDATE auction_listings
                   SET status='retry_soon',
                       last_seen=(now() AT TIME ZONE 'utc')
                 WHERE id=%s
                """,
                to_mark_retry,
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
    """
    Turn the unified snapshot dict from fetch_live_snapshot(row) into a CloseResult.
    snap is expected to include:
      ended (bool)
      sold (bool)
      final_price (float|None)
      live (bool)
      current_price (float|None)  # may exist but we don't persist this here
    """
    ended = bool(snap.get("ended"))
    sold = bool(snap.get("sold"))
    live = bool(snap.get("live"))

    if ended:
        # listing is no longer active
        fp = snap.get("final_price")
        fp_int: Optional[int] = None
        if fp is not None:
            try:
                fp_int = int(round(float(fp)))
            except Exception:
                fp_int = None

        # fallback: if API somehow didn't give us price but we have recent db high-water mark
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
        # ended but no final we trust
        return CloseResult(
            id=a.id,
            action="FINALIZE",
            status="unsold",
            final_price=None,
            confidence=None,
        )

    # not ended
    if live:
        return CloseResult(
            id=a.id,
            action="MARK",
            status="live",
            final_price=None,
            confidence=None,
        )

    # weird fallback
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
    Process a single auction using eBay APIs, not HTML scraping.
    - obeys pacing
    - if rate-limited, sets global cooldown
    - returns CloseResult
    """
    if time.perf_counter() > deadline:
        return CloseResult(a.id, "MARK", "retry_soon", None)

    logger.info(f"[close] ({idx}/{total}) Processing auction {a.id} (ext={a.external_id})")

    # jitter to avoid burst fingerprint
    time.sleep(random.uniform(0.4, 0.9))
    _pacing_sleep()

    # Build a row-like dict for fetch_live_snapshot()
    row_for_api = {
        "id": a.id,
        "item_id": a.external_id,
        "external_id": a.external_id,
        "ebay_id": a.external_id,
        # we don't necessarily have these here but the function accepts them
        "current_price": None,
        "market_price": None,
        "bid_count": None,
    }

    try:
        snap = fetch_live_snapshot(row_for_api)
    except EbayApiRateLimited:
        # global cooldown
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

    return _snapshot_to_result(a, snap)


# ===========================
# Batch processor
# ===========================
def _process_due_auctions(due: Sequence[Auction], budget_seconds: float) -> None:
    if not due:
        logger.info("[close] no due auctions")
        return

    # process in order of soonest end_time first
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
            f"[close] Limiting close batch to {MAX_PER_HEARTBEAT}/{total} auctions this pass"
        )
        due = due[:MAX_PER_HEARTBEAT]
        total = len(due)
    else:
        logger.info(f"[close] Processing {total} due auctions")

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

    _apply_results_bulk(results)


# ===========================
# Stale cleanup
# ===========================
def _finalize_stale(now_utc: datetime) -> None:
    """
    After STALE_UNENDED_FALLBACK, anything that should have ended ages ago but
    is still not finalised gets force-closed as unsold. This keeps the open pool clean.
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

    logger.info(f"[close] Stale pass finalized: {len(to_finalize_unsold)}")


# ===========================
# Public entry point
# ===========================
def tick() -> None:
    try:
        logger.info("[close] ===== START CLOSE PASS =====")
        t0 = time.perf_counter()
        now = _now_utc()

        if CLOSE_DISABLE:
            logger.info("[close] CLOSE_DISABLE=1 — skipping close pass")
            return

        # Stage 1: mark ending soon
        try:
            cutoff = now + GRACE_WINDOW
            rows = get_open_auctions_ending_before(cutoff) or []
            logger.info(
                f"[close] ENDING_SOON candidates: {len(rows)} (cutoff={cutoff.isoformat()})"
            )
            updated = _mark_ending_soon_bulk(cutoff)
            logger.info(f"[close] ENDING_SOON bulk updated: {updated}")
        except Exception as e:
            logger.error(f"[close] ENDING_SOON pass failed: {e}")

        # Stage 2: actually close the ones due now-ish
        try:
            due_cutoff = now + CLOSE_DELAY
            rows = get_open_auctions_ending_before(due_cutoff) or []
            due = [_as_auction(r) for r in rows]
            logger.info(
                f"[close] Due candidates: {len(due)} (cutoff={due_cutoff.isoformat()})"
            )
            _process_due_auctions(due, PASS_BUDGET_SECONDS)
        except Exception as e:
            logger.error(f"[close] due auctions pass failed: {e}")

        # Stage 3: stale cleanup (12h zombies)
        try:
            _finalize_stale(now)
        except Exception as e:
            logger.error(f"[close] stale fallback pass failed: {e}")

        logger.info(f"[close] PASS DONE in {time.perf_counter() - t0:.2f}s")
    except Exception as e:
        logger.error(f"[close] FATAL in tick(): {e}")
