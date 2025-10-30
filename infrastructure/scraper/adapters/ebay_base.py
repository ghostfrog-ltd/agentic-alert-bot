from __future__ import annotations

import time
from time import perf_counter
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from collections import deque

import requests

from infrastructure.utils.logger import get_logger
from infrastructure.utils.model_key import normalise_model
from infrastructure.db.schema import (
    resolve_source_id,
    resolve_source_field,
    bulk_upsert_auction_listings,
    bulk_append_price_history,
)

logger = get_logger(__name__)


def _parse_iso_utc(ts: Optional[str]) -> Optional[datetime]:
    """
    Convert an ISO8601 string like '2025-10-30T18:22:00.000Z' into aware UTC datetime.
    Return None if ts is falsy or invalid.
    """
    if not ts:
        return None
    try:
        # Make sure Z -> +00:00 so fromisoformat is happy.
        ts_fixed = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_fixed)
        # Force UTC awareness
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _secs_left(end_time: Optional[datetime]) -> Optional[int]:
    """
    Return seconds remaining until end_time (aware UTC). If already passed, return 0.
    """
    if not end_time:
        return None
    if end_time.tzinfo is None:
        end_aware = end_time.replace(tzinfo=timezone.utc)
    else:
        end_aware = end_time.astimezone(timezone.utc)
    now_aware = datetime.now(timezone.utc)
    delta = (end_aware - now_aware).total_seconds()
    return int(delta) if delta > 0 else 0


