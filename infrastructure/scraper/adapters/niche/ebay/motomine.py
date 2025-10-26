# infrastructure/scraper/adapters/niche/ebay/motomine.py

import json
import random
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime, timezone, timedelta
import os
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from http.cookiejar import LWPCookieJar
import requests.exceptions as req_exc

from core.contracts import AuctionAdapter
from infrastructure.db.schema import (
    upsert_auction_listing,
    resolve_source_id,
    resolve_source_field,
)
from infrastructure.utils.logger import get_logger
from infrastructure.utils.scrape_gate import gate_scrape, mark_scraped

logger = get_logger(__name__)

# ------------------------------------
# CONFIG
# ------------------------------------
SELLER = "surreymotorcyclesalvage"

STORE_URL = "https://www.ebay.co.uk/str/{seller}?_pgn={page}&_ipg=240"
SELLER_ITEMS = "https://www.ebay.co.uk/sch/{seller}/m.html?_ipg=240&_pgn={page}"
DESKTOP_SRP = "https://www.ebay.co.uk/sch/i.html?LH_SpecificSeller=1&_sasl={seller}&_ipg=240&_pgn={page}&rt=nc"

COOKIE_PATH = "/tmp/ebay_motomine_cookies.lwp"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Referer": "https://www.ebay.co.uk/",
}

PRICE_RX = re.compile(r"£?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)")
BIDS_RX = re.compile(r"(\d+)\s+bids?", re.IGNORECASE)
ENDDATE_RX = re.compile(r'"endDate"\s*:\s*"([^"]+)"')
ITEM_ID_RX = re.compile(r"/itm/(?:[^/]+/)?(?P<id>\d{9,15})(?:[/?#]|$)")
INTERSTITIAL_TITLE_RX = re.compile(r"checking your browser", re.I)

# ------------------------------------
# SOURCE RESOLUTION (cached)
# ------------------------------------
_SOURCE_NAME: str | None = None
_SOURCE_ID: int | None = None

def _resolve_source(domain_hint: str) -> tuple[str, int | None]:
    global _SOURCE_NAME, _SOURCE_ID
    if _SOURCE_NAME is not None:
        return _SOURCE_NAME, _SOURCE_ID

    candidates = [domain_hint, "ebay-uk", "ebay", "motomine", "motomind", SELLER]
    for key in candidates:
        try:
            sname = resolve_source_field(key, "name")
            if sname:
                sid = resolve_source_id(key)
                _SOURCE_NAME = str(sname)
                _SOURCE_ID = int(sid) if sid is not None else None
                logger.info(f"[{domain_hint}] sources resolved -> name='{_SOURCE_NAME}', id={_SOURCE_ID}")
                return _SOURCE_NAME, _SOURCE_ID
        except Exception:
            continue

    _SOURCE_NAME, _SOURCE_ID = domain_hint, None
    logger.warning(f"[{domain_hint}] sources row not found; using source='{_SOURCE_NAME}' (no id)")
    return _SOURCE_NAME, _SOURCE_ID

# ------------------------------------
# SESSION & NETWORK HELPERS
# ------------------------------------
def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)

    try:
        cj = LWPCookieJar(COOKIE_PATH)
        if os.path.exists(COOKIE_PATH):
            cj.load(ignore_discard=True, ignore_expires=True)
        s.cookies = cj
    except Exception:
        pass
    return s

def _save_cookies(session: requests.Session):
    try:
        if isinstance(session.cookies, LWPCookieJar):
            session.cookies.save(ignore_discard=True, ignore_expires=True)
    except Exception:
        pass

def _looks_like_interstitial(soup: BeautifulSoup, html: str) -> bool:
    title = (soup.title.string.strip() if soup.title and soup.title.string else "").lower()
    body = html.lower()
    return (
        INTERSTITIAL_TITLE_RX.search(title) is not None
        or "security measure" in title
        or "cf-chl" in body
        or "please verify you are a human" in body
        or ("robot" in body and len(html) < 120000)
    )

def _robust_get(url: str, session: requests.Session, base_referer: str | None = None, tries: int = 4, timeout: int = 60):
    last = None
    for i in range(tries):
        try:
            if base_referer:
                session.headers["Referer"] = base_referer
            r = session.get(url, timeout=timeout)
            html = r.text
            soup = BeautifulSoup(html, "lxml")
            if not _looks_like_interstitial(soup, html):
                return r
            time.sleep(2.0 + i * 2.0 + random.uniform(0, 1.0))
            try:
                session.get("https://www.ebay.co.uk/", timeout=15)
            except Exception:
                pass
        except (req_exc.ChunkedEncodingError, req_exc.ContentDecodingError, req_exc.ConnectionError, req_exc.ReadTimeout) as e:
            last = e
            logger.warning(f"[net] {_strip_params(url)} -> transient error: {e} (try {i+1}/{tries})")
            time.sleep(1.0 + i * 1.5 + random.uniform(0, 1.0))
            continue
        except Exception as e:
            last = e
            logger.warning(f"[net] {_strip_params(url)} -> error: {e} (try {i+1}/{tries})")
            time.sleep(1.0 + i * 1.5 + random.uniform(0, 1.0))
            continue
    logger.error(f"[net] {_strip_params(url)} -> giving up after {tries} tries; last={last}")
    return None

