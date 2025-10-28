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

# -------------------------
# Per-host pacing / cooldown
# -------------------------
_LAST_CALL: dict[str, float] = {}
_MIN_GAP = {
    "coindesk.com": 6.0,  # tune per host
}
# Backoff used for 429 Retry-After and our manual cooloffs
_BACKOFF: dict[str, float] = {}   # host -> seconds

def _host(url: str) -> str:
    return urlparse(url).netloc

def _rate_limit(url: str):
    host = _host(url)
    now = time.time()
    gap = _MIN_GAP.get(host, 0.0)
    gap += random.uniform(0.3, 0.9)  # jitter
    gap = max(gap, _BACKOFF.get(host, 0.0))  # include active cooldown

    last = _LAST_CALL.get(host, 0.0)
    wait = last + gap - now
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[host] = time.time()

def _trip_host(host: str, seconds: float):
    # set/extend cooldown; cap to 3 minutes
    seconds = float(max(0.0, min(seconds, 180.0)))
    current = _BACKOFF.get(host, 0.0)
    _BACKOFF[host] = max(current, seconds)

def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],  # keep 429 manual
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

# -------------------------
# GET with 429/503 handling
# -------------------------
def get(
    url: str,
    timeout: int = 25,
    headers: Optional[dict] = None,
    session: requests.Session | None = None,
    **kwargs
) -> requests.Response:
    """
    Thin wrapper around requests.get that:
      - rate-limits per host
      - handles 429 with Retry-After + exponential backoff
      - handles 503 with a brief retry and per-host cooldown
      - passes through arbitrary requests kwargs
    """
    _rate_limit(url)
    s = session or SESSION
    h = DEFAULT_HEADERS.copy()
    if headers:
        h.update(headers)
    if "allow_redirects" not in kwargs:
        kwargs["allow_redirects"] = True

    host = _host(url)

    # First attempt
    r = s.get(url, timeout=timeout, headers=h, **kwargs)

    # --- 429: honour Retry-After and backoff ourselves, then one more attempt
    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        if ra and ra.isdigit():
            sleep_for = int(ra)
        else:
            # exponential-ish growth up to 60s
            prev = _BACKOFF.get(host, 6.0) or 6.0
            sleep_for = min(prev * 2, 60.0)
        _trip_host(host, sleep_for)  # trip cooldown so other calls also wait
        time.sleep(sleep_for + random.uniform(0.3, 0.9))
        _LAST_CALL[host] = time.time()
        r = s.get(url, timeout=timeout, headers=h, **kwargs)

    # --- 503: brief retry and trip host cooldown
    if r.status_code == 503:
        # set a shared cooldown 60–150s to avoid hammering during WAF burps
        _trip_host(host, random.uniform(60.0, 150.0))
        # small sleep + retry once (your Session retry may have already retried 5xx)
        time.sleep(random.uniform(1.0, 2.5))
        _LAST_CALL[host] = time.time()
        r2 = s.get(url, timeout=timeout, headers=h, **kwargs)
        if r2.status_code == 503:
            # keep cooldown; raise for caller to decide (e.g., postpone)
            r2.raise_for_status()
        r = r2

    r.raise_for_status()
    if r.ok:
        # success -> clear any host backoff so normal pacing resumes
        _BACKOFF.pop(host, None)
    return r

# -------------------------
# Generic sleepy helper
# -------------------------
import time as _t, random as _r
_last_call = 0.0
def sleep_rate(base=4.0, jitter=0.35, floor=2.5):
    global _last_call
    now = _t.time()
    spread = base * jitter
    interval = max(floor, base + _r.uniform(-spread, spread))
    wait = (_last_call + interval) - now
    if wait > 0:
        _t.sleep(wait)
    _last_call = _t.time()

# -------------------------
# Heuristics for ended pages
# -------------------------
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

# -------------------------
# HEAD with same semantics
# -------------------------
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

    host = _host(url)

    r = s.head(url, timeout=timeout, headers=h, **kwargs)

    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        if ra and ra.isdigit():
            sleep_for = int(ra)
        else:
            prev = _BACKOFF.get(host, 6.0) or 6.0
            sleep_for = min(prev * 2, 60.0)
        _trip_host(host, sleep_for)
        time.sleep(sleep_for + random.uniform(0.3, 0.9))
        _LAST_CALL[host] = time.time()
        r = s.head(url, timeout=timeout, headers=h, **kwargs)

    if r.status_code == 503:
        _trip_host(host, random.uniform(60.0, 150.0))
        time.sleep(random.uniform(1.0, 2.0))
        _LAST_CALL[host] = time.time()
        r2 = s.head(url, timeout=timeout, headers=h, **kwargs)
        if r2.status_code == 503:
            r2.raise_for_status()
        r = r2

    r.raise_for_status()
    if r.ok:
        _BACKOFF.pop(host, None)
    return r
