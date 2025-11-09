#!/usr/bin/env python3
from __future__ import annotations

from infrastructure.db.schema import get_connection
from infrastructure.utils.logger import get_logger

from agent.actions.maintenance.rebuild_model_keys import rebuild_model_keys as model_keys
from agent.actions.process.comps import run as comps
from agent.actions.alert.roi_listings import run as roi

logger = get_logger(__name__)


def reset_db_and_get_count() -> int:
    """
    Run the SQL reset sequence and return the number of rows
    that were in auction_listings before the reset.
    """
    conn = get_connection()

    with conn:
        with conn.cursor() as cur:
            # 1) Just to see how many rows you have before nuking model_key
            logger.info("[reset] Counting auction_listings rows before reset...")
            cur.execute("SELECT COUNT(*) AS auction_listings_before FROM auction_listings;")
            (auction_listings_before,) = cur.fetchone()
            logger.info(
                "[reset] auction_listings_before = %s",
                auction_listings_before,
            )

            # 2) Reset model_key on all listings
            logger.info("[reset] Nulling auction_listings.model_key ...")
            cur.execute("UPDATE auction_listings SET model_key = NULL;")

            # 3) Clear downstream tables
            logger.info("[reset] Truncating comps, alerts, alert_state ...")
            cur.execute("TRUNCATE TABLE comps;")
            cur.execute("TRUNCATE TABLE alerts;")
            cur.execute("TRUNCATE TABLE alert_state;")

            # 4) Recreate latest_comps materialized view
            logger.info("[reset] Dropping latest_comps materialized view (if exists) ...")
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS latest_comps;")

            logger.info("[reset] Creating latest_comps materialized view ...")
            cur.execute(
                """
                CREATE MATERIALIZED VIEW latest_comps AS
                SELECT DISTINCT ON (model_key)
                       model_key,
                       median_final_price,
                       mean_final_price,
                       samples,
                       computed_at
                  FROM comps
                 ORDER BY model_key, computed_at DESC;
                """
            )

            # 5) Initial populate (will be empty until comps is repopulated)
            logger.info("[reset] Refreshing latest_comps (will be empty for now) ...")
            cur.execute("REFRESH MATERIALIZED VIEW latest_comps;")

            # conn.__exit__ will COMMIT here (because no exception)
            logger.info("[reset] DB reset sequence completed.")

            return auction_listings_before


def main() -> None:
    # Run the reset and get the count from auction_listings
    count = reset_db_and_get_count()
    logger.info("[reset] Calling rebuild_model_keys(%s) ...", count)

    # Rebuild model_keys with the count
    model_keys(count)

    # Recompute comps
    logger.info("[reset] Running comps() ...")
    comps()

    # Run ROI alerts
    logger.info("[reset] Running roi() ...")
    roi()

    logger.info("[reset] All done.")


if __name__ == "__main__":
    main()
