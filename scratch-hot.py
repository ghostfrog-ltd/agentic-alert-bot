#!/usr/bin/env python3
from __future__ import annotations

from typing import List, Dict, Any

from infrastructure.utils.logger import get_logger
from agent.actions.alert import hot_listings as hot  # backend hot listings logic

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


def build_hot_listings_message(limit: int = 3) -> str:
    """
    Local copy of the /hot message builder for debugging.

    Uses the same data source as the Telegram /hot command:
    hot.get_top_hot_alert_rows(limit=limit)
    """
    rows: List[Dict[str, Any]] = hot.get_top_hot_alert_rows(limit=limit)

    print(rows)

    if not rows:
        return "No hot listings found."

    lines: list[str] = []
    for idx, row in enumerate(rows, start=1):
        title = row.get("title", "Unknown title")
        model_key = row.get("model_key", "unknown")
        score = row.get("score", 0.0)
        current_price = row.get("price_current", 0.0)
        max_bid = row.get("max_bid", 0.0)
        bids = row.get("bid_count", 0)
        url = row.get("url", "")
        time_left_s = row.get("time_left_s")

        time_str = _format_time_left_s(time_left_s)

        lines.append(
            f"{idx}) {title}\n"
            f"   Model: {model_key}\n"
            f"   Score: {score:.2f} | Current: £{current_price:.2f} | Max bid: £{max_bid:.2f}\n"
            f"   Bids: {bids} | Ends in: {time_str}\n"
            f"   {url}"
        )

    return "\n".join(lines)


def main() -> None:
    logger.info("Running scratch-hot.py …")

    # As requested:
    msg = build_hot_listings_message(limit=3)

    print("\n---- HOT LISTINGS MESSAGE ----\n")
    print(msg)
    print("\n------------------------------\n")


if __name__ == "__main__":
    main()
