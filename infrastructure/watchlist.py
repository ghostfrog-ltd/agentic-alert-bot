# infrastructure/watchlist.py
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
    Grab auctions that are:
    - watched
    - not finalized
    - ending soon (within HOT_WINDOW)
    We also pull their external_id so we can hit eBay's API.
    """
    now = _utcnow()
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
              AND end_time <= %s + interval '10 minutes'
            ORDER BY end_time ASC
            LIMIT %s
            """,
            (now, now, POLL_BATCH_LIMIT),
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
    Update the watch row with freshest live info and next poll time.
    We'll mark confidence='live' the first time we successfully poll it.
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
    - Far away? Chill.
    - Closing soon? Hammer harder.
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
    Decide if we should shout about this to you.
    - It needs to be cold (well under market)
    - and it's ending soon (within ALERT_WINDOW)
    """
    if coldness_val is None:
        return False
    if coldness_val < COLDNESS_ALERT_THRESHOLD:
        return False
    return (end_time - _utcnow()) <= ALERT_WINDOW


def _send_alert(row: dict, coldness_val: float, snap: dict) -> None:
    """
    Placeholder alert handler.
    This is where you'd fire Telegram/email/whatever.
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
    # TODO: integrate send_email() or Telegram alert


def poll_hot_and_alert() -> None:
    """
    Phase 1 of heartbeat:
    - Look at stuff ending soon
    - Hit eBay API live to refresh its current price / bid count
    - If it's stupidly underpriced, yell
    - Schedule next poll time
    """
    start = _utcnow()
    rows = _fetch_hot_candidates()
    if not rows:
        logger.info("[watchlist] no hot candidates")
        return

    logger.info("[watchlist] hot candidates: %d", len(rows))

    for row in rows:
        # safety budget, don't sit here forever
        if (_utcnow() - start).total_seconds() > MAX_PHASE_SECONDS:
            logger.info("[watchlist] poll_hot phase time budget reached")
            break

        # build the shape fetch_live_snapshot() expects
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
    Grab watched auctions that have *already ended*, not finalized yet.
    We now also want external_id so we can ask eBay for final_price.
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
    Mark a watched auction as done:
    - set sold/unsold
    - record final_price
    - flip finalized/watch flags
    - timestamp closed_at
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
    - For watched items that *should* have ended now,
      ask eBay for their final state.
    - Lock them in as sold/unsold with final_price.
    """
    start = _utcnow()
    rows = _fetch_ended_unfinalized()
    if not rows:
        logger.info("[watchlist] no ended watched auctions to finalize")
        return

    logger.info("[watchlist] finalizing %d ended watched auctions", len(rows))

    for row in rows:
        # don't blow MAX_PHASE_SECONDS
        if (_utcnow() - start).total_seconds() > MAX_PHASE_SECONDS:
            logger.info("[watchlist] finalize phase time budget reached")
            break

        snapshot_request_row = {
            "id": row["id"],
            "item_id": row.get("external_id"),
            "external_id": row.get("external_id"),
            "ebay_id": row.get("external_id"),
            # we don't really care about current live bid now; it's ended
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
            # eBay still thinks it's live (edge case: time drift, relisted, etc).
            logger.info("[watchlist] id=%s claims still live post-end", row["id"])
            continue

        # Decide sold status string for DB
        sold_status = "sold" if sold else "unsold"

        # final_price may be float or None. We push whatever Trading API gave us.
        _finalize_listing(
            row_id=row["id"],
            final_price=final_price,
            sold_status=sold_status,
            confidence="api",
        )
