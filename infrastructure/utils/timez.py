# infrastructure/utils/timez.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, Union

UTC = timezone.utc

def now_utc() -> datetime:
    """Return an aware UTC 'now'."""
    return datetime.now(UTC)

def to_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normalize any datetime to aware UTC (idempotent).
    - None -> None
    - Naive -> assume UTC, attach tzinfo
    - Aware (non-UTC) -> convert to UTC
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)

def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Safe ISO8601 string in UTC (or None)."""
    d = to_aware_utc(dt)
    return d.isoformat() if d else None

Number = Union[int, float]

def from_unix(ts: Number) -> datetime:
    """Unix seconds -> aware UTC datetime."""
    return datetime.fromtimestamp(float(ts), UTC)

def to_unix(dt: Optional[datetime]) -> Optional[float]:
    """Datetime -> Unix seconds (UTC)."""
    d = to_aware_utc(dt)
    return d.timestamp() if d else None

__all__ = ["UTC", "now_utc", "to_aware_utc", "iso_utc", "from_unix", "to_unix"]
