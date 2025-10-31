from __future__ import annotations

import os
import random
import time
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import (
    connection,                         # single-threaded main-thread writes only
    get_open_auctions_ending_before,    # read helper
    get_recent_max_price,
)

logger = get_logger(__name__)

# ===========================
# Knobs & env flags
# ===========================
GRACE_WINDOW = timedelta(minutes=5)
CLOSE_DELAY = timedelta(seconds=90)
MAX_PER_HEARTBEAT = 30

STALE_UNENDED_FALLBACK = timedelta(hours=12)

PASS_BUDGET_SECONDS = float(os.getenv("CLOSE_PASS_BUDGET_S", "25"))
CLOSE_DISABLE = os.getenv("CLOSE_DISABLE", "0") in ("1", "true", "True")

# HTTP tuning
CONNECT_TIMEOUT_S = 2.0
READ_TIMEOUT_S = 3.0
TOTAL_TIMEOUT = (CONNECT_TIMEOUT_S, READ_TIMEOUT_S)

# 🔻 Make it gentler by default
MAX_WORKERS = int(os.getenv("CLOSE_MAX_WORKERS", "1"))  # was "4"
MIN_GAP_SECONDS = float(os.getenv("CLOSE_MIN_GAP_S", "5.8"))
GLOBAL_COOLDOWN_S = float(os.getenv("CLOSE_COOLDOWN_S", "34"))

PROBE_MAX_BYTES = 65536

_RETRY = Retry(
    total=1,
    connect=1,
    read=0,
    status=1,
    backoff_factor=0.2,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET"]),
)

_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=_RETRY)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)

_COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

END_MARKERS = (
    "this listing has ended",
    "bidding has ended",
    "listing ended",
    "item has ended",
    "item is no longer available",
)
SOLD_MARKERS = ("sold", "winning bid", "winner")
ANTI_BOT_SNIPPETS = ("to ensure this is not a bot", "/challenge?ctx", "captcha")

# ===========================
# Types
# ===========================
@dataclass
class Auction:
    id: int
    url: str
    end_time: Optional[datetime] = None


def _as_auction(row: Any) -> Auction:
    """
    Normalise whatever shape DB row/helper returns into an Auction.
    We support both legacy scrape rows and API-ingested rows.

    Expected fields from DB (varies by codepath):
    - id / auction_id
    - detail_url / url / web_url  (must be a public listing URL we can probe)
    - end_time (UTC timestamptz)
    """
    if isinstance(row, Auction):
        return row

    # dict-like row
    if isinstance(row, dict):
        return Auction(
            id=(
                row.get("id")
                or row.get("auction_id")
            ),
            url=(
                row.get("detail_url")
                or row.get("url")
                or row.get("web_url")      # <- allow API field name
            ),
            end_time=row.get("end_time"),
        )

    # fallback: attribute-style row
    return Auction(
        id=getattr(row, "id", None) or getattr(row, "auction_id", None),
        url=(
            getattr(row, "detail_url", None)
            or getattr(row, "url", None)
            or getattr(row, "web_url", None)  # <- allow API field name
        ),
        end_time=getattr(row, "end_time", None),
    )


@dataclass
class CloseResult:
    id: int
    action: str
    status: Optional[str]
    final_price: Optional[int]
    confidence: Optional[str] = None  # 'page' | 'history' | None

# ===========================
# Global pacing state
# ===========================
_last_fetch_ts: float = 0.0
_cooldown_until: float = 0.0


def _pacing_sleep() -> None:
    """Respect global cooldown and min inter-request gap."""
    global _last_fetch_ts, _cooldown_until
    now = time.perf_counter()

    # Cooldown (after anti-bot/429)
    if now < _cooldown_until:
        time.sleep(_cooldown_until - now)
        now = time.perf_counter()

    # Min gap between requests
    since_last = now - _last_fetch_ts
    gap = max(0.0, MIN_GAP_SECONDS - since_last)
    if gap > 0:
        time.sleep(gap)

    _last_fetch_ts = time.perf_counter()

