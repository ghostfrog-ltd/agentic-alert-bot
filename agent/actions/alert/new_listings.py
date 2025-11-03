from __future__ import annotations
from typing import Iterable, Optional
from datetime import datetime, timedelta

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import connection
from infrastructure.utils.emailer import send_email
from infrastructure.utils.timez import now_utc, to_aware_utc

logger = get_logger(__name__)

ALERT_NAME = "alert_new_listings_digest"

WINDOW_MINUTES = 60
MAX_ITEMS = 50
SOURCES_FILTER: Optional[Iterable[str]] = None
ASSUME_PENNIES = False
MAX_BODY_CHARS = 12000
FIRST_RUN_LOOKBACK_HOURS = 24  # or 168 for a full week

TO_EMAIL = "info@ghostfrog.co.uk"


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


def _extract_numeric_item_id(raw_id: str | None) -> str | None:
    """
    eBay Browse API itemIds often look like 'v1|123456789012|0'.
    We only want the numeric middle bit for a public /itm/ URL.

    If it's already just digits, keep it. If it's None or weird, return None.
    """
    if not raw_id:
        return None

    # common case: v1|123456789012|0
    if "|" in raw_id:
        parts = raw_id.split("|")
        # usually parts[1] is the numeric ID
        for p in parts:
            if p.isdigit():
                return p

    # fallback: if the whole thing is digits already
    if raw_id.isdigit():
        return raw_id

    # couldn't get anything clean
    return None


def _build_uk_url(raw_external_id: str | None) -> str:
    """
    Force eBay UK domain so we land on GBP / UK context instead of USD.
    We ignore whatever 'url' the API gave us (which is usually .com).
    """
    numeric_id = _extract_numeric_item_id(raw_external_id)
    if not numeric_id:
        # fallback: just return something so the email doesn't explode
        return "https://www.ebay.co.uk/"

    return f"https://www.ebay.co.uk/itm/{numeric_id}"


def _fetch_new_listings(cur, cutoff: datetime):
    """
    Grab recent listings since cutoff.
    We now also select external_id so we can build a .co.uk URL ourselves.
    """
    cutoff = to_aware_utc(cutoff)

    if SOURCES_FILTER:
        cur.execute(
            f"""
            SELECT
                source,
                title,
                price_current,
                url,
                external_id,
                first_seen
            FROM auction_listings
            WHERE first_seen > %s
              AND source = ANY(%s)
            ORDER BY first_seen ASC
            LIMIT {MAX_ITEMS}
            """,
            (cutoff, list(SOURCES_FILTER)),
        )
    else:
        cur.execute(
            f"""
            SELECT
                source,
                title,
                price_current,
                url,
                external_id,
                first_seen
            FROM auction_listings
            WHERE first_seen > %s
            ORDER BY first_seen ASC
            LIMIT {MAX_ITEMS}
            """,
            (cutoff,),
        )

    return cur.fetchall()


def _format_money(v):
    if v is None:
        return "£—"
    try:
        return f"£{(int(v) / 100):.2f}" if ASSUME_PENNIES else f"£{float(v):.2f}"
    except Exception:
        return f"£{v}"


def run():
    conn = connection
    cur = conn.cursor()

    # try to force UTC for comparisons / ISO timestamps
    try:
        cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        pass

    last_sent = _get_last_sent_at(cur)
    now_aware = now_utc()

    # first run looks back a big window (24h default)
    default_cut = (
        now_aware - timedelta(hours=FIRST_RUN_LOOKBACK_HOURS)
        if last_sent is None
        else (now_aware - timedelta(minutes=WINDOW_MINUTES))
    )

    # cutoff = later of (default_cut, last_sent), unless no last_sent at all
    cutoff = max(default_cut, last_sent) if last_sent else default_cut

    rows = _fetch_new_listings(cur, cutoff)
    if not rows:
        return

    by_source_count: dict[str, int] = {}
    newest_seen = cutoff

    lines_html: list[str] = []

    # rows are now:
    #   (source, title, price_current, url, external_id, first_seen)
    for source, title, price, _raw_url, external_id, first_seen in rows:
        first_seen = to_aware_utc(first_seen)

        # update source counts for the subject line
        by_source_count[source] = by_source_count.get(source, 0) + 1

        # watermark so we don't resend the same stuff next run
        if first_seen and first_seen > newest_seen:
            newest_seen = first_seen

        safe_title = (title or "").strip().replace("\n", " ")

        # ignore the stored URL (which might be .com / USD)
        # always generate a UK URL from the eBay item ID
        uk_url = _build_uk_url(external_id)

        lines_html.append(
            (
                '<li style="margin-bottom:8px;">'
                f'<strong>[{source}]</strong> '
                f'<a href="{uk_url}" target="_blank" '
                'style="color:#0b65c2;text-decoration:none;font-weight:600;">'
                f'{safe_title}</a> — {_format_money(price)}'
                '</li>'
            )
        )

    subject = "🕹 New listings ({}) — {}".format(
        len(rows),
        ", ".join(f"{s}:{c}" for s, c in sorted(by_source_count.items())),
    )

    body_html = f"""
    <div style="font-family:system-ui,Arial,sans-serif;
                font-size:14px;
                line-height:1.45;
                color:#111;">
      <p style="margin:0 0 12px 0;">
        New listings since {cutoff.isoformat()}:
      </p>
      <ul style="margin:0 0 16px 20px;padding:0;">
        {''.join(lines_html)}
      </ul>
      <p style="margin:0;font-size:12px;color:#666;">
        (Showing up to {MAX_ITEMS} this run; older items will follow next.)
      </p>
    </div>
    """

    if len(body_html) > MAX_BODY_CHARS:
        body_html = (
            body_html[:MAX_BODY_CHARS]
            + '<p style="font-size:12px;color:#666;">…(truncated)</p>'
        )

    try:
        # IMPORTANT: match your new emailer signature
        send_email(
            subject=subject,
            body=body_html,
            to_addr=TO_EMAIL,
            is_html=True,
        )

        _set_last_sent_at(cur, newest_seen)
        conn.commit()

        logger.info(
            "[alert_new_listings] emailed %d items; watermark -> %s",
            len(rows),
            newest_seen.isoformat(),
        )

    except Exception as e:
        conn.rollback()
        logger.error(
            "[alert_new_listings] failed to send or persist state: %s",
            e,
        )
