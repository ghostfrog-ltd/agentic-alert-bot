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
# Milestone / siren behaviour
# --------------------------------

# Bucket size for milestone alerts (25% steps → bucket_1, bucket_2, ...)
BUCKET_STEP: float = 0.25  # 0.25 = 25% ROI per bucket

# "NEW insane item" alert: first time we see something this good
NEW_HIGH_ROI: float = 3.0            # 3.0 = 300% ROI
NEW_HIGH_PROFIT_GBP: float = 100.0   # at least £100 profit

# Last-hour "spam me" window
ENDGAME_WINDOW: timedelta = timedelta(hours=1)
ENDGAME_MIN_ROI: float = 0.25        # only spam if still a decent deal
ENDGAME_MIN_PROFIT_GBP: float = 50.0

# --------------------------------
# Alert / email behaviour
# --------------------------------
RECORD_ALERTS: bool = True
SEND_EMAIL_DIGEST: bool = True

ALERT_NAME: str = "roi_listings_digest"     # used in alert_state to track last-sent
TO_EMAIL: str = "info@ghostfrog.co.uk"
MAX_EMAIL_ITEMS: int = 20                   # cap items in a single email
EMAIL_COOLDOWN = timedelta(minutes=30)      # don't email more often than this

# --------------------------------
# Investible model_key filters
# --------------------------------
# Only these shapes of model_key are allowed to drive ROI/comps/alerts.
# Everything else (games, accessories, virtual items, noise) is ignored.
INVESTIBLE_PREFIXES = (
    "bike_",      # all bikes
)

INVESTIBLE_SUFFIXES = (
    "_console",   # ps5_console, ps4_console, xbox_one_console, switch_console, etc.
)

INVESTIBLE_EXACT = {
    # specific exceptions we *do* want to track
    # "neo_geo_cd_console",
    # "pc_engine_console",
}

# Explicitly banned model keys that produce junk ROI
NON_INVESTIBLE_EXACT = {
    "generic_retro_console",  # all the £10 4K HDMI sticks etc.
}


def _is_investible_model_key(model_key: Optional[str]) -> bool:
    """
    Decide whether this model_key is allowed to participate in ROI.

    - bikes are always investible
    - console hardware (ends with *_console)
    - any special whitelisted exact keys
    - anything in NON_INVESTIBLE_EXACT is *never* investible
    """
    if not model_key:
        return False

    mk = model_key.lower().strip()
    if not mk:
        return False

    # Hard exclusion
    if mk in NON_INVESTIBLE_EXACT:
        return False

    # Bikes always investible
    if mk.startswith(INVESTIBLE_PREFIXES):
        return True

    # Console hardware: ps5_console, switch_console, etc.
    if any(mk.endswith(suffix) for suffix in INVESTIBLE_SUFFIXES):
        return True

    # Explicit whitelisted console keys
    if mk in INVESTIBLE_EXACT:
        return True

    # Everything else: games, accessories, unknowns, etc.
    return False


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


def latest_comps_map() -> Dict[str, Dict[str, A]()]()
