from datetime import datetime
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

class AdapterRegistry:
    def __init__(self, adapters):
        self.adapters = adapters

    def crawl_all(self):
        seen = set()
        for adapter in self.adapters:
            for url in adapter.fetch_listing_urls():
                if url in seen:
                    continue
                seen.add(url)
                try:
                    yield adapter.parse_article(url)
                except Exception as e:
                    logger.warning(f"{adapter.__class__.__name__} failed {url}: {e}")
