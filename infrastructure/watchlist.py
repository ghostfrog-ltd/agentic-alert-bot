# infrastructure/watchlist.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import connection
from agent.actions.close_auctions import _probe_get, _extract_final_price, _parse_status  # reuse

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
    if current_price is None or market_price is None:
        return None
    try:
        return 1.0 - (float(current_price) / float(market_price))
    except ZeroDivisionError:
        return None


def _fetch_hot_candidates() -> List[dict]:
    now = _utcnow()
    with connection, connection.cursor() as cur:
        cur.execute(
            """
            SELECT id,
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
    if coldness_val is None:
        return False
    if coldness_val < COLDNESS_ALERT_THRESHOLD:
        return False
    return (end_time - _utcnow()) <= ALERT_WINDOW


def _send_alert(row: dict, coldness_val: float, snap: dict) -> None:
    logger.warning(
        "[ALERT] Candidate %s still cold (%.1f%% under) with %s left. price=£%s vs market=£%s",
        row["id"],
        coldness_val * 100.0,
        str(row["end_time"] - _utcnow()),
        snap.get("current_price"),
        snap.get("market_price"),
    )
    # TODO: integrate send_email() or Telegram alert


def poll_hot_and_alert(fetch_live_snapshot) -> None:
    start = _utcnow()
    rows = _fetch_hot_candidates()
    if not rows:
        logger.info("[watchlist] no hot candidates")
        return

    logger.info("[watchlist] hot candidates: %d", len(rows))

    for row in rows:
        if (_utcnow() - start).total_seconds() > MAX_PHASE_SECONDS:
            logger.info("[watchlist] poll_hot phase time budget reached")
            break

        snap = fetch_live_snapshot(row)
        current_price = snap.get("current_price")
        market_price = snap.get("market_price")
        bid_count = snap.get("bid_count")

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
    now = _utcnow()
    with connection, connection.cursor() as cur:
        cur.execute(
            """
            SELECT id,
                   url,
                   end_time,
                   last_known_price,
                   final_price,
                   confidence
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
) -> None:
    with connection, connection.cursor() as cur:
        cur.execute(
            """
            UPDATE auction_listings
               SET final_price = %s,
                   status = %s,
                   confidence = 'page',
                   finalized = TRUE,
                   watch = FALSE,
                   closed_at = (now() AT TIME ZONE 'utc')
             WHERE id = %s
            """,
            (final_price, sold_status, row_id),
        )


def finalize_hot_batch() -> None:
    start = _utcnow()
    rows = _fetch_ended_unfinalized()
    if not rows:
        logger.info("[watchlist] no ended watched auctions to finalize")
        return

    logger.info("[watchlist] finalizing %d ended watched auctions", len(rows))

    for row in rows:
        if (_utcnow() - start).total_seconds() > MAX_PHASE_SECONDS:
            logger.info("[watchlist] finalize phase time budget reached")
            break

        sc, snippet, antibot = _probe_get(row["url"])
        if antibot or sc in (429, None):
            logger.warning("[watchlist] anti-bot or no response finalizing id=%s", row["id"])
            continue

        if sc in (404, 410):
            _finalize_listing(row["id"], None, "unsold")
            continue

        if not (200 <= sc < 400) or not snippet:
            logger.warning("[watchlist] bad status %s for id=%s", sc, row["id"])
            continue

        info = _parse_status(snippet)
        if info["ended"]:
            fp = _extract_final_price(snippet)
            sold_status = "sold" if fp is not None else "unsold"
            _finalize_listing(row["id"], fp, sold_status)
        else:
            logger.info("[watchlist] id=%s claims still live post-end", row["id"])
            continue
