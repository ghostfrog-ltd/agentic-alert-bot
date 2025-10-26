# infrastructure/utils/rss_helpers.py
from __future__ import annotations
from typing import Iterable, Set
from bs4 import BeautifulSoup

from infrastructure.utils.http import get
from infrastructure.utils.logger import get_logger
from infrastructure.utils.scrape_gate import gate_scrape, mark_scraped
from infrastructure.utils.url_helpers import normalize_url, is_article_url

logger = get_logger(__name__)

def fetch_rss_listing_urls(
    *,
    source_key: str,
    rss_url: str,
    listing_base: str,
    headers: dict | None = None,
    junk_paths: Iterable[str] = (),
    allowed_domains: Set[str] | None = None,
    prefer_interval_s: int | None = None,
    pre_mark: bool = False,
) -> list[str]:
    """
    Throttled RSS → article URL collector.

    - Respects DB throttle via gate_scrape()
    - Filters duplicates, non-article URLs, and junk paths
    - Always marks last_scraped_at in a finally-block
    """
    allowed, meta = gate_scrape(source_key, prefer_interval_s=prefer_interval_s, pre_mark=pre_mark)
    if not allowed:
        next_due = meta.next_due_at.isoformat() if meta.next_due_at else "unknown"
        logger.info(f"[{source_key}] throttle: skip (interval={meta.interval_s}s, next_due={next_due})")
        return []

    urls: list[str] = []
    seen: set[str] = set()
    domains = allowed_domains or {source_key, f"www.{source_key}"}

    try:
        r = get(rss_url, timeout=20, headers=headers)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "xml")

        junk_paths = tuple(junk_paths)  # in case a generator is passed

        for item in soup.find_all("item"):
            link_tag = item.find("link")
            href = link_tag.get_text(strip=True) if link_tag else None
            if not href:
                continue

            url = normalize_url(href, base_url=listing_base)
            if not url:
                continue
            if not is_article_url(url, domains):
                continue
            if any(p in url for p in junk_paths):
                continue
            if url in seen:
                continue

            seen.add(url)
            urls.append(url)
        return urls

    finally:
        # mark even if caller stops early or an exception occurs
        mark_scraped(meta)