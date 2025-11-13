# infrastructure/utils/usage_tracker.py
from __future__ import annotations

from infrastructure.utils.db_connection import create_connection
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)


def increment_api_usage(service: str, count: int = 1) -> int:
    """
    Atomically bump today's usage counter for a service (e.g. 'ebay').

    Returns the new total for today.
    """
    conn = create_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO api_usage (service, call_count, date)
                VALUES (%s, %s, CURRENT_DATE)
                ON CONFLICT (service, date)
                DO UPDATE SET
                    call_count = api_usage.call_count + EXCLUDED.call_count,
                    updated_at = (now() AT TIME ZONE 'utc')
                RETURNING call_count;
                """,
                (service, count),
            )
            row = cur.fetchone()
            new_total = int(row[0]) if row else 0

        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    logger.info("[Usage] %s calls today = %s", service, new_total)
    return new_total


def get_api_usage_today(service: str) -> int:
    """
    Return today's API usage count for the given service.
    If no entry exists yet, returns 0.
    """
    conn = create_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT call_count
                FROM api_usage
                WHERE service = %s
                  AND date = CURRENT_DATE
                """,
                (service,),
            )
            row = cur.fetchone()
            today = int(row[0]) if row else 0

        conn.commit()  # harmless for SELECT
        return today
    finally:
        try:
            conn.close()
        except Exception:
            pass
