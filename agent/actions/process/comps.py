# agent/actions/process/comps.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from infrastructure.db.schema import ensure_utc_session
from infrastructure.utils import db_connection
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)
connection = db_connection.connection

# ---------------------------------
# Tunable knobs
# ---------------------------------
COMPS_WINDOW_DAYS: int = 30  # how many days of history to aggregate
COMPS_MIN_INTERVAL_HOURS: int = 24  # minimum time between full recomputes
COMPS_KEEP_PER_KEY: int = 60  # how many snapshots per model_key to retain


# ---------------------------------
# Local DB helpers
# ---------------------------------
def _ensure_utc_session(cur) -> None:
    try:
        cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        # Not fatal; best-effort
        pass


def _get_last_run() -> Optional[datetime]:
    """
    Look at comps and ask: when did we last compute anything?
    We use MAX(computed_at) as the "last run time".
    """
    sql = "SELECT MAX(computed_at) FROM comps"
    with connection.cursor() as cur:
        _ensure_utc_session(cur)
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return None
    return row[0]


def _compute_daily_comps(days: int = COMPS_WINDOW_DAYS) -> None:
    """
    Insert new per-model_key stats for the last N days of sold/confirmed listings.
    Mirrors the old compute_daily_comps() in schema.py.
    """
    logger.info("[process.comps] computing daily comps for last %s days", days)
    sql = """
        INSERT INTO comps (model_key, median_final_price, mean_final_price, samples, computed_at)
        SELECT model_key,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY final_price)::numeric AS median_final_price,
               AVG(final_price)::numeric AS mean_final_price,
               COUNT(*)::int AS samples,
               (now() AT TIME ZONE 'utc') AS computed_at
        FROM auction_listings
        WHERE status IN ('sold')
          AND final_price IS NOT NULL
          AND model_key IS NOT NULL
          AND end_time >= (now() AT TIME ZONE 'utc' - (%s || ' days')::interval)
        GROUP BY model_key
    """
    with connection, connection.cursor() as cur:
        _ensure_utc_session(cur)
        cur.execute(sql, (str(days),))


def _prune_old_comps(keep_per_key: int = COMPS_KEEP_PER_KEY) -> None:
    """
    Keep only the newest N rows per model_key in comps.
    Stops comps table from growing forever.
    """
    logger.info("[process.comps] pruning old comps (keep_per_key=%s)", keep_per_key)
    sql = """
        WITH ranked AS (
          SELECT model_key, computed_at,
                 ROW_NUMBER() OVER (
                     PARTITION BY model_key
                     ORDER BY computed_at DESC
                 ) AS rn
          FROM comps
        )
        DELETE FROM comps c
        USING ranked r
        WHERE c.model_key = r.model_key
          AND c.computed_at = r.computed_at
          AND r.rn > %s
    """
    with connection, connection.cursor() as cur:
        _ensure_utc_session(cur)
        cur.execute(sql, (keep_per_key,))


def refresh_latest_comps_matview():
    with connection, connection.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("REFRESH MATERIALIZED VIEW latest_comps")


# ---------------------------------
# Public entrypoint
# ---------------------------------
def run(force: bool = False) -> None:
    """
    Action entrypoint.

    - If force=False, only runs if last run was >= COMPS_MIN_INTERVAL_HOURS ago
      (or comps is empty).
    - If force=True, always recomputes regardless of last_run timestamp.
    """
    try:
        now_utc = datetime.now(timezone.utc)

        last_run = _get_last_run()
        if last_run:
            age = now_utc - last_run
        else:
            age = timedelta.max  # "never run" => effectively infinite age

        should_run = force or (age >= timedelta(hours=COMPS_MIN_INTERVAL_HOURS))

        if not should_run:
            logger.debug(
                "[process.comps] skip: last_run=%s age=%s (min_interval=%sh)",
                last_run,
                age,
                COMPS_MIN_INTERVAL_HOURS,
            )
            return

        logger.info(
            "[process.comps] starting recompute (force=%s, last_run=%s, age=%s)",
            force,
            last_run,
            age,
        )

        # 1) compute fresh snapshot
        _compute_daily_comps(COMPS_WINDOW_DAYS)

        # 2) housekeeping (best-effort)
        try:
            _prune_old_comps(COMPS_KEEP_PER_KEY)
        except Exception as e:
            logger.warning("[process.comps] prune_old_comps failed: %s", e)

        try:
            refresh_latest_comps_matview()
        except Exception as e:
            logger.warning("[process.comps] refresh_latest_comps_matview failed: %s", e)

        logger.info("[process.comps] done")

    except Exception as e:
        logger.exception("[process.comps] failed: %s", e)


if __name__ == "__main__":
    run(force=True)
