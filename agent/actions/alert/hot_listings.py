from __future__ import annotations

"""
Scan live auction listings that end soon, score them against comps,
record alerts idempotently, and email on first creation.

This is the global sniper:
- looks at ANY live listing ending within WINDOW_HOURS
- compares its current price to our comps for that model_key
- if it's obviously underpriced, we raise an alert and (once) email you
"""

import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, TypedDict

from psycopg2.extras import RealDictCursor

from infrastructure.db.schema import get_connection
from infrastructure.utils.logger import get_logger
from infrastructure.utils.emailer import send_email  # uses .env SMTP config
from infrastructure.adapters.telegram import TelegramAdapter
from agent.actions.telegram.hot_listings import build_hot_listings_message

logger = get_logger(__name__)

# -----------------------------
# ENV-CONFIGURABLE KNOBS
# -----------------------------
THRESHOLD_ALERT      = float(os.getenv("GF_ALERT_THRESHOLD", "0.70"))
WINDOW_HOURS         = int(os.getenv("GF_ALERT_WINDOW_HOURS", "4"))
MIN_COMP_SAMPLES     = int(os.getenv("GF_ALERT_MIN_SAMPLES", "3"))
EMAIL_SUBJECT_PREFIX = os.getenv("GF_ALERT_SUBJECT_PREFIX", "[GhostFrog Alert]")
MAX_EMAILS_PER_TICK  = int(os.getenv("GF_ALERT_MAX_EMAILS_PER_TICK", "10"))

__all__ = ["run"]


# =============================
# Local scoring model (snipe)
# =============================

@dataclass
class Comp:
    median_final_price: float
    samples: int


@dataclass
class Listing:
    external_id: str
    price_current: Optional[float]
    bids_count: Optional[int]
    time_left_s: Optional[int]
    model_key: Optional[str]


def snipe_score(listing: Listing, comp: Comp) -> float:
    """
    Returns a score in [0, 1] where higher = better deal.
    Factors in:
    - discount vs median_final_price
    - urgency (time left)
    - bids penalty (more bids → slightly worse)
    """
    if listing.price_current is None or comp is None:
        return 0.0

    margin = comp.median_final_price - listing.price_current
    if margin <= 0:
        return 0.0

    # How underpriced is it?
    margin_score = min(
        margin / max(comp.median_final_price, 1e-6),
        1.0,
    )

    # Urgency: more urgent (ending soon) = higher score
    if listing.time_left_s is None:
        urgency = 0.0
    else:
        h = listing.time_left_s / 3600
        if h <= 1:
            urgency = 1.0
        elif h <= 4:
            urgency = 0.6
        elif h <= 24:
            urgency = 0.2
        else:
            urgency = 0.0

    # Bids penalty: more bids means less "hidden gem"
    bids = listing.bids_count or 0
    bids_penalty = min(bids / 20.0, 1.0)

    score = (0.6 * margin_score) + (0.3 * urgency) - (0.5 * bids_penalty)
    return max(0.0, min(score, 1.0))


def suggest_max_bid(
    fair_price: float,
    est_fees_pct: float = 0.13,
    postage: float = 8.0,
    buffer: float = 5.0,
    take_pct: float = 0.82,
) -> float:
    """
    Very rough "what should I bid" helper.

    - take_pct: how much of fair_price you're targeting (e.g. 82%)
    - est_fees_pct: eBay/PP etc fees
    - postage: expected shipping cost
    - buffer: extra safety margin
    """
    target = fair_price * take_pct
    fees = target * est_fees_pct
    return max(0.0, round(target - fees - postage - buffer, 2))


# =============================
# DB types / helpers
# =============================

class CompRow(TypedDict):
    model_key: str
    median_final_price: Decimal
    mean_final_price: Decimal
    samples: int
    computed_at: datetime


def _compose_email_subject(model_key: str, score: float) -> str:
    return f"{EMAIL_SUBJECT_PREFIX} {model_key} — score {score:.2f}"


