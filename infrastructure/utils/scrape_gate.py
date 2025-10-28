# infrastructure/utils/scrape_gate.py
from __future__ import annotations
import os, random
from dataclasses import dataclass
from typing import Optional, Tuple
from datetime import datetime, timedelta

from infrastructure.db.schema import (
    resolve_source_id,
    resolve_source_field,
    connection,
)
# ✅ use the central time helpers
from infrastructure.utils.timez import now_utc, to_aware_utc

@dataclass
class SourceMeta:
    id: Optional[int]
    name: str
    interval_s: int                       # base interval (no jitter)
    last_scraped_at: Optional[datetime]   # aware UTC or None
    effective_interval_s: Optional[float] = None

    @property
    def next_due_at(self) -> Optional[datetime]:
        if self.last_scraped_at is None:
            return None
        last = to_aware_utc(self.last_scraped_at)
        return last + timedelta(seconds=self.interval_s) if last else None

def _env_name(source_name: str, suffix: str) -> str:
    return f"{source_name.upper().replace('-', '_')}_{suffix}"

def _get_env_int(source_name: str, suffix: str) -> Optional[int]:
    v = os.getenv(_env_name(source_name, suffix), "").strip()
    try:
        return int(v) if v else None
    except Exception:
        return None

def _get_env_float(source_name: str, suffix: str) -> Optional[float]:
    v = os.getenv(_env_name(source_name, suffix), "").strip()
    try:
        return float(v) if v else None
    except Exception:
        return None

def _get_source_meta(source_key: str, default_interval_s: int = 6 * 60 * 60) -> SourceMeta:
    name = resolve_source_field(source_key, "name") or source_key
    sid = resolve_source_id(source_key)

    interval_s = default_interval_s
    last: Optional[datetime] = None

    env_interval = _get_env_int(name, "INTERVAL_S")
    if env_interval is not None:
        interval_s = env_interval

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
            last = to_aware_utc(row[1])

    return SourceMeta(id=sid, name=name, interval_s=interval_s, last_scraped_at=last)

def mark_scraped(meta: SourceMeta, when: Optional[datetime] = None) -> None:
    if meta.id is None:
        return
    ts = to_aware_utc(when) or now_utc()
    with connection, connection.cursor() as cur:
        cur.execute("UPDATE sources SET last_scraped_at = %s WHERE id = %s", (ts, meta.id))

DEFAULT_JITTER_PCT = 0.15

def gate_scrape(
    source_key: str,
    prefer_interval_s: Optional[int] = None,
    pre_mark: bool = True,
    jitter_pct: Optional[float] = None,
    skip_probability: float = 0.0,
) -> Tuple[bool, SourceMeta]:
    meta = _get_source_meta(source_key)
    base_interval = meta.interval_s

    if prefer_interval_s is not None:
        base_interval = max(base_interval, int(prefer_interval_s))

    j = jitter_pct if jitter_pct is not None else DEFAULT_JITTER_PCT
    j = max(0.0, min(j, 0.90))

    effective_interval = float(base_interval) if j == 0.0 else random.uniform(
        base_interval * (1.0 - j),
        base_interval * (1.0 + j),
    )

    meta = SourceMeta(
        id=meta.id,
        name=meta.name,
        interval_s=base_interval,
        last_scraped_at=to_aware_utc(meta.last_scraped_at),
        effective_interval_s=effective_interval,
    )

    now = now_utc()
    if meta.last_scraped_at is not None:
        elapsed = (now - meta.last_scraped_at).total_seconds()
        if elapsed < effective_interval:
            return False, meta

    if skip_probability > 0 and random.random() < skip_probability:
        return False, meta

    if pre_mark:
        mark_scraped(meta, when=now)

    return True, meta
