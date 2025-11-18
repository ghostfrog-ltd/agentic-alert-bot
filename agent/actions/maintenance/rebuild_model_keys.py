from __future__ import annotations

import json  # needed for raw_attrs parsing
from psycopg2.extras import RealDictCursor
from infrastructure.db.schema import get_connection
from infrastructure.utils.model_key import normalise_model
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

# Set how many rows you want to process in one run
LIMIT_ROWS = 9000      # adjust manually each time
UNKNOWN_KEY = "unknown"

connection = get_connection()


def _parse_raw_attrs(raw):
    """
    Accept dict (JSON/JSONB) or JSON string; return dict or None.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _normalise_attrs(attrs: dict | None) -> dict | None:
    """
    Make attrs safe for key builders that expect strings:
      - If value is a list/tuple, use its first element.
      - Coerce non-None values to str.
    """
    if not attrs:
        return attrs
    out: dict = {}
    for k, v in attrs.items():
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        if v is None:
            out[k] = None
        else:
            out[k] = v if isinstance(v, str) else str(v)
    return out


def rebuild_model_keys(limit: int = LIMIT_ROWS) -> None:
    updated_total = 0

    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, title, raw_attrs, source
              FROM auction_listings
             WHERE model_key IS NULL
             ORDER BY id
             LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

        for row in rows:
            source = row["source"] or ""
            title = row["title"] or ""
            attrs_raw = _parse_raw_attrs(row.get("raw_attrs"))
            attrs = _normalise_attrs(attrs_raw)
            key = normalise_model(title=title, attrs=attrs, source=source) or UNKNOWN_KEY

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
