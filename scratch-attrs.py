from __future__ import annotations

"""
inspect_console_attrs.py

Hard-coded to source = 'ebay-consoles'.

Loops through all auction_listings rows for that source, reads the JSONB
raw_attrs column, and prints a list of:

    ATTRIBUTE_NAME (N unique)
        - value 1
        - value 2
        - ...

So you can see every attribute key and all the distinct values it takes.
"""

import json
from collections import defaultdict
from typing import Any, Dict, Set

from infrastructure.db.schema import get_connection
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

#SOURCE = "ebay-consoles"
#SOURCE = "ebay-apple"
#SOURCE = "ebay-actioncams"
#SOURCE = "ebay-retro-pc"
#SOURCE = "ebay-watches"
#SOURCE = "ebay-tools"
SOURCE = "motomine"


def _to_str(v: Any) -> str | None:
    """Normalise raw JSON values into a readable string."""
    if v is None:
        return None

    # Flatten nested dicts/lists into JSON for visibility
    if isinstance(v, (dict, list, tuple, set)):
        try:
            s = json.dumps(v, ensure_ascii=False, sort_keys=True)
        except TypeError:
            s = str(v)
    else:
        s = str(v)

    s = s.strip()
    return s or None


def main() -> None:
    conn = get_connection()
    attr_values: Dict[str, Set[str]] = defaultdict(set)

    with conn, conn.cursor() as cur:
        logger.info("Loading raw_attrs for source=%s ...", SOURCE)
        cur.execute(
            """
            SELECT raw_attrs
            FROM auction_listings
            WHERE source = %s
              AND raw_attrs IS NOT NULL
            """,
            (SOURCE,),
        )

        rows = cur.fetchall()
        logger.info("Fetched %d rows", len(rows))

        for (raw_attrs,) in rows:
            if not raw_attrs:
                continue

            # raw_attrs might be returned as a dict (JSONB) or as text
            if isinstance(raw_attrs, str):
                try:
                    data = json.loads(raw_attrs)
                except json.JSONDecodeError:
                    logger.warning("Could not decode raw_attrs string: %r", raw_attrs[:200])
                    continue
            else:
                data = raw_attrs

            if not isinstance(data, dict):
                # If it's not an object, nothing useful to aggregate
                continue

            for key, value in data.items():
                # Handle list vs scalar
                if isinstance(value, (list, tuple, set)):
                    for item in value:
                        s = _to_str(item)
                        if s:
                            attr_values[key].add(s)
                else:
                    s = _to_str(value)
                    if s:
                        attr_values[key].add(s)

    # Print out all attributes and their distinct values
    print(f"\n=== Attribute values for source='{SOURCE}' ===\n")

    for attr_name in sorted(attr_values.keys()):
        values = sorted(attr_values[attr_name])
        print("=" * 80)
        print(f"{attr_name} ({len(values)} unique)")
        for val in values:
            print(f" - {val}")
        print()

    logger.info("Done – printed %d attributes", len(attr_values))


if __name__ == "__main__":
    main()