# ------------------------------------
# HTML HELPERS
# ------------------------------------
def _canonical_item_url(item_id: str) -> str:
    return f"https://www.ebay.co.uk/itm/{item_id}"

def _strip_params(u: str) -> str:
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}{p.path}"

def _extract_item_id_from_href(href: str) -> str | None:
    if not href:
        return None
    m = ITEM_ID_RX.search(href)
    if m:
        return m.group("id")
    q = parse_qs(urlparse(href).query)
    if "item" in q and q["item"]:
        v = q["item"][0]
        if v.isdigit() and 9 <= len(v) <= 15:
            return v
    return None

def _extract_listing_urls_from_doc(soup: BeautifulSoup, base_url: str) -> list[str]:
    urls, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if "/itm/" not in href:
            continue
        href_abs = urljoin(base_url, href)
        if "/itm/123456" in href_abs:
            continue
        item_id = _extract_item_id_from_href(href_abs)
        if not item_id:
            continue
        u = _canonical_item_url(item_id)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls

def _extract_price_from_text(text: str) -> int | None:
    if not text:
        return None
    m = PRICE_RX.search(text.replace("\u00a0", " "))
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return int(round(float(raw)))
    except Exception:
        return None

def _price_from_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for obj in items:
            if not isinstance(obj, dict):
                continue
            offers = obj.get("offers")
            if isinstance(offers, dict):
                price = offers.get("price")
                currency = offers.get("priceCurrency") or "GBP"
                if price is not None:
                    try:
                        return int(round(float(str(price)))), currency
                    except Exception:
                        pass
    return None

def _extract_price_from_item_page(soup: BeautifulSoup, html: str):
    j = _price_from_jsonld(soup)
    if j:
        return j
    for sel in [
        "div[data-testid='x-price-primary'] span.ux-textspans",
        ".x-price-primary .ux-textspans",
        "#prcIsum",
        "#mm-saleDscPrc",
        "span[itemprop='price']",
        "span[data-testid='x-bin-price']",
        ".vi-price span",  # loose fallback on classic layout
    ]:
        el = soup.select_one(sel)
        if el:
            p = _extract_price_from_text(el.get_text(" ", strip=True))
            if p is not None:
                return p, "GBP"
    og = soup.select_one('meta[property="og:price:amount"]')
    if og and og.get("content"):
        try:
            return int(round(float(og["content"]))), "GBP"
        except Exception:
            pass
    p = _extract_price_from_text(html)
    if p is not None:
        return p, "GBP"
    return None

def _extract_bids_count(soup: BeautifulSoup, html: str) -> int:
    for sel in [
        "#vi-bidCount__cnt",
        "[data-testid='x-bid-count']",
        "span.x-bid-count",
        "a[href*='#bidCount'] span",
    ]:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            m = re.search(r"\d+", txt)
            if m:
                try:
                    return int(m.group(0))
                except Exception:
                    pass
    m = BIDS_RX.search(html)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return 0

def _extract_end_time(soup: BeautifulSoup, html: str) -> datetime | None:
    t = soup.select_one("time[datetime]")
    if t and t.has_attr("datetime"):
        iso = t["datetime"].strip()
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    m = ENDDATE_RX.search(html)
    if m:
        iso = m.group(1)
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    return None

def _extract_item_id(url: str, soup: BeautifulSoup | None = None, html: str | None = None) -> str | None:
    m = ITEM_ID_RX.search(url)
    if m:
        return m.group("id")
    if soup:
        meta = soup.select_one("meta[name='irItemId']")
        if meta and meta.get("content"):
            return meta["content"].strip()
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for obj in items:
                if isinstance(obj, dict):
                    pid = obj.get("productID")
                    if pid and str(pid).isdigit():
                        return str(pid)
    q = parse_qs(urlparse(url).query)
    v = (q.get("item") or [None])[0]
    if v and v.isdigit() and 9 <= len(v) <= 15:
        return v
    # do NOT fabricate a hash id; skip instead
    return None

def _infer_sale_type(_soup: BeautifulSoup, page_text: str) -> str | None:
    t = page_text.lower()
    # Prefer auction if bids are present
    if " bid" in t or " bids" in t:
        return "auction"
    if "buy it now" in t or "buy it now price" in t:
        return "bin"
    if "best offer" in t:
        return "best_offer"
    return None

