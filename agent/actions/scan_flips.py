from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta

from psycopg2.extras import RealDictCursor

from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------
# Tunable thresholds / assumptions
# --------------------------------
MIN_PROFIT_GBP: float = 50.0        # minimum £ profit you care about
MIN_ROI: float = 0.25               # minimum ROI (0.25 = 25%)
FEE_RATE: float = 0.13              # assumed selling fee rate on resale
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
# Alert / email behaviour
# --------------------------------
RECORD_ALERTS: bool = True
SEND_EMAIL_DIGEST: bool = True

ALERT_NAME: str = "flipbot_digest"          # used in alert_state to track last-sent
TO_EMAIL: str = "info@ghostfrog.co.uk"
MAX_EMAIL_ITEMS: int = 20                   # cap items in a single email
EMAIL_COOLDOWN = timedelta(minutes=30)      # don't email more often than this


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

    def as_log(self) -> str:
        return (
            f"[FLIP] {self.title[:80]} "
            f"| buy £{self.purchase_cost:.2f} → sell £{self.comps_median:.2f} "
            f"| fees £{self.fees:.2f} | ship £{self.outbound_ship:.2f} "
            f"| PROFIT £{self.profit:.2f} ({self.roi*100:.1f}% ROI) "
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
# DB helpers (imports moved *inside* to avoid circulars)
# --------------------------------
def _fetch_active_listings() -> List[Dict[str, Any]]:
    """
    Pull listings that are still live/active and have a current price.
    We normalise status with LOWER(status) so we don't miss rows if adapters
    used different casing historically ('OPEN' vs 'open').
    """
    from infrastructure.db import schema  # local import to avoid circular on import

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


def _comps_lookup() -> Dict[str, Dict[str, Any]]:
    """
    Returns latest comps per model_key from DB.
    shape: {
      model_key: {
        "model_key": "...",
        "median_final_price": <Decimal or float>,
        "mean_final_price": <Decimal or float>,
        "samples": <int>,
        "computed_at": <datetime>,
      },
      ...
    }
    """
    from infrastructure.db import schema  # local import

    try:
        return schema.latest_comps_map()
    except Exception as e:
        logger.warning("[scan_flips] latest_comps_map() failed: %s", e)
        return {}


def _maybe_record_alert(op: Opportunity) -> tuple[bool, Optional[int]]:
    """
    Persist this opportunity to alerts, deduped by external_id.
    We treat alert.score = projected £profit and alert.max_bid = 'ceiling bid'.

    Returns (created_now, alert_id).
    """
    if not RECORD_ALERTS:
        return False, None

    from infrastructure.db import schema  # local import

    try:
        # naive ceiling: resale median minus fees and outbound ship cost
        est_cap = op.comps_median - op.fees - op.outbound_ship

        created_now, alert_id = schema.record_alert(
            op.external_id,
            score=float(op.profit),
            max_bid=float(_money(est_cap)),
        )
        return created_now, alert_id

    except Exception as e:
        logger.warning(
            "[scan_flips] record_alert failed for external_id=%s: %s",
            op.external_id,
            e,
        )
        return False, None


def _send_email_digest(new_ops: List[Opportunity]) -> None:
    if not new_ops:
        return

    from infrastructure.utils.emailer import send_email  # local import
    from infrastructure.db import schema  # local import

    subject = f"🐸 FlipBot: {len(new_ops)} new eBay deals (≥ £{MIN_PROFIT_GBP:.0f})"

    rows_html: List[str] = []
    for op in new_ops[:MAX_EMAIL_ITEMS]:
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
                f'({op.roi*100:.0f}% ROI) '
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
        'FlipBot opportunities 🐸</h2>'
        + "".join(rows_html) +
        "</div>"
    )

    try:
        send_email(
            subject=subject,
            body=body_html,
            to_addr=TO_EMAIL,   # <-- this MUST be to_addr
            is_html=True,       # <-- and this is allowed now
        )
        schema.set_alert_last_sent(ALERT_NAME)
        logger.info(
            "[scan_flips] email sent to %s with %d items",
            TO_EMAIL,
            len(new_ops),
        )
    except Exception as e:
        logger.error("[scan_flips] email send failed: %s", e)



# --------------------------------
# Core shortlist logic
# --------------------------------
def _shortlist(
    listings: List[Dict[str, Any]],
    comps_by_model: Dict[str, Dict[str, Any]],
) -> List[Opportunity]:
    """
    For each active listing:
      - If we have comps for its model_key
      - And enough samples
      - And profit/ROI clears thresholds
    ...then we wrap it as an Opportunity.
    """
    out: List[Opportunity] = []

    for li in listings:
        source = li.get("source") or ""
        external_id = li.get("external_id") or ""
        title = li.get("title") or ""
        url = li.get("url") or ""
        model_key = li.get("model_key")
        ask_price = float(li.get("price_current") or 0.0)

        # can't do anything without model normalisation
        if not model_key:
            continue

        comp = comps_by_model.get(model_key)
        if not comp:
            continue

        comps_median = float(comp.get("median_final_price") or 0.0)
        comps_samples = int(comp.get("samples") or 0)

        # filter out weak comps
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

        # final gate
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
                )
            )

    # Sort best-first (profit first, ROI tie-break)
    out.sort(key=lambda o: (o.profit, o.roi), reverse=True)
    return out


# --------------------------------
# Public entry point
# --------------------------------
def run(limit_output: int = 20) -> List[Opportunity]:
    """
    Main entry point:
      1. Pull active listings
      2. Join to latest comps
      3. Filter for real flips (profit/ROI gates)
      4. Log top N
      5. Record each opportunity in alerts (deduped)
      6. Optionally email only the *new* ones, with cooldown
    """
    from infrastructure.db import schema  # local import

    # 1. load DB data
    try:
        listings = _fetch_active_listings()
    except Exception as e:
        logger.error("[scan_flips] fetch active listings failed: %s", e)
        return []

    comps_by_model = _comps_lookup()

    # 2. shortlist profitable flips
    opps = _shortlist(listings, comps_by_model)
    if not opps:
        logger.info(
            "[scan_flips] no opportunities ≥ £%.2f / ROI ≥ %.0f%%",
            MIN_PROFIT_GBP,
            MIN_ROI * 100,
        )
        return []

    # 3. log top N for console visibility
    top = opps[:limit_output]
    logger.info(
        "[scan_flips] %d opportunities found (showing %d)",
        len(opps),
        len(top),
    )
    for op in top:
        logger.info(op.as_log())

    # 4. record / dedupe in alerts, collect brand new ones
    newly_created: List[Opportunity] = []
    if RECORD_ALERTS or SEND_EMAIL_DIGEST:
        for op in opps:
            created_now, _alert_id = _maybe_record_alert(op)
            if created_now:
                newly_created.append(op)

    # 5. email digest of JUST the new ones, respecting cooldown
    if SEND_EMAIL_DIGEST and newly_created:
        last_sent = schema.get_alert_last_sent(ALERT_NAME)

        # If never sent, send now.
        if last_sent is None:
            _send_email_digest(newly_created)
        else:
            last_sent_aware = _to_aware_utc(last_sent)
            if last_sent_aware is None:
                # DB gave us something weird/naive -> just send
                _send_email_digest(newly_created)
            else:
                since = _now_utc() - last_sent_aware
                if since >= EMAIL_COOLDOWN:
                    _send_email_digest(newly_created)
                else:
                    logger.info(
                        "[scan_flips] skipping email (cooldown %.0f min not reached)",
                        EMAIL_COOLDOWN.total_seconds() / 60.0,
                    )

    return opps
