from dataclasses import dataclass
from typing import Optional

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
    if listing.price_current is None or comp is None:
        return 0.0
    margin = comp.median_final_price - listing.price_current
    if margin <= 0:
        return 0.0

    margin_score = min(margin / max(comp.median_final_price, 1e-6), 1.0)

    # urgency
    if listing.time_left_s is None:
        urgency = 0.0
    else:
        h = listing.time_left_s / 3600
        urgency = 1.0 if h <= 1 else 0.6 if h <= 4 else 0.2 if h <= 24 else 0.0

    bids = listing.bids_count or 0
    bids_penalty = min(bids / 20.0, 1.0)

    score = (0.6 * margin_score) + (0.3 * urgency) - (0.5 * bids_penalty)
    return max(0.0, min(score, 1.0))

def suggest_max_bid(fair_price: float, est_fees_pct: float = 0.13, postage: float = 8.0, buffer: float = 5.0, take_pct: float = 0.82) -> float:
    target = fair_price * take_pct
    fees = target * est_fees_pct
    return max(0.0, round(target - fees - postage - buffer, 2))
