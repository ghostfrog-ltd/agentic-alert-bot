import json
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from http.cookiejar import LWPCookieJar

from core.contracts import AuctionAdapter
from infrastructure.db.schema import (
    upsert_auction_listing,
    resolve_source_id,
    resolve_source_field,
    append_price_history,
    mark_listing_seen
)
from infrastructure.utils.logger import get_logger
from infrastructure.utils.scrape_gate import gate_scrape, mark_scraped
from infrastructure.utils.model_key import normalise_model
from infrastructure.utils.http import sleep_rate, is_ended_listing  # your shared helpers

logger = get_logger(__name__)

# ------------------------------------
# CONFIG
# ------------------------------------
CATEGORY_IDS = [139971, 54968, 139973]  # consoles / accessories / games

BASE_URL = (
    "https://www.ebay.co.uk/b/{cat}?_ipg=240&_pgn={page}"
    "&LH_BIN=1&LH_ItemCondition=3000&LH_Location=1&_sop=10"
)

SEARCH_BASE = (
    "https://www.ebay.co.uk/sch/i.html?_nkw=&_sacat={cat}"
    "&LH_BIN=1&LH_ItemCondition=3000&LH_Location=1&_ipg=240&_pgn={page}&_sop=10"
)

REFERERS = [
    "https://www.ebay.co.uk/",
    "https://www.ebay.co.uk/sch/i.html?_nkw=playstation",
    "https://www.ebay.co.uk/b/139971",
]

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
ITEM_ID_RX = re.compile(r"/itm/(?:[^/]+/)?(?P<id>\d{9,15})(?:[/?#]|$)")
INTERSTITIAL_TITLE_RX = re.compile(r"(checking your browser|just a moment)", re.I)
CF_BODY_MARKERS = (
    "cf-chl", "cf-browser-verification", "cloudflare", "Checking your browser before you access eBay"
)

# ------------------------------------
# SOURCE RESOLUTION (memoized)
# ------------------------------------
_SOURCE_NAME: str | None = None
_SOURCE_ID: int | None = None


def _resolve_source(domain_hint: str) -> tuple[str, int | None]:
    global _SOURCE_NAME, _SOURCE_ID
    if _SOURCE_NAME is not None:
        return _SOURCE_NAME, _SOURCE_ID

    candidates = [domain_hint, "ebay-uk", "ebay"]
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
# SESSION / HTTP
# ------------------------------------
def _build_session(cookie_path: str = "/tmp/ebay_cookies.lwp") -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies = LWPCookieJar(cookie_path)
    try:
        s.cookies.load(ignore_discard=True, ignore_expires=True)
    except Exception:
        pass

    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=1.5,
        status_forcelist=(429, 502, 503, 520, 521, 522),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _save_cookies(session: requests.Session):
    try:
        if isinstance(session.cookies, LWPCookieJar):
            session.cookies.save(ignore_discard=True, ignore_expires=True)
    except Exception:
        pass


# ------------------------------------
# CONSENT WALL HANDLER
# ------------------------------------
def _maybe_accept_consent(html: str, session: requests.Session) -> bool:
    if "consent.ebay.com" not in html and 'name="eciConsent"' not in html:
        return False
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    if not form or not form.get("action"):
        return False
    action = form["action"]
    payload = {}
    for inp in soup.find_all("input"):
        n = inp.get("name")
        v = inp.get("value", "")
        if not n:
            continue
        if "accept" in n.lower():
            v = v or "true"
        payload[n] = v
    try:
        sleep_rate(base=3.0)
        session.post(action, data=payload, timeout=20, allow_redirects=True)
        return True
    except Exception:
        return False


# ------------------------------------
# HELPERS
# ------------------------------------

