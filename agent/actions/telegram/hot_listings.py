# agent/actions/telegram/hot_listings.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from infrastructure.utils.logger import get_logger
from agent.actions.alert import hot_listings as hot  # reuse config + helpers

logger = get_logger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _format_time_left(time_left_s: int | None, end_time) -> str:
    """
    Prefer time_left_s if present; fall back to end_time.
    """
    if time_left_s is not None:
        if time_left_s <= 0:
            return "expired"
        mins = time_left_s // 60
        if mins < 60:
            return f"{mins} mins"
        hours = mins // 60
        rem = mins % 60
        return f"{hours}h {rem}m" if rem else f"{hours}h"

    if not end_time:
        return "unknown"

    try:
        if end_time.tzinfo is None:
            end = end_time.replace(tzinfo=timezone.utc)
        else:
            end = end_time.astimezone(timezone.utc)

        delta = end - _now_utc()
        total_minutes = int(delta.total_seconds() // 60)
        if total_minutes <= 0:
            return "expired"
        if total_minutes < 60:
            return f"{total_minutes} mins"
        hours = total_minutes // 60
        rem = total_minutes % 60
        return f"{hours}h {rem}m" if rem else f"{hours}h"
    except Exception:
        return "unknown"


def _format_row_for_telegram(r: dict, idx: int = 1) -> list[str]:
    """
    Format a single hot alert row into Telegram-friendly lines.
    """
    title = r.get("title") or "(no title)"
    url = r.get("url") or ""
    model_key = r.get("model_key") or "unknown"
    score = float(r.get("score") or 0.0)
    max_bid = float(r.get("max_bid") or 0.0)
    current_price = float(r.get("price_current") or 0.0)
    bids = int(r.get("bids_count") or 0)
    time_left_s = r.get("time_left_s")
    end_time = r.get("end_time")

    time_left_str = _format_time_left(time_left_s, end_time)

    lines: list[str] = []
    lines.append(f"{idx}) {title}")
    lines.append(f"   Model: {model_key}")
    lines.append(
        f"   Score: {score:.2f} | Current: £{current_price:.2f} | "
        f"Max bid: £{max_bid:.2f}"
    )
    lines.append(f"   Bids: {bids} | Ends in: {time_left_str}")
    if url:
        lines.append(f"   {url}")
    lines.append("")  # blank line between items
    return lines


def build_hot_listings_message(
    limit: int = 3,
    row: dict | None = None,
) -> str:
    """
    Build a Telegram-friendly summary of hot listings.

    Two modes:

      - Firehose / single-row mode:
          build_hot_listings_message(row=<alert_row_dict>)
        → formats a single listing into a short message. The row is
          passed in from agent.actions.alert.hot_listings.run() when a
          new alert is created.

      - Command mode (/hot):
          build_hot_listings_message(limit=3)
        → asks the alert.hot_listings module for the top-N alerts
          (already scored & stored in alerts table) and formats them.
    """
    # Firehose / single-row mode
    if row is not None:
        lines: list[str] = []
        lines.append(
            f"🔥 New hot listing detected "
            f"(score ≥ {hot.THRESHOLD_ALERT:.2f}):\n"
        )
        lines.extend(_format_row_for_telegram(row, idx=1))
        return "\n".join(lines).strip()

    # Command mode: top-N from alerts via alert hot_listings helper
    try:
        rows: List[dict] = hot.get_top_hot_alert_rows(limit)
    except Exception:
        logger.exception("[Telegram /hot] failed to fetch hot alerts")
        return "⚠️ Could not fetch hot listings (DB error)."

    if not rows:
        return (
            "😴 No hot listings at the moment.\n"
            f"(score ≥ {hot.THRESHOLD_ALERT:.2f} and {limit} max results.)"
        )

    top = rows[:limit]
    lines: list[str] = []

    lines.append(
        f"🔥 Top {len(top)} hot listings "
        f"(score ≥ {hot.THRESHOLD_ALERT:.2f}):\n"
    )

    for idx, r in enumerate(top, start=1):
        lines.extend(_format_row_for_telegram(r, idx=idx))

    return "\n".join(lines).strip()
