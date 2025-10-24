import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup

# Tracking params to strip
STRIP_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "ref"
}

def is_article_url(u: str, domain_whitelist: set[str] = None) -> bool:
    if not u:
        return False
    if "/cdn-cgi/l/email-protection" in u:
        return False
    p = urlparse(u)
    if domain_whitelist and p.netloc and p.netloc not in domain_whitelist:
        return False
    bad_prefixes = ("/cdn-cgi/", "/wp-json/", "/feed", "/tags/", "/category/")
    if any(p.path.startswith(bp) for bp in bad_prefixes):
        return False
    return True

def normalize_url(u: str) -> str:
    p = urlparse(u)
    netloc = p.netloc.lower().removeprefix("www.")
    if "/cdn-cgi/l/email-protection" in p.path:
        return ""  # reject later
    path = p.path.rstrip("/") or "/"
    q = [(k, v) for k, v in parse_qsl(p.query) if k not in STRIP_QUERY_KEYS]
    query = urlencode(q, doseq=True)
    return urlunparse((p.scheme or "https", netloc, path, "", query, ""))

def canonical_from_html(html: str, fallback_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("link[rel='canonical']")
    if link and link.get("href"):
        return normalize_url(link["href"])
    return normalize_url(fallback_url)

def stable_hash(source: str, url: str) -> str:
    base = f"{source}::{url}".encode("utf-8")
    return hashlib.sha256(base).hexdigest()[:16]