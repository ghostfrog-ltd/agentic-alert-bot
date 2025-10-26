# infrastructure/utils/timez.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

def now_utc() -> datetime:
    """Aware UTC 'now'."""
    return datetime.now(timezone.utc)

def to_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
