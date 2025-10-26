from __future__ import annotations
from typing import Iterable, Optional
from datetime import datetime, timedelta, timezone

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import connection
from infrastructure.utils.emailer import send_email

logger = get_logger(__name__)

ALERT_NAME = "alert_new_listings_digest"
WINDOW_MINUTES = 60
MAX_ITEMS = 50
SOURCES_FILTER: Optional[Iterable[str]] = None
ASSUME_PENNIES = False
MAX_BODY_CHARS = 12000
FIRST_RUN_LOOKBACK_HOURS = 24  # or 168 for a full week


# ---------- time helpers (AWARE UTC end-to-end for timestamptz) ----------
def to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now_aware() -> datetime:
    return datetime.now(timezone.utc)


# ---------- state ----------
def _get_last_sent_at(cur) -> datetime | None:
    cur.execute("SELECT last_sent_at FROM alert_state WHERE name=%s", (ALERT_NAME,))
    row = cur.fetchone()
    return to_aware_utc(row[0]) if row and row[0] else None


def _set_last_sent_at(cur, ts: datetime):
    cur.execute(
        """
        INSERT INTO alert_state (name, last_sent_at)
        VALUES (%s, %s)
        ON CONFLICT (name) DO UPDATE SET last_sent_at=EXCLUDED.last_sent_at
        """,
        (ALERT_NAME, to_aware_utc(ts)),
    )


# ---------- data ----------
def _fetch_new_listings(cur, cutoff: datetime):
    """
    NEW: filter on first_seen (timestamptz), not fetched_at.
    ASC order so we can advance the watermark safely without skipping.
    """
    cutoff = to_aware_utc(cutoff)
    if SOURCES_FILTER:
        cur.execute(
            f"""
            SELECT source, title, price_current, url, first_seen
            FROM auction_listings
            WHERE first_seen > %s AND source = ANY(%s::text[])
            ORDER BY first_seen ASC
            LIMIT {MAX_ITEMS}
            """,
            (cutoff, list(SOURCES_FILTER)),
        )
    else:
        cur.execute(
            f"""
            SELECT source, title, price_current, url, first_seen
            FROM auction_listings
            WHERE first_seen > %s
            ORDER BY first_seen ASC
            LIMIT {MAX_ITEMS}
            """,
            (cutoff,),
        )
    return cur.fetchall()


def _debug_peek(cur, cutoff: datetime):
    """Log quick counts to confirm which timestamp is gating results."""
    cutoff = to_aware_utc(cutoff)
    cur.execute("SELECT COUNT(*) FROM auction_listings WHERE first_seen > %s", (cutoff,))
    c1 = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM auction_listings WHERE fetched_at > %s", (cutoff,))
    c2 = cur.fetchone()[0]
    cur.execute("SELECT MAX(first_seen), MAX(fetched_at) FROM auction_listings")
    max_first, max_fetch = cur.fetchone()
    logger.info(
        "[alert_new_listings] peek: first_seen>%s -> %s, fetched_at>%s -> %s | max first_seen=%s, max fetched_at=%s",
        cutoff.isoformat(), c1, cutoff.isoformat(), c2,
        (to_aware_utc(max_first).isoformat() if max_first else None),
        (to_aware_utc(max_fetch).isoformat() if max_fetch else None),
    )


# ---------- formatting ----------
def _format_money(v):
    if v is None:
        return "£—"
    try:
        return f"£{(int(v) / 100):.2f}" if ASSUME_PENNIES else f"£{float(v):.2f}"
    except Exception:
        return f"£{v}"


# ---------- main ----------
def run():
    conn = connection
    cur = conn.cursor()

    # Ensure the session speaks UTC; harmless if already set
    try:
        cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        pass

    last_sent = _get_last_sent_at(cur)  # aware UTC or None
    now_aware = utc_now_aware()

    # If never sent, look back FIRST_RUN_LOOKBACK_HOURS; else use WINDOW_MINUTES or last_sent (whichever is later)
    default_cut = (now_aware - timedelta(hours=FIRST_RUN_LOOKBACK_HOURS)) if last_sent is None else (now_aware - timedelta(minutes=WINDOW_MINUTES))
    cutoff = max(default_cut, last_sent) if last_sent else default_cut  # aware UTC

    # Peek to see what the DB actually has beyond the cutoff
    _debug_peek(cur, cutoff)

    rows = _fetch_new_listings(cur, cutoff)
    if not rows:
        logger.info("[alert_new_listings] no new listings since %s", cutoff.isoformat())
        return

    lines = []
    by_source_count = {}
    newest_seen = cutoff  # aware UTC

    for source, title, price, url, first_seen in rows:
        first_seen = to_aware_utc(first_seen)
        by_source_count[source] = by_source_count.get(source, 0) + 1
        if first_seen and first_seen > newest_seen:
            newest_seen = first_seen
        safe_title = (title or "").strip().replace("\n", " ")
        lines.append(f"- [{source}] {safe_title} — {_format_money(price)}\n  {url}")

    subject = "🕹 New listings ({}) — {}".format(
        len(rows),
        ", ".join(f"{s}:{c}" for s, c in sorted(by_source_count.items()))
    )
    body = (
        f"New listings since {cutoff.isoformat()}:\n\n"
        + "\n".join(lines)
        + f"\n\n(Showing up to {MAX_ITEMS} this run; older items in the window will follow next.)"
    )
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n…(truncated)"

    try:
        send_email(subject=subject, body=body)
        _set_last_sent_at(cur, newest_seen)  # persist as aware UTC
        conn.commit()
        logger.info(
            "[alert_new_listings] emailed %d items; watermark -> %s",
            len(rows),
            newest_seen.isoformat(),
        )
    except Exception as e:
        conn.rollback()
        logger.error(f"[alert_new_listings] failed to send or persist state: {e}")
