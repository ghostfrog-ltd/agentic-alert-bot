from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import connection
from infrastructure.ebay.api import (
    fetch_live_snapshot,
    EbayApiRateLimited,
    EbayApiTempError,
)

logger = get_logger(__name__)

HOT_WINDOW = timedelta(minutes=10)
ALERT_WINDOW = timedelta(minutes=5)
FINALIZE_BATCH_LIMIT = 10
POLL_BATCH_LIMIT = 25
MAX_PHASE_SECONDS = 5.0
COLDNESS_ALERT_THRESHOLD = 0.30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coldness(current_price: float, market_price: float) -> Optional[float]:
    """
    How far under 'market value' is this live auction?
    0.30 means 30% cheaper than what we think it's worth.
    """
    if current_price is None or market_price is None:
        return None
    try:
        return 1.0 - (float(current_price) / float(market_price))
    except ZeroDivisionError:
        return None


def _fetch_hot_candidates() -> List[dict]:
    """
    Grab watched auctions that are:
    - watch = TRUE
    - not finalized
    - ending within HOT_WINDOW
    Returns dict rows with external_id etc. for polling.
    """
    now = _utcnow()
    cutoff = now + HOT_WINDOW
    with connection, connection.cursor() as cur:
        cur.execute(
            """
            SELECT id,
                   external_id,
                   url,
                   end_time,
                   last_known_price,
                   watch_score,
                   next_watch_check_at,
                   status,
                   market_price,
                   current_price,
                   bid_count
            FROM auction_listings
            WHERE watch = TRUE
              AND finalized = FALSE
              AND end_time > %s
              AND end_time <= %s
            ORDER BY end_time ASC
            LIMIT %s
            """,
            (now, cutoff, POLL_BATCH_LIMIT),
        )
        colnames = [c[0] for c in cur.description]
        return [dict(zip(colnames, rec)) for rec in cur.fetchall()]


def _update_listing_after_poll(
    row_id: int,
    current_price: Optional[float],
    bid_count: Optional[int],
    next_check_at: datetime,
) -> None:
    """
    After polling live, keep freshest info on the row.
    Also mark confidence='live' the first time we successfully poll it.
    """
    with connection, connection.cursor() as cur:
        cur.execute(
            """
            UPDATE auction_listings
               SET last_known_price = COALESCE(%s, last_known_price),
                   current_price    = COALESCE(%s, current_price),
                   bid_count        = COALESCE(%s, bid_count),
                   next_watch_check_at = %s,
                   confidence       = CASE
                                         WHEN confidence IS NULL THEN 'live'
                                         ELSE confidence
                                       END
             WHERE id = %s
            """,
            (current_price, current_price, bid_count, next_check_at, row_id),
        )


def _schedule_next_check(end_time: datetime) -> datetime:
    """
    Dynamic poll frequency:
    - Far away? chill.
    - Close? hammer.
    """
    now = _utcnow()
    mins_left = max(0.0, (end_time - now).total_seconds() / 60.0)

    if mins_left > 60:
        delta = timedelta(minutes=30)
    elif mins_left > 15:
        delta = timedelta(minutes=10)
    elif mins_left > 5:
        delta = timedelta(minutes=2)
    else:
        delta = timedelta(seconds=30)

    return now + delta


def _should_alert(coldness_val: Optional[float], end_time: datetime) -> bool:
    """
    Decide if we should shout.
    Needs to be cold (well under market) AND almost ending.
    """
    if coldness_val is None:
        return False
    if coldness_val < COLDNESS_ALERT_THRESHOLD:
        return False
    return (end_time - _utcnow()) <= ALERT_WINDOW


def _send_alert(row: dict, coldness_val: float, snap: dict) -> None:
    """
    Hook for Telegram/email/etc. For now we just log loud.
    """
    logger.warning(
        "[ALERT] Candidate %s still cold (%.1f%% under) with %s left. "
        "price=£%s vs market=£%s (bids=%s)",
        row["id"],
        coldness_val * 100.0,
        str(row["end_time"] - _utcnow()),
        snap.get("current_price"),
        snap.get("market_price"),
        snap.get("bid_count"),
    )


