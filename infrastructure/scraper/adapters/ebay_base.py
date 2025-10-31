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
from infrastructure.utils.usage_tracker import increment_api_usage

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
    DOMAIN: str = "ebay-base"

    CATEGORY_IDS: List[int] = []
    SELLER_USERNAME: Optional[str] = None
    FETCH_MODE: str = "category"
    SALE_TYPE: str | List[str] = "bin"
    RETRO_KEYWORDS: List[str] = []
    MODERN_KEYWORDS: List[str] = []

    # Tunables
    FLUSH_EVERY = 50
    FLUSH_SECONDS = 3
    MIN_TIME_BATCH = 10
    CATEGORY_PAUSE_SECONDS = 1.5

    # Politeness
    MAX_PAGES = 5
    MAX_LISTINGS = 200

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
        try:
            sid = resolve_source_id(self.DOMAIN, use_domain=True)
            if sid is not None:
                sname = resolve_source_field(self.DOMAIN, "name", use_domain=True)
                logger.info(f"[{self.DOMAIN}] sources resolved by domain -> name='{sname}', id={sid}")
                return (str(sname) if sname else self.DOMAIN, int(sid))
        except Exception as e:
            logger.warning(f"[{self.DOMAIN}] domain lookup failed: {e}")

        try:
            sname = resolve_source_field(self.DOMAIN, "name", use_domain=False)
            if sname:
                sid = resolve_source_id(self.DOMAIN, use_domain=False)
                logger.info(f"[{self.DOMAIN}] sources resolved by name -> name='{sname}', id={sid}")
                return (str(sname), int(sid) if sid is not None else None)
        except Exception:
            pass

        for legacy_key in ("ebay-uk", "ebay"):
            try:
                sname = resolve_source_field(legacy_key, "name", use_domain=False)
                if sname:
                    sid = resolve_source_id(legacy_key, use_domain=False)
                    logger.info(
                        f"[{self.DOMAIN}] sources resolved via legacy key '{legacy_key}' -> name='{sname}', id={sid}"
                    )
                    return (str(sname), int(sid) if sid is not None else None)
            except Exception:
                continue

        logger.warning(f"[{self.DOMAIN}] sources row not found; using fallback '{self.DOMAIN}'")
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

    def _filter_to_this_seller(self, collected: list[dict]) -> list[dict]:
        expected = getattr(self, "SELLER_USERNAME", None)
        if not expected:
            return collected
        expected_norm = expected.lower().strip()
        filtered: list[dict] = []
        for item in collected:
            seller_norm = (item.get("seller_username") or "").lower().strip()
            if seller_norm == expected_norm:
                filtered.append(item)
            else:
                logger.debug(
                    "[%s] dropped foreign seller '%s' (kept only '%s') for title=%r",
                    getattr(self, "DOMAIN", "?"),
                    seller_norm,
                    expected_norm,
                    item.get("title"),
                )
        return filtered

    def flush_batch(self):
        """
        Write accumulated listing rows + price history rows to DB
        in bulk, then clear buffers.
        """
        if not self._batch_buffer and not self._ph_buffer:
            return

        t0 = perf_counter()
        n_list = len(self._batch_buffer)
        n_hist = len(self._ph_buffer)

        try:
            # ✅ DEDUPE listings on external_id before bulk upsert
            if n_list:
                deduped = {}
                for row in self._batch_buffer:
                    ext_id = row.get("external_id")
                    # last one wins, doesn't matter which because they're same listing
                    deduped[ext_id] = row
                safe_rows = list(deduped.values())

                bulk_upsert_auction_listings(safe_rows)
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
        Hit eBay Browse API for a given category.
        sale_type: "bin", "auction", or None for all.
        """
        base = os.getenv("EBAY_API_BASE", "").rstrip("/")
        if not base:
            logger.error(f"[{self.DOMAIN}] EBAY_API_BASE missing in env")
            return []

        # Map our sale_type to eBay's listingType filter
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

        self._hist_api.append(perf_counter() - t_api_start)
        if r.status_code != 200:
            logger.warning(
                f"[{self.DOMAIN}] API cat={category_id} ({sale_type}) status {r.status_code}: {r.text[:200]}"
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
            logger.warning(
                f"[{self.DOMAIN}] unexpected payload for cat={category_id} ({sale_type})"
            )
            return []

        return items

    def _fetch_seller_items(
        self,
        token: str,
        seller_username: str,
        sale_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        base = os.getenv("EBAY_API_BASE", "").rstrip("/")
        if not base:
            logger.error(f"[{self.DOMAIN}] EBAY_API_BASE missing in env")
            return []

        buying_opt = None
        if sale_type == "bin":
            buying_opt = "FIXED_PRICE"
        elif sale_type == "auction":
            buying_opt = "AUCTION"

        filter_bits = [f"seller_username:{{{seller_username}}}"]
        if buying_opt:
            filter_bits.append(f"buyingOptions:{{{buying_opt}}}")
        filter_param = ",".join(filter_bits)

        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        offset = 0
        page_count = 0
        while True:
            qs = [
                "q=a",
                f"filter={filter_param}",
                f"limit={limit}",
                f"offset={offset}",
                "sort=endingSoon",
            ]
            url = f"{base}/buy/browse/v1/item_summary/search?" + "&".join(qs)

            t_api_start = perf_counter()
            try:
                r = requests.get(url, headers=self._build_headers(token), timeout=10)
            except Exception as e:
                logger.warning(f"[{self.DOMAIN}] API request failed seller={seller_username} ({sale_type}): {e}")
                break

            self._hist_api.append(perf_counter() - t_api_start)
            if r.status_code != 200:
                logger.warning(
                    f"[{self.DOMAIN}] API seller={seller_username} status {r.status_code}: {r.text[:200]}"
                )
                break
            increment_api_usage("ebay")

            try:
                payload = r.json()
            except Exception as e:
                logger.warning(f"[{self.DOMAIN}] bad JSON seller={seller_username}: {e}")
                break

            items = payload.get("itemSummaries") or payload.get("item_summary") or []
            if not isinstance(items, list) or not items:
                break

            new_batch = []
            for it in items:
                iid = it.get("itemId")
                if not iid or iid in seen_ids:
                    continue
                seen_ids.add(iid)
                new_batch.append(it)

            if not new_batch:
                break
            all_items.extend(new_batch)

            page_count += 1
            if page_count >= getattr(self, "MAX_PAGES", 10):
                logger.info(f"[{self.DOMAIN}] seller={seller_username} reached page cap ({page_count})")
                break
            if len(all_items) >= getattr(self, "MAX_LISTINGS", 1000):
                logger.info(f"[{self.DOMAIN}] seller={seller_username} reached MAX_LISTINGS")
                break

            offset += limit
            time.sleep(0.25)
        return all_items

    def _normalize_item(self, raw: dict[str, Any], sale_type: str):
        item_id = raw.get("itemId")
        title = raw.get("title") or ""
        buying_opts = raw.get("buyingOptions") or []
        seller_info = raw.get("seller") or {}
        seller_username = seller_info.get("username")
        price_info = raw.get("price") or {}
        price_value = price_info.get("value")
        web_url = raw.get("itemWebUrl") or raw.get("itemUrl") or ""
        end_time = _parse_iso_utc(raw.get("itemEndDate"))
        time_left_s = _secs_left(end_time)
        bids_count = 0

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
            "seller_username": (seller_username or "").strip()[:255],
        }

        ph = (item_id, price_current_int or 0, bids_count)
        return row, ph

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------
    def fetch_listings_api(self, ebay_token: str) -> None:
        sale_types = (
            self.SALE_TYPE if isinstance(self.SALE_TYPE, (list, tuple)) else [self.SALE_TYPE]
        )

        # -------------------------
        # SELLER MODE
        # -------------------------
        if self.FETCH_MODE == "seller":
            seller = self.SELLER_USERNAME
            if not seller:
                logger.warning(f"[{self.DOMAIN}] seller mode but no SELLER_USERNAME set")
                return

            for sale_type in sale_types:
                t0 = perf_counter()
                items = self._fetch_seller_items(ebay_token, seller, sale_type)
                if not items:
                    logger.info(f"[{self.DOMAIN}] seller {seller} {sale_type}: 0 items")
                    continue

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

                logger.info(
                    f"[{self.DOMAIN}] seller {seller} {sale_type}: {added} listings"
                )

            self.flush_batch()
            return

        # -------------------------
        # CATEGORY MODE
        # -------------------------
        for cat_id in self.CATEGORY_IDS:
            for sale_type in sale_types:
                t0 = perf_counter()
                items = self._fetch_category_items(ebay_token, cat_id, sale_type)
                if not items:
                    logger.info(f"[{self.DOMAIN}] cat {cat_id} {sale_type}: 0 items")
                    time.sleep(self.CATEGORY_PAUSE_SECONDS)
                    continue

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

                logger.info(f"[{self.DOMAIN}] cat {cat_id} {sale_type}: {added} listings")
                time.sleep(self.CATEGORY_PAUSE_SECONDS)

        self.flush_batch()
