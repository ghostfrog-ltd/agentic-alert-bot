from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Any, Set, Optional

import requests
from bs4 import BeautifulSoup
from psycopg2.extras import execute_values

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import connection, ensure_utc_session
from datetime import timedelta
from infrastructure.utils.timez import now_utc

logger = get_logger(__name__)


# -------------------------------------------------
# Low-level HTML helpers (your moto scraper bits)
# -------------------------------------------------
def _parse_price(price_str: Optional[str]) -> Optional[Decimal]:
    if not price_str:
        return None
    numeric = re.sub(r"[^\d.,]", "", price_str).replace(",", "")
    if not numeric:
        return None
    try:
        return Decimal(numeric)
    except Exception:
        logger.debug(f"[motomine] failed to parse price '{price_str}' -> '{numeric}'")
        return None


def _extract_items_from_html(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/itm/(?:.*?/)?(\d+)", href)
        if not m:
            continue

        item_id = m.group(1)
        if item_id in seen:
            continue
        seen.add(item_id)

        container = (
            a.find_parent("div", class_="su-card-container")
            or a.find_parent("li")
            or a
        )

        title_el = (
            container.select_one(".s-card__title")
            or container.select_one(".s-item__title")
            or a
        )
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = (
            container.select_one(".s-card__price")
            or container.select_one(".s-item__price")
        )
        price_raw = price_el.get_text(strip=True) if price_el else None
        price_val = _parse_price(price_raw)

        items.append(
            {
                "external_id": item_id,
                "title": title,
                "price_raw": price_raw,
                "price_value": price_val,
                "url": href,
            }
        )

    return items


def _fetch_seller_items(domain: str, max_pages: int = 3, delay: float = 2.0) -> List[Dict[str, Any]]:
    """
    Scrape listing pages for a seller based on the domain (seller name).
    Ensures unique external_id across all pages.
    """
    base_url = f"https://www.ebay.co.uk/sch/i.html?_ssn={domain}"
    headers = {
        "User-Agent": "ghostfrog-moto-scraper/1.0 (+https://ghostfrog.co.uk)",
        "Accept": "text/html,application/xhtml+xml",
    }

    all_items: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for page in range(1, max_pages + 1):
        page_url = f"{base_url}&_pgn={page}"
        logger.info(f"[{domain}] Fetching page {page}: {page_url}")
        try:
            r = requests.get(page_url, headers=headers, timeout=20)
        except Exception as e:
            logger.error(f"[{domain}] Request failed: {e}")
            break

        if r.status_code != 200:
            logger.warning(f"[{domain}] HTTP {r.status_code} - stopping")
            break

        page_items = _extract_items_from_html(r.text)
        logger.info(f"[{domain}] Page {page} -> {len(page_items)} items")

        if not page_items:
            break

        new_count = 0
        for it in page_items:
            eid = it.get("external_id")
            if not eid:
                continue
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            all_items.append(it)
            new_count += 1

        logger.info(f"[{domain}] Page {page} added {new_count} new items (total {len(all_items)})")

        time.sleep(delay)

    return all_items


def _bulk_upsert_auction_listings(rows: List[Dict[str, Any]]) -> int:
    """
    Bulk upsert of listings data from scrapers.
    Expects keys:
      source, external_id, title, price_current, bids_count, end_time,
      url, detail_url, sale_type, roi_estimate, max_bid, notes,
      source_id, model_key, time_left_s, status
    """
    if not rows:
        logger.info("[motomine] No rows to upsert")
        return 0

    # Deduplicate by conflict key (external_id) just in case
    deduped: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        eid = r.get("external_id")
        if not eid:
            continue
        deduped[eid] = r  # last one wins, doesn't really matter here

    rows = list(deduped.values())

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

    logger.info(f"[motomine] Upserted {len(rows)} listings")
    return len(rows)


# -------------------------------------------------
# Adapter used by Heartbeat
# -------------------------------------------------
class Adapter:
    DOMAIN = "motomine"   # <<--- THIS is what sources.domain must match

    def __init__(self, max_pages: int = 3, delay: float = 2.0):
        self.max_pages = max_pages
        self.delay = delay

    def fetch_listings_api(self, ebay_token: str) -> None:
        """
        Called by _run_adapter() in your sources runner.

        - Heartbeat has *already* checked enabled + intervals and set last_scraped_at.
        - We just:
            - find source_id
            - scrape HTML
            - upsert rows
        """
        domain = self.DOMAIN

        # get source_id from sources
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id FROM sources WHERE domain = %s LIMIT 1",
                (domain,),
            )
            row = cur.fetchone()

        if not row:
            logger.warning(f"[{domain}] No source row found; skipping")
            return

        source_id = row[0]

        # scrape listings
        items = _fetch_seller_items(domain, max_pages=self.max_pages, delay=self.delay)
        logger.info(f"[{domain}] Scraped {len(items)} listings via HTML")

        scrape_ts = now_utc()
        default_duration = timedelta(days=7)

        if not items:
            return

        rows: List[Dict[str, Any]] = []
        for it in items:
            rows.append(
                {
                    "source": domain,
                    "external_id": it["external_id"],
                    "title": it["title"],
                    "price_current": it["price_value"],
                    "bids_count": None,
                    "end_time": scrape_ts + default_duration,
                    "url": it["url"],
                    "detail_url": it["url"],
                    "sale_type": "auction",
                    "roi_estimate": None,
                    "max_bid": None,
                    "notes": None,
                    "source_id": source_id,
                    "model_key": None,
                    "time_left_s": None,
                    "status": "OPEN",
                }
            )

        _bulk_upsert_auction_listings(rows)
