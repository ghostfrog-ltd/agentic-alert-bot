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
from psycopg2.extras import RealDictCursor

from infrastructure.db.schema import (
    connection,
    get_latest_comp_for_model,  # per-model_key comp lookup
    record_alert,               # returns (created_now, alert_id)
    mark_alert_emailed,         # sets sent_at so we never re-send
)
from core.scoring.snipe import Listing, Comp, snipe_score, suggest_max_bid
from infrastructure.utils.logger import get_logger
from infrastructure.utils.emailer import send_email  # uses .env SMTP vars

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
    ends_at,
    bids_count: int,
) -> str:
    return (
        f"Model: {model_key}\n"
        f"Title: {title}\n"
        f"URL: {url}\n\n"
        f"Current: £{current_price:.2f}\n"
        f"Median fair: £{median_final_price:.2f}\n"
        f"Suggested max bid: £{suggested_max_bid:.2f}\n"
        f"Ends: {ends_at}\n"
        f"Bids: {bids_count}\n"
    )


def _fetch_listings_ending_soon(hours: int) -> list[dict]:
    """
    Grab auctions that are still live, have an end_time, and finish within <hours>.
    We don't assume they're watched; this is a global radar.
    """
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
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
            (f"{hours} hours",),
        )
        return cur.fetchall()


def run() -> None:
    """
    1) Fetch live listings ending within WINDOW_HOURS.
    2) For each, look up latest comp for its model_key.
    3) Score with snipe_score(); skip if weak deal.
    4) record_alert() (idempotent).
    5) If first time we've seen it and email budget allows, send email + mark_alert_emailed().
    """

    rows = _fetch_listings_ending_soon(WINDOW_HOURS)
    if not rows:
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

        created_now, alert_id = record_alert(listing.external_id, score, max_bid)

        logger.info(
            "[ALERT] %s | score=%.2f | fair=%.2f | curr=%.2f | max_bid=%.2f | %s",
            mk,
            score,
            comp.median_final_price,
            listing.price_current,
            max_bid,
            r.get("url", ""),
        )

        # Only email the first time we see this steal.
        if created_now and alert_id:
            if emails_sent >= MAX_EMAILS_PER_TICK:
                logger.warning(
                    "[ALERT] email cap (%d) reached this tick; skipping further emails",
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
                ends_at=r.get("end_time"),
                bids_count=bids_count,
            )

            try:
                send_email(subject, body)
                mark_alert_emailed(alert_id)
                emails_sent += 1
                logger.info(
                    "[ALERT][email] sent for %s (sent this tick: %d)",
                    listing.external_id,
                    emails_sent,
                )
            except Exception as e:
                logger.warning(
                    "[ALERT][email] FAILED for %s: %s",
                    listing.external_id,
                    e,
                )
