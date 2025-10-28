# agent/actions/scan_flips.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from decimal import Decimal, ROUND_HALF_UP

from psycopg2.extras import RealDictCursor

from infrastructure.utils.logger import get_logger
from infrastructure.db import schema
from infrastructure.utils.emailer import send_email  # <- uses your existing email helper

logger = get_logger(__name__)

# --------------------------------
# Config knobs (tweak to taste)
# --------------------------------
MIN_PROFIT_GBP: float = 50.0
MIN_ROI: float = 0.25
FEE_RATE: float = 0.13
INBOUND_SHIP_DEFAULT_GBP: float = 0.0
OUTBOUND_SHIP_DEFAULT_GBP: float = 7.0

# Per-source overrides (optional)
PER_SOURCE: Dict[str, Dict[str, float]] = {
    # "ebay-consoles": {"min_profit": 50.0, "min_roi": 0.25, "outbound_ship": 6.0, "fee_rate": 0.13},
}

# Alerts & Email
RECORD_ALERTS: bool = True
SEND_EMAIL_DIGEST: bool = True
ALERT_NAME: str = "flipbot_digest"          # used in alert_state
TO_EMAIL: str = "info@ghostfrog.co.uk"      # adjust if needed
MAX_EMAIL_ITEMS: int = 20                   # cap items per email


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


def _money(v: float) -> float:
    return float(Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _source_cfg(source: Optional[str]) -> Tuple[float, float, float, float]:
    if not source:
        return MIN_PROFIT_GBP, MIN_ROI, OUTBOUND_SHIP_DEFAULT_GBP, FEE_RATE
    cfg = PER_SOURCE.get(source, {})
    return (
        cfg.get("min_profit", MIN_PROFIT_GBP),
        cfg.get("min_roi", MIN_ROI),
        cfg.get("outbound_ship", OUTBOUND_SHIP_DEFAULT_GBP),
        cfg.get("fee_rate", FEE_RATE),
    )


def _fetch_active_listings() -> List[Dict[str, Any]]:
    q = """
        SELECT
            source, external_id, title, url, model_key,
            COALESCE(price_current, 0) AS price_current,
            status, end_time, time_left_s
        FROM auction_listings
        WHERE status IN ('active','live','OPEN','ENDING_SOON')
          AND price_current IS NOT NULL
    """
    with schema.connection.cursor(cursor_factory=RealDictCursor) as cur:
        schema.ensure_utc_session(cur)
        cur.execute(q)
        return list(cur.fetchall())


def _comps_lookup() -> Dict[str, Dict[str, Any]]:
    try:
        return schema.latest_comps_map()
    except Exception as e:
        logger.warning("[scan_flips] latest_comps_map() failed: %s", e)
        return {}


def _estimate_profit(
    *,
    ask_price: float,
    comps_median: float,
    fee_rate: float,
    outbound_ship: float,
    inbound_ship: float,
) -> Tuple[float, float, float]:
    fees = _money(comps_median * fee_rate)           # fees on resale price
    purchase_cost = ask_price + inbound_ship
    profit = _money(comps_median - fees - outbound_ship - purchase_cost)
    roi = 0.0 if purchase_cost <= 0 else profit / purchase_cost
    return fees, profit, roi


def _shortlist(listings: List[Dict[str, Any]], comps_by_model: Dict[str, Dict[str, Any]]) -> List[Opportunity]:
    out: List[Opportunity] = []

    for li in listings:
        source = li.get("source")
        external_id = li.get("external_id") or ""
        title = li.get("title") or ""
        url = li.get("url") or ""
        model_key = li.get("model_key")
        ask = float(li.get("price_current") or 0.0)

        if not model_key:
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
            ask_price=ask,
            comps_median=comps_median,
            fee_rate=fee_rate,
            outbound_ship=outbound_ship,
            inbound_ship=INBOUND_SHIP_DEFAULT_GBP,
        )

        if profit >= min_profit and roi >= min_roi:
            out.append(
                Opportunity(
                    source=source or "",
                    external_id=external_id,
                    title=title,
                    url=url,
                    model_key=model_key,
                    comps_samples=comps_samples,
                    comps_median=_money(comps_median),
                    purchase_cost=_money(ask + INBOUND_SHIP_DEFAULT_GBP),
                    outbound_ship=_money(outbound_ship),
                    fees=_money(fees),
                    profit=_money(profit),
                    roi=roi,
                )
            )

    out.sort(key=lambda o: (o.profit, o.roi), reverse=True)
    return out


