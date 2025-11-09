from __future__ import annotations

import os
import time
from time import perf_counter
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from collections import deque
from psycopg2.extras import execute_values

import requests

from infrastructure.utils.logger import get_logger
from infrastructure.utils.model_key import normalise_model
from infrastructure.db.schema import (
    resolve_source_id,
    resolve_source_field,
    get_connection,
    to_aware_utc,
    ensure_utc_session
)
from infrastructure.utils.usage_tracker import increment_api_usage

logger = get_logger(__name__)

connection = get_connection()

# -----------------
# Constants / defaults
# -----------------
DEFAULT_Q = "a"  # minimal harmless primary query to satisfy Browse's rule


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


def _iso_z(dt: datetime) -> str:
    # ISO8601 with Z suffix
    return dt.replace(microsecond=0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def is_configurable_item(raw: dict[str, Any]) -> bool:
    """
    Detect multi-variation / configurable-style listings so we can skip them entirely.

    Handles both:
    - Legacy/Finding style: isMultiVariationListing
    - Browse API style: itemGroupType == 'SELLER_DEFINED_VARIATIONS'
    - Fallback: presence of variation-ish blocks
    """
    # Finding API style: {"isMultiVariationListing": "true"} or ["true"] or {"__value__": "true"}
    val = raw.get("isMultiVariationListing")
    if val is not None:
        if isinstance(val, (list, tuple)):
            if val:
                val = val[0]
        if isinstance(val, dict) and "__value__" in val:
            val = val.get("__value__")
        if isinstance(val, bool):
            if val:
                return True
        elif isinstance(val, str) and val.lower() == "true":
            return True

    # Browse API style group listings
    group_type = raw.get("itemGroupType")
    if isinstance(group_type, str):
        gt = group_type.strip().upper()
        if gt in {"SELLER_DEFINED_VARIATIONS", "GROUP", "MULTI_SKU"}:
            return True

    # Very defensive fallback
    if "variations" in raw or "variation" in raw:
        return True

    return False


def get_item_id(raw: dict[str, Any]) -> str:
    iid = raw.get("itemId") or raw.get("item_id") or raw.get("legacyItemId")
    return str(iid) if iid is not None else "unknown"


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
        if not self._batch_buffer and not self._ph_buffer:
            return

        t0 = perf_counter()
        n_list = len(self._batch_buffer)
        n_hist = len(self._ph_buffer)

        try:
            if n_list:
                deduped = {}
                for row in self._batch_buffer:
                    ext_id = row.get("external_id")
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
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        }

    def _fetch_category_items(
            self,
            token: str,
            category_id: int,
            sale_type: str | None = None,
            limit: int = 50,
    ) -> list[dict[str, Any]]:
        base = os.getenv("EBAY_API_BASE", "").rstrip("/")
        if not base:
            logger.error(f"[{self.DOMAIN}] EBAY_API_BASE missing in env")
            return []

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
        """
        Fetch ONLY items from a specific seller using the Browse API.
        - Uses filter=sellers:{username} (correct filter)
        - Bounds by itemEndDate window to avoid 12023 "too large" errors
        - Includes a tiny q='a' solely to satisfy Browse's required primary param
        """
        base = os.getenv("EBAY_API_BASE", "").rstrip("/")
        if not base:
            logger.error(f"[{self.DOMAIN}] EBAY_API_BASE missing in env")
            return []

        # Map "bin"/"auction" to Browse API buyingOptions
        buying_opt = None
        if sale_type == "bin":
            buying_opt = "FIXED_PRICE"
        elif sale_type == "auction":
            buying_opt = "AUCTION"

        # Try wider window first, then tighten if eBay complains
        window_days_options = [60, 14, 7]

        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for days in window_days_options:
            start_iso = _iso_z(datetime.now(timezone.utc))
            end_iso = _iso_z(datetime.now(timezone.utc) + timedelta(days=days))

            filter_bits = [
                f"sellers:{{{seller_username}}}",
                f"itemEndDate:[{start_iso}..{end_iso}]",
                "itemLocationCountry:GB",
            ]
            if buying_opt:
                filter_bits.append(f"buyingOptions:{{{buying_opt}}}")
            filter_param = ",".join(filter_bits)

            offset = 0
            page_count = 0

            while True:
                qs = [
                    f"q={DEFAULT_Q}",  # required by Browse; filters do the real work
                    f"filter={filter_param}",  # seller + window + (optional) buyingOptions
                    f"limit={limit}",
                    f"offset={offset}",
                    "sort=endingSoon",
                ]
                url = f"{base}/buy/browse/v1/item_summary/search?" + "&".join(qs)

                t_api_start = perf_counter()
                try:
                    r = requests.get(url, headers=self._build_headers(token), timeout=10)
                except Exception as e:
                    logger.warning(
                        f"[{self.DOMAIN}] API request failed seller={seller_username} ({sale_type}): {e}"
                    )
                    break

                self._hist_api.append(perf_counter() - t_api_start)

                if r.status_code != 200:
                    txt = (r.text or "").lower()
                    logger.warning(
                        f"[{self.DOMAIN}] API seller={seller_username} status {r.status_code}: {r.text[:200]}"
                    )
                    # If "too large", break to next (tighter) window; otherwise bail
                    if r.status_code == 400 and ("too large" in txt or "too many" in txt):
                        break
                    else:
                        return all_items  # return what we have so far (likely empty)
                increment_api_usage("ebay")

                try:
                    payload = r.json()
                except Exception as e:
                    logger.warning(f"[{self.DOMAIN}] bad JSON seller={seller_username}: {e}")
                    break

                items = payload.get("itemSummaries") or payload.get("item_summary") or []
                if not isinstance(items, list) or not items:
                    break

                # de-dupe across pages
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
                    logger.info(
                        f"[{self.DOMAIN}] seller={seller_username} reached page cap ({page_count})"
                    )
                    break
                if len(all_items) >= getattr(self, "MAX_LISTINGS", 1000):
                    logger.info(
                        f"[{self.DOMAIN}] seller={seller_username} reached MAX_LISTINGS"
                    )
                    break

                offset += limit
                time.sleep(0.25)

            # if we fetched anything in this window, stop tightening
            if all_items:
                break

        return all_items

    def _normalize_item(self, raw: dict[str, Any], sale_type: str):

        #logger.info("[%s] Looking at item ID=%r with title: %r",
        #            self.DOMAIN, raw.get("itemId"), raw.get("title"))

        # HARD GATE: skip multi-variation / configurable-style listings entirely
        if is_configurable_item(raw):
            logger.info(
                "[%s] skipping configurable/multi-variation listing itemId=%s title=%r",
                self.DOMAIN,
                get_item_id(raw),
                raw.get("title"),
            )
            return None

        item_id = raw.get("itemId")

        # Skip any legacy or malformed external_id (v1-prefixed junk)
        #if isinstance(item_id, str) and "v1" in item_id.lower():
        #    return None

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

        # sanity: drop mismatched type
        if sale_type == "bin" and ("AUCTION" in buying_opts):
            return None
        if sale_type == "auction" and "AUCTION" not in buying_opts:
            return None

        title_lower = title.lower()
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
            "notes": '',
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

                    # HARD GATE: only keep exact seller match
                    if row["seller_username"].lower().strip() != seller.lower().strip():
                        logger.debug(
                            "[%s] skipping foreign seller '%s' (wanted '%s') itemId=%s title=%r",
                            self.DOMAIN,
                            row["seller_username"],
                            seller,
                            row.get("external_id"),
                            row.get("title"),
                        )
                        continue

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


