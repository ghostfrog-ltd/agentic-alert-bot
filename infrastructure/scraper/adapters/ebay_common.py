# infrastructure/scraper/adapters/ebay_common.py
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timezone
from http.cookiejar import LWPCookieJar
from typing import Optional, Tuple, List
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from infrastructure.utils.http import sleep_rate, is_ended_listing  # your shared helpers
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

# ---------- constants / regex ----------
PRICE_RX = re.compile(r"£?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)")
ITEM_ID_RX = re.compile(r"/itm/(?:[^/]+/)?(?P<id>\d{9,15})(?:[/?#]|$)")
INTERSTITIAL_TITLE_RX = re.compile(r"(checking your browser|just a moment)", re.I)
CF_BODY_MARKERS = (
    "cf-chl", "cf-browser-verification", "cloudflare",
    "Checking your browser before you access eBay", "captcha", "security measure"
)

DEFAULT_HEADERS = {
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

REFERERS = [
    "https://www.ebay.co.uk/",
    "https://www.ebay.co.uk/sch/i.html?_nkw=playstation",
    "https://www.ebay.co.uk/b/139971",
]


# ---------- session ----------
def build_session(headers: dict | None = None, cookie_path: str = "/tmp/ebay_cookies.lwp") -> requests.Session:
    s = requests.Session()
    s.headers.update(headers or DEFAULT_HEADERS)
    s.cookies = LWPCookieJar(cookie_path)
    try:
        s.cookies.load(ignore_discard=True, ignore_expires=True)
    except Exception:
        pass

    retry = Retry(
        total=3, read=3, connect=3,
        backoff_factor=1.5,
        status_forcelist=(429, 502, 503, 520, 521, 522),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def save_cookies(session: requests.Session):
    try:
        if isinstance(session.cookies, LWPCookieJar):
            session.cookies.save(ignore_discard=True, ignore_expires=True)
    except Exception:
        pass


# ---------- interstitial / consent ----------
def looks_like_interstitial(soup: BeautifulSoup, html: str) -> bool:
    t = (soup.title.string if soup.title and soup.title.string else "").lower()
    body = html.lower()
    return (
            INTERSTITIAL_TITLE_RX.search(t) is not None
            or any(m in body for m in CF_BODY_MARKERS)
    )


def maybe_accept_consent(html: str, session: requests.Session) -> bool:
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


# ---------- network ----------
def _get(url: str, session: requests.Session, timeout: int = 30) -> requests.Response:
    sleep_rate(base=4.0)  # per-item pacing
    r = session.get(url, timeout=timeout)
    if r.status_code in (429, 403, 502, 503, 520, 521, 522):
        raise RuntimeError(f"status {r.status_code}")
    return r


def refetch_get(url: str, session: requests.Session, base_referer: str | None = None,
                tries: int = 3) -> requests.Response:
    last = None
    for i in range(tries):
        ref = base_referer or REFERERS[i % len(REFERERS)]
        session.headers["Referer"] = ref
        try:
            r = _get(url, session, timeout=30)
            html = r.text
            if maybe_accept_consent(html, session):
                sleep_rate(base=2.5)
                r = _get(url, session, timeout=30)
                html = r.text
            if len(html) < 20000:
                # thin page fallback — drop query string
                url2 = url.split("?")[0]
                r = _get(url2, session, timeout=30)
                html = r.text
            soup = BeautifulSoup(html, "lxml")
            if not looks_like_interstitial(soup, html):
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


# ---------- URL helpers ----------
def canonical_item_url(item_id: str) -> str:
    return f"https://www.ebay.co.uk/itm/{item_id}"


def extract_item_id_from_href(href: str) -> Optional[str]:
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


def extract_listing_urls_from_doc(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if "/itm/" not in href:
            continue
        href_abs = urljoin(base_url, href)
        if "/itm/123456" in href_abs:
            continue
        item_id = extract_item_id_from_href(href_abs)
        if not item_id:
            continue
        u = canonical_item_url(item_id)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


# ---------- parsing ----------
def extract_price_from_text(text: str) -> Optional[int]:
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


def price_from_jsonld(soup: BeautifulSoup) -> Optional[Tuple[int, str]]:
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


def extract_price_from_item_page(soup: BeautifulSoup, html: str) -> Optional[Tuple[int, str]]:
    j = price_from_jsonld(soup)
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
            p = extract_price_from_text(val)
            if p is not None:
                return p, "GBP"
    p = extract_price_from_text(html)
    if p is not None:
        return p, "GBP"
    return None


# before: returned naive UTC
def _extract_end_time(soup: BeautifulSoup, html: str) -> datetime | None:
    t = soup.select_one("time[datetime]")
    if t and t.has_attr("datetime"):
        try:
            return (
                datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
                .astimezone(timezone.utc)          # <-- keep AWARE UTC
            )
        except Exception:
            return None
    return None

# before: made 'now' naive; now make both aware and normalize
def _secs_left(end_time: datetime | None) -> int | None:
    if not end_time:
        return None
    if end_time.tzinfo is None:
        end_aware = end_time.replace(tzinfo=timezone.utc)
    else:
        end_aware = end_time.astimezone(timezone.utc)
    now_aware = datetime.now(timezone.utc)
    delta = (end_aware - now_aware).total_seconds()
    return int(delta) if delta > 0 else 0


def is_valid_item_page(soup: BeautifulSoup, html: str) -> bool:
    # 0) obvious walls
    if looks_like_interstitial(soup, html):
        return False

    # 1) ended or invalid list (handled elsewhere)
    try:
        if is_ended_listing(html.lower()):
            return False
    except Exception:
        pass

    # 2) plausible title
    has_title = bool(soup.select_one("#itemTitle") or soup.select_one("h1"))
    if not has_title:
        return False

    # 3) price markers (json-ld or SSR)
    if price_from_jsonld(soup):
        return True

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
            val = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if val and any(ch.isdigit() for ch in val):
                return True

    # 4) thin pages are usually soft-blocks
    if len(html) < 20000:
        return False
    return False


def warn_blocked(domain: str, url: str, r: requests.Response | None, reason: str = ""):
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
