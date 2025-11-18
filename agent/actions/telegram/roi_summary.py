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


def build_roi_message(
    op: roi_listings.Opportunity | None = None,
    limit: int = 3,
) -> str:
    """
    If `op` is supplied → return a single-item Telegram ROI alert message.
    If `op` is None → summary mode for the /roi command:

        - call roi_listings.get_top_roi_opportunities(limit)
        - return the top N opportunities formatted

    This keeps backward compatibility with the ROI pipeline (which calls this
    with `op=` for firehose messages) and makes the /roi command read-only.
    """
    # -------------------------------
    # SINGLE OP MODE (pipeline firehose)
    # -------------------------------
    if op is not None:
        roi_pct = op.roi * 100.0
        time_left_str = _format_time_left_s(op.time_left_s)

        lines = [
            "🐸 ROI alert",
            f"{op.title}",
            "",
            f"ROI: {roi_pct:.1f}% | Profit: £{op.profit:.2f}",
            f"Buy £{op.purchase_cost:.2f} → Sell £{op.comps_median:.2f}",
            f"Ends in: {time_left_str}",
            f"{op.url}",
        ]

        return "\n".join(lines).strip()

    # -------------------------------
    # SUMMARY MODE (/roi command)
    # -------------------------------
    try:
        opps: List[roi_listings.Opportunity] = roi_listings.get_top_roi_opportunities(
            limit=limit
        )
    except Exception:
        logger.exception("[Telegram /roi] get_top_roi_opportunities() failed")
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
        lines.append("")

    return "\n".join(lines).strip()
