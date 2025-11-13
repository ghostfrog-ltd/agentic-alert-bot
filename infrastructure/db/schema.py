from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from infrastructure.utils import db_connection
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

# Re-export the lazy proxy so existing imports keep working:
connection = db_connection.connection


def to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get_conn():
    # Use the auto-healing shared connection under the proxy
    return db_connection.get_connection()


def resolve_source_field(key: str, field: str, use_domain: bool = False):
    col = "domain" if use_domain else "name"
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(f"SELECT {field} FROM sources WHERE {col} = %s LIMIT 1", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def resolve_source_id(key: str, use_domain: bool = False):
    col = "domain" if use_domain else "name"
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(f"SELECT id FROM sources WHERE {col} = %s LIMIT 1", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def resolve_source_niche(domain: str) -> Optional[str]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT niche FROM sources WHERE base_url LIKE %s OR name = %s LIMIT 1",
            (f"%{domain}%", domain),
        )
        row = cur.fetchone()
        return row[0] if row else None


def ensure_utc_session(cur):
    try:
        cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        # not fatal
        pass


def get_connection():
    """
    Public helper: shared, auto-healing connection.
    """
    return db_connection.get_connection()


def get_fresh_connection():
    """
    Public helper: brand new, short-lived connection.
    """
    logger.debug("[DB] Opening fresh short-lived connection from schema")
    return db_connection.create_connection()
