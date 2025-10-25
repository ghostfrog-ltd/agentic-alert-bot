import re
import random, time
from datetime import datetime

from bs4 import BeautifulSoup

from infrastructure.utils.http import get  # our HTTP helper
from core.contracts import SiteAdapter, Article
from infrastructure.db.schema import resolve_source_id, resolve_source_niche, resolve_source_field
from infrastructure.utils.text import extract_content, classify_sentiment, is_footer_only
from infrastructure.utils.url_helpers import (
    is_article_url,
    canonical_from_html,
    stable_hash,
    normalize_url,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    ),
    "X-Ghostfrog-Bot": "true",
}


class Adapter(SiteAdapter):
    DOMAIN = "decrypt.co"
    LISTING = "https://decrypt.co/"
    RSS = "https://decrypt.co/feed/"

    def can_handle(self, url: str) -> bool:
        return self.DOMAIN in url

    def fetch_listing_urls(self):
        r = get(self.RSS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "xml")

        seen = set()
        for item in soup.find_all("item"):
            link_tag = item.find("link")
            href = link_tag.get_text(strip=True) if link_tag else None
            if not href:
                continue

            url = normalize_url(href, base_url=self.LISTING)
            if not url:
                continue
            if not is_article_url(url, {self.DOMAIN}):
                continue
            if url in seen:
                continue
            # Skip known junk paths (video pages, podcast, etc.)
            if any(bad in url for bad in ("/videos/")):
                continue

            seen.add(url)
            yield url

    def parse_article(self, url: str):
        # Politeness delay
        time.sleep(random.uniform(1.5, 3.5))

        r = get(url, timeout=30)
        html = r.text
        soup = BeautifulSoup(html, "lxml")

        # Canonical URL (keep your existing helper call style)
        canonical_url = canonical_from_html(html, url)

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
                if date_str.endswith("Z"):
                    date_str = date_str.replace("Z", "+00:00")
                published_at = datetime.fromisoformat(date_str)
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
        tags = [t.get_text(strip=True) for t in soup.select('a[rel~="tag"], .tags a') if t.get_text(strip=True)]

        # Extract content
        # ----- Content extraction -----
        content = None
        main = soup.find("main")
        if main:
            header = main.find("header")
            # Find the first div after the header (article body)
            body_div = None
            if header:
                sib = header.find_next_sibling()
                while sib and sib.name != "div":
                    sib = sib.find_next_sibling()
                body_div = sib
            else:
                # Fallback to first div under main if no header
                body_div = next((c for c in main.children if getattr(c, "name", None) == "div"), None)

            if body_div:
                # Clean up unwanted elements
                for sel in ("nav", "aside", "script", "style", "footer"):
                    for el in body_div.find_all(sel):
                        el.decompose()

                # Extract text from paragraphs
                paragraphs = [p.get_text(" ", strip=True) for p in body_div.find_all("p")]
                raw_text = " ".join(p for p in paragraphs if p) or body_div.get_text(" ", strip=True)

                # Strip ticker-like values
                price_like = re.compile(r"^\$?\d{1,3}(?:[,\d]{0,})(?:\.\d+)?$")
                lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
                kept = [ln for ln in lines if not price_like.match(ln)]
                text_clean = " ".join(kept) if kept else raw_text

                # Final sanity check
                if text_clean and len(text_clean) > 120:
                    content = text_clean

        # Fallback to generic extractor if we got nothing
        if not content:
            content = extract_content(html)

        # If it's just footer/boilerplate, suppress so DB remains empty and we retry later
        if content and is_footer_only(content):
            content = None

        # Sentiment
        sentiment, _score = classify_sentiment(title if title else (summary or content))

        # IDs
        h = stable_hash(self.DOMAIN, canonical_url)
        source_id = resolve_source_id(self.DOMAIN)

        niche = resolve_source_niche(self.DOMAIN)
        website = resolve_source_field(self.DOMAIN, 'type')

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
