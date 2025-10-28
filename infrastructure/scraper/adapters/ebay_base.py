# infrastructure/scraper/adapters/ebay_base.py
from __future__ import annotations

import random
import time
from time import perf_counter
from datetime import datetime, timezone
from typing import Optional
from collections import deque

from bs4 import BeautifulSoup

from core.contracts import AuctionAdapter
from infrastructure.db.schema import (
    resolve_source_id,
    resolve_source_field,
)
from infrastructure.utils.logger import get_logger
from infrastructure.utils.scrape_gate import gate_scrape, mark_scraped
from infrastructure.utils.model_key import normalise_model

from .ebay_common import (
    build_session, save_cookies, refetch_get,
    extract_listing_urls_from_doc, extract_end_time,
    extract_price_from_item_page, extract_item_id_from_href,
    canonical_item_url, is_valid_item_page, warn_blocked, looks_like_interstitial,
    secs_left,
)

logger = get_logger(__name__)

SEARCH_BASE = (
    "https://www.ebay.co.uk/sch/i.html?_nkw=&_sacat={cat}"
    "&LH_BIN=1&LH_ItemCondition=3000&LH_Location=1&_ipg=240&_pgn={page}&_sop=10"
)

VANITY_BASE = (
    "https://www.ebay.co.uk/b/{cat}?_ipg=240&_pgn={page}"
    "&LH_BIN=1&LH_ItemCondition=3000&LH_Location=1&_sop=10"
)


