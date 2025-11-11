# agent/actions/telegram/roi_summary.py
from __future__ import annotations

from typing import List

from agent.actions.alert import roi_listings
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)


def _format_time_left_s(time_left_s: int | None) -> str:
    if time_left_s is None:
        return "unknown"

    if time_left_s <= 0:
        return "expired"

    mins = time_left_s // 60
    if mins < 60:
        return f"{mins} mins"

    hours = mins // 60
    rem_mins = mins % 60
    if rem_mins == 0:
        return f"{hours}h"
    return f"{hours}h {rem_mins}m"


def build_roi_message(limit: int = 3) -> str:
    """
    Use the existing ROI agent to compute opportunities, then format a
    Telegram-friendly summary.

    This calls roi_listings.run(), so it will:
      - recompute ROI for active listings
      - update auction_listings
      - record snapshots / alerts
      - (maybe) send the existing email digest
    """
    try:
        # run() already returns a list[Opportunity] sorted by profit/ROI
        opps: List[roi_listings.Opportunity] = roi_listings.run(
            limit_output=limit
        )
    except Exception:
        logger.exception("[Telegram /roi] roi_listings.run() failed")
        return "⚠️ Could not fetch ROI listings (agent error)."

    if not opps:
        return (
            "😴 No opportunities meeting ROI / profit thresholds right now.\n"
            f"(MIN_PROFIT_GBP=£{roi_listings.MIN_PROFIT_GBP:.0f}, "
            f"MIN_ROI={roi_listings.MIN_ROI*100:.0f}%.)"
        )

    top = opps[:limit]

    lines: list[str] = []
    lines.append(
        f"📊 Top {len(top)} ROI opportunities "
        f"(≥ £{roi_listings.MIN_PROFIT_GBP:.0f}, "
        f"≥ {roi_listings.MIN_ROI*100:.0f}% ROI):\n"
    )

    for idx, op in enumerate(top, start=1):
        roi_pct = op.roi * 100.0
        time_left_str = _format_time_left_s(op.time_left_s)

        lines.append(f"{idx}) {op.title}")
        lines.append(
            f"   ROI: {roi_pct:.1f}% | Profit: £{op.profit:.2f} | "
            f"Buy £{op.purchase_cost:.2f} → Sell £{op.comps_median:.2f}"
        )
        lines.append(f"   Ends in: {time_left_str}")
        lines.append(f"   {op.url}")
        lines.append("")  # blank line between items

    return "\n".join(lines).strip()
