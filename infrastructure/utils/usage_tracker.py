# infrastructure/utils/usage_tracker.py
from __future__ import annotations
from infrastructure.db.schema import connection
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

def increment_api_usage(service: str, count: int = 1) -> int:
    """
    Atomically bump today's usage counter for a service (e.g. 'ebay').

    Returns the new total for today.
    """
    with connection, connection.cursor() as cur:
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

    logger.info("[Usage] %s calls today = %s", service, new_total)
    return new_total
