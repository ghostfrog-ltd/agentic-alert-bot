# agent/actions/reconcile_auctions.py
from datetime import datetime, timezone
from infrastructure.db import connection
from infrastructure.db.schema import finalize_auction
from infrastructure.utils.logger import get_logger
from .close_auctions import _fetch_detail_snapshot

logger = get_logger(__name__)

def _get_tentative_batch(limit: int = 100) -> list[dict]:
    sql = """
    SELECT id, detail_url
      FROM auction_listings
     WHERE status = 'ENDED_TENTATIVE'
       AND detail_url IS NOT NULL
     LIMIT %s
    """
    with connection.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in rows]

def run(limit: int = 100):
    rows = _get_tentative_batch(limit)
    for r in rows:
        try:
            ended, final_price, sale_type = _fetch_detail_snapshot(r['detail_url'])
            if ended and final_price is not None:
                finalize_auction(r['id'], final_price, 'HIGH', 'ENDED_CONFIRMED', sale_type)
                logger.info(f"[reconcile] Auction {r['id']} upgraded to ENDED_CONFIRMED @ {final_price}")
        except Exception as e:
            logger.warning(f"[reconcile] Auction {r['id']} reconcile failed: {e}")
