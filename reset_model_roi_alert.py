#!/usr/bin/env python3
from __future__ import annotations

from datetime import timedelta

from infrastructure.db.schema import get_connection
from infrastructure.utils.logger import get_logger

from agent.actions.maintenance.rebuild_model_keys import rebuild_model_keys as model_keys
from agent.actions.alert.roi_listings import run as roi

logger = get_logger(__name__)

# how many days of sold/ended history to use for comps
COMPS_WINDOW_DAYS: int = 365  # be generous so we definitely have data


def reset_db_and_get_count() -> int:
    """
    Hard reset of model_key + comps + alert/ROI tables.

    - Count auction_listings
    - Null all model_key values
    - Drop & recreate comps (no legacy rows or indexes)
    - Truncate alerts / alert_state
    - Truncate ROI tables if present
    - Drop & recreate latest_comps matview
    """
    conn = get_connection()

    with conn:
        with conn.cursor() as cur:
            # 1) Count current listings
            logger.info("[reset] Counting auction_listings rows before reset...")
            cur.execute("SELECT COUNT(*) AS auction_listings_before FROM auction_listings;")
            (auction_listings_before,) = cur.fetchone()
            logger.info("[reset] auction_listings_before = %s", auction_listings_before)

            # 2) Reset model_key on all listings
            logger.info("[reset] Nulling auction_listings.model_key ...")
            cur.execute("UPDATE auction_listings SET model_key = NULL;")

            # 3) Drop & recreate comps so absolutely nothing survives
            logger.info("[reset] Dropping comps table ...")
            cur.execute("DROP TABLE IF EXISTS comps CASCADE;")

            logger.info("[reset] Recreating comps table ...")
            cur.execute(
                """
                CREATE TABLE comps (
                    model_key          text PRIMARY KEY,
                    median_final_price numeric,
                    mean_final_price   numeric,
                    samples            integer,
                    computed_at        timestamp with time zone
                );
                """
            )

            # 4) Clear downstream tables that depend on comps/model_key
            logger.info("[reset] Truncating alerts and alert_state ...")
            cur.execute("TRUNCATE TABLE alerts;")
            cur.execute("TRUNCATE TABLE alert_state;")

            # Truncate ROI-related tables if they exist
            logger.info("[reset] Truncating ROI tables (if they exist) ...")
            cur.execute(
                """
                DO $$
                BEGIN
                    IF to_regclass('public.roi_snapshots') IS NOT NULL THEN
                        EXECUTE 'TRUNCATE TABLE roi_snapshots;';
                    END IF;
                    IF to_regclass('public.roi_alert_markers') IS NOT NULL THEN
                        EXECUTE 'TRUNCATE TABLE roi_alert_markers;';
                    END IF;
                END$$;
                """
            )

            # 5) Drop & recreate latest_comps materialized view
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

            logger.info("[reset] Refreshing latest_comps (empty at this stage) ...")
            cur.execute("REFRESH MATERIALIZED VIEW latest_comps;")

            logger.info("[reset] DB reset sequence completed.")
            return auction_listings_before


def recompute_comps_from_auction_listings(window_days: int = COMPS_WINDOW_DAYS) -> None:
    """
    Rebuild comps directly from auction_listings, ignoring the old process.comps code.

    - Uses status IN ('sold','ended')
    - Uses COALESCE(final_price, price_current) as realized sale
    - Requires model_key NOT NULL and != 'unknown'
    - Restricts to last `window_days` of history
    """
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            logger.info(
                "[reset] Recomputing comps from auction_listings for last %s days "
                "(statuses IN ('sold','ended'), model_key NOT NULL/unknown)",
                window_days,
            )

            # Clean slate
            cur.execute("TRUNCATE TABLE comps;")

            # Insert fresh comps
            cur.execute(
                """
                INSERT INTO comps (model_key, median_final_price, mean_final_price, samples, computed_at)
                SELECT
                    model_key,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY COALESCE(final_price, price_current)
                    )::numeric AS median_final_price,
                    AVG(COALESCE(final_price, price_current))::numeric AS mean_final_price,
                    COUNT(*)::int AS samples,
                    (now() AT TIME ZONE 'utc') AS computed_at
                FROM auction_listings
                WHERE status IN ('sold', 'ended')
                  AND COALESCE(final_price, price_current) IS NOT NULL
                  AND end_time >= (now() AT TIME ZONE 'utc' - (%s || ' days')::interval)
                  AND model_key IS NOT NULL
                  AND LOWER(model_key) <> 'unknown'
                GROUP BY model_key;
                """,
                (str(window_days),),
            )

            # Debug: how many comps did we just create?
            cur.execute("SELECT COUNT(*) FROM comps;")
            (comps_count,) = cur.fetchone()
            logger.info("[reset] comps rows after recompute = %s", comps_count)

            cur.execute(
                """
                SELECT model_key, samples, median_final_price
                FROM comps
                ORDER BY samples DESC NULLS LAST
                LIMIT 10;
                """
            )
            rows = cur.fetchall()
            if rows:
                for model_key, samples, median in rows:
                    logger.info(
                        "[reset] sample comp: model_key=%s, samples=%s, median=%s",
                        model_key,
                        samples,
                        median,
                    )
            else:
                logger.info("[reset] no sample comps to show (comps table empty)")

            # Refresh matview now that comps is populated
            logger.info("[reset] Refreshing latest_comps after recompute ...")
            cur.execute("REFRESH MATERIALIZED VIEW latest_comps;")


def main() -> None:
    count = reset_db_and_get_count()
    logger.info("[reset] Calling rebuild_model_keys(%s) ...", count)

    # Rebuild model_key for all listings (should include sold/ended too)
    model_keys(count)

    # Directly recompute comps from auction_listings
    recompute_comps_from_auction_listings(COMPS_WINDOW_DAYS)

    logger.info("[reset] Running roi() ...")
    roi()

    logger.info("[reset] All done.")


if __name__ == "__main__":
    main()
