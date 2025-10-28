from infrastructure.scraper.registry import AdapterRegistry
from infrastructure.scraper.auction_registry import AuctionRegistry
from infrastructure.db.schema import upsert_article, resolve_source_field
from infrastructure.utils.logger import get_logger
from infrastructure.db import schema
import traceback  # <-- add

# News adapters
from infrastructure.scraper.adapters.niche.crypto import coindesk, cointelegraph, decrypt, bitcoinmagazine

# Auction adapters
from infrastructure.scraper.adapters.niche.ebay import motomine, consoles

logger = get_logger(__name__)

# News registry
news_registry = AdapterRegistry(
    adapters=[
        coindesk.Adapter(),
        cointelegraph.Adapter(),
        decrypt.Adapter(),
        bitcoinmagazine.Adapter(),
    ],
    repo=schema
)

# Auction registry
auction_registry = AuctionRegistry(
    adapters=[
        motomine.Adapter(),
        consoles.Adapter(),
    ]
)

def source_enabled(name: str) -> bool:
    """
    Check if a source is enabled in the sources table.
    Defaults to True if the source isn't found (safe fallback).
    """
    value = resolve_source_field(name, "enabled")
    return bool(value) if value is not None else True


def run_news():
    """Scrape and save news articles from enabled sources."""
    results = []
    try:
        for adapter in news_registry.adapters:
            if not source_enabled(adapter.DOMAIN):
                logger.info(f"[scrape-news] Skipping disabled source: {adapter.DOMAIN}")
                continue

        for article in (news_registry.crawl_all() or []):
            if getattr(article, "type", "website") in ("news", "website", "market"):
                try:
                    upsert_article(article)
                    logger.info(f"[scrape-news] Saved: {getattr(article,'title','')} — {getattr(article,'url','')}")
                except Exception as e:
                    logger.warning(f"[scrape-news] Save failed {getattr(article,'url','')}: {e}")
            else:
                logger.debug(f"[scrape-news] Skipping non-website type: {getattr(article,'type','')}")
            results.append(article)
    except Exception as e:
        logger.error(f"[scrape-news] run_news() failed: {e}")
    return results


def run_auctions():
    """Scrape and save auction listings from enabled auction sources."""
    try:
        for adapter in auction_registry.adapters:
            if not source_enabled(adapter.DOMAIN):
                logger.info(f"[scrape-auctions] Skipping disabled source: {adapter.DOMAIN}")
                continue

            # Guard fetch phase so tz bugs don't abort the whole cycle
            try:
                urls = adapter.fetch_listing_urls()
            except Exception as e:
                logger.warning(
                    f"[scrape-auctions] {adapter.DOMAIN} fetch failed: {e}\n{traceback.format_exc()}"
                )
                continue

            logger.info(f"[scrape-auctions] {adapter.DOMAIN}: fetched {len(urls)} URLs")

            for url in urls:
                try:
                    adapter.parse_auction(url)
                except Exception as e:
                    logger.warning(
                        f"[scrape-auctions] {adapter.DOMAIN} parse failed {url}: {e}\n{traceback.format_exc()}"
                    )

            # flush once per adapter (bulk insert instead of per row)
            try:
                adapter.flush_batch()
            except AttributeError:
                # not all adapters may have batching yet
                pass
            except Exception as e:
                logger.error(f"[scrape-auctions] {adapter.DOMAIN} flush_batch failed: {e}")
    except Exception as e:
        logger.error(f"[scrape-auctions] run_auctions() failed: {e}\n{traceback.format_exc()}")


def run():
    """
    Main runner for all scrapers.
    - Runs news scrapers and saves articles.
    - Runs auction scrapers and upserts auction listings.
    Returns list of news articles (auction results are written directly to DB).
    """
    logger.info("[scrape] Starting full scrape cycle.")
    #run_news()
    run_auctions()
    logger.info("[scrape] Scrape cycle complete..")
