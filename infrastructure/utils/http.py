# infrastructure/utils/http.py
import time, random
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse

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
    "coindesk.com": 6.0,   # ↑ from 2.5 → 6s (tune as needed)
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

def get(url: str, timeout: int = 25, headers: Optional[dict] = None) -> requests.Response:
    _rate_limit(url)
    h = DEFAULT_HEADERS.copy()
    if headers:
        h.update(headers)

    r = SESSION.get(url, timeout=timeout, headers=h)

    if r.status_code == 429:
        # honor Retry-After if present; else exponential per-host backoff
        host = _host(url)
        ra = r.headers.get("Retry-After")
        if ra:
            try:
                sleep_for = int(ra)
            except ValueError:
                sleep_for = 10
        else:
            prev = _BACKOFF.get(host, 6.0) or 6.0
            sleep_for = min(prev * 2, 60.0)  # cap at 60s
            _BACKOFF[host] = sleep_for

        time.sleep(sleep_for + random.uniform(0.3, 0.9))
        _LAST_CALL[host] = time.time()

        r = SESSION.get(url, timeout=timeout, headers=h)

    r.raise_for_status()
    # success → reset backoff for this host
    if r.ok:
        _BACKOFF.pop(_host(url), None)
    return r
