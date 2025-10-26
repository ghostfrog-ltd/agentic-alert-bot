import json
import random
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
from core.contracts import AuctionAdapter
from infrastructure.db.schema import (  upsert_auction_listing, resolve_source_id, resolve_source_field, )
from infrastructure.utils.logger import get_logger
logger = get_logger(__name__)
from infrastructure.utils.scrape_gate import gate_scrape, mark_scraped

# ------------------------------------
# CONFIG
# ------------------------------------
SELLER = "surreymotorcyclesalvage"

STORE_URL = "https://www.ebay.co.uk/str/{seller}?_pgn={page}&_ipg=240"
SELLER_ITEMS = "https://www.ebay.co.uk/sch/{seller}/m.html?_ipg=240&_pgn={page}"
DESKTOP_SRP = "https://www.ebay.co.uk/sch/i.html?LH_SpecificSeller=1&_sasl={seller}&_ipg=240&_pgn={page}&rt=nc"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.ebay.co.uk/",
    "Cache-Control": "no-cache",
}

PRICE_RX = re.compile(r"£?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)")
BIDS_RX = re.compile(r"(\d+)\s+bids?", re.IGNORECASE)
ENDDATE_RX = re.compile(r'"endDate"\s*:\s*"([^"]+)"')
ITEM_ID_RX = re.compile(r"/itm/(?:[^/]+/)?(?P<id>\d{9,15})(?:[/?#]|$)")

# ------------------------------------
# SOURCE RESOLUTION (cached)
# ------------------------------------
_SOURCE_NAME: str | None = None
_SOURCE_ID: int | None = None

def _resolve_source(domain_hint: str) -> tuple[str, int | None]:
    """
    Resolve sources.name (TEXT NOT NULL) and optional sources.id.
    Tries a few reasonable keys, caches the first success.
    """
    global _SOURCE_NAME, _SOURCE_ID
    if _SOURCE_NAME is not None:
        return _SOURCE_NAME, _SOURCE_ID

    candidates = [domain_hint, "ebay-uk", "ebay", "motomine", SELLER]
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
            # keep trying other keys
            continue

    # Fallback: DB requires a non-null source string
    _SOURCE_NAME, _SOURCE_ID = domain_hint, None
    logger.warning(f"[{domain_hint}] sources row not found; using source='{_SOURCE_NAME}' (no id)")
    return _SOURCE_NAME, _SOURCE_ID

# ------------------------------------
# HELPERS
# ------------------------------------

def _get(url: str, session: requests.Session, timeout: int = 30, retries: int = 2, backoff: float = 0.8):
    last = None
    for i in range(retries + 1):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code in (429, 403, 502, 503, 520, 521, 522):
                raise RuntimeError(f"status {r.status_code}")
            return r
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(backoff * (1.5 ** i) + random.uniform(0, 0.4))
            else:
                raise RuntimeError(f"GET {url} failed after {retries+1} attempts: {last}") from last

def _is_interstitial(soup: BeautifulSoup, html: str) -> bool:
    title = (soup.title.string.strip() if soup.title and soup.title.string else "").lower()
    if "security measure" in title:
        return True
    return ("robot" in html.lower() and len(html) < 120000)

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
    """Scan all anchors for '/itm/', extract numeric ID, and build canonical UK URLs. Skip placeholder IDs."""
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
        return j  # (price, currency)
    for sel in [
        "#prcIsum",
        "#prcIsum_bidPrice",
        ".x-price-primary",
        ".notranslate",
        "span[itemprop='price']",
        "div[data-testid='x-price-primary'] span",
    ]:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                p = _extract_price_from_text(txt)
                if p is not None:
                    return p, "GBP"
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