# ------------------------------------
# ADAPTER
# ------------------------------------
class Adapter(AuctionAdapter):
    DOMAIN = "motomine"

    def can_handle(self, url: str) -> bool:
        return ("ebay.co.uk" in url) or ("motomine.co.uk" in url)

    def fetch_listing_urls(self) -> list[str]:
        allowed, meta = gate_scrape(self.DOMAIN, prefer_interval_s=None, pre_mark=False)
        if not allowed:
            next_due = meta.next_due_at.isoformat() if meta.next_due_at else "unknown"
            logger.info(f"[{self.DOMAIN}] throttle: skip (interval={meta.interval_s}s, next_due={next_due})")
            return []

        session = _build_session()
        page = 1
        consecutive_empty = 0
        all_urls: list[str] = []

        while True:
            urls_this: list[str] = []

            # Storefront
            store_url = STORE_URL.format(seller=SELLER, page=page)
            r = _robust_get(store_url, session, base_referer="https://www.ebay.co.uk/")
            if r:
                soup = BeautifulSoup(r.text, "lxml")
                urls_this = _extract_listing_urls_from_doc(soup, store_url)

            # Seller items
            if not urls_this:
                seller_url = SELLER_ITEMS.format(seller=SELLER, page=page)
                r = _robust_get(seller_url, session, base_referer="https://www.ebay.co.uk/")
                if r:
                    soup = BeautifulSoup(r.text, "lxml")
                    urls_this = _extract_listing_urls_from_doc(soup, seller_url)

            # SRP fallback
            if not urls_this:
                desktop_url = DESKTOP_SRP.format(seller=SELLER, page=page)
                r = _robust_get(desktop_url, session, base_referer="https://www.ebay.co.uk/")
                if r:
                    soup = BeautifulSoup(r.text, "lxml")
                    urls_this = _extract_listing_urls_from_doc(soup, desktop_url)

            if not urls_this:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
                for u in urls_this:
                    if u not in all_urls:
                        all_urls.append(u)

            if consecutive_empty >= 2 or page > 10:
                logger.info(f"[{self.DOMAIN}] no more results after page {page}; stopping.")
                break

            page += 1
            time.sleep(random.uniform(2.5, 5.0))
            mark_scraped(meta)  # keep per-page backoff marks

        _save_cookies(session)
        return all_urls

    def parse_auction(self, url: str) -> bool:
        session = _build_session()
        try:
            fetch_url = url if "nordt=true" in url else (url.split("?")[0] + "?nordt=true")
            r = _robust_get(fetch_url, session, base_referer="https://www.ebay.co.uk/", timeout=60)
            if not r:
                r = _robust_get(url, session, base_referer="https://www.ebay.co.uk/", timeout=60)
                if not r:
                    logger.warning(f"[{self.DOMAIN}] drop item (no response): {url}")
                    return False

            html = r.text
            soup = BeautifulSoup(html, "lxml")

            if _looks_like_interstitial(soup, html):
                logger.warning(f"[{self.DOMAIN}] interstitial on item, skip: {url}")
                return False

            h1 = soup.select_one("#itemTitle") or soup.select_one("h1")
            title = (
                h1.get_text(" ", strip=True)
                if h1 else (soup.title.get_text(" ", strip=True) if soup.title else "")
            )
            title = title.replace("Details about  ", "").strip() or ""
            if not title or title.lower().startswith("checking your browser"):
                return False

            price_tuple = _extract_price_from_item_page(soup, html)
            price_current = price_tuple[0] if isinstance(price_tuple, tuple) else price_tuple
            try:
                price_current = int(price_current or 0)
            except Exception:
                price_current = 0

            external_id = _extract_item_id(url, soup, html)
            if not external_id:
                logger.warning(f"[{self.DOMAIN}] no external_id, skip: {url}")
                return False

            bids_count = int(_extract_bids_count(soup, html) or 0)
            end_time = _extract_end_time(soup, html)

            title = (title or "").strip()[:255]
            url_clean = _strip_params(url)[:1024]
            detail_url = _canonical_item_url(external_id)
            sale_type = _infer_sale_type(soup, html)
            source_name, source_id = _resolve_source(self.DOMAIN)

            upsert_auction_listing(
                source=source_name,                # TEXT NOT NULL
                external_id=external_id,           # eBay item id
                title=title,
                price_current=price_current,
                bids_count=bids_count,
                end_time=end_time,                 # may be None
                url=url_clean,                     # listing-page URL (cleaned)
                detail_url=detail_url,             # canonical detail URL
                sale_type=sale_type,               # 'auction' | 'bin' | 'best_offer' | None
                roi_estimate=None,
                max_bid=None,
                notes=None,
                source_id=source_id,               # optional FK to sources.id
            )

            logger.info(
                f"[{self.DOMAIN}] upsert ok ext_id={external_id} price={price_current} bids={bids_count} end={end_time} url={url_clean}"
            )
            return True

        except Exception as e:
            logger.warning(f"[{self.DOMAIN}] parse failed {url}: {e}")
            return False
        finally:
            _save_cookies(session)