# ===========================
# Helpers
# ===========================
_PRICE_RX = re.compile(
    r"(?:£|\bGBP[^\d]{0,3})(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)


def _extract_final_price(snippet: str) -> Optional[int]:
    if not snippet:
        return None
    m = _PRICE_RX.search(snippet)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return int(round(float(raw)))
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _probe_get(url: str) -> tuple[int | None, str | None, bool]:
    """Streaming GET (first ~64KB). Returns (status, snippet, anti_bot)."""
    try:
        with _SESSION.get(
            url,
            headers=_COMMON_HEADERS,
            timeout=TOTAL_TIMEOUT,
            stream=True,
            allow_redirects=True,
        ) as r:
            sc = r.status_code
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=4096):
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) >= PROBE_MAX_BYTES:
                    break
            try:
                text = buf.decode(r.encoding or "utf-8", errors="ignore")
            except Exception:
                text = buf.decode("utf-8", errors="ignore")
            low = (text or "").lower()
            anti = any(s in low for s in ANTI_BOT_SNIPPETS)
            return sc, text, anti
    except requests.RequestException:
        return None, None, False


def _parse_status(snippet: str) -> dict:
    t = (snippet or "").lower()
    ended = any(k in t for k in END_MARKERS) or ("ended" in t and "listing" in t)
    sold = any(k in t for k in SOLD_MARKERS) or ("winning bid" in t)
    return {"ended": ended, "sold": sold}

# ===========================
# DB bulk operations
# ===========================
def _mark_ending_soon_bulk(cutoff: datetime) -> int:
    """
    Mark anything ending soon as 'ending_soon'.
    We now include 'api_active' in the allowed statuses so that rows created
    by the API ingester (instead of the legacy HTML scraper) still get picked up.
    """
    with connection, connection.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '3000ms'")
        cur.execute(
            """
            UPDATE auction_listings
               SET status = 'ending_soon'
             WHERE end_time <= %s
               AND status IN (
                    'OPEN',
                    'ending_soon',
                    'live',
                    'active',
                    'retry_soon',
                    'api_active'      -- < added for API-ingested rows
               )
            """,
            (cutoff,),
        )
        return cur.rowcount


def _apply_results_bulk(results: list[CloseResult]) -> None:
    if not results:
        return

    to_finalize_sold = [
        (r.final_price, r.id)
        for r in results
        if r.action == "FINALIZE" and r.status == "sold" and r.final_price is not None
    ]
    to_finalize_unsold = [
        (r.id,) for r in results if r.action == "FINALIZE" and r.status == "unsold"
    ]
    to_mark_live = [
        (r.id,) for r in results if r.action == "MARK" and r.status == "live"
    ]
    to_mark_retry = [
        (r.id,) for r in results if r.action == "MARK" and r.status == "retry_soon"
    ]

    with connection, connection.cursor() as cur:
        if to_finalize_sold:
            cur.executemany(
                """
                UPDATE auction_listings
                   SET status='sold',
                       final_price=%s,
                       last_seen=(now() AT TIME ZONE 'utc')
                 WHERE id=%s
                """,
                to_finalize_sold,
            )
        if to_finalize_unsold:
            cur.executemany(
                """
                UPDATE auction_listings
                   SET status='unsold',
                       final_price=NULL,
                       last_seen=(now() AT TIME ZONE 'utc')
                 WHERE id=%s
                """,
                to_finalize_unsold,
            )
        if to_mark_live:
            cur.executemany(
                "UPDATE auction_listings SET status='live' WHERE id=%s",
                to_mark_live,
            )
        if to_mark_retry:
            cur.executemany(
                "UPDATE auction_listings SET status='retry_soon' WHERE id=%s",
                to_mark_retry,
            )

    logger.info(
        "[close] batch done: finalized_sold=%d, finalized_unsold=%d, live=%d, retry=%d",
        len(to_finalize_sold),
        len(to_finalize_unsold),
        len(to_mark_live),
        len(to_mark_retry),
    )