def poll_hot_and_alert() -> None:
    """
    Phase 1 of heartbeat:
    - Look at watched stuff ending soon
    - Hit eBay API live to refresh its bid/price
    - If it's way under market and about to end, scream
    - Schedule next poll time
    """
    start = _utcnow()
    rows = _fetch_hot_candidates()
    if not rows:
        logger.info("[watchlist] no hot candidates")
        return

    logger.info("[watchlist] hot candidates: %d", len(rows))

    for row in rows:
        # protect heartbeat budget
        if (_utcnow() - start).total_seconds() > MAX_PHASE_SECONDS:
            logger.info("[watchlist] poll_hot phase time budget reached")
            break

        snapshot_request_row = {
            "id": row["id"],
            "item_id": row.get("external_id"),
            "external_id": row.get("external_id"),
            "ebay_id": row.get("external_id"),
            "current_price": row.get("current_price"),
            "market_price": row.get("market_price"),
            "bid_count": row.get("bid_count"),
        }

        try:
            snap = fetch_live_snapshot(snapshot_request_row)
        except EbayApiRateLimited:
            logger.warning("[watchlist] rate limited while polling id=%s", row["id"])
            snap = {}
        except EbayApiTempError as e:
            logger.warning("[watchlist] temp API error while polling id=%s: %s", row["id"], e)
            snap = {}
        except Exception as e:
            logger.warning("[watchlist] unexpected API error while polling id=%s: %s", row["id"], e)
            snap = {}

        current_price = snap.get("current_price")
        market_price  = snap.get("market_price")
        bid_count     = snap.get("bid_count")

        coldness_val = _coldness(current_price, market_price)

        if _should_alert(coldness_val, row["end_time"]):
            _send_alert(row, coldness_val, snap)

        next_check_at = _schedule_next_check(row["end_time"])
        _update_listing_after_poll(
            row_id=row["id"],
            current_price=current_price,
            bid_count=bid_count,
            next_check_at=next_check_at,
        )


def _fetch_ended_unfinalized() -> List[dict]:
    """
    Watched auctions that *should* have ended but aren't locked in yet.
    We'll confirm final_price, sold/unsold, then finalize them.
    """
    now = _utcnow()
    with connection, connection.cursor() as cur:
        cur.execute(
            """
            SELECT id,
                   external_id,
                   end_time,
                   last_known_price,
                   final_price,
                   confidence,
                   status
            FROM auction_listings
            WHERE watch = TRUE
              AND finalized = FALSE
              AND end_time <= %s
            ORDER BY end_time ASC
            LIMIT %s
            """,
            (now, FINALIZE_BATCH_LIMIT),
        )
        colnames = [c[0] for c in cur.description]
        return [dict(zip(colnames, rec)) for rec in cur.fetchall()]


def _finalize_listing(
    row_id: int,
    final_price: Optional[float],
    sold_status: str,
    confidence: str,
) -> None:
    """
    Flip watched auction to fully closed state.
    """
    with connection, connection.cursor() as cur:
        cur.execute(
            """
            UPDATE auction_listings
               SET final_price = %s,
                   status = %s,
                   confidence = %s,
                   finalized = TRUE,
                   watch = FALSE,
                   closed_at = (now() AT TIME ZONE 'utc')
             WHERE id = %s
            """,
            (final_price, sold_status, confidence, row_id),
        )


def finalize_hot_batch() -> None:
    """
    Phase 2 of heartbeat:
    - For watched items that ended,
      ask eBay for their final sale state.
    - Record final_price + sold/unsold,
      then retire them.
    """
    start = _utcnow()
    rows = _fetch_ended_unfinalized()
    if not rows:
        logger.info("[watchlist] no ended watched auctions to finalize")
        return

    logger.info("[watchlist] finalizing %d ended watched auctions", len(rows))

    for row in rows:
        # stay within budget
        if (_utcnow() - start).total_seconds() > MAX_PHASE_SECONDS:
            logger.info("[watchlist] finalize phase time budget reached")
            break

        snapshot_request_row = {
            "id": row["id"],
            "item_id": row.get("external_id"),
            "external_id": row.get("external_id"),
            "ebay_id": row.get("external_id"),
            "current_price": row.get("last_known_price"),
            "market_price": None,
            "bid_count": None,
        }

        try:
            snap = fetch_live_snapshot(snapshot_request_row)
        except EbayApiRateLimited:
            logger.warning("[watchlist] rate limited finalizing id=%s", row["id"])
            continue
        except EbayApiTempError as e:
            logger.warning("[watchlist] temp API error finalizing id=%s: %s", row["id"], e)
            continue
        except Exception as e:
            logger.warning("[watchlist] unexpected API error finalizing id=%s: %s", row["id"], e)
            continue

        ended       = snap.get("ended")
        sold        = snap.get("sold")
        final_price = snap.get("final_price")

        if not ended:
            # eBay thinks it's somehow still live (relist/lag/etc)
            logger.info("[watchlist] id=%s claims still live post-end", row["id"])
            continue

        sold_status = "sold" if sold else "unsold"

        _finalize_listing(
            row_id=row["id"],
            final_price=final_price,
            sold_status=sold_status,
            confidence="api",
        )
