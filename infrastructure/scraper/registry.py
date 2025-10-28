import time
import random
import requests
from datetime import datetime, timedelta

from infrastructure.utils.logger import get_logger
from core.contracts import SiteAdapter
from infrastructure.db.schema import resolve_source_field

logger = get_logger(__name__)

# knobs you can tune
MAX_PER_ADAPTER = 20                   # stop after N articles per adapter per crawl
JITTER_BETWEEN_ARTICLES = (0.3, 0.8)   # polite tiny sleep between parses
BACKOFF_ON_429_SECONDS = 8             # small extra pause before the next URL
RETRY_INTERVAL = timedelta(days=1)     # retry content scraping once per day


class AdapterRegistry:
    def __init__(self, adapters: list[SiteAdapter], repo):
        """
        repo must provide:
          - find_by_url(url) -> dict | None
          - upsert_article(article)
          - touch_updated_at(url, ts)
        """
        self.adapters = adapters
        self.repo = repo

    # ------------------------------------
    # Internal helpers
    # ------------------------------------
    def _source_name_for(self, adapter: SiteAdapter) -> str:
        """
        Resolve a stable source name for the adapter.
        Prefer adapter.source or adapter.SOURCE, fallback to class name.
        """
        return (
            getattr(adapter, "source", None)
            or getattr(adapter, "SOURCE", None)
            or adapter.__class__.__name__.lower()
        )

    def _is_source_enabled(self, source_name: str, cache: dict[str, bool]) -> bool:
        """
        Read enabled flag from the sources table via resolve_source_field.
        Cache results for the duration of the crawl run.
        """
        if source_name in cache:
            return cache[source_name]

        try:
            enabled_val = resolve_source_field(source_name, "enabled")
            is_enabled = bool(enabled_val)
        except Exception as e:
            logger.warning(f"[crawl_all] could not read enabled flag for '{source_name}': {e}")
            # fallback: treat missing entry as enabled
            is_enabled = True

        cache[source_name] = is_enabled
        return is_enabled

    def should_retry(self, row, now_utc):
        """Return True if we should scrape again based on content + last updated time."""
        if row and row.get("content"):
            return False
        last_check = (row or {}).get("updated_at_utc") or (row or {}).get("fetched_at")
        if not last_check:
            return True
        return now_utc - last_check >= RETRY_INTERVAL

    # ------------------------------------
    # Main entry point
    # ------------------------------------
    def crawl_all(self):
        seen = set()
        now_utc = datetime.utcnow()
        enabled_cache: dict[str, bool] = {}

        for adapter in self.adapters:
            source_name = self._source_name_for(adapter)

            # Skip disabled sources
            if not self._is_source_enabled(source_name, enabled_cache):
                logger.info(
                    f"[crawl_all] source '{source_name}' disabled — skipping {adapter.__class__.__name__}"
                )
                continue

            try:
                # If adapter returns None, use empty list so 'for' doesn't explode
                listing_iter = adapter.fetch_listing_urls()
                listing = list(listing_iter or [])
            except Exception as e:
                logger.warning(f"{adapter.__class__.__name__} listing failed: {e}")
                continue

            count = 0
            for url in listing:
                if count >= MAX_PER_ADAPTER:
                    break
                if not url or url in seen:
                    continue
                seen.add(url)

                row = self.repo.find_by_url(url)

                # Skip if we already have content OR we're not due a retry yet
                if row and not self.should_retry(row, now_utc):
                    continue

                try:
                    article = adapter.parse_article(url)

                    if getattr(article, "content", None):
                        self.repo.upsert_article(article)
                        logger.debug(f"{adapter.__class__.__name__} saved {url}")
                    else:
                        # No content (footer/junk) → mark timestamp so we try again tomorrow
                        if row:
                            self.repo.touch_updated_at(url, now_utc)
                        else:
                            # record minimal row; upsert keeps content NULL
                            self.repo.upsert_article(article)
                        logger.debug(f"{adapter.__class__.__name__} will retry later {url}")

                    count += 1
                    time.sleep(random.uniform(*JITTER_BETWEEN_ARTICLES))

                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 429:
                        logger.warning(
                            f"{adapter.__class__.__name__} rate-limited {url}; backing off {BACKOFF_ON_429_SECONDS}s"
                        )
                        time.sleep(BACKOFF_ON_429_SECONDS)
                        continue
                    logger.warning(f"{adapter.__class__.__name__} failed {url}: {e}")

                except Exception as e:
                    logger.warning(f"{adapter.__class__.__name__} failed {url}: {e}")
