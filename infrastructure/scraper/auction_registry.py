from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

class AuctionRegistry:
    """
    Lightweight registry for auction adapters.
    Each adapter must implement:
        - fetch_listing_urls()
        - parse_auction(url)
    """

    def __init__(self, adapters):
        self.adapters = adapters or []

    def crawl_all(self):
        """
        Crawl all auction adapters.
        For each adapter:
          - fetch URLs
          - parse each auction
        Returns: list of results (could be True/False per parse_auction).
        """
        results = []
        for adapter in self.adapters:
            try:
                urls = adapter.fetch_listing_urls()
                logger.info(f"[auction-registry] {adapter.DOMAIN}: fetched {len(urls)} URLs")
                for url in urls:
                    try:
                        result = adapter.parse_auction(url)
                        results.append(result)
                    except Exception as e:
                        logger.warning(f"[auction-registry] {adapter.DOMAIN} parse failed {url}: {e}")
            except Exception as e:
                logger.error(f"[auction-registry] {adapter.DOMAIN} failed to fetch listings: {e}")
        return results
