from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from infrastructure.utils import db_connection
from infrastructure.utils.logger import get_logger

connection = db_connection.connection

logger = get_logger(__name__)


def to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def resolve_source_field(key: str, field: str, use_domain: bool = False):
    col = "domain" if use_domain else "name"
    with connection.cursor() as cur:
        cur.execute(f"SELECT {field} FROM sources WHERE {col} = %s LIMIT 1", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def resolve_source_id(key: str, use_domain: bool = False):
    col = "domain" if use_domain else "name"
    with connection.cursor() as cur:
        cur.execute(f"SELECT id FROM sources WHERE {col} = %s LIMIT 1", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def resolve_source_niche(domain: str) -> Optional[str]:
    with connection.cursor() as cur:
        cur.execute("SELECT niche FROM sources WHERE base_url LIKE %s OR name = %s LIMIT 1", (f'%{domain}%', domain))
        row = cur.fetchone()
        return row[0] if row else None


def ensure_utc_session(cur):
    try:
        cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        pass


def get_connection():
    return connection