class EbayAdapterBase(AuctionAdapter):
    """
    Reusable base for eBay niches. Configure:
      - DOMAIN: str
      - CATEGORY_IDS: list[int]
      - RETRO_KEYWORDS: list[str]
      - MODERN_KEYWORDS: list[str]
      - MAX_PAGES_PER_CAT: int
      - SALE_TYPE: str ("bin" | "auction" | "mixed")
    """
    DOMAIN: str = "ebay-base"
    CATEGORY_IDS: list[int] = []
    RETRO_KEYWORDS: list[str] = []
    MODERN_KEYWORDS: list[str] = []
    MAX_PAGES_PER_CAT: int = 8
    SALE_TYPE: str = "bin"  # you can adjust per niche

    # batching knobs
    FLUSH_EVERY = 50       # items
    FLUSH_SECONDS = 3      # time-based flush guard
    MIN_TIME_BATCH = 10

    def __init__(self):
        self.session = build_session()
        self._source_name, self._source_id = self._resolve_source()

        # buffers
        self._batch_buffer: list[dict] = []       # listings upsert buffer
        self._ph_buffer: list[tuple] = []         # price history buffer: (external_id, price, bids_count)
        self._last_flush = time.time()

        # ---- benchmarking buffers (rolling)
        self._hist_http: deque[float] = deque(maxlen=500)
        self._hist_parse: deque[float] = deque(maxlen=500)
        self._hist_db: deque[float] = deque(maxlen=500)   # per-URL enqueue cost (bulk is timed separately)
        self._bench_n: int = 0

    # ---------- internal: batch flushing
    def _maybe_flush(self):
        n_list = len(self._batch_buffer)
        n_hist = len(self._ph_buffer)
        n_total = n_list + n_hist

        due_by_size = n_list >= self.FLUSH_EVERY or n_hist >= (self.FLUSH_EVERY * 2)
        due_by_time = (time.time() - self._last_flush) >= self.FLUSH_SECONDS

        # Only time-flush if we’ve accumulated at least a modest batch
        if (due_by_size or (due_by_time and n_total >= self.MIN_TIME_BATCH)) and n_total:
            self.flush_batch()

    def flush_batch(self):
        """Bulk upsert listings + bulk append price history in a single go."""
        from time import perf_counter
        from infrastructure.db.schema import bulk_upsert_auction_listings, bulk_append_price_history

        n_list, n_hist = len(self._batch_buffer), len(self._ph_buffer)
        if not n_list and not n_hist:
            return

        t0 = perf_counter()
        try:
            if n_list:
                bulk_upsert_auction_listings(self._batch_buffer)
                self._batch_buffer.clear()
            if n_hist:
                try:
                    bulk_append_price_history(self._ph_buffer)
                except Exception as e:
                    logger.warning(f"[{self.DOMAIN}] bulk price_history failed: {e}")
                finally:
                    self._ph_buffer.clear()
        finally:
            self._last_flush = time.time()
            dt = perf_counter() - t0
            logger.info(f"[{self.DOMAIN}] bulk flush in {dt:.3f}s (listings={n_list}, price_history={n_hist})")

    # ----- source resolution
    def _resolve_source(self) -> tuple[str, int | None]:
        candidates = [self.DOMAIN, "ebay-uk", "ebay"]
        for key in candidates:
            try:
                sname = resolve_source_field(key, "name")
                if sname:
                    sid = resolve_source_id(key)
                    logger.info(f"[{self.DOMAIN}] sources resolved -> name='{sname}', id={sid}")
                    return str(sname), (int(sid) if sid is not None else None)
            except Exception:
                continue
        logger.warning(f"[{self.DOMAIN}] sources row not found; using source='{self.DOMAIN}' (no id)")
        return self.DOMAIN, None

    # ----- optional: title categorization hook
    def categorize_title(self, title_lower: str) -> str:
        if any(k in title_lower for k in self.RETRO_KEYWORDS):
            return "retro"
        if any(k in title_lower for k in self.MODERN_KEYWORDS):
            return "modern"
        return "unknown"

    # ----- adapter API
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
            for cat in self.CATEGORY_IDS:
                page = 1
                consecutive_empty = 0
                while True:
                    serp = SEARCH_BASE.format(cat=cat, page=page)
                    try:
                        time.sleep(random.uniform(3.0, 7.0))  # SERP pacing
                        r = refetch_get(serp, self.session)
                        soup = BeautifulSoup(r.text, "lxml")
                    except Exception as e:
                        logger.warning(f"[{self.DOMAIN}] fetch category {cat} p{page} failed: {e}")
                        break

                    urls_this = extract_listing_urls_from_doc(soup, serp)

                    if not urls_this:
                        # try vanity
                        vanity_url = VANITY_BASE.format(cat=cat, page=page)
                        try:
                            r2 = refetch_get(vanity_url, self.session)
                            soup2 = BeautifulSoup(r2.text, "lxml")
                            urls_this = extract_listing_urls_from_doc(soup2, vanity_url)
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

                    if consecutive_empty >= 2 or page >= self.MAX_PAGES_PER_CAT:
                        break
                    page += 1

            logger.info(f"[{self.DOMAIN}] collected {len(all_urls)} URLs total")
            return all_urls
        finally:
            save_cookies(self.session)
            mark_scraped(meta)

    # ----- benchmarking helpers
    @staticmethod
    def _percentiles(data: deque[float]) -> tuple[float, float, float]:
        """Return (p50, p90, p100) in seconds for a small sample."""
        if not data:
            return (0.0, 0.0, 0.0)
        s = sorted(data)
        n = len(s)
        def at(p: float) -> float:
            if n == 1:
                return s[0]
            idx = max(0, min(n - 1, int(p * (n - 1))))
            return s[idx]
        return (at(0.50), at(0.90), s[-1])

    def _bench_tick(self, d_http: float, d_parse: float, d_db: float, url: str, http_status: Optional[int]):
        self._hist_http.append(d_http)
        self._hist_parse.append(d_parse)
        self._hist_db.append(d_db)
        self._bench_n += 1

        # Per-URL fine detail at DEBUG
        logger.debug(
            f"[{self.DOMAIN}] bench url={url} "
            f"http={d_http:.3f}s parse={d_parse:.3f}s db={d_db:.3f}s status={http_status}"
        )

        # Periodic summary at INFO
        if self._bench_n % 50 == 0:
            p50h, p90h, maxh = self._percentiles(self._hist_http)
            p50p, p90p, maxp = self._percentiles(self._hist_parse)
            p50d, p90d, maxd = self._percentiles(self._hist_db)
            logger.info(
                f"[{self.DOMAIN}] bench n={self._bench_n} "
                f"HTTP p50/p90/max={p50h:.3f}/{p90h:.3f}/{maxh:.3f}s | "
                f"PARSE p50/p90/max={p50p:.3f}/{p90p:.3f}/{maxp:.3f}s | "
                f"DB p50/p90/max={p50d:.3f}/{p90d:.3f}/{maxd:.3f}s"
            )

    def parse_auction(self, url: str) -> bool:
        http_status = None
        t_http_start = perf_counter()
        try:
            try:
                # warm-up / keep-alive touch — counted in HTTP time
                self.session.get("https://www.ebay.co.uk/", timeout=15)
            except Exception:
                pass

            # -------- HTTP (include interstitial re-fetch if needed)
            fetch_url = url if "nordt=true" in url else (url.split("?")[0] + "?nordt=true")
            r = refetch_get(fetch_url, self.session)
            http_status = getattr(r, "status_code", None)
            html = r.text
            soup = BeautifulSoup(html, "lxml")

            html_lower = html.lower()
            from infrastructure.utils.http import is_ended_listing as _is_ended  # avoid cycles
            if _is_ended(html_lower):
                d_http = perf_counter() - t_http_start
                self._bench_tick(d_http, 0.0, 0.0, url, http_status)
                logger.info(f"[{self.DOMAIN}] ended/invalid listing, skipping (ok): {url}")
                save_cookies(self.session)
                return False

            if looks_like_interstitial(soup, html):
                r = refetch_get(url, self.session)
                http_status = getattr(r, "status_code", http_status)
                html = r.text
                soup = BeautifulSoup(html, "lxml")
                html_lower = html.lower()
                if _is_ended(html_lower):
                    d_http = perf_counter() - t_http_start
                    self._bench_tick(d_http, 0.0, 0.0, url, http_status)
                    logger.info(f"[{self.DOMAIN}] ended/invalid listing (retry), skipping (ok): {url}")
                    save_cookies(self.session)
                    return False

            if not is_valid_item_page(soup, html):
                d_http = perf_counter() - t_http_start
                self._bench_tick(d_http, 0.0, 0.0, url, http_status)
                warn_blocked(self.DOMAIN, url, r, reason="no title/price markers")
                save_cookies(self.session)
                return False

            d_http = perf_counter() - t_http_start

            # -------- PARSE
            t_parse_start = perf_counter()
            h1 = soup.select_one("#itemTitle") or soup.select_one("h1")
            title = (
                h1.get_text(" ", strip=True)
                if h1 else (soup.title.get_text(" ", strip=True) if soup.title else "")
            )
            title = title.replace("Details about  ", "").strip() or ""
            title_lower = title.lower()

            # model + price
            model_key = normalise_model(title)
            pc = extract_price_from_item_page(soup, html)
            price_current = int((pc[0] if isinstance(pc, tuple) else pc) or 0)

            external_id = extract_item_id_from_href(url)
            if not external_id:
                raise ValueError("could not extract item id")

            end_time = extract_end_time(soup, html)
            time_left_s = secs_left(end_time)
            url_clean = url.split("?")[0][:1024]
            detail_url = canonical_item_url(external_id)
            category_hint = self.categorize_title(title_lower)
            d_parse = perf_counter() - t_parse_start

            # -------- enqueue for BULK DB
            t_db_enqueue = perf_counter()
            self._batch_buffer.append({
                "source": self._source_name,
                "external_id": external_id,
                "title": title[:255],
                "price_current": price_current,
                "bids_count": 0,
                "end_time": end_time,  # naive UTC (per helper)
                "url": url_clean,
                "detail_url": detail_url,
                "sale_type": self.SALE_TYPE,
                "roi_estimate": None,
                "max_bid": None,
                "notes": category_hint,
                "source_id": self._source_id,
                "model_key": model_key,
                "time_left_s": time_left_s,
                "status": "live",
            })
            if price_current and price_current > 0:
                # (external_id, price, bids_count)
                self._ph_buffer.append((external_id, price_current, 0))
            d_db = perf_counter() - t_db_enqueue  # enqueue cost only

            # occasional bulk flush on size/time
            self._maybe_flush()

            # ---- record benchmark (enqueue-time DB only; bulk cost is logged in flush_batch)
            self._bench_tick(d_http, d_parse, d_db, url, http_status)

            save_cookies(self.session)
            return True

        except Exception as _e:
            d_http = perf_counter() - t_http_start
            self._bench_tick(d_http, 0.0, 0.0, url, http_status)
            logger.warning(f"[{self.DOMAIN}] parse_auction failed: {_e}")
            save_cookies(self.session)
            return False