def _maybe_record_alert(op: Opportunity) -> tuple[bool, Optional[int]]:
    """
    Store/dedupe via alerts table: score=profit, max_bid=(sale_est - fees - ship).
    Returns (created_now, alert_id).
    """
    if not RECORD_ALERTS:
        return False, None
    try:
        est_cap = op.comps_median - op.fees - op.outbound_ship
        created_now, alert_id = schema.record_alert(
            op.external_id,
            score=float(op.profit),
            max_bid=float(_money(est_cap)),
        )
        return created_now, alert_id
    except Exception as e:
        logger.warning("[scan_flips] record_alert failed for %s: %s", op.external_id, e)
        return False, None


def _send_email_digest(new_ops: List[Opportunity]) -> None:
    if not new_ops:
        return

    # Subject & body
    subject = f"🐸 FlipBot: {len(new_ops)} new eBay deals (≥ £{MIN_PROFIT_GBP:.0f})"
    lines = []
    for op in new_ops[:MAX_EMAIL_ITEMS]:
        lines.append(
            f"• {op.title} "
            f"| Buy £{op.purchase_cost:.2f} → Sell £{op.comps_median:.2f} "
            f"| Fees £{op.fees:.2f} | Ship £{op.outbound_ship:.2f} "
            f"| Profit £{op.profit:.2f} ({op.roi*100:.0f}% ROI)\n  {op.url}"
        )
    if len(new_ops) > MAX_EMAIL_ITEMS:
        lines.append(f"... and {len(new_ops)-MAX_EMAIL_ITEMS} more.")

    body = "\n".join(lines)

    try:
        send_email(to=TO_EMAIL, subject=subject, body=body)
        schema.set_alert_last_sent(ALERT_NAME)  # stamp last-sent time
        logger.info("[scan_flips] email sent to %s with %d items", TO_EMAIL, len(new_ops))
    except Exception as e:
        logger.error("[scan_flips] email send failed: %s", e)


def run(limit_output: int = 20) -> List[Opportunity]:
    """
    Scans DB for flips, logs the top, records alerts, and optionally emails a digest
    of *newly created* alerts this run (deduped by external_id).
    """
    try:
        listings = _fetch_active_listings()
    except Exception as e:
        logger.error("[scan_flips] fetch active listings failed: %s", e)
        return []

    comps_by_model = _comps_lookup()
    opps = _shortlist(listings, comps_by_model)

    if not opps:
        logger.info("[scan_flips] no opportunities ≥ £%.2f / ROI ≥ %.0f%%", MIN_PROFIT_GBP, MIN_ROI * 100)
        return []

    top = opps[:limit_output]
    logger.info("[scan_flips] %d opportunities found (showing %d)", len(opps), len(top))
    for op in top:
        logger.info(op.as_log())

    # Record + collect new ones for email
    newly_created: List[Opportunity] = []
    if RECORD_ALERTS or SEND_EMAIL_DIGEST:
        for op in opps:
            created_now, _alert_id = _maybe_record_alert(op)
            if created_now:
                newly_created.append(op)

    # Email digest of the NEW opportunities created in this run
    if SEND_EMAIL_DIGEST and newly_created:
        # Optional: throttle to avoid too-frequent emails
        last_sent = schema.get_alert_last_sent(ALERT_NAME)
        if last_sent is None:
            # first run -> send (bootstrap)
            _send_email_digest(newly_created)
        else:
            # You can enforce a minimal cadence here if desired.
            _send_email_digest(newly_created)

    return opps
