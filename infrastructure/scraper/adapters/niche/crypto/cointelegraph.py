import random, time
from bs4 import BeautifulSoup
from infrastructure.utils.http import get
from core.contracts import SiteAdapter, Article
from infrastructure.db.schema import resolve_source_id, resolve_source_niche, resolve_source_field
from infrastructure.utils.text import extract_content, classify_sentiment, is_footer_only
from infrastructure.utils.url_helpers import ( canonical_from_html,  stable_hash,  normalize_url,)
from datetime import datetime
from email.utils import parsedate_to_datetime
from infrastructure.utils.logger import get_logger
from infrastructure.utils.rss_helpers import fetch_rss_listing_urls

logger = get_logger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    ),
    "X-Ghostfrog-Bot": "true",
}


class Adapter(SiteAdapter):
    DOMAIN = "cointelegraph.com"
    LISTING = "https://cointelegraph.com/"
    RSS = "https://cointelegraph.com/rss/"

    def can_handle(self, url: str) -> bool:
        return self.DOMAIN in url

    def fetch_listing_urls(self):
        return fetch_rss_listing_urls(
            source_key=self.DOMAIN,
            rss_url=self.RSS,
            listing_base=self.LISTING,
            headers=HEADERS,
            junk_paths=("/videos/", "/video/", "/podcast/", "/podcasts/"),
            allowed_domains={self.DOMAIN, f"www.{self.DOMAIN}"},
            prefer_interval_s=3600,
            pre_mark=False,
        )

    def parse_article(self, url: str):
        # Politeness delay
        time.sleep(random.uniform(1.5, 3.5))

        r = get(url, timeout=30)
        html = r.text
        soup = BeautifulSoup(html, "lxml")

        # Canonical URL (keep your existing helper call style)
        canonical_url = canonical_from_html(html, url) or normalize_url(url) or url

        # Title
        title_el = soup.select_one('meta[property="og:title"]')
        title = (
            (title_el.get("content") if title_el else None)
            or (soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None)
            or canonical_url
        )

        # Summary / description
        desc_el = soup.select_one('meta[name="description"]') or soup.select_one(
            'meta[property="og:description"]'
        )
        summary = desc_el.get("content") if desc_el else None

        # Published date
        date_str = (
            (soup.select_one('meta[property="article:published_time"]') or {}).get("content")
            or (soup.select_one("time[datetime]") or {}).get("datetime")
        )
        published_at = None
        if date_str:
            try:
                published_at = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                try:
                    published_at = parsedate_to_datetime(date_str)
                except Exception:
                    published_at = None

        # Author
        author_el = soup.select_one('meta[name="author"]') or soup.select_one(".byline .name")
        author = (
            author_el.get("content")
            if author_el and hasattr(author_el, "get") and author_el.has_attr("content")
            else (author_el.get_text(strip=True) if author_el else None)
        )

        # Tags
        tags = [t.get("content", "").strip() for t in soup.select('meta[property="article:tag"]') if t.get("content")]
        if not tags:
            tags = [t.get_text(strip=True) for t in soup.select('a[rel~="tag"], .tags a') if t.get_text(strip=True)]

        # Extract content
        content = extract_content(html)

        # If it's just footer/boilerplate, suppress so DB remains empty and we retry later
        if content and is_footer_only(content):
            content = None

        # Sentiment
        text_for_sentiment = title or summary or (content[:300] if content else "")
        sentiment, _score = classify_sentiment(text_for_sentiment)

        # IDs
        h = stable_hash(self.DOMAIN, canonical_url)
        source_id = resolve_source_id(self.DOMAIN)

        niche = resolve_source_niche(self.DOMAIN)
        website = resolve_source_field(self.DOMAIN, "type") or "website"

        return Article(
            source_id=source_id,
            source=self.DOMAIN,
            type=website,
            url=canonical_url,
            title=title,
            summary=summary,
            published_at=published_at,
            author=author,
            tags=tags,
            hash_id=h,
            content=content,
            sentiment=sentiment,
            niche=niche
        )