class EbayAdapterBase:
    """
    Base class for all eBay niche adapters.

    Subclasses MUST define:
      - DOMAIN: str  (ex: "ebay-consoles")
      - CATEGORY_IDS: list[int]  (list of eBay category IDs to watch)
      - SALE_TYPE: str           ("bin", "auction", "mixed")
      - RETRO_KEYWORDS: list[str]
      - MODERN_KEYWORDS: list[str]

    This new version is API-first. We do NOT pretend to be a browser.
    We use the eBay Browse API via our OAuth application token.

    Pipeline:
      fetch_listings_api(token) ->
        for each category:
          call Browse API
          normalize each item into our internal row dict
          buffer rows
        flush_batch() to Postgres
    """

    DOMAIN: str = "ebay-base"
    CATEGORY_IDS: List[int] = []
    SALE_TYPE: str = "bin"  # "bin", "auction", "mixed"
    RETRO_KEYWORDS: List[str] = []
    MODERN_KEYWORDS: List[str] = []

    # DB batching knobs
    FLUSH_EVERY = 50         # flush after N new listing rows or price records
    FLUSH_SECONDS = 3        # flush if this many seconds pass AND we have at least MIN_TIME_BATCH
    MIN_TIME_BATCH = 10      # don't time-flush if we're only holding a tiny handful

    # Basic polite pacing between category pulls
    CATEGORY_PAUSE_SECONDS = 1.5

    def __init__(self):
        # Resolve data source row (source_id, etc.) so we can stamp listings
        self._source_name, self._source_id = self._resolve_source()

        # Buffers that eventually get persisted in flush_batch()
        self._batch_buffer: list[dict[str, Any]] = []   # main "auction_listings"-style rows
        self._ph_buffer: list[tuple[str, int, int]] = []  # (external_id, price_current, bids_count)

        # For timing-based flushes
        self._last_flush = time.time()

        # rolling perf stats (optional, useful for tuning/logging)
        self._hist_api: deque[float] = deque(maxlen=500)
        self._hist_norm: deque[float] = deque(maxlen=500)
        self._hist_db: deque[float] = deque(maxlen=500)
        self._bench_n: int = 0

    # ------------------------------------------------------------------
    # Source resolution
    # ------------------------------------------------------------------
    def _resolve_source(self) -> tuple[str, Optional[int]]:
        """
        Try to match this adapter's DOMAIN (or common fallbacks) to a row
        in your 'sources' table so we can store source_id.
        """
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

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------
    def categorize_title(self, title_lower: str) -> str:
        """
        Simple niche categorization. Subclasses can override or extend.
        """
        if any(k in title_lower for k in self.RETRO_KEYWORDS):
            return "retro"
        if any(k in title_lower for k in self.MODERN_KEYWORDS):
            return "modern"
        return "unknown"

    def _model_key_for(self, title: str) -> Optional[str]:
        """
        Your simple model classifier. If normalise_model returns tuple, we take first element.
        If it's empty-ish, return None.
        """
        try:
            mk = normalise_model(title)
        except Exception:
            return None

        if isinstance(mk, tuple):
            mk = mk[0]

        if not mk:
            return None
        if isinstance(mk, str) and mk.strip() == "":
            return None
        return mk  # e.g. "ps5", "vehicle", etc.

    # ------------------------------------------------------------------
    # Internal batch flushing
    # ------------------------------------------------------------------
    def _maybe_flush(self):
        """
        Decide if we should flush to DB based on batch size or elapsed time.
        """
        n_list = len(self._batch_buffer)
        n_hist = len(self._ph_buffer)
        n_total = n_list + n_hist

        due_by_size = n_list >= self.FLUSH_EVERY or n_hist >= (self.FLUSH_EVERY * 2)
        due_by_time = (time.time() - self._last_flush) >= self.FLUSH_SECONDS

        if (due_by_size or (due_by_time and n_total >= self.MIN_TIME_BATCH)) and n_total:
            self.flush_batch()

    def flush_batch(self):
        """
        Bulk-persist both listing snapshots and price history.
        Keeps the same contract you already had in your previous base.
        """
        if not self._batch_buffer and not self._ph_buffer:
            return

        t0 = perf_counter()
        n_list = len(self._batch_buffer)
        n_hist = len(self._ph_buffer)

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
            dt = perf_counter() - t0
            self._last_flush = time.time()
            logger.info(
                f"[{self.DOMAIN}] bulk flush in {dt:.3f}s "
                f"(listings={n_list}, price_history={n_hist})"
            )

    # ------------------------------------------------------------------
    # eBay API helpers
    # ------------------------------------------------------------------
    def _build_headers(self, token: str) -> dict[str, str]:
        """
        Headers for eBay Browse API calls.
        """
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _fetch_category_items(
        self,
        token: str,
        category_id: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Hit eBay's Browse API (production: api.ebay.com, sandbox: api.sandbox.ebay.com)
        to retrieve active listings for a category.

        We assume:
        - Your .env has EBAY_API_BASE, like https://api.sandbox.ebay.com or https://api.ebay.com
        - Heartbeat already ensured token is valid.
        """
        import os
        base = os.getenv("EBAY_API_BASE", "").rstrip("/")
        if not base:
            logger.error(f"[{self.DOMAIN}] EBAY_API_BASE missing in env")
            return []

        # Browse API: item_summary/search
        # We'll ask by category_id. You can enrich with filters later.
        url = (
            f"{base}/buy/browse/v1/item_summary/search"
            f"?category_ids={category_id}"
            f"&limit={limit}"
            f"&sort=endingSoon"
        )

        t_api_start = perf_counter()
        try:
            r = requests.get(url, headers=self._build_headers(token), timeout=10)
        except Exception as e:
            logger.warning(f"[{self.DOMAIN}] API request failed cat={category_id}: {e}")
            return []

        d_api = perf_counter() - t_api_start
        self._hist_api.append(d_api)

        if r.status_code != 200:
            logger.warning(
                f"[{self.DOMAIN}] API {category_id} status {r.status_code}: {r.text[:200]}"
            )
            return []

        try:
            payload = r.json()
        except Exception as e:
            logger.warning(f"[{self.DOMAIN}] bad JSON cat={category_id}: {e}")
            return []

        items = payload.get("itemSummaries") or payload.get("item_summary") or []
        if not isinstance(items, list):
            logger.warning(f"[{self.DOMAIN}] unexpected payload for cat={category_id}")
            return []

        return items

    def _normalize_item(
        self,
        raw: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """
        Turn an eBay API item into our internal row shape.
        This mirrors the dicts you used to append to _batch_buffer in parse_auction().
        """

        # Required fields we expect from Browse API:
        item_id = raw.get("itemId")
        title = raw.get("title") or ""
        buying_opts = raw.get("buyingOptions") or []
        seller_info = raw.get("seller") or {}
        seller_username = seller_info.get("username")
        price_info = raw.get("price") or {}
        price_value = price_info.get("value")
        currency = price_info.get("currency")
        web_url = raw.get("itemWebUrl") or raw.get("itemUrl") or ""
        # Auction end info:
        end_time_iso = raw.get("itemEndDate")  # present for auctions/endingSoon
        end_time = _parse_iso_utc(end_time_iso)
        time_left_s = _secs_left(end_time)

        # We don't always get bids in Browse summary. If not available, default 0.
        # In future we could pull more detail with a per-item call.
        bids_count = 0

        # Filter by sale type if the subclass wants to force BIN or AUCTION
        # buyingOptions example: ["FIXED_PRICE", "BEST_OFFER"] or ["AUCTION"]
        if self.SALE_TYPE == "bin":
            # skip if this is strictly auction-only
            if buying_opts == ["AUCTION"]:
                return None
        elif self.SALE_TYPE == "auction":
            # skip if it's only fixed price
            if "AUCTION" not in buying_opts:
                return None
        # if "mixed": allow anything

        # basic categorisation for notes
        title_lower = title.lower()
        category_hint = self.categorize_title(title_lower)

        # model_key classification (PS5 / vehicle / etc.)
        model_key = self._model_key_for(title)

        # We store price as int or numeric. You were using int(price_current).
        # price_value from eBay is string or number. We'll try to coerce to int(rounded).
        price_current_int = None
        if price_value is not None:
            try:
                price_current_int = int(round(float(str(price_value))))
            except Exception:
                price_current_int = None

        row = {
            "source": self._source_name,
            "external_id": item_id,
            "title": title[:255],
            "price_current": price_current_int or 0,
            "bids_count": bids_count,
            "end_time": end_time,          # aware UTC datetime or None
            "url": web_url[:1024],
            "detail_url": web_url[:1024],
            "sale_type": self.SALE_TYPE,
            "roi_estimate": None,
            "max_bid": None,
            "notes": category_hint,
            "source_id": self._source_id,
            "model_key": model_key,
            "time_left_s": time_left_s,
            "status": "live",
            # Optional extras if you want to persist them later:
            # "currency": currency,
            # "buying_options": ",".join(buying_opts) if buying_opts else None,
            # "seller_username": seller_username,
        }

        # For price history buffer
        ph = (item_id, price_current_int or 0, bids_count)

        return row, ph

    # ------------------------------------------------------------------
    # Public: main entry to pull + persist
    # ------------------------------------------------------------------
    def fetch_listings_api(self, ebay_token: str) -> None:
        """
        This is the new "scrape" for API mode.
        - Loops over CATEGORY_IDS
        - Calls eBay Browse API
        - Normalizes each item
        - Buffers each into _batch_buffer / _ph_buffer
        - Flushes to DB in batches

        Heartbeat should call this once per tick for this adapter.
        """

        for cat_id in self.CATEGORY_IDS:
            cat_t0 = perf_counter()

            items = self._fetch_category_items(token=ebay_token, category_id=cat_id)
            if not items:
                logger.info(f"[{self.DOMAIN}] cat {cat_id}: 0 items from API")
                # polite pause between categories anyway
                time.sleep(self.CATEGORY_PAUSE_SECONDS)
                continue

            norm_start = perf_counter()
            added = 0
            for raw in items:
                norm = self._normalize_item(raw)
                if not norm:
                    continue
                row, ph = norm

                # enqueue for DB flush
                self._batch_buffer.append(row)
                # only log price history if we have a nonzero price
                if row["price_current"]:
                    self._ph_buffer.append(ph)

                added += 1
                self._maybe_flush()

            d_norm = perf_counter() - norm_start
            d_cat = perf_counter() - cat_t0

            # record perf stats (API, normalize+enqueue, db-enqueue cost)
            self._hist_norm.append(d_norm)
            self._hist_db.append(d_cat)
            self._bench_n += 1

            logger.info(
                f"[{self.DOMAIN}] cat {cat_id}: {added} listings "
                f"(api+norm total {d_cat:.2f}s)"
            )

            # polite gap before next category, so we don't look aggressive
            time.sleep(self.CATEGORY_PAUSE_SECONDS)

        # final flush after all categories
        self.flush_batch()
