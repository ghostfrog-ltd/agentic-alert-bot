from typing import Sequence, Iterable
from core.contracts import SiteAdapter, Article

class AdapterRegistry:
    def __init__(self, adapters: Sequence[SiteAdapter]):
        self.adapters = adapters

    def find(self, url: str) -> SiteAdapter | None:
        for a in self.adapters:
            if a.can_handle(url):
                return a
        return None

    def crawl_all(self) -> Iterable[Article]:
        """Pull listings from all adapters, then parse each article."""
        seen = set()
        for adapter in self.adapters:
            for url in adapter.fetch_listing_urls():
                if url in seen:
                    continue
                seen.add(url)
                try:
                    yield adapter.parse_article(url)
                except Exception as e:
                    # log and continue
                    print(f"[WARN] {adapter.__class__.__name__} failed {url}: {e}")
