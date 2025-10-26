# infrastructure/utils/scrape_gate.py
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from infrastructure.db.schema import (
    resolve_source_id,
    resolve_source_field,
    connection,   # assuming you expose this already
)

@dataclass
class SourceMeta:
    id: int | None
    name: str
    interval_s: int
    last_scraped_at: datetime | None

    @property
    def next_due_at(self) -> datetime | None:
        if self.last_scraped_at is None:
            return None
        return self.last_scraped_at + timedelta(seconds=self.interval_s)

def _get_source_meta(source_key: str, default_interval_s: int = 6*60*60) -> SourceMeta:
    """
    Resolve a source row (by name/domain/etc.) and return its throttle fields.
    Falls back to defaults if not found.
    """
    # prefer explicit name if present
    name = resolve_source_field(source_key, "name") or source_key
    sid = resolve_source_id(source_key)

    interval_s = default_interval_s
    last = None

    # Optional ENV override for quick tuning, e.g. MOTOMINE_INTERVAL_S=300
    env_key = f"{name.upper().replace('-', '_')}_INTERVAL_S"
    try:
        interval_s = int(os.getenv(env_key, "") or interval_s)
    except Exception:
        pass

    # If we have a row, fetch interval+timestamp from DB
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
            last = row[1]

    return SourceMeta(id=sid, name=name, interval_s=interval_s, last_scraped_at=last)

def mark_scraped(meta: SourceMeta, when: datetime | None = None) -> None:
    """Persist last_scraped_at for this source (no-op if we don't have an id)."""
    if meta.id is None:
        return
    ts = (when or datetime.utcnow().replace(tzinfo=timezone.utc)).replace(tzinfo=None)
    with connection.cursor() as cur:
        cur.execute("UPDATE sources SET last_scraped_at = %s WHERE id = %s", (ts, meta.id))
    connection.commit()

def gate_scrape(
    source_key: str,
    prefer_interval_s: int | None = None,
    pre_mark: bool = True,
) -> tuple[bool, SourceMeta]:
    """
    Decide whether we should run this source now.
    - Reads interval + last_scraped_at from DB (or defaults).
    - Optional prefer_interval_s lets an adapter suggest its own minimum (DB still wins).
    - If pre_mark=True and we are allowed, we immediately set last_scraped_at to avoid stampedes.
    Returns (allowed, meta).
    """
    meta = _get_source_meta(source_key)
    interval_s = meta.interval_s

    if prefer_interval_s is not None:
        # Use the *max* to respect longer DB intervals
        interval_s = max(interval_s, int(prefer_interval_s))
        meta = SourceMeta(id=meta.id, name=meta.name, interval_s=interval_s, last_scraped_at=meta.last_scraped_at)

    now = datetime.utcnow().replace(tzinfo=timezone.utc).replace(tzinfo=None)
    if meta.last_scraped_at is not None:
        elapsed = (now - meta.last_scraped_at).total_seconds()
        if elapsed < interval_s:
            return False, meta

    if pre_mark:
        mark_scraped(meta, when=now)

    return True, meta
