from __future__ import annotations

import traceback
from infrastructure.utils.logger import get_logger
from infrastructure.scraper.adapters.niche.ebay.consoles import Adapter as ConsolesAdapter

logger = get_logger(__name__)


def run_consoles_api(*, ebay_token: str):
    """
    Pull live listings for console categories using the eBay Browse API.
    This:
    - Calls eBay's API (not HTML scraping)
    - Normalises listings
    - Buffers them in memory
    - Bulk upserts them into Postgres
    """
    adapter = ConsolesAdapter()

    try:
        logger.info("[scrape-consoles] begin consoles API fetch")
        adapter.fetch_listings_api(ebay_token)
        logger.info("[scrape-consoles] consoles API fetch complete")
    except Exception as e:
        logger.warning(
            "[scrape-consoles] consoles API fetch failed: %s\n%s",
            e,
            traceback.format_exc()
        )


def run(*, ebay_token: str):
    """
    Heartbeat entry point.
    Right now this only runs the consoles API ingest.
    We are intentionally NOT running:
      - motomine HTML scraping
      - parse_auction per-URL loops
      - crypto news scrapers
    because production eBay approval needs us clean & API-driven.
    """
    logger.info("[scrape] Begin scrape (API mode)")
    run_consoles_api(ebay_token=ebay_token)
    logger.info("[scrape] End scrape (API mode)")
