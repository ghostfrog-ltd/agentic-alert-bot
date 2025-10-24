import re, hashlib, requests
from bs4 import BeautifulSoup
from datetime import datetime
from core.contracts import SiteAdapter, Article
from infrastructure.db.schema import resolve_source_id

from    infrastructure.utils.url_helpers import (
    is_article_url,
    canonical_from_html,
    stable_hash
)

class ExampleNewsAdapter(SiteAdapter):
    DOMAIN = "ghostfrog.co.uk"
    LISTING = "https://ghostfrog.co.uk"

    def can_handle(self, url: str) -> bool:
        return self.DOMAIN in url

    def fetch_listing_urls(self):
        # ... fetch and parse the page
        html = requests.get(self.LISTING, timeout=15).text
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a"):
            href = a.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://{self.DOMAIN}{href}"
            if is_article_url(href, {self.DOMAIN}):
                yield href

    def parse_article(self, url: str):
        r = requests.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "lxml")

        source_id =  resolve_source_id(self.DOMAIN)
        title = soup.select_one("h1").get_text(strip=True)
        summary = soup.select_one("meta[name='description']")["content"] if soup.select_one("meta[name='description']") else None
        date_str = soup.select_one("time[datetime]")["datetime"] if soup.select_one("time[datetime]") else None
        published_at = datetime.fromisoformat(date_str.replace("Z","+00:00")) if date_str else None
        author = soup.select_one(".byline .name")
        author = author.get_text(strip=True) if author else None
        tags = [t.get_text(strip=True) for t in soup.select(".tags a")]

        canonical_url = canonical_from_html(r.text, url)
        h = stable_hash(self.DOMAIN, canonical_url)

        return Article(
            source_id=source_id,
            source=self.DOMAIN,
            type='website',  # add this
            url=url,
            title=title,
            summary=summary,
            published_at=published_at,
            author=author,
            tags=tags,
            hash_id=h,
            content="",
            sentiment="unclassified",
        )