def _compose_email_body(
    *,
    model_key: str,
    title: str,
    url: str,
    current_price: float,
    median_final_price: float,
    suggested_max_bid: float,
    roi_pct: float,
    ends_at,
    bids_count: int,
) -> str:
    return (
        f"Model: {model_key}\n"
        f"Title: {title}\n"
        f"URL: {url}\n\n"
        f"Current: £{current_price:.2f}\n"
        f"Median fair: £{median_final_price:.2f}\n"
        f"ROI vs median: {roi_pct:.1f}%\n"
        f"Suggested max bid: £{suggested_max_bid:.2f}\n"
        f"Ends: {ends_at}\n"
        f"Bids: {bids_count}\n"
    )


def ensure_utc_session(cur) -> None:
    try:
        cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        # Not critical if this fails, but try to keep everything in UTC.
        pass


def _fetch_listings_ending_soon(hours: int) -> list[dict]:
    """
    Grab auctions that are still live, have an end_time, and finish within <hours>.
    We don't assume they're watched; this is a global radar.
    """
    conn = get_connection()
    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            ensure_utc_session(cur)
            cur.execute(
                """
                SELECT external_id,
                       model_key,
                       price_current,
                       bids_count,
                       time_left_s,
                       end_time,
                       title,
                       url
                FROM auction_listings
                WHERE status = 'live'
                  AND end_time IS NOT NULL
                  AND end_time <= NOW() + INTERVAL %s
                """,
                (f"{hours} hours}",),
            )
            return cur.fetchall()


def mark_alert_emailed(alert_id: int) -> None:
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            ensure_utc_session(cur)
            cur.execute(
                "UPDATE alerts "
                "SET sent_at = (now() AT TIME ZONE 'utc') "
                "WHERE id = %s",
                (alert_id,),
            )


def get_latest_comp_for_model(model_key: str) -> Optional[CompRow]:
    """
    Get the current 'market' snapshot for a given model_key.
    This is what you'll use in alerts:
    - if listing_price / comp['median_final_price'] < threshold -> scream.
    """
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            ensure_utc_session(cur)
            cur.execute(
                """
                SELECT model_key,
                       median_final_price,
                       mean_final_price,
                       samples,
                       computed_at
                FROM latest_comps
                WHERE model_key = %s
                LIMIT 1
                """,
                (model_key,),
            )
            row = cur.fetchone()

    if not row:
        return None

    return {
        "model_key": row[0],
        "median_final_price": row[1],
        "mean_final_price": row[2],
        "samples": row[3],
        "computed_at": row[4],
    }


