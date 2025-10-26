from datetime import datetime, timezone, timedelta
from psycopg2.extras import RealDictCursor

from infrastructure.db.schema import connection, latest_comps_map, record_alert
from core.scoring.snipe import Listing, Comp, snipe_score, suggest_max_bid
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

THRESHOLD_ALERT = 0.70
WINDOW_HOURS = 4

def run():
    comps = latest_comps_map()
    if not comps:
        logger.info("[scan] no comps yet; skip")
        return

    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT external_id, model_key, price_current, bids_count, time_left_s, end_time, title, url
            FROM auction_listings
            WHERE status = 'live'
              AND end_time IS NOT NULL
              AND end_time <= NOW() + INTERVAL '%s hours'
        """, (WINDOW_HOURS,))
        rows = cur.fetchall()

    for r in rows:
        mk = r["model_key"]
        if not mk or mk not in comps:
            continue
        comp = Comp(median_final_price=float(comps[mk]["median_final_price"]), samples=int(comps[mk]["samples"]))
        lst = Listing(
            external_id=r["external_id"],
            price_current=float(r["price_current"] or 0.0),
            bids_count=r["bids_count"],
            time_left_s=r["time_left_s"],
            model_key=mk
        )
        score = snipe_score(lst, comp)
        if score >= THRESHOLD_ALERT:
            max_bid = suggest_max_bid(comp.median_final_price)
            record_alert(lst.external_id, score, max_bid)
            logger.info(
                "[ALERT] %s | score=%.2f | fair=%.2f | curr=%.2f | max_bid=%.2f | %s",
                mk, score, comp.median_final_price, lst.price_current, max_bid, r["url"]
            )