def _is_valid_item_page(soup: BeautifulSoup, html: str) -> bool:
    """
    Heuristics to decide whether we actually got a real item page
    (as opposed to consent/interstitial/soft-block/thin shell).
    """
    # 0) obvious walls
    if _looks_like_interstitial(soup, html):
        return False

    # 1) ended/invalid listings are "not valid for parse"
    # (they're handled as OK skips elsewhere)
    try:
        if is_ended_listing(html.lower()):
            return False
    except Exception:
        pass

    # 2) require a plausible title
    has_title = bool(soup.select_one("#itemTitle") or soup.select_one("h1"))
    if not has_title:
        return False

    # 3) look for price via JSON-LD offers first
    try:
        for tag in soup.find_all("script", type="application/ld+json"):
            data = None
            try:
                data = json.loads(tag.string or "")
            except Exception:
                continue
            objs = data if isinstance(data, list) else [data]
            for obj in objs:
                if isinstance(obj, dict):
                    offers = obj.get("offers")
                    if isinstance(offers, dict) and offers.get("price"):
                        return True
    except Exception:
        pass

    # 4) fall back to common SSR price selectors
    price_selectors = [
        "div[data-testid='x-price-primary'] span.ux-textspans",
        ".x-price-primary .ux-textspans",
        "#prcIsum",
        "#mm-saleDscPrc",
        "span[itemprop='price']",
        "span[data-testid='x-bin-price']",
        'meta[property="og:price:amount"]',
        'meta[name="twitter:data1"]',
        "#convbidPrice",
        "#prcIsum_bidPrice",
        ".vi-price__span",
        ".notranslate#mm-saleDscPrc",
    ]
    for sel in price_selectors:
        el = soup.select_one(sel)
        if el:
            # meta tags carry value in "content"
            val = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if val and any(ch.isdigit() for ch in val):
                return True

    # 5) thin pages are almost always soft-blocks
    if len(html) < 20000:
        return False

    # If in doubt, treat as not valid
    return False

def _looks_like_interstitial(soup: BeautifulSoup, html: str) -> bool:
    t = (soup.title.string if soup.title and soup.title.string else "").lower()
    body = html.lower()
    return (
        INTERSTITIAL_TITLE_RX.search(t) is not None
        or any(m in body for m in CF_BODY_MARKERS)
        or "captcha" in body
        or "security measure" in body
    )


def _warn_blocked(domain: str, url: str, r: requests.Response | None, reason: str = ""):
    status = getattr(r, "status_code", "NA")
    text = getattr(r, "text", "") or ""
    length = len(text)
    title = ""
    try:
        s = BeautifulSoup(text[:5000], "lxml")
        if s.title and s.title.string:
            title = s.title.string.strip()
    except Exception:
        pass
    msg = f"[{domain}] invalid/blocked: {url} (status={status}, len={length}, title={title!r})"
    if reason:
        msg += f" reason={reason}"
    logger.warning(msg)


def _get(url: str, session: requests.Session, timeout: int = 30):
    sleep_rate(base=4.0)  # per-item pacing
    r = session.get(url, timeout=timeout)
    if r.status_code in (429, 403, 502, 503, 520, 521, 522):
        raise RuntimeError(f"status {r.status_code}")
    return r


def _refetch_get(url: str, session: requests.Session, base_referer: str | None = None, tries: int = 3):
    last = None
    for i in range(tries):
        ref = base_referer or REFERERS[i % len(REFERERS)]
        session.headers["Referer"] = ref
        try:
            r = _get(url, session, timeout=30)
            html = r.text
            if _maybe_accept_consent(html, session):
                sleep_rate(base=2.5)
                r = _get(url, session, timeout=30)
                html = r.text
            if len(html) < 20000:
                # retry without nordt if thin page
                url2 = url.split("?")[0]
                r = _get(url2, session, timeout=30)
                html = r.text
            soup = BeautifulSoup(html, "lxml")
            if not _looks_like_interstitial(soup, html):
                return r
            last = r
        except Exception as e:
            last = e
        time.sleep(2.0 + i * 2.5 + random.uniform(0, 1.5))
        try:
            session.get("https://www.ebay.co.uk/", timeout=15)
        except Exception:
            pass
    if isinstance(last, requests.Response):
        return last
    raise RuntimeError(f"refetch failed for {url}: {last}")


