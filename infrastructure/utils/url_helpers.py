import hashlib
from urllib.parse import (
    urlparse,
    urlunparse,
    parse_qsl,
    urlencode,
    urljoin,
    urldefrag,
)
from bs4 import BeautifulSoup

# Tracking params to strip from URLs
STRIP_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "ref"
}

def is_article_url(u: str, domain_whitelist: set[str] = None) -> bool:
    """
    Basic heuristic to determine if a URL looks like an article URL.
    """
    if not u:
        return False

    # block Cloudflare email protection
    if "/cdn-cgi/l/email-protection" in u:
        return False

    p = urlparse(u)

    # Optional domain allowlist
    if domain_whitelist and p.netloc and p.netloc not in domain_whitelist:
        return False

    # Filter out some common non-article paths
    bad_prefixes = (
        "/cdn-cgi/",
        "/wp-json/",
        "/feed",
        "/tags/",
        "/category/",
    )
    if any(p.path.startswith(bp) for bp in bad_prefixes):
        return False

    return True


def normalize_url(u: str, base_url: str | None = None) -> str:
    """
    Clean and normalize URLs:
    - Resolve relative URLs against base_url (if provided)
    - Strip tracking params
    - Strip fragments
    - Force https if scheme is missing
    """
    if not u:
        return ""

    # Resolve relative URLs
    if base_url and (u.startswith("/") or not urlparse(u).netloc):
        u = urljoin(base_url, u)

    # Strip fragments (#...)
    u, _ = urldefrag(u)

    p = urlparse(u)
    netloc = p.netloc.lower().removeprefix("www.")

    # Filter out CF email protection early
    if "/cdn-cgi/l/email-protection" in p.path:
        return ""

    path = p.path.rstrip("/") or "/"

    # Strip unwanted query params
    q = [(k, v) for k, v in parse_qsl(p.query) if k not in STRIP_QUERY_KEYS]
    query = urlencode(q, doseq=True)

    return urlunparse((p.scheme or "https", netloc, path, "", query, ""))


def canonical_from_html(html: str, fallback_url: str) -> str:
    """
    Extract canonical URL from a page if available,
    otherwise fallback to the normalized fallback_url.
    Handles relative canonical links properly.
    """
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("link[rel='canonical']")
    if link and link.get("href"):
        return normalize_url(link["href"], base_url=fallback_url)
    return normalize_url(fallback_url, base_url=fallback_url)


def stable_hash(source: str, url: str) -> str:
    """
    Generate a stable short hash from source and url.
    Good for deduplication of articles.
    """
    base = f"{source}::{url}".encode("utf-8")
    return hashlib.sha256(base).hexdigest()[:16]
