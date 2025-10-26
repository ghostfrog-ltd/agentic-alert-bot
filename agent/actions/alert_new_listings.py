from __future__ import annotations
from typing import Iterable, Optional
from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import connection
from infrastructure.utils.emailer import send_email
from datetime import datetime, timedelta, timezone

logger = get_logger(__name__)

ALERT_NAME = "alert_new_listings_digest"
WINDOW_MINUTES = 60
MAX_ITEMS = 50
SOURCES_FILTER: Optional[Iterable[str]] = None
ASSUME_PENNIES = False
MAX_BODY_CHARS = 12000
FIRST_RUN_LOOKBACK_HOURS = 24  # or 168 for a full week


def _get_last_sent_at(cur):
    cur.execute("SELECT last_sent_at FROM alert_state WHERE name=%s", (ALERT_NAME,))
    row = cur.fetchone()
    return row[0] if row else None


def _set_last_sent_at(cur, ts: datetime):
    cur.execute("""
        INSERT INTO alert_state (name, last_sent_at)
        VALUES (%s, %s)
        ON CONFLICT (name) DO UPDATE SET last_sent_at=EXCLUDED.last_sent_at
    """, (ALERT_NAME, ts))


def _fetch_new_listings(cur, cutoff: datetime):
    """
    IMPORTANT: ASC order so we can advance the watermark safely
    without skipping older-but-still-new rows.
    """
    if SOURCES_FILTER:
        cur.execute(f"""
            SELECT source, title, price_current, url, fetched_at
            FROM auction_listings
            WHERE fetched_at > %s AND source = ANY(%s::text[])
            ORDER BY fetched_at ASC
            LIMIT {MAX_ITEMS}
        """, (cutoff, list(SOURCES_FILTER)))
    else:
        cur.execute(f"""
            SELECT source, title, price_current, url, fetched_at
            FROM auction_listings
            WHERE fetched_at > %s
            ORDER BY fetched_at ASC
            LIMIT {MAX_ITEMS}
        """, (cutoff,))
    return cur.fetchall()


def _format_money(v):
    if v is None:
        return "£—"
    try:
        return f"£{(int(v) / 100):.2f}" if ASSUME_PENNIES else f"£{float(v):.2f}"
    except Exception:
        return f"£{v}"


def _utc_aware(dt):
    if dt is None:
        return None
    # If naive, assume it’s already UTC from the DB and attach tzinfo
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def run():
    conn = connection
    cur = conn.cursor()

    last_sent = _get_last_sent_at(cur)  # may be naive or aware
    now = datetime.now(timezone.utc)  # aware
    cutoff = _utc_aware(last_sent) or (now - timedelta(minutes=WINDOW_MINUTES))
    cutoff = _utc_aware(cutoff)  # ensure aware

    rows = _fetch_new_listings(cur, cutoff)
    if not rows:
        logger.info("[alert_new_listings] no new listings since %s", cutoff.isoformat())
        return

    lines = []
    by_source_count = {}
    newest_seen = cutoff  # aware

    for source, title, price, url, fetched_at in rows:
        fetched_at = _utc_aware(fetched_at)  # normalize row timestamp
        by_source_count[source] = by_source_count.get(source, 0) + 1
        if fetched_at and fetched_at > newest_seen:
            newest_seen = fetched_at
        safe_title = (title or "").strip().replace("\n", " ")
        lines.append(f"- [{source}] {safe_title} — {_format_money(price)}\n  {url}")

    subject = f"🕹 New listings ({len(rows)}) — " + ", ".join(f"{s}:{c}" for s, c in sorted(by_source_count.items()))
    body = (
            f"New listings since {cutoff.isoformat()}:\n\n"
            + "\n".join(lines)
            + "\n\n(Showing up to {MAX_ITEMS} this run; older items in the window will follow next.)"
    )
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n…(truncated)"

    try:
        send_email(subject=subject, body=body)
        _set_last_sent_at(cur, newest_seen)  # aware ⇒ fine for timestamptz
        conn.commit()
        logger.info("[alert_new_listings] emailed %d items; watermark -> %s", len(rows), newest_seen.isoformat())
    except Exception as e:
        conn.rollback()
        logger.error(f"[alert_new_listings] failed to send or persist state: {e}")
