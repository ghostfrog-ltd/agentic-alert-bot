from infrastructure.scraper.registry import AdapterRegistry
from infrastructure.db.schema import upsert_article
from infrastructure.db import schema
from infrastructure.utils.logger import get_logger

#crypto
from infrastructure.scraper.adapters.niche.crypto import coindesk, cointelegraph, decrypt

logger = get_logger(__name__)

registry = AdapterRegistry(
    adapters=[
        coindesk.Adapter(),
        cointelegraph.Adapter(),
        decrypt.Adapter(),
    ],
    repo=schema,
)

def run():
    """
    Runs one scrape cycle and ALWAYS returns a list (never None).
    Also keeps your 'website' type branch.
    """
    results = []
    try:
        for article in (registry.crawl_all() or []):
            if getattr(article, "type", "website") in ("news", "website", "market"):
                try:
                    upsert_article(article)  # OK to keep if you want explicit control here
                    logger.info(f"[scrape] Saved: {getattr(article,'title','')} — {getattr(article,'url','')}")
                except Exception as e:
                    logger.warning(f"[scrape] Save failed {getattr(article,'url','')}: {e}")
            else:
                logger.debug(f"[scrape] Skipping non-website type: {getattr(article,'type','')}")
            results.append(article)
    except Exception as e:
        logger.error(f"[scrape] run() failed: {e}")
    return results
