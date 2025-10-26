# agent/actions/scan_ending_soon.py
"""
Scan live auction listings that end soon, score them against comps,
record alerts idempotently, and email on first creation.
"""

from __future__ import annotations

import os
from psycopg2.extras import RealDictCursor

from infrastructure.db.schema import (
    connection,
    latest_comps_map,
    record_alert,        # should return (created_now, alert_id)
    mark_alert_emailed,  # sets sent_at so we never re-send this alert
)
from core.scoring.snipe import Listing, Comp, snipe_score, suggest_max_bid
from infrastructure.utils.logger import get_logger
from infrastructure.utils.emailer import send_email  # uses .env SMTP vars

logger = get_logger(__name__)

# -----------------------------
# ENV-CONFIGURABLE KNOBS
# -----------------------------
THRESHOLD_ALERT     = float(os.getenv("GF_ALERT_THRESHOLD", "0.70"))
WINDOW_HOURS        = int(os.getenv("GF_ALERT_WINDOW_HOURS", "4"))
MIN_COMP_SAMPLES    = int(os.getenv("GF_ALERT_MIN_SAMPLES", "3"))
EMAIL_SUBJECT_PREFIX= os.getenv("GF_ALERT_SUBJECT_PREFIX", "[GhostFrog Alert]")
MAX_EMAILS_PER_TICK = int(os.getenv("GF_ALERT_MAX_EMAILS_PER_TICK", "10"))

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


def run() -> None:
    """
    1) Load latest comps per model_key
    2) Fetch live listings ending within WINDOW_HOURS
    3) Score each listing; if >= threshold -> record alert (idempotent)
    4) On first creation, email details and mark as emailed
    """
    comps = latest_comps_map()
    if not comps:
        return

    # Fetch items that end soon (live and with a known end_time)
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT external_id, model_key, price_current, bids_count, time_left_s,
                   end_time, title, url
            FROM auction_listings
            WHERE status = 'live'
              AND end_time IS NOT NULL
              AND end_time <= NOW() + INTERVAL %s
            """,
            (f"{WINDOW_HOURS} hours",),
        )
        rows = cur.fetchall()

    if not rows:
        return

    emails_sent = 0

    for r in rows:
        mk = r.get("model_key")
        if not mk or mk not in comps:
            continue

        samples = int(comps[mk].get("samples") or 0)
        if samples < MIN_COMP_SAMPLES:
            continue

        comp = Comp(
            median_final_price=float(comps[mk]["median_final_price"]),
            samples=samples,
        )

        lst = Listing(
            external_id=r["external_id"],
            price_current=float(r["price_current"] or 0.0),
            bids_count=int(r["bids_count"] or 0),
            time_left_s=int(r["time_left_s"] or 0),
            model_key=mk,
        )

        score = snipe_score(lst, comp)
        if score < THRESHOLD_ALERT:
            continue

        max_bid = suggest_max_bid(comp.median_final_price)

        # Record (idempotent) and decide whether to email
        created_now, alert_id = record_alert(lst.external_id, score, max_bid)

        logger.info(
            "[ALERT] %s | score=%.2f | fair=%.2f | curr=%.2f | max_bid=%.2f | %s",
            mk,
            score,
            comp.median_final_price,
            lst.price_current,
            max_bid,
            r["url"],
        )

        if created_now and alert_id:
            if emails_sent >= MAX_EMAILS_PER_TICK:
                logger.warning("[ALERT] email cap (%d) reached this tick; skipping further emails", MAX_EMAILS_PER_TICK)
                continue

            subject = _compose_email_subject(mk, score)
            body = _compose_email_body(
                model_key=mk,
                title=r.get("title") or "",
                url=r.get("url") or "",
                current_price=lst.price_current,
                median_final_price=comp.median_final_price,
                suggested_max_bid=max_bid,
                ends_at=r.get("end_time"),
                bids_count=lst.bids_count,
            )
            try:
                send_email(subject, body)
                mark_alert_emailed(alert_id)
                emails_sent += 1
                logger.info("[ALERT][email] sent for %s (sent this tick: %d)", lst.external_id, emails_sent)
            except Exception as e:
                logger.warning("[ALERT][email] FAILED for %s: %s", lst.external_id, e)