def _extract_item_id(url: str, soup: BeautifulSoup | None = None, html: str | None = None) -> str:
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
    return f"hash-{abs(hash(url))}"

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

        session = requests.Session()
        session.headers.update(HEADERS)

        # Warm up
        try:
            session.get("https://www.ebay.co.uk/", timeout=15)
            session.get(f"https://www.ebay.co.uk/usr/{SELLER}", timeout=15)
            time.sleep(random.uniform(0.8, 1.6))
        except Exception:
            pass

        page = 1
        consecutive_empty = 0
        all_urls: list[str] = []

        while True:
            urls_this: list[str] = []

            # 1) Storefront
            store_url = STORE_URL.format(seller=SELLER, page=page)
            try:
                rs = _get(store_url, session, timeout=20, retries=2)
                logger.info(f"[{self.DOMAIN}] GET {store_url} -> {rs.status_code} {len(rs.text)} bytes (store)")
                ss = BeautifulSoup(rs.text, "lxml")
                urls_this = _extract_listing_urls_from_doc(ss, store_url)
                if page == 1 and not urls_this:
                    raw_itm = [a.get('href','') for a in ss.find_all('a') if '/itm/' in (a.get('href','') or '')]
                    logger.info(f"[{self.DOMAIN}] store p1 raw '/itm/' anchors={len(raw_itm)} sample={raw_itm[:5]}")
            except Exception as e:
                logger.warning(f"[{self.DOMAIN}] store page failed p{page}: {e}")

            # 2) Seller items
            if not urls_this:
                seller_url = SELLER_ITEMS.format(seller=SELLER, page=page)
                try:
                    r = _get(seller_url, session, timeout=20, retries=2)
                    logger.info(f"[{self.DOMAIN}] GET {seller_url} -> {r.status_code} {len(r.text)} bytes")
                    soup = BeautifulSoup(r.text, "lxml")
                    urls_this = _extract_listing_urls_from_doc(soup, seller_url)
                except Exception as e:
                    logger.warning(f"[{self.DOMAIN}] seller items page failed p{page}: {e}")

            # 3) SRP fallback
            if not urls_this:
                desktop_url = DESKTOP_SRP.format(seller=SELLER, page=page)
                try:
                    rd = _get(desktop_url, session, timeout=20, retries=2)
                    logger.info(f"[{self.DOMAIN}] GET {desktop_url} -> {rd.status_code} {len(rd.text)} bytes")
                    sd = BeautifulSoup(rd.text, "lxml")
                    interstitial = _is_interstitial(sd, rd.text)
                    logger.info(f"[{self.DOMAIN}] p{page} interstitial?={interstitial} html_len={len(rd.text)} (SRP)")
                    urls_this = _extract_listing_urls_from_doc(sd, desktop_url)
                except Exception as e:
                    logger.warning(f"[{self.DOMAIN}] desktop SRP failed p{page}: {e}")

            logger.info(f"[{self.DOMAIN}] page {page}: found {len(urls_this)} item URLs")

            if not urls_this:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
                for u in urls_this:
                    if u not in all_urls:
                        all_urls.append(u)

            if consecutive_empty >= 2:
                logger.info(f"[{self.DOMAIN}] no more results after page {page}; stopping.")
                break

            page += 1
            if page > 20:
                logger.warning(f"[{self.DOMAIN}] pagination cap (20) reached; stopping.")
                break

            time.sleep(random.uniform(1.5, 3.0))

            mark_scraped(meta)

        return all_urls

    def parse_auction(self, url: str) -> bool:
        """Fetch + parse the auction page and upsert into DB. Return True on success."""
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            r = _get(url, session, timeout=30, retries=2)
            if r.status_code != 200:
                logger.warning(f"[{self.DOMAIN}] GET {url} -> {r.status_code}")
                return False

            html = r.text
            soup = BeautifulSoup(html, "lxml")

            # Title
            h1 = soup.select_one("#itemTitle") or soup.select_one("h1")
            title = (
                h1.get_text(" ", strip=True)
                if h1 else (soup.title.get_text(" ", strip=True) if soup.title else "")
            )
            title = title.replace("Details about  ", "").strip() or ""

            # Skip CF/interstitial pages (don't poison DB with that title)
            if title.lower().startswith("checking your browser"):
                logger.info(f"[{self.DOMAIN}] interstitial item page; skipping {url}")
                return False

            # Price
            pc = _extract_price_from_item_page(soup, html)
            price_current = pc[0] if isinstance(pc, tuple) else pc
            try:
                price_current = int(price_current or 0)
            except Exception:
                price_current = 0

            # Required fields
            external_id = _extract_item_id(url, soup, html)
            bids_count = int(_extract_bids_count(soup, html) or 0)
            end_time = _extract_end_time(soup, html)

            # Clean values
            title = (title or "").strip()
            if len(title) > 255:
                title = title[:255]

            url_clean = _strip_params(url)
            if len(url_clean) > 1024:
                url_clean = url_clean[:1024]

            # Resolve source (from sources table)
            source_name, source_id = _resolve_source(self.DOMAIN)

            # Upsert
            upsert_auction_listing(
                source=source_name,           # TEXT NOT NULL
                external_id=external_id,
                title=title,
                price_current=price_current,
                bids_count=bids_count,
                end_time=end_time,            # can be None
                url=url_clean,
                roi_estimate=None,
                max_bid=None,
                notes=None,
                source_id=source_id,          # optional
            )

            logger.info(
                f"[{self.DOMAIN}] upsert ok ext_id={external_id} price={price_current} bids={bids_count} end={end_time} url={url_clean}"
            )
            return True

        except Exception as e:
            logger.warning(f"[{self.DOMAIN}] parse failed {url}: {e}")
            return False
