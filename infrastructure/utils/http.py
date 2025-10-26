# infrastructure/utils/http.py
import time, random
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    ),
    "X-Ghostfrog-Bot": "true",
}

# per-host pacing
_LAST_CALL = {}
_MIN_GAP = {
    "coindesk.com": 6.0,  # ↑ from 2.5 → 6s (tune as needed)
}

# 429 backoff state
_BACKOFF = {}  # host -> seconds


def _host(url: str) -> str:
    return urlparse(url).netloc


def _rate_limit(url: str):
    host = _host(url)
    now = time.time()
    gap = _MIN_GAP.get(host, 0.0)

    # add jitter so patterns aren't robotic
    gap += random.uniform(0.3, 0.9)

    # include any active backoff (e.g., after 429)
    gap = max(gap, _BACKOFF.get(host, 0.0))

    last = _LAST_CALL.get(host, 0.0)
    wait = last + gap - now
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[host] = time.time()


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],  # remove 429 here; we handle it manually
        allowed_methods=["GET", "HEAD"],
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(DEFAULT_HEADERS)
    return s


SESSION = make_session()


# replace your get(...) with this version:

def get(
        url: str,
        timeout: int = 25,
        headers: Optional[dict] = None,
        session: requests.Session | None = None,
        **kwargs
) -> requests.Response:
    """
    Thin wrapper around requests.get that:
      - rate limits per host
      - handles 429 with backoff
      - passes through arbitrary requests kwargs (e.g., allow_redirects, proxies)
    """
    _rate_limit(url)

    s = session or SESSION
    h = DEFAULT_HEADERS.copy()
    if headers:
        h.update(headers)

    # default to following redirects unless caller overrides
    if "allow_redirects" not in kwargs:
        kwargs["allow_redirects"] = True

    r = s.get(url, timeout=timeout, headers=h, **kwargs)

    if r.status_code == 429:
        host = _host(url)
        ra = r.headers.get("Retry-After")
        if ra:
            try:
                sleep_for = int(ra)
            except ValueError:
                sleep_for = 10
        else:
            prev = _BACKOFF.get(host, 6.0) or 6.0
            sleep_for = min(prev * 2, 60.0)
            _BACKOFF[host] = sleep_for

        time.sleep(sleep_for + random.uniform(0.3, 0.9))
        _LAST_CALL[host] = time.time()

        r = s.get(url, timeout=timeout, headers=h, **kwargs)

    r.raise_for_status()
    if r.ok:
        _BACKOFF.pop(_host(url), None)
    return r


import time, random

_last_call = 0.0


def sleep_rate(base=4.0, jitter=0.35, floor=2.5):
    global _last_call
    now = time.time()
    # enforce min gap since last call
    spread = base * jitter
    interval = max(floor, base + random.uniform(-spread, spread))
    wait = (_last_call + interval) - now
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


ENDED_MARKERS = (
    "This listing was ended", "This listing has ended", "This listing was ended by the seller",
    "Looks like this item has been sold", "The listing you’re looking for has ended",
    "invalid item", "no longer available"
)


def is_ended_listing(html_lower: str) -> bool:
    return any(k.lower() in html_lower for k in ENDED_MARKERS)


def warn_blocked(domain: str, url: str, r: requests.Response | None, reason: str = ""):
    status = getattr(r, "status_code", "NA")
    length = len(getattr(r, "text", "") or "")
    msg = f"[{domain}] invalid/blocked item page: {url} (status={status}, len={length})"
    if reason:
        msg += f" reason={reason}"
    logger.warning(msg)

# optional: add a HEAD helper that mirrors get()
def head(
    url: str,
    timeout: int = 15,
    headers: Optional[dict] = None,
    session: requests.Session | None = None,
    **kwargs
) -> requests.Response:
    _rate_limit(url)
    s = session or SESSION
    h = DEFAULT_HEADERS.copy()
    if headers:
        h.update(headers)
    if "allow_redirects" not in kwargs:
        kwargs["allow_redirects"] = True
    r = s.head(url, timeout=timeout, headers=h, **kwargs)
    if r.status_code == 429:
        host = _host(url)
        ra = r.headers.get("Retry-After")
        sleep_for = int(ra) if ra and ra.isdigit() else min(_BACKOFF.get(host, 6.0) * 2 if _BACKOFF.get(host) else 6.0, 60.0)
        _BACKOFF[host] = sleep_for
        time.sleep(sleep_for + random.uniform(0.3, 0.9))
        _LAST_CALL[host] = time.time()
        r = s.head(url, timeout=timeout, headers=h, **kwargs)
    r.raise_for_status()
    if r.ok:
        _BACKOFF.pop(_host(url), None)
    return r