def _canonical_item_url(item_id: str) -> str:
    return f"https://www.ebay.co.uk/itm/{item_id}"


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
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            offers = obj.get("offers")
            if isinstance(offers, dict):
                price = offers.get("price")
                curr = offers.get("priceCurrency") or "GBP"
                if price:
                    try:
                        return int(round(float(str(price)))), curr
                    except Exception:
                        pass
    return None


def _extract_price_from_item_page(soup: BeautifulSoup, html: str):
    j = _price_from_jsonld(soup)
    if j:
        return j
    selectors = [
        "div[data-testid='x-price-primary'] span.ux-textspans",
        ".x-price-primary .ux-textspans",
        "#prcIsum",
        "#mm-saleDscPrc",
        "span[itemprop='price']",
        "span[data-testid='x-bin-price']",
        'meta[name="twitter:data1"]',
        "#convbidPrice",
        "#prcIsum_bidPrice",
        ".vi-price__span",
        ".notranslate#mm-saleDscPrc",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            val = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            p = _extract_price_from_text(val)
            if p is not None:
                return p, "GBP"
    # last resort: search whole HTML
    p = _extract_price_from_text(html)
    if p is not None:
        return p, "GBP"
    return None


def _extract_end_time(soup: BeautifulSoup, html: str) -> datetime | None:
    t = soup.select_one("time[datetime]")
    if t and t.has_attr("datetime"):
        try:
            return (
                datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
        except Exception:
            return None
    return None


def _secs_left(end_time: datetime | None) -> int | None:
    if not end_time:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = (end_time - now).total_seconds()
    return int(delta) if delta > 0 else 0


# ------------------------------------
# ADAPTER
# ------------------------------------
class Adapter(AuctionAdapter):
    DOMAIN = "ebay-consoles"

    def __init__(self):
        self.session = _build_session()

    def can_handle(self, url: str) -> bool:
        return "ebay.co.uk" in url

    def fetch_listing_urls(self) -> list[str]:
        allowed, meta = gate_scrape(self.DOMAIN, prefer_interval_s=None, pre_mark=False)
        if not allowed:
            next_due = meta.next_due_at.isoformat() if meta.next_due_at else "unknown"
            logger.info(f"[{self.DOMAIN}] throttle: skip (interval={meta.interval_s}s, next_due={next_due})")
            return []

        try:
            try:
                self.session.get("https://www.ebay.co.uk/", timeout=15)
            except Exception:
                pass

            all_urls: list[str] = []

            for cat in CATEGORY_IDS:
                page = 1
                consecutive_empty = 0
                while True:
                    cat_url = SEARCH_BASE.format(cat=cat, page=page)
                    try:
                        time.sleep(random.uniform(3.0, 7.0))  # SERP pacing
                        r = _refetch_get(cat_url, self.session)
                        soup = BeautifulSoup(r.text, "lxml")
                    except Exception as e:
                        logger.warning(f"[{self.DOMAIN}] fetch category {cat} p{page} failed: {e}")
                        break

                    urls_this = _extract_listing_urls_from_doc(soup, cat_url)
                    if not urls_this:
                        try:
                            vanity_url = BASE_URL.format(cat=cat, page=page)
                            r2 = _refetch_get(vanity_url, self.session)
                            soup2 = BeautifulSoup(r2.text, "lxml")
                            urls_this = _extract_listing_urls_from_doc(soup2, vanity_url)
                        except Exception:
                            pass

                    logger.info(f"[{self.DOMAIN}] cat {cat} page {page}: {len(urls_this)} urls")

                    if not urls_this:
                        consecutive_empty += 1
                    else:
                        consecutive_empty = 0
                        for u in urls_this:
                            if u not in all_urls:
                                all_urls.append(u)

                    if consecutive_empty >= 2 or page > 8:
                        break
                    page += 1

            logger.info(f"[{self.DOMAIN}] collected {len(all_urls)} URLs total")
            return all_urls
        finally:
            _save_cookies(self.session)
            mark_scraped(meta)

    def parse_auction(self, url: str) -> bool:
        try:
            try:
                self.session.get("https://www.ebay.co.uk/", timeout=15)
            except Exception:
                pass

            fetch_url = url if "nordt=true" in url else (url.split("?")[0] + "?nordt=true")
            r = _refetch_get(fetch_url, self.session)
            html = r.text
            soup = BeautifulSoup(html, "lxml")

            html_lower = html.lower()
            if is_ended_listing(html_lower):
                logger.info(f"[{self.DOMAIN}] ended/invalid listing, skipping (ok): {url}")
                _save_cookies(self.session)
                return False

            if _looks_like_interstitial(soup, html):
                r = _refetch_get(url, self.session)
                html = r.text
                soup = BeautifulSoup(html, "lxml")
                html_lower = html.lower()
                if is_ended_listing(html_lower):
                    logger.info(f"[{self.DOMAIN}] ended/invalid listing (retry), skipping (ok): {url}")
                    _save_cookies(self.session)
                    return False

            if not _is_valid_item_page(soup, html):
                _warn_blocked(self.DOMAIN, url, r, reason="no title/price markers")
                _save_cookies(self.session)
                return False

            h1 = soup.select_one("#itemTitle") or soup.select_one("h1")
            title = (
                h1.get_text(" ", strip=True)
                if h1 else (soup.title.get_text(" ", strip=True) if soup.title else "")
            )
            title = title.replace("Details about  ", "").strip() or ""

            if INTERSTITIAL_TITLE_RX.search(title):
                logger.warning(f"[{self.DOMAIN}] interstitial-like title, skip: {title!r} | {url}")
                _save_cookies(self.session)
                return False

            model_key = normalise_model(title)
            pc = _extract_price_from_item_page(soup, html)
            price_current = pc[0] if isinstance(pc, tuple) else pc
            price_current = int(price_current or 0)

            external_id = _extract_item_id_from_href(url)
            if not external_id:
                raise ValueError("could not extract item id")

            end_time = _extract_end_time(soup, html)
            time_left_s = _secs_left(end_time)
            url_clean = url.split("?")[0][:1024]
            detail_url = _canonical_item_url(external_id)

            title_lower = title.lower()
            retro_keywords = [
                "ps1", "playstation 1", "ps2", "gamecube", "n64",
                "gba", "game boy", "snes", "nes", "dreamcast", "sega",
            ]
            modern_keywords = [
                "ps3", "ps4", "ps5", "xbox one", "xbox series", "switch", "steam deck",
            ]
            if any(k in title_lower for k in retro_keywords):
                category_hint = "retro"
            elif any(k in title_lower for k in modern_keywords):
                category_hint = "modern"
            else:
                category_hint = "unknown"

            source_name, source_id = _resolve_source(self.DOMAIN)

            upsert_auction_listing(
                source=source_name,
                external_id=external_id,
                title=title[:255],
                price_current=price_current,
                bids_count=0,
                end_time=end_time,
                url=url_clean,
                detail_url=detail_url,
                sale_type="bin",
                roi_estimate=None,
                max_bid=None,
                notes=category_hint,
                source_id=source_id,
                model_key=model_key,
                time_left_s=time_left_s,
                status="live",
            )

            # 👇 Then mark it as seen / update live snapshot
            try:
                mark_listing_seen(
                    external_id=external_id,
                    price_current=price_current,
                    bids_count=0,
                    end_time=end_time,
                    time_left_s=time_left_s,
                    model_key=model_key,
                    status="live",
                )
            except Exception as _e:
                logger.warning(f"[{self.DOMAIN}] mark_listing_seen failed: {_e}")

            if price_current and price_current > 0:
                try:
                    append_price_history(external_id=external_id, price=price_current, bids_count=0)
                except Exception as _e:
                    logger.warning(f"[{self.DOMAIN}] price_history append failed: {_e}")

            _save_cookies(self.session)
            return True

        except Exception as _e:
            logger.warning(f"[{self.DOMAIN}] parse_auction failed: {_e}")
            _save_cookies(self.session)
            return False