def create_alerts() -> None:
    """
    Ensure the alerts table exists. Safe to call on every tick.
    """
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            ensure_utc_session(cur)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id SERIAL PRIMARY KEY,
                    external_id TEXT UNIQUE NOT NULL,
                    score DOUBLE PRECISION,
                    max_bid NUMERIC,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
                    sent_at TIMESTAMPTZ
                )
                """
            )


def record_alert(external_id: str, score: float, max_bid: float) -> tuple[bool, int | None]:
    """
    Insert or update an alert row.
    Returns (created_now, alert_id).
    """
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            ensure_utc_session(cur)
            cur.execute(
                """
                INSERT INTO alerts (external_id, score, max_bid, created_at, updated_at)
                VALUES (%s, %s, %s, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'))
                ON CONFLICT (external_id) DO UPDATE
                    SET score = EXCLUDED.score,
                        max_bid = EXCLUDED.max_bid,
                        updated_at = (now() AT TIME ZONE 'utc')
                RETURNING id, (xmax = 0) AS inserted;
                """,
                (external_id, score, max_bid),
            )
            row = cur.fetchone()

    if not row:
        return False, None

    created_now = bool(row[1])
    return created_now, row[0]


def run() -> None:
    """
    1) Ensure alerts table exists.
    2) Fetch live listings ending within WINDOW_HOURS.
    3) For each, look up latest comp for its model_key.
    4) Score with snipe_score(); skip if weak deal.
    5) record_alert() (idempotent).
    6) If first time we've seen it and email budget allows,
       send email + mark_alert_emailed().
       Regardless of email budget, also broadcast to Telegram firehose
       (if TELEGRAM_FIREHOSE_CHANNEL_ID is configured).
    """
    # Make sure the alerts table is there (idempotent).
    create_alerts()

    # --- Telegram firehose setup (optional) ---
    adapter: TelegramAdapter | None = None
    firehose_chat_id: int | None = None
    firehose_raw = os.getenv("TELEGRAM_FIREHOSE_CHANNEL_ID")
    if firehose_raw:
        try:
            firehose_chat_id = int(firehose_raw)
            adapter = TelegramAdapter.from_env()
        except Exception as e:
            logger.warning(
                "[hot_listings] Telegram firehose disabled (env issue): %s",
                e,
            )

    rows = _fetch_listings_ending_soon(WINDOW_HOURS)
    if not rows:
        logger.info(
            "[hot_listings] no live auctions ending within %d hours",
            WINDOW_HOURS,
        )
        return

    emails_sent = 0

    for r in rows:
        mk = r.get("model_key")
        if not mk:
            # listing doesn't have a normalised model_key yet -> can't price it
            continue

        comp_row = get_latest_comp_for_model(mk)
        if not comp_row:
            # we don't have comps for this model_key yet
            continue

        samples = int(comp_row.get("samples") or 0)
        if samples < MIN_COMP_SAMPLES:
            # not enough historical sold data to trust this comp
            continue

        median_final_price = float(comp_row["median_final_price"])

        current_price = float(r.get("price_current") or 0.0)
        bids_count = int(r.get("bids_count") or 0)
        time_left_s = int(r.get("time_left_s") or 0)

        listing = Listing(
            external_id=r["external_id"],
            price_current=current_price,
            bids_count=bids_count,
            time_left_s=time_left_s,
            model_key=mk,
        )

        comp = Comp(
            median_final_price=median_final_price,
            samples=samples,
        )

        score = snipe_score(listing, comp)

        # Higher score = better buy (cheaper vs market).
        if score < THRESHOLD_ALERT:
            continue

        max_bid = suggest_max_bid(comp.median_final_price)

        # Simple ROI vs median (as a percentage)
        roi_pct = 0.0
        if median_final_price > 0:
            roi_pct = (median_final_price - current_price) / median_final_price * 100.0

        created_now, alert_id = record_alert(listing.external_id, score, max_bid)

        logger.info(
            "[hot_listings] %s | score=%.2f | ROI=%.1f%% | fair=%.2f | curr=%.2f | "
            "max_bid=%.2f | time_left_s=%d | %s",
            mk,
            score,
            roi_pct,
            comp.median_final_price,
            listing.price_current,
            max_bid,
            time_left_s,
            r.get("url", ""),
        )

        # --- Firehose broadcast for *new* steals (regardless of email cap) ---
        if created_now and adapter and firehose_chat_id is not None:
            try:
                msg = build_hot_listings_message(row=r)
                adapter.send_message(msg, chat_id=firehose_chat_id)
            except Exception as e:
                logger.warning(
                    "[hot_listings][firehose] Telegram send failed for %s: %s",
                    listing.external_id,
                    e,
                )

        # Only email the first time we see this steal.
        if created_now and alert_id:
            if emails_sent >= MAX_EMAILS_PER_TICK:
                logger.warning(
                    "[hot_listings] email cap (%d) reached this tick; skipping further emails",
                    MAX_EMAILS_PER_TICK,
                )
                continue

            subject = _compose_email_subject(mk, score)
            body = _compose_email_body(
                model_key=mk,
                title=r.get("title") or "",
                url=r.get("url") or "",
                current_price=current_price,
                median_final_price=median_final_price,
                suggested_max_bid=max_bid,
                roi_pct=roi_pct,
                ends_at=r.get("end_time"),
                bids_count=bids_count,
            )

            try:
                # send_email should pull SMTP + default recipient from .env
                send_email(subject, body)
                mark_alert_emailed(alert_id)
                emails_sent += 1
                logger.info(
                    "[hot_listings][email] sent for %s (sent this tick: %d)",
                    listing.external_id,
                    emails_sent,
                )
            except Exception as e:
                logger.warning(
                    "[hot_listings][email] FAILED for %s: %s",
                    listing.external_id,
                    e,
                )
