from __future__ import annotations

from psycopg2.extras import RealDictCursor
from infrastructure.db.schema import get_connection
from infrastructure.utils.model_key import normalise_model
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

# Set how many rows you want to process in one run
LIMIT_ROWS = 9000      # adjust manually each time
UNKNOWN_KEY = "unknown"

connection = get_connection()

def rebuild_model_keys(limit: int = LIMIT_ROWS) -> None:
    updated_total = 0

    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, title
              FROM auction_listings
             WHERE model_key IS NULL
             ORDER BY id
             LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

        for row in rows:
            title = row["title"] or ""
            key = normalise_model(title) or UNKNOWN_KEY

            cur.execute(
                "UPDATE auction_listings SET model_key = %s WHERE id = %s",
                (key, row["id"]),
            )
            updated_total += 1

        connection.commit()

    logger.info(
        "[rebuild_model_keys] batch complete — updated %d rows (limit=%d)",
        updated_total,
        limit,
    )


if __name__ == "__main__":
    rebuild_model_keys()
