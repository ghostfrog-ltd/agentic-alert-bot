from __future__ import annotations

import traceback
from infrastructure.utils.logger import get_logger

from infrastructure.scraper.adapters.niche.ebay.consoles import Adapter as ConsolesAdapter
from infrastructure.scraper.adapters.niche.ebay.retro_pc import Adapter as RetroPcAdapter

logger = get_logger(__name__)


def _run_adapter(adapter, ebay_token: str) -> None:
    """
    Generic runner for a single adapter instance.
    Calls its API fetch and logs errors per-domain so
    consoles and retro-pc failures don't hide each other.
    """
    domain = getattr(adapter, "DOMAIN", "unknown-domain")

    try:
        logger.info(f"[scrape:{domain}] begin API fetch")
        adapter.fetch_listings_api(ebay_token)
        logger.info(f"[scrape:{domain}] API fetch complete")
    except Exception as e:
        logger.warning(
            f"[scrape:{domain}] API fetch failed: {e}\n{traceback.format_exc()}"
        )


def run(*, ebay_token: str):
    """
    Heartbeat entry point.

    Currently ingests:
      - ebay-consoles  (BIN + auction if adapter.SALE_TYPE is a list)
      - ebay-retro-pc  (same deal)

    We are intentionally NOT running:
      - motomine HTML scraping
      - per-URL close_auctions scrapes
      - any non-eBay scrapers

    because we want to stay API-first / low-risk.
    """
    logger.info("[scrape] Begin scrape (API mode)")

    consoles_adapter = ConsolesAdapter()
    retro_pc_adapter = RetroPcAdapter()

    _run_adapter(consoles_adapter, ebay_token)
    _run_adapter(retro_pc_adapter, ebay_token)

    logger.info("[scrape] End scrape (API mode)")