def ensure_utc_session(cur):
    try:
        cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        pass


def bulk_append_price_history(rows: list[tuple[str, int, int]]):
    """
    rows: (external_id, price, bids_count)
    """
    if not rows:
        return
    sql = """
        INSERT INTO auction_price_history (external_id, price, bids_count, recorded_at)
        VALUES %s
        ON CONFLICT DO NOTHING
    """
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("SET LOCAL synchronous_commit TO OFF;")
        execute_values(
            cur,
            sql,
            rows,
            template="(%s, %s, %s, (now() AT TIME ZONE 'utc'))",
            page_size=500,
        )


def bulk_upsert_auction_listings(rows: list[dict]):
    """
    Bulk upsert of listings data from scrapers.
    """
    if not rows:
        return
    cols = [
        "source", "external_id", "title", "price_current", "bids_count", "end_time",
        "url", "detail_url", "sale_type", "roi_estimate", "max_bid", "notes",
        "source_id", "model_key", "time_left_s", "status"
    ]
    values = [tuple(r.get(c) for c in cols) for r in rows]

    sql = f"""
        INSERT INTO auction_listings ({", ".join(cols)})
        VALUES %s
        ON CONFLICT (external_id) DO UPDATE
        SET title         = EXCLUDED.title,
            price_current = COALESCE(EXCLUDED.price_current, auction_listings.price_current),
            bids_count    = COALESCE(EXCLUDED.bids_count,    auction_listings.bids_count),
            end_time      = COALESCE(EXCLUDED.end_time,      auction_listings.end_time),
            url           = EXCLUDED.url,
            detail_url    = EXCLUDED.detail_url,
            sale_type     = EXCLUDED.sale_type,
            roi_estimate  = EXCLUDED.roi_estimate,
            max_bid       = EXCLUDED.max_bid,
            notes         = EXCLUDED.notes,
            source_id     = EXCLUDED.source_id,
            model_key     = COALESCE(EXCLUDED.model_key,     auction_listings.model_key),
            time_left_s   = COALESCE(EXCLUDED.time_left_s,   auction_listings.time_left_s),
            status        = COALESCE(EXCLUDED.status,        auction_listings.status)
    """
    conn = connection
    with conn, conn.cursor() as cur:
        ensure_utc_session(cur)
        cur.execute("SET LOCAL synchronous_commit TO OFF;")
        execute_values(cur, sql, values, page_size=250)