# ===========================
# Worker
# ===========================
def _close_one(a: Auction, idx: int, total: int, deadline: float) -> CloseResult:
    if time.perf_counter() > deadline:
        return CloseResult(a.id, "MARK", "retry_soon", None)

    logger.info(f"[close] ({idx}/{total}) Processing auction {a.id}")

    # 🐢 slower pre-request jitter
    time.sleep(random.uniform(0.55, 1.15))
    _pacing_sleep()

    sc, snippet, antibot = _probe_get(a.url)

    # Handle anti-bot or rate limiting
    if antibot or sc == 429:
        global _cooldown_until
        _cooldown_until = time.perf_counter() + GLOBAL_COOLDOWN_S + random.uniform(1.0, 3.0)
        logger.warning(
            "[close] anti-bot/429 detected — cooling down for ~%.1fs",
            GLOBAL_COOLDOWN_S,
        )
        return CloseResult(a.id, "MARK", "retry_soon", None)

    if sc is None:
        return CloseResult(a.id, "MARK", "retry_soon", None)

    if sc in (404, 410):
        return CloseResult(a.id, "FINALIZE", "unsold", None)

    if not (200 <= sc < 400) or not snippet:
        return CloseResult(a.id, "MARK", "retry_soon", None)

    info = _parse_status(snippet)
    if info["ended"]:
        fp = _extract_final_price(snippet)
        if fp is None:
            recent = get_recent_max_price(a.id, window_minutes=360)
            if recent is not None:
                try:
                    fp = int(round(float(recent)))
                except Exception:
                    fp = None

        if fp is not None:
            return CloseResult(a.id, "FINALIZE", "sold", fp)
        else:
            return CloseResult(a.id, "FINALIZE", "unsold", None)

    return CloseResult(a.id, "MARK", "live", None)

# ===========================
# Batch processor
# ===========================
def _process_due_auctions(due: Sequence[Auction], budget_seconds: float) -> None:
    if not due:
        logger.info("[close] no due auctions")
        return

    try:
        due = sorted(
            due,
            key=lambda a: a.end_time or datetime.max.replace(tzinfo=timezone.utc),
        )
    except Exception:
        pass

    total = len(due)
    if total > MAX_PER_HEARTBEAT:
        logger.info(
            f"[close] Limiting close batch to {MAX_PER_HEARTBEAT}/{total} auctions this pass"
        )
        due = due[:MAX_PER_HEARTBEAT]
        total = len(due)
    else:
        logger.info(f"[close] Processing {total} due auctions")

    deadline = time.perf_counter() + budget_seconds
    results: list[CloseResult] = []

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [
            ex.submit(_close_one, a, i + 1, total, deadline)
            for i, a in enumerate(due)
        ]
        for fut in as_completed(futs):
            try:
                r = fut.result()
                results.append(r)
            except Exception as e:
                logger.warning(f"[close] worker error: {e}")

    _apply_results_bulk(results)

# ===========================
# Public entry point
# ===========================
def tick() -> None:
    try:
        logger.info("[close] ===== START CLOSE PASS =====")
        t0 = time.perf_counter()
        now = _now_utc()

        if CLOSE_DISABLE:
            logger.info("[close] CLOSE_DISABLE=1 — skipping close pass")
            return

        # Stage 1: ENDING_SOON
        try:
            cutoff = now + GRACE_WINDOW
            rows = get_open_auctions_ending_before(cutoff) or []
            logger.info(
                f"[close] ENDING_SOON candidates: {len(rows)} (cutoff={cutoff.isoformat()})"
            )
            updated = _mark_ending_soon_bulk(cutoff)
            logger.info(f"[close] ENDING_SOON bulk updated: {updated}")
            logger.info(
                f"[close] ENDING_SOON pass took {time.perf_counter() - t0:.2f}s"
            )
        except Exception as e:
            logger.error(f"[close] ENDING_SOON pass failed: {e}")

        # Stage 2: Due
        try:
            due_cutoff = now + CLOSE_DELAY
            rows = get_open_auctions_ending_before(due_cutoff) or []
            due = [_as_auction(r) for r in rows]
            logger.info(
                f"[close] Due candidates: {len(due)} (cutoff={due_cutoff.isoformat()})"
            )
            _process_due_auctions(due, PASS_BUDGET_SECONDS)
        except Exception as e:
            logger.error(f"[close] due auctions pass failed: {e}")

        # Stage 3: Stale fallback
        try:
            stale_rows = get_open_auctions_ending_before(
                now - STALE_UNENDED_FALLBACK
            ) or []
            stale = [_as_auction(r) for r in stale_rows]
            logger.info(f"[close] Stale candidates: {len(stale)}")
            logger.info("[close] Stale pass finalized: 0")
        except Exception as e:
            logger.error(f"[close] stale fallback pass failed: {e}")

        logger.info(f"[close] PASS DONE in {time.perf_counter() - t0:.2f}s")
    except Exception as e:
        logger.error(f"[close] FATAL in tick(): {e}")
