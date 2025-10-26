# infrastructure/utils/scrape_gate.py
from __future__ import annotations
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from infrastructure.db.schema import (
    resolve_source_id,
    resolve_source_field,
    connection,   # shared global conn
)

# -------------------------
# Time helpers
# -------------------------
def _to_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

@dataclass
class SourceMeta:
    id: Optional[int]
    name: str
    interval_s: int                 # base interval (no jitter)
    last_scraped_at: Optional[datetime]   # AWARE UTC or None
    effective_interval_s: Optional[float] = None  # jittered interval used for the decision

    @property
    def next_due_at(self) -> Optional[datetime]:
        """
        Uses the *base* interval (no jitter) so this is stable for logs/UI.
        """
        if self.last_scraped_at is None:
            return None
        last = _to_aware_utc(self.last_scraped_at)
        return last + timedelta(seconds=self.interval_s) if last else None

# -------------------------
# Internal helpers
# -------------------------

def _env_name(source_name: str, suffix: str) -> str:
    # ebay-consoles -> EBAY_CONSOLES_SUFFIX
    return f"{source_name.upper().replace('-', '_')}_{suffix}"

def _get_env_int(source_name: str, suffix: str) -> Optional[int]:
    k = _env_name(source_name, suffix)
    v = os.getenv(k, "").strip()
    if not v:
        return None
    try:
        return int(v)
    except Exception:
        return None

def _get_env_float(source_name: str, suffix: str) -> Optional[float]:
    k = _env_name(source_name, suffix)
    v = os.getenv(k, "").strip()
    if not v:
        return None
    try:
        return float(v)
    except Exception:
        return None

def _get_source_meta(source_key: str, default_interval_s: int = 6 * 60 * 60) -> SourceMeta:
    """
    Resolve a source row (by name/domain/etc.) and return its throttle fields.
    Falls back to defaults if not found.
    """
    name = resolve_source_field(source_key, "name") or source_key
    sid = resolve_source_id(source_key)

    interval_s = default_interval_s
    last: Optional[datetime] = None

    # Optional ENV override e.g. EBAY_CONSOLES_INTERVAL_S=300
    env_interval = _get_env_int(name, "INTERVAL_S")
    if env_interval is not None:
        interval_s = env_interval

    # Pull from DB if present
    if sid is not None:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT scrape_interval_seconds, last_scraped_at
                FROM sources
                WHERE id = %s
                LIMIT 1
            """, (sid,))
            row = cur.fetchone()
        if row:
            if row[0] is not None:
                interval_s = int(row[0])
            last = _to_aware_utc(row[1])  # normalize to aware UTC

    return SourceMeta(id=sid, name=name, interval_s=interval_s, last_scraped_at=last)

def mark_scraped(meta: SourceMeta, when: Optional[datetime] = None) -> None:
    """
    Persist last_scraped_at for this source (aware UTC). No-op if no id.
    """
    if meta.id is None:
        return
    ts = _to_aware_utc(when) or _now_utc()
    # use a transaction and write an aware timestamptz
    with connection, connection.cursor() as cur:
        cur.execute("UPDATE sources SET last_scraped_at = %s WHERE id = %s", (ts, meta.id))

# -------------------------
# Public gate with jitter
# -------------------------

# global default jitter — e.g. ±15%
DEFAULT_JITTER_PCT = 0.15

def gate_scrape(
    source_key: str,
    prefer_interval_s: Optional[int] = None,
    pre_mark: bool = True,
    jitter_pct: Optional[float] = None,  # optional override
    skip_probability: float = 0.0,
) -> Tuple[bool, SourceMeta]:
    """
    Decide whether we should run this source now.

    - Base interval comes from DB (sources.scrape_interval_seconds), else default 6h.
    - Jitter applied automatically using DEFAULT_JITTER_PCT if none supplied.
    - Optional skip_probability to occasionally skip a run to look more human.
    """
    meta = _get_source_meta(source_key)
    base_interval = meta.interval_s

    # Respect adapter preference only if it's longer than base
    if prefer_interval_s is not None:
        base_interval = max(base_interval, int(prefer_interval_s))

    # Use passed jitter or default global
    j = jitter_pct if jitter_pct is not None else DEFAULT_JITTER_PCT
    j = max(0.0, min(j, 0.90))  # clamp

    # Calculate effective jittered interval
    if j > 0.0:
        lo = base_interval * (1.0 - j)
        hi = base_interval * (1.0 + j)
        effective_interval = random.uniform(lo, hi)
    else:
        effective_interval = float(base_interval)

    # Rebuild meta with effective interval (for logging/inspection)
    meta = SourceMeta(
        id=meta.id,
        name=meta.name,
        interval_s=base_interval,
        last_scraped_at=_to_aware_utc(meta.last_scraped_at),
        effective_interval_s=effective_interval,
    )

    # Due check (all aware UTC)
    now = _now_utc()
    if meta.last_scraped_at is not None:
        elapsed = (now - meta.last_scraped_at).total_seconds()
        if elapsed < effective_interval:
            return False, meta

    # Optional skip for human-like randomness
    if skip_probability > 0 and random.random() < skip_probability:
        return False, meta

    if pre_mark:
        mark_scraped(meta, when=now)

    return True, meta
