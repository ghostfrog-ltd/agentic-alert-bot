from datetime import datetime, timedelta, timezone
import traceback
from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import connection

# Adapters
from infrastructure.scraper.adapters.niche.ebay.motomine import Adapter as MotoMineAdapter
from infrastructure.scraper.adapters.niche.ebay.consoles import Adapter as ConsolesAdapter
from infrastructure.scraper.adapters.niche.ebay.retro_pc import Adapter as RetroPcAdapter
from infrastructure.scraper.adapters.niche.ebay.actioncams import Adapter as ActionCamAdapter
from infrastructure.scraper.adapters.niche.ebay.watches import Adapter as WatchAdapter
from infrastructure.scraper.adapters.niche.ebay.apple import Adapter as AppleAdapter
from infrastructure.scraper.adapters.niche.ebay.tools import Adapter as ToolAdapter

from infrastructure.scraper.adapters.niche.ebay.motors import Adapter as MotorsAdapter
from infrastructure.scraper.adapters.niche.ebay.lego import Adapter as LegoAdapter
from infrastructure.scraper.adapters.niche.ebay.pokemon import Adapter as PokemonAdapter
from infrastructure.scraper.adapters.niche.ebay.samsung import Adapter as SamsungAdapter
from infrastructure.scraper.adapters.niche.ebay.headphones import Adapter as HeadphonesAdapter

from infrastructure.scraper.adapters.niche.ebay.hondaNc750 import Adapter as nc750Adapter

logger = get_logger(__name__)

# ----------------------------------------------------------------------
# DB-aware adapter runner
# ----------------------------------------------------------------------
def _run_adapter(adapter, ebay_token: str) -> None:
    domain = getattr(adapter, "DOMAIN", "unknown-domain")
    now = datetime.now(timezone.utc)  # ✅ timezone-aware

    # read last_scraped_at + scrape_interval_seconds from DB
    with connection.cursor() as cur:
        cur.execute("""
            SELECT scrape_interval_seconds, last_scraped_at
            FROM sources
            WHERE domain = %s AND enabled = TRUE
            LIMIT 1
        """, (domain,))
        row = cur.fetchone()
        if not row:
            logger.warning(f"[scrape:{domain}] skipped (no source record or disabled)")
            return

        interval, last_run = row
        interval = int(interval or 0)

    # normalize last_run to aware UTC if not already
    if last_run and last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)

    # gate based on interval
    if last_run and interval > 0:
        elapsed = (now - last_run).total_seconds()
        if elapsed < interval:
            remaining = interval - elapsed
            next_time = last_run + timedelta(seconds=interval)
            logger.info(
                f"[scrape:{domain}] gated → next allowed run at "
                f"{next_time.strftime('%H:%M:%S')} "
                f"(in {int(remaining//60)}m {int(remaining%60)}s; interval={interval}s)"
            )
            return

    # run the adapter
    try:
        logger.info(f"[scrape:{domain}] begin API fetch")
        adapter.fetch_listings_api(ebay_token)
        logger.info(f"[scrape:{domain}] API fetch complete")

        # update last_scraped_at on success
        with connection.cursor() as cur:
            cur.execute("""
                UPDATE sources
                SET last_scraped_at = (now() AT TIME ZONE 'utc')
                WHERE domain = %s
            """, (domain,))
        connection.commit()

    except Exception as e:
        logger.warning(f"[scrape:{domain}] API fetch failed: {e}\n{traceback.format_exc()}")
        connection.rollback()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def run(*, ebay_token: str):
    logger.info("[scrape] Begin scrape (API mode)")

    adapters = [
        MotoMineAdapter(),

        AppleAdapter(),
        ConsolesAdapter(),
        RetroPcAdapter(),
        ActionCamAdapter(),
        WatchAdapter(),
        ToolAdapter(),

        MotorsAdapter(),
        LegoAdapter(),
        PokemonAdapter(),
        SamsungAdapter(),
        HeadphonesAdapter(),

        nc750Adapter(),
    ]

    for adapter in adapters:
        _run_adapter(adapter, ebay_token)

    logger.info("[scrape] End scrape (API mode)")
