from __future__ import annotations

"""
agent/actions/alert/roi_listings.py

Scan all active listings in auction_listings, join them to latest comps,
estimate profit/ROI, and:

- Persist roi_estimate + max_bid back onto auction_listings
- Record time-series ROI snapshots in roi_snapshots
- Fire milestone alerts using roi_alert_markers:
    - "new_high" when ROI is insanely good
    - bucket milestones (25%, 50%, 75%, 100%, etc.)
- Spam siren alerts in the final hour of an auction
- (Optionally) record per-listing alerts in alerts and send an HTML digest
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta

from psycopg2.extras import RealDictCursor

from infrastructure.utils.logger import get_logger
from infrastructure.adapters.telegram import TelegramAdapter
from agent.actions.telegram.roi_summary import build_roi_message
import os

logger = get_logger(__name__)

# --------------------------------
# Tunable thresholds / assumptions
# --------------------------------
MIN_PROFIT_GBP: float = 50.0  # minimum £ profit you care about
MIN_ROI: float = 0.25  # minimum ROI (0.25 = 25%)
FEE_RATE: float = 0.13  # assumed selling fee rate on resale
INBOUND_SHIP_DEFAULT_GBP: float = 0.0
OUTBOUND_SHIP_DEFAULT_GBP: float = 7.0

# Per-source overrides (optional; e.g. consoles ship cheaper)
PER_SOURCE: Dict[str, Dict[str, float]] = {
    # "ebay-consoles": {
    #     "min_profit": 50.0,
    #     "min_roi": 0.25,
    #     "outbound_ship": 6.0,
    #     "fee_rate": 0.13,
    # },
}

# --------------------------------
# Milestone / siren behaviour
# --------------------------------

# Bucket size for milestone alerts (25% steps → bucket_1, bucket_2, ...)
BUCKET_STEP: float = 0.25  # 0.25 = 25% ROI per bucket

# "NEW insane item" alert: first time we see something this good
NEW_HIGH_ROI: float = 3.0  # 3.0 = 300% ROI
NEW_HIGH_PROFIT_GBP: float = 100.0  # at least £100 profit

# Last-hour "spam me" window
ENDGAME_WINDOW: timedelta = timedelta(hours=1)
ENDGAME_MIN_ROI: float = 0.25  # only spam if still a decent deal
ENDGAME_MIN_PROFIT_GBP: float = 50.0

# Siren cooldown: at most one siren email per listing per 5 minutes
SIREN_COOLDOWN: timedelta = timedelta(minutes=5)

# --------------------------------
# Alert / email behaviour
# --------------------------------
RECORD_ALERTS: bool = True
SEND_EMAIL_DIGEST: bool = True

ALERT_NAME: str = "roi_listings_digest"  # used in alert_state to track last-sent
TO_EMAIL: str = "info@ghostfrog.co.uk"
MAX_EMAIL_ITEMS: int = 20  # cap items in a single email
EMAIL_COOLDOWN = timedelta(minutes=30)  # don't email more often than this

# --------------------------------
# TEMP safety: accessory-ish titles
# --------------------------------
# Keeping this for later if you want to re-enable, but we no longer use it
# to block ROI. Everything is investible now; UNKNOWN is the only hard block.
ACCESSORY_TITLE_KEYWORDS = (
    "cable",
    "charging cable",
    "charge cable",
    "usb cable",
    "power cable",
    "power lead",
    "lead",
    "charger",
    "charging dock",
    "charging station",
    "dock",
    "stand",
    "skin",
    "cover",
    "shell",
    "faceplate",
    "case",
    "grip",
)


def _is_investible_model_key(model_key: Optional[str]) -> bool:
    """
    Simplified: treat any non-empty, non-UNKNOWN model_key as investible.

    UNKNOWN is our quarantine bucket; nothing with that key should ever get ROI.
    """
    if not model_key:
        return False
    mk = str(model_key).strip()
    if not mk:
        return False
    # Hard block: UNKNOWN should never get comps/ROI
    if mk.upper() == "unknown":
        return False
    return True


# --------------------------------
# local time helpers (fallback-safe)
# --------------------------------
def _now_utc() -> datetime:
    """Return timezone-aware UTC now()."""
    return datetime.now(timezone.utc)


def _to_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime is aware+UTC. Accepts None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _humanise_time_left(end_time: Optional[datetime]) -> str:
    """Return a human-friendly '47 mins' / '2h 10m' / 'expired' style string."""
    if end_time is None:
        return "unknown"

    now = _now_utc()
    delta = end_time - now
    total_minutes = int(delta.total_seconds() // 60)

    if total_minutes <= 0:
        return "expired"

    if total_minutes < 60:
        return f"{total_minutes} mins"

    hours = total_minutes // 60
    mins = total_minutes % 60
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h {mins}m"


# --------------------------------
# Data model we pass around
# --------------------------------
@dataclass
class Opportunity:
    source: str
    external_id: str
    title: str
    url: str
    model_key: Optional[str]
    comps_samples: int
    comps_median: float
    purchase_cost: float
    outbound_ship: float
    fees: float
    profit: float
    roi: float
    end_time: Optional[datetime] = None
    time_left_s: Optional[int] = None

    def as_log(self) -> str:
        return (
            f"[ROI] {self.title[:80]} "
            f"| buy £{self.purchase_cost:.2f} → sell £{self.comps_median:.2f} "
            f"| fees £{self.fees:.2f} | ship £{self.outbound_ship:.2f} "
            f"| PROFIT £{self.profit:.2f} ({self.roi * 100:.1f}% ROI) "
            f"| comps n={self.comps_samples} | {self.url}"
        )


# --------------------------------
# Pure helpers (no DB touched)
# --------------------------------
def _money(v: float) -> float:
    """Round to GBP-style 2dp using bankers' rounding."""
    return float(Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _source_cfg(source: Optional[str]) -> Tuple[float, float, float, float]:
    """
    Per-source overrides for min_profit, min_roi, outbound_ship, fee_rate.
    Falls back to the global defaults above.
    """
    if not source:
        return MIN_PROFIT_GBP, MIN_ROI, OUTBOUND_SHIP_DEFAULT_GBP, FEE_RATE
    cfg = PER_SOURCE.get(source, {})
    return (
        cfg.get("min_profit", MIN_PROFIT_GBP),
        cfg.get("min_roi", MIN_ROI),
        cfg.get("outbound_ship", OUTBOUND_SHIP_DEFAULT_GBP),
        cfg.get("fee_rate", FEE_RATE),
    )


def _estimate_profit(
    *,
    ask_price: float,
    comps_median: float,
    fee_rate: float,
    outbound_ship: float,
    inbound_ship: float,
) -> Tuple[float, float, float]:
    """
    Estimate resale economics:
      - We assume we resell at comps_median.
      - We pay fee_rate% on the resale (seller fees).
      - We eat outbound shipping on resale.
      - We include inbound_ship in acquisition cost.
    Returns (fees, profit, roi).
    """
    fees = _money(comps_median * fee_rate)
    purchase_cost = ask_price + inbound_ship
    profit = _money(comps_median - fees - outbound_ship - purchase_cost)
    roi = 0.0 if purchase_cost <= 0 else (profit / purchase_cost)
    return fees, profit, roi


# --------------------------------
# DB helpers
# --------------------------------
def _fetch_active_listings() -> List[Dict[str, Any]]:
    """
    Pull listings that are still live/active and have a current price.
    """
    from infrastructure.db import schema  # local import

    q = """
        SELECT
            source,
            external_id,
            title,
            url,
            model_key,
            COALESCE(price_current, 0) AS price_current,
            status,
            end_time,
            time_left_s
        FROM auction_listings
        WHERE LOWER(status) IN ('active','live','open','ending_soon')
          AND price_current IS NOT NULL
    """

    with schema.connection.cursor(cursor_factory=RealDictCursor) as cur:
        schema.ensure_utc_session(cur)
        cur.execute(q)
        return list(cur.fetchall())


def latest_comps_map() -> Dict[str, Dict[str, Any]]:
    """
    Return a dict of {model_key: {median_final_price, ...}} using DISTINCT ON.
    Mostly for debugging / introspection.
    """
    from infrastructure.db import schema  # local import

    conn = schema.get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        schema.ensure_utc_session(cur)
        cur.execute(
            """
            WITH lc AS (
              SELECT DISTINCT ON (model_key)
                     model_key, median_final_price, mean_final_price, samples, computed_at
              FROM comps
              ORDER BY model_key, computed_at DESC
            )
            SELECT * FROM lc
        """
        )
        rows = cur.fetchall()
        return {r["model_key"]: r for r in rows}


def _comps_lookup() -> Dict[str, Dict[str, Any]]:
    """
    Returns latest comps per model_key from DB.
    """
    try:
        return latest_comps_map()
    except Exception as e:
        logger.warning("[roi_listings] latest_comps_map() failed: %s", e)
        return {}


def record_alert(external_id: str, score: float, max_bid: float) -> tuple[bool, int | None]:
    """
    Insert or update an alert row.
    Returns (created_now, alert_id).

    NOTE: requires a UNIQUE constraint or index on alerts.external_id, e.g.:

        ALTER TABLE alerts
        ADD CONSTRAINT alerts_external_id_key UNIQUE (external_id);
    """

    logger.info(
        "[roi_listings.record_alert] ext=%s score=%.2f max_bid=%.2f",
        external_id, score, max_bid,
    )

    from infrastructure.db import schema

    conn = schema.get_connection()
    try:
        with conn.cursor() as cur:
            schema.ensure_utc_session(cur)
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
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if not row:
        return False, None

    created_now = bool(row[1])
    return created_now, row[0]


def _maybe_record_alert(op: Opportunity) -> tuple[bool, Optional[int]]:
    """
    Persist this opportunity to alerts, deduped by external_id.
    We treat alert.score = projected £profit and alert.max_bid = 'ceiling bid'.

    Returns (created_now, alert_id).
    """
    if not RECORD_ALERTS:
        return False, None

    try:
        # naive ceiling: resale median minus fees and outbound ship cost
        est_cap = op.comps_median - op.fees - op.outbound_ship

        created_now, alert_id = record_alert(
            op.external_id,
            score=float(op.profit),
            max_bid=float(_money(est_cap)),
        )
        return created_now, alert_id

    except Exception as e:
        logger.warning(
            "[roi_listings] record_alert failed for external_id=%s: %s",
            op.external_id,
            e,
        )
        return False, None


def set_alert_last_sent(name: str, when: Optional[datetime] = None) -> None:
    """
    Record that an alert was last sent at `when` (or now() if None).

    Ensures last_sent_at is NEVER NULL so it satisfies the NOT NULL constraint
    on alert_state.last_sent_at.
    """
    from infrastructure.db import schema  # local import

    conn = schema.get_connection()

    # If caller didn't pass a time, use "now" in UTC.
    ts = _to_aware_utc(when) if when is not None else _now_utc()

    try:
        with conn.cursor() as cur:
            schema.ensure_utc_session(cur)
            cur.execute(
                """
                INSERT INTO alert_state (name, last_sent_at)
                VALUES (%s, %s)
                ON CONFLICT (name)
                DO UPDATE SET last_sent_at = EXCLUDED.last_sent_at
                """,
                (name, ts),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _send_email_digest(new_ops: List[Opportunity]) -> None:
    if not new_ops:
        return

    from infrastructure.utils.emailer import send_email  # local import

    subject = (
        f"🐸 ROI Listings: {len(new_ops)} new high-ROI deals "
        f"(≥ £{MIN_PROFIT_GBP:.0f})"
    )

    adapter: TelegramAdapter | None = None
    firehose_chat_id: int | None = None

    firehose_chat_raw = os.getenv("TELEGRAM_FIREHOSE_CHANNEL_ID")
    if firehose_chat_raw:
        try:
            firehose_chat_id = int(firehose_chat_raw)
            adapter = TelegramAdapter.from_env()
        except Exception as e:
            logger.warning(
                "[roi_listings] Telegram firehose disabled (env issue): %s", e
            )

    rows_html: List[str] = []

    for op in new_ops[:MAX_EMAIL_ITEMS]:
        # Firehose Telegram per-op if adapter + chat_id are available
        if adapter and firehose_chat_id is not None:
            try:
                msg = build_roi_message(op=op)
                adapter.send_message(msg, chat_id=firehose_chat_id)
            except Exception as e:
                logger.warning(
                    "[roi_listings] Telegram send failed for %s: %s",
                    op.external_id,
                    e,
                )

        rows_html.append(
            (
                '<p style="margin-bottom:12px;font-family:system-ui,Arial,sans-serif;'
                'font-size:14px;line-height:1.4;">'
                f'<a href="{op.url}" '
                'style="color:#0b65c2;text-decoration:none;font-weight:600;">'
                f'{op.title}</a><br>'
                f'Buy £{op.purchase_cost:.2f} → Sell £{op.comps_median:.2f} '
                f'| Fees £{op.fees:.2f} | Ship £{op.outbound_ship:.2f} '
                f'| <strong>Profit £{op.profit:.2f}</strong> '
                f'({op.roi * 100:.0f}% ROI) '
                f'| comps n={op.comps_samples}'
                '</p>'
            )
        )

    if len(new_ops) > MAX_EMAIL_ITEMS:
        rows_html.append(
            f'<p style="font-family:system-ui,Arial,sans-serif;'
            f'font-size:13px;color:#666;">... and {len(new_ops) - MAX_EMAIL_ITEMS} more.</p>'
        )

    body_html = (
        '<div style="font-family:system-ui,Arial,sans-serif;'
        'color:#111;font-size:14px;line-height:1.45;">'
        '<h2 style="margin:0 0 16px;font-size:16px;line-height:1.3;">'
        'High-ROI listings 🐸</h2>'
        + "".join(rows_html)
        + "</div>"
    )

    try:
        send_email(
            subject=subject,
            body=body_html,
            to_addr=TO_EMAIL,
            is_html=True,
        )
        set_alert_last_sent(ALERT_NAME)
        logger.info(
            "[roi_listings] email sent to %s with %d items",
            TO_EMAIL,
            len(new_ops),
        )
    except Exception as e:
        logger.error("[roi_listings] email send failed: %s", e)


# --------------------------------
# ROI snapshots + marker helpers
# --------------------------------
def _ensure_support_tables(cur) -> None:
    """
    Create roi_snapshots + roi_alert_markers if they don't exist.
    Safe to call every run.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS roi_snapshots (
            id BIGSERIAL PRIMARY KEY,
            external_id TEXT NOT NULL,
            source TEXT NOT NULL,
            model_key TEXT,
            current_price NUMERIC(12,2) NOT NULL,
            roi_estimate NUMERIC(8,4) NOT NULL,
            profit_estimate NUMERIC(12,2) NOT NULL,
            ends_at TIMESTAMPTZ,
            time_left_s INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS roi_alert_markers (
            external_id TEXT NOT NULL,
            marker TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
            PRIMARY KEY (external_id, marker)
        )
        """
    )


def _record_roi_snapshot(cur, op: Opportunity) -> None:
    cur.execute(
        """
        INSERT INTO roi_snapshots (
            external_id,
            source,
            model_key,
            current_price,
            roi_estimate,
            profit_estimate,
            ends_at,
            time_left_s
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            op.external_id,
            op.source,
            op.model_key,
            op.purchase_cost,  # purchase_cost includes inbound; you may swap to ask_price if desired
            op.roi,
            op.profit,
            _to_aware_utc(op.end_time) if op.end_time else None,
            op.time_left_s,
        ),
    )


def _marker_last_created_at(
        cur,
        external_id: str,
        marker: str,
) -> Optional[datetime]:
    """
    Return the last created_at timestamp for (external_id, marker), if any.
    Used for cooldown logic (e.g. siren emails).
    """
    cur.execute(
        """
        SELECT created_at
        FROM roi_alert_markers
        WHERE external_id = %s AND marker = %s
        LIMIT 1
        """,
        (external_id, marker),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _marker_exists(cur, external_id: str, marker: str) -> bool:
    cur.execute(
        "SELECT 1 FROM roi_alert_markers WHERE external_id=%s AND marker=%s",
        (external_id, marker),
    )
    return cur.fetchone() is not None


def _insert_marker(cur, external_id: str, marker: str) -> None:
    cur.execute(
        """
        INSERT INTO roi_alert_markers (external_id, marker, created_at)
        VALUES (%s, %s, (now() AT TIME ZONE 'utc'))
        ON CONFLICT (external_id, marker) DO UPDATE
            SET created_at = EXCLUDED.created_at
        """,
        (external_id, marker),
    )


def _send_new_high_email(op: Opportunity, time_left_str: str) -> None:
    from infrastructure.utils.emailer import send_email  # local import

    subject = f"🔥 NEW {op.roi * 100:.0f}% ROI (£{op.profit:.0f}) – {op.title[:80]}"
    body = (
        f"{op.title}\n\n"
        f"Source: {op.source}\n"
        f"URL: {op.url}\n\n"
        f"ROI: {op.roi * 100:.1f}%\n"
        f"Profit: £{op.profit:.2f}\n"
        f"Ends in: {time_left_str}\n"
    )
    try:
        send_email(subject=subject, body=body, to_addr=TO_EMAIL, is_html=False)
        logger.info(
            "[roi_listings][new_high] emailed for %s (%s)",
            op.external_id,
            subject,
        )
    except Exception as e:
        logger.warning("[roi_listings][new_high] email failed: %s", e)


def _send_bucket_email(op: Opportunity, bucket: int, time_left_str: str) -> None:
    from infrastructure.utils.emailer import send_email  # local import

    roi_pct = op.roi * 100.0
    subject = f"📈 ROI milestone {roi_pct:.0f}% (£{op.profit:.0f}) – {op.title[:80]}"
    body = (
        f"{op.title}\n\n"
        f"Bucket: {bucket} (step {BUCKET_STEP * 100:.0f}%)\n"
        f"Source: {op.source}\n"
        f"URL: {op.url}\n\n"
        f"ROI: {roi_pct:.1f}%\n"
        f"Profit: £{op.profit:.2f}\n"
        f"Ends in: {time_left_str}\n"
    )
    try:
        send_email(subject=subject, body=body, to_addr=TO_EMAIL, is_html=False)
        logger.info(
            "[roi_listings][bucket] emailed bucket_%d for %s",
            bucket,
            op.external_id,
        )
    except Exception as e:
        logger.warning("[roi_listings][bucket] email failed: %s", e)


def _send_siren_email(op: Opportunity, time_left_str: str) -> None:
    """
    Siren alert email.

    Actual dedupe / cooldown behaviour is handled by _process_roi_alerts
    using roi_alert_markers + SIREN_COOLDOWN. This function just sends
    the email for a single opportunity.
    """
    from infrastructure.utils.emailer import send_email  # local import

    roi_pct = op.roi * 100.0
    subject = (
        f"🚨 {roi_pct:.0f}% ROI (£{op.profit:.0f}) – {op.title[:80]} – "
        f"ends in {time_left_str} – BID NOW"
    )
    body = (
        f"{op.title}\n\n"
        f"ROI: {roi_pct:.1f}%\n"
        f"Profit: £{op.profit:.2f}\n"
        f"Ends in: {time_left_str}\n"
        f"URL: {op.url}\n"
    )
    try:
        send_email(subject=subject, body=body, to_addr=TO_EMAIL, is_html=False)
        logger.info(
            "[roi_listings][siren] emailed for %s (%s)", op.external_id, subject
        )
    except Exception as e:
        logger.warning("[roi_listings][siren] email failed: %s", e)


def _process_roi_alerts(opps: List[Opportunity]) -> None:
    """
    For each Opportunity:
      - record an ROI snapshot
      - fire one-shot "new_high" alert when it's insanely good
      - fire one-shot bucket alerts as ROI crosses 25%/50%/75%/100%/...
      - spam siren alerts every heartbeat in the last ENDGAME_WINDOW

    IMPORTANT:
      - We *always* record snapshots, even for ended listings.
      - We only send per-listing emails (new_high / bucket / siren)
        for listings that have NOT yet ended (end_time > now).
        This prevents "Ends in: expired" emails.
    """
    if not opps:
        return

    from infrastructure.db import schema  # local import

    conn = schema.get_connection()
    now = _now_utc()

    try:
        with conn.cursor() as cur:
            schema.ensure_utc_session(cur)
            _ensure_support_tables(cur)

            for op in opps:
                # 1) Snapshot every run for time-series analysis
                try:
                    _record_roi_snapshot(cur, op)
                except Exception as e:
                    logger.warning(
                        "[roi_listings] failed to record roi_snapshot for %s: %s",
                        op.external_id,
                        e,
                    )

                # Normalise end_time to aware UTC
                end_time = _to_aware_utc(op.end_time) if op.end_time else None

                # 2) If we know the listing has already ended,
                #    DO NOT send any of the per-listing emails.
                #    (We still recorded the snapshot above.)
                if end_time is not None and end_time <= now:
                    continue

                # From here on, emails are only for still-live listings
                time_left_str = _humanise_time_left(end_time)

                # Extra safety: if the listing literally ended in the tiny window
                # between our earlier `now` check and _humanise_time_left(),
                # don't send any per-listing emails.
                if time_left_str == "expired":
                    logger.debug(
                        "[roi_listings] skipping alerts for %s (became expired during processing)",
                        op.external_id,
                    )
                    continue

                # 3) NEW insane item (one-shot via markers)
                if op.profit >= NEW_HIGH_PROFIT_GBP and op.roi >= NEW_HIGH_ROI:
                    marker = "new_high"
                    if not _marker_exists(cur, op.external_id, marker):
                        _insert_marker(cur, op.external_id, marker)
                        _send_new_high_email(op, time_left_str)

                # 4) Bucket milestones (25%, 50%, 75%, 100%, ...)
                if op.profit >= MIN_PROFIT_GBP and op.roi >= MIN_ROI:
                    bucket = int(op.roi // BUCKET_STEP)
                    marker = f"bucket_{bucket}"
                    if not _marker_exists(cur, op.external_id, marker):
                        _insert_marker(cur, op.external_id, marker)
                        _send_bucket_email(op, bucket, time_left_str)

                # 5) Last-hour siren, but at most one email per SIREN_COOLDOWN
                if end_time is not None:
                    if (
                        end_time - now <= ENDGAME_WINDOW
                        and op.profit >= ENDGAME_MIN_PROFIT_GBP
                        and op.roi >= ENDGAME_MIN_ROI
                    ):
                        marker = "siren"
                        last_siren = _marker_last_created_at(cur, op.external_id, marker)

                        allowed = False
                        if last_siren is None:
                            allowed = True
                        else:
                            last_siren_aware = _to_aware_utc(last_siren)
                            if last_siren_aware is None or (now - last_siren_aware) >= SIREN_COOLDOWN:
                                allowed = True

                        if allowed:
                            _insert_marker(cur, op.external_id, marker)
                            _send_siren_email(op, time_left_str)

        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("[roi_listings] _process_roi_alerts failed: %s", e)


# --------------------------------
# Core shortlist logic
# --------------------------------
def _build_all_opps_for_roi(
    listings: List[Dict[str, Any]],
    comps_by_model: Dict[str, Dict[str, Any]],
) -> List[Opportunity]:
    """
    Build Opportunity objects for ROI/max_bid updates, without applying
    the profit/ROI thresholds used for alerts. Still requires decent comps.
    """
    out: List[Opportunity] = []

    for li in listings:
        source = li.get("source") or ""
        external_id = li.get("external_id") or ""
        title = li.get("title") or ""
        url = li.get("url") or ""
        model_key = li.get("model_key")
        ask_price = float(li.get("price_current") or 0.0)
        end_time = li.get("end_time")
        time_left_s = li.get("time_left_s")

        # Must have a usable, non-UNKNOWN model_key
        if not _is_investible_model_key(model_key):
            continue

        comp = comps_by_model.get(model_key)
        if not comp:
            continue

        comps_median = float(comp.get("median_final_price") or 0.0)
        comps_samples = int(comp.get("samples") or 0)

        # require at least some comp quality
        if comps_samples < 3 or comps_median <= 0.0:
            continue

        _min_profit, _min_roi, outbound_ship, fee_rate = _source_cfg(source)

        fees, profit, roi = _estimate_profit(
            ask_price=ask_price,
            comps_median=comps_median,
            fee_rate=fee_rate,
            outbound_ship=outbound_ship,
            inbound_ship=INBOUND_SHIP_DEFAULT_GBP,
        )

        out.append(
            Opportunity(
                source=source,
                external_id=external_id,
                title=title,
                url=url,
                model_key=model_key,
                comps_samples=comps_samples,
                comps_median=_money(comps_median),
                purchase_cost=_money(ask_price + INBOUND_SHIP_DEFAULT_GBP),
                outbound_ship=_money(outbound_ship),
                fees=_money(fees),
                profit=_money(profit),
                roi=roi,
                end_time=_to_aware_utc(end_time) if end_time else None,
                time_left_s=int(time_left_s) if time_left_s is not None else None,
            )
        )

    return out


def _shortlist(
    listings: List[Dict[str, Any]],
    comps_by_model: Dict[str, Dict[str, Any]],
) -> List[Opportunity]:
    """
    Same as _build_all_opps_for_roi, but applies MIN_PROFIT_GBP / MIN_ROI
    gates to decide "real opportunities" for alerts/email.
    """
    out: List[Opportunity] = []

    for li in listings:
        source = li.get("source") or ""
        external_id = li.get("external_id") or ""
        title = li.get("title") or ""
        url = li.get("url") or ""
        model_key = li.get("model_key")
        ask_price = float(li.get("price_current") or 0.0)
        end_time = li.get("end_time")
        time_left_s = li.get("time_left_s")

        # Must have a usable, non-UNKNOWN model_key
        if not _is_investible_model_key(model_key):
            continue

        comp = comps_by_model.get(model_key)
        if not comp:
            continue

        comps_median = float(comp.get("median_final_price") or 0.0)
        comps_samples = int(comp.get("samples") or 0)

        if comps_samples < 3 or comps_median <= 0.0:
            continue

        min_profit, min_roi, outbound_ship, fee_rate = _source_cfg(source)

        fees, profit, roi = _estimate_profit(
            ask_price=ask_price,
            comps_median=comps_median,
            fee_rate=fee_rate,
            outbound_ship=outbound_ship,
            inbound_ship=INBOUND_SHIP_DEFAULT_GBP,
        )

        if profit >= min_profit and roi >= min_roi:
            out.append(
                Opportunity(
                    source=source,
                    external_id=external_id,
                    title=title,
                    url=url,
                    model_key=model_key,
                    comps_samples=comps_samples,
                    comps_median=_money(comps_median),
                    purchase_cost=_money(ask_price + INBOUND_SHIP_DEFAULT_GBP),
                    outbound_ship=_money(outbound_ship),
                    fees=_money(fees),
                    profit=_money(profit),
                    roi=roi,
                    end_time=_to_aware_utc(end_time) if end_time else None,
                    time_left_s=int(time_left_s) if time_left_s is not None else None,
                )
            )

    out.sort(key=lambda o: (o.profit, o.roi), reverse=True)
    return out


def get_alert_last_sent(name: str) -> Optional[datetime]:
    from infrastructure.db import schema

    with schema.get_connection().cursor() as cur:
        schema.ensure_utc_session(cur)
        cur.execute("SELECT last_sent_at FROM alert_state WHERE name=%s", (name,))
        row = cur.fetchone()
        return row[0] if row else None


def _update_roi_estimates(opps: List[Opportunity]) -> None:
    """
    Persist roi_estimate + max_bid back onto auction_listings
    for the supplied opportunities.

    If core.scoring.snipe.suggest_max_bid is not available, we fall back
    to a simple heuristic max_bid = 0.8 * comps_median.
    """
    if not opps:
        return

    from infrastructure.db import schema

    # Try to use your proper snipe logic if it's available
    try:
        from core.scoring.snipe import suggest_max_bid as _suggest_max_bid  # type: ignore
        have_snipe = True
    except ModuleNotFoundError:
        have_snipe = False
        logger.warning(
            "[roi_listings] core.scoring.snipe not available; "
            "falling back to simple max_bid heuristic (0.8 * comps_median)"
        )

    conn = schema.get_connection()
    try:
        with conn.cursor() as cur:
            schema.ensure_utc_session(cur)
            rows = []
            for op in opps:
                if have_snipe:
                    # Use your proper snipe logic
                    max_bid = float(_suggest_max_bid(op.comps_median))
                else:
                    # Fallback: 80% of comps median as a rough ceiling
                    max_bid = float(_money(op.comps_median * 0.8))

                rows.append((float(op.roi), max_bid, op.external_id))

            cur.executemany(
                "UPDATE auction_listings "
                "SET roi_estimate = %s, max_bid = %s "
                "WHERE external_id = %s",
                rows,
            )
        conn.commit()

        logger.info(
            "[roi_listings] updated roi_estimate + max_bid for %d listings",
            len(opps),
        )
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("[roi_listings] failed to update roi_estimate/max_bid: %s", e)


# --------------------------------
# Public entry points
# --------------------------------
def get_top_roi_opportunities(limit: int = 20) -> List[Opportunity]:
    """
    Lightweight, read-only helper for consumers (e.g. Telegram /roi)
    to fetch the top-N ROI opportunities *without* triggering the full
    ROI pipeline (DB writes, emails, markers, etc).

    It reuses the same shortlist logic as run(), but only returns data.
    """
    try:
        listings = _fetch_active_listings()
    except Exception as e:
        logger.error(
            "[roi_listings] fetch active listings failed in get_top_roi_opportunities: %s",
            e,
        )
        return []

    comps_by_model = _comps_lookup()
    opps = _shortlist(listings, comps_by_model)
    return opps[:limit]


def run(limit_output: int = 20) -> List[Opportunity]:
    """
    Main entry point:
      1. Pull active listings
      2. Join to latest comps
      3. Compute ROI for all listings with decent comps and:
         - persist roi_estimate/max_bid to auction_listings
         - record roi_snapshots
         - fire NEW / bucket / siren alerts
      4. Shortlist "real opportunities" using profit/ROI gates
      5. Log top N
      6. Record each opportunity in alerts (deduped by external_id)
      7. Optionally email only the *new* ones, with cooldown
    """
    # 1. load DB data
    try:
        listings = _fetch_active_listings()
    except Exception as e:
        logger.error("[roi_listings] fetch active listings failed: %s", e)
        return []

    comps_by_model = _comps_lookup()

    # 2. compute ROI for all listings with comps and persist to DB
    all_for_roi = _build_all_opps_for_roi(listings, comps_by_model)
    _update_roi_estimates(all_for_roi)

    # 2b. fire ROI snapshots + NEW / bucket / siren alerts
    _process_roi_alerts(all_for_roi)

    # 3. shortlist profitable flips for alerts/emails
    opps = _shortlist(listings, comps_by_model)
    if not opps:
        logger.info(
            "[roi_listings] no opportunities ≥ £%.2f / ROI ≥ %.0f%%",
            MIN_PROFIT_GBP,
            MIN_ROI * 100,
        )
        return []

    top = opps[:limit_output]
    logger.info(
        "[roi_listings] %d opportunities found (showing %d)",
        len(opps),
        len(top),
    )
    for op in top:
        logger.info(op.as_log())

    newly_created: List[Opportunity] = []
    if RECORD_ALERTS or SEND_EMAIL_DIGEST:
        for op in opps:
            created_now, _alert_id = _maybe_record_alert(op)
            if created_now:
                newly_created.append(op)

    if SEND_EMAIL_DIGEST and newly_created:
        # Filter newly_created to only those with end_time before now
        now = _now_utc()
        newly_created = [op for op in newly_created if op.end_time is not None and op.end_time < now]

        if not newly_created:
            logger.info("[roi_listings] no newly created opportunities with past end_time to email")
        else:
            last_sent = get_alert_last_sent(ALERT_NAME)
            if last_sent is None:
                _send_email_digest(newly_created)
            else:
                last_sent_aware = _to_aware_utc(last_sent)
                if last_sent_aware is None:
                    _send_email_digest(newly_created)
                else:
                    since = _now_utc() - last_sent_aware
                    if since >= EMAIL_COOLDOWN:
                        _send_email_digest(newly_created)
                    else:
                        logger.info(
                            "[roi_listings] skipping email (cooldown %.0f min not reached)",
                            EMAIL_COOLDOWN.total_seconds() / 60.0,
                        )

    return opps
