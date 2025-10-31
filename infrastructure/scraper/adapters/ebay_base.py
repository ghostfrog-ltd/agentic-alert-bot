from __future__ import annotations

import os
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
from infrastructure.utils.usage_tracker import increment_api_usage  # ✅ track API usage

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _parse_iso_utc(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        ts_fixed = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_fixed)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _secs_left(end_time: Optional[datetime]) -> Optional[int]:
    if not end_time:
        return None
    if end_time.tzinfo is None:
        end_aware = end_time.replace(tzinfo=timezone.utc)
    else:
        end_aware = end_time.astimezone(timezone.utc)
    now_aware = datetime.now(timezone.utc)
    delta = (end_aware - now_aware).total_seconds()
    return int(delta) if delta > 0 else 0


# ----------------------------------------------------------------------
# Base Adapter
# ----------------------------------------------------------------------
class EbayAdapterBase:
    """
    Base class for all eBay niche adapters.
    Subclasses define DOMAIN, CATEGORY_IDS, SALE_TYPE, RETRO_KEYWORDS, etc.
    """

    DOMAIN: str = "ebay-base"
    CATEGORY_IDS: List[int] = []
    SALE_TYPE: str | List[str] = "bin"  # or ["bin", "auction"]
    RETRO_KEYWORDS: List[str] = []
    MODERN_KEYWORDS: List[str] = []

    FLUSH_EVERY = 50
    FLUSH_SECONDS = 3
    MIN_TIME_BATCH = 10
    CATEGORY_PAUSE_SECONDS = 1.5

    def __init__(self):
        self._source_name, self._source_id = self._resolve_source()
        self._batch_buffer: list[dict[str, Any]] = []
        self._ph_buffer: list[tuple[str, int, int]] = []
        self._last_flush = time.time()
        self._hist_api: deque[float] = deque(maxlen=500)
        self._hist_norm: deque[float] = deque(maxlen=500)
        self._hist_db: deque[float] = deque(maxlen=500)
        self._bench_n: int = 0

        # ------------------------------------------------------------------
        # Source resolution
        # ------------------------------------------------------------------
        def _resolve_source(self) -> tuple[str, Optional[int]]:
            """
            Bind this adapter to a row in `sources`.

            Priority:
            1. First try to resolve via sources.domain == self.DOMAIN
            2. Then try sources.name == self.DOMAIN
            3. Then fall back to legacy "ebay-uk" / "ebay"
            4. Finally, give up and return (self.DOMAIN, None)

            Returns:
                (resolved_name, resolved_id_or_None)
            """
            from infrastructure.db.schema import (
                resolve_source_field,
                resolve_source_id,
                connection,
                ensure_utc_session,
            )
            # 1️⃣ Try strict domain match
            try:
                sid = resolve_source_id(self.DOMAIN, use_domain=True)
                if sid is not None:
                    sname = resolve_source_field(self.DOMAIN, "name", use_domain=True)
                    logger.info(
                        f"[{self.DOMAIN}] sources resolved by domain -> "
                        f"name='{sname}', id={sid}"
                    )
                    return (str(sname) if sname else self.DOMAIN, int(sid))
            except Exception as e:
                logger.warning(f"[{self.DOMAIN}] domain lookup failed: {e}")

            # 2️⃣ Try matching by name
            try:
                sname = resolve_source_field(self.DOMAIN, "name", use_domain=False)
                if sname:
                    sid = resolve_source_id(self.DOMAIN, use_domain=False)
                    logger.info(
                        f"[{self.DOMAIN}] sources resolved by name -> "
                        f"name='{sname}', id={sid}"
                    )
                    return (str(sname), int(sid) if sid is not None else None)
            except Exception:
                pass

            # 3️⃣ Legacy fallback so older rows keep working
            for legacy_key in ("ebay-uk", "ebay"):
                try:
                    sname = resolve_source_field(legacy_key, "name", use_domain=False)
                    if sname:
                        sid = resolve_source_id(legacy_key, use_domain=False)
                        logger.info(
                            f"[{self.DOMAIN}] sources resolved via legacy key "
                            f"'{legacy_key}' -> name='{sname}', id={sid}"
                        )
                        return (str(sname), int(sid) if sid is not None else None)
                except Exception:
                    continue

            # 4️⃣ Fallback if nothing matched
            logger.warning(
                f"[{self.DOMAIN}] sources row not found; using fallback '{self.DOMAIN}'"
            )
            return self.DOMAIN, None

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------
    def categorize_title(self, title_lower: str) -> str:
        if any(k in title_lower for k in self.RETRO_KEYWORDS):
            return "retro"
        if any(k in title_lower for k in self.MODERN_KEYWORDS):
            return "modern"
        return "unknown"

    def _model_key_for(self, title: str) -> Optional[str]:
        try:
            mk = normalise_model(title)
        except Exception:
            return None
        if isinstance(mk, tuple):
            mk = mk[0]
        if not mk or (isinstance(mk, str) and not mk.strip()):
            return None
        return mk

    # ------------------------------------------------------------------
    # DB flushing
    # ------------------------------------------------------------------
    def _maybe_flush(self):
        n_list = len(self._batch_buffer)
        n_hist = len(self._ph_buffer)
        n_total = n_list + n_hist
        due_by_size = n_list >= self.FLUSH_EVERY or n_hist >= (self.FLUSH_EVERY * 2)
        due_by_time = (time.time() - self._last_flush) >= self.FLUSH_SECONDS
        if (due_by_size or (due_by_time and n_total >= self.MIN_TIME_BATCH)) and n_total:
            self.flush_batch()

    def flush_batch(self):
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
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _fetch_category_items(
        self,
        token: str,
        category_id: int,
        sale_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Hit eBay Browse API for a category.
        sale_type: "bin", "auction", or None for all.
        """
        base = os.getenv("EBAY_API_BASE", "").rstrip("/")
        if not base:
            logger.error(f"[{self.DOMAIN}] EBAY_API_BASE missing in env")
            return []

        # map our sale_type to eBay filter
        listing_filter = None
        if sale_type == "bin":
            listing_filter = "FIXED_PRICE"
        elif sale_type == "auction":
            listing_filter = "AUCTION"

        qs = [
            f"category_ids={category_id}",
            f"limit={limit}",
            "sort=endingSoon",
        ]
        if listing_filter:
            qs.append(f"filter=listingType:{listing_filter}")

        url = f"{base}/buy/browse/v1/item_summary/search?" + "&".join(qs)

        t_api_start = perf_counter()
        try:
            r = requests.get(url, headers=self._build_headers(token), timeout=10)
        except Exception as e:
            logger.warning(f"[{self.DOMAIN}] API request failed cat={category_id} ({sale_type}): {e}")
            return []

        d_api = perf_counter() - t_api_start
        self._hist_api.append(d_api)

        if r.status_code != 200:
            logger.warning(
                f"[{self.DOMAIN}] API {category_id} ({sale_type}) "
                f"status {r.status_code}: {r.text[:200]}"
            )
            return []

        increment_api_usage("ebay")

        try:
            payload = r.json()
        except Exception as e:
            logger.warning(f"[{self.DOMAIN}] bad JSON cat={category_id} ({sale_type}): {e}")
            return []

        items = payload.get("itemSummaries") or payload.get("item_summary") or []
        if not isinstance(items, list):
            logger.warning(f"[{self.DOMAIN}] unexpected payload for cat={category_id} ({sale_type})")
            return []

        return items

    def _normalize_item(
        self,
        raw: dict[str, Any],
        sale_type: str,
    ) -> Optional[tuple[dict[str, Any], tuple[str, int, int]]]:
        """
        Normalize a single eBay item into internal row shape.
        sale_type = "bin" or "auction" for this batch.
        """
        item_id = raw.get("itemId")
        title = raw.get("title") or ""
        buying_opts = raw.get("buyingOptions") or []
        seller_info = raw.get("seller") or {}
        seller_username = seller_info.get("username")
        price_info = raw.get("price") or {}
        price_value = price_info.get("value")
        web_url = raw.get("itemWebUrl") or raw.get("itemUrl") or ""
        end_time_iso = raw.get("itemEndDate")
        end_time = _parse_iso_utc(end_time_iso)
        time_left_s = _secs_left(end_time)
        bids_count = 0

        # ensure sale_type consistency vs eBay response
        if sale_type == "bin" and buying_opts == ["AUCTION"]:
            return None
        if sale_type == "auction" and "AUCTION" not in buying_opts:
            return None

        title_lower = title.lower()
        category_hint = self.categorize_title(title_lower)
        model_key = self._model_key_for(title)

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
            "end_time": end_time,
            "url": web_url[:1024],
            "detail_url": web_url[:1024],
            "sale_type": sale_type,
            "roi_estimate": None,
            "max_bid": None,
            "notes": category_hint,
            "source_id": self._source_id,
            "model_key": model_key,
            "time_left_s": time_left_s,
            "status": "live",
        }

        ph = (item_id, price_current_int or 0, bids_count)
        return row, ph

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------
    def fetch_listings_api(self, ebay_token: str) -> None:
        sale_types = (
            self.SALE_TYPE
            if isinstance(self.SALE_TYPE, (list, tuple))
            else [self.SALE_TYPE]
        )

        for cat_id in self.CATEGORY_IDS:
            for sale_type in sale_types:
                cat_t0 = perf_counter()

                items = self._fetch_category_items(
                    token=ebay_token,
                    category_id=cat_id,
                    sale_type=sale_type,
                )
                if not items:
                    logger.info(f"[{self.DOMAIN}] cat {cat_id} {sale_type}: 0 items")
                    time.sleep(self.CATEGORY_PAUSE_SECONDS)
                    continue

                norm_start = perf_counter()
                added = 0

                for raw in items:
                    norm = self._normalize_item(raw, sale_type)
                    if not norm:
                        continue
                    row, ph = norm
                    self._batch_buffer.append(row)
                    if row["price_current"]:
                        self._ph_buffer.append(ph)
                    added += 1
                    self._maybe_flush()

                d_norm = perf_counter() - norm_start
                d_cat = perf_counter() - cat_t0
                self._hist_norm.append(d_norm)
                self._hist_db.append(d_cat)
                self._bench_n += 1

                logger.info(
                    f"[{self.DOMAIN}] cat {cat_id} {sale_type}: {added} listings "
                    f"(api+norm total {d_cat:.2f}s)"
                )

                time.sleep(self.CATEGORY_PAUSE_SECONDS)

        self.flush_batch()
