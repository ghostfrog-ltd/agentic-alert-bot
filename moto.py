#!/usr/bin/env python3
from __future__ import annotations
import os, re, sys, time, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from bs4 import BeautifulSoup
from psycopg2.extras import execute_values
from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import connection, ensure_utc_session

logger = get_logger(__name__)


# ---------------------
# ENV
# ---------------------
def load_dotenv(p: str | Path = ".env"):
    f = Path(p)
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------------
# HTML scraping
# ---------------------
def parse_price(price_str: str | None):
    if not price_str:
        return None
    numeric = re.sub(r"[^\d.,]", "", price_str).replace(",", "")
    try:
        return Decimal(numeric)
    except Exception:
        return None


def extract_items_from_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()

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
        price = price_el.get_text(strip=True) if price_el else None

        items.append({
            "external_id": item_id,
            "title": title,
            "price_raw": price,
            "price_value": parse_price(price),
            "url": href,
        })

    return items


def fetch_seller_items(domain: str, max_pages: int = 3, delay: float = 2.0):
    """Scrape all listing pages for a seller, using domain to build URL."""
    base_url = f"https://www.ebay.co.uk/sch/i.html?_ssn={domain}"
    headers = {
        "User-Agent": "ghostfrog-moto-scraper/1.0 (+https://ghostfrog.co.uk)",
        "Accept": "text/html,application/xhtml+xml",
    }

    all_items = []
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

        page_items = extract_items_from_html(r.text)
        logger.info(f"[{domain}] Page {page} -> {len(page_items)} items")
        if not page_items:
            break

        all_items.extend(page_items)
        time.sleep(delay)

    return all_items


# ---------------------
# DB UPSERT
# ---------------------
def bulk_upsert_auction_listings(rows: list[dict]):
    if not rows:
        logger.info("[motomine] No rows to upsert")
        return 0

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


# ---------------------
# MAIN
# ---------------------
def main():
    load_dotenv()
    domain = "motomine"
    now = datetime.now(timezone.utc)

    # get source settings
    with connection.cursor() as cur:
        cur.execute("""
            SELECT id, enabled, scrape_interval_seconds, last_scraped_at
            FROM sources
            WHERE domain = %s
            LIMIT 1
        """, (domain,))
        src = cur.fetchone()

    if not src:
        logger.warning(f"[{domain}] No source row found")
        return

    source_id, enabled, interval, last_scraped = src

    if not enabled:
        logger.info(f"[{domain}] Source disabled; skipping")
        return

    if last_scraped and interval:
        elapsed = (now - last_scraped).total_seconds()
        if elapsed < interval:
            remaining = interval - elapsed
            next_time = last_scraped + timedelta(seconds=interval)
            logger.info(
                f"[{domain}] gated → next run at {next_time.strftime('%H:%M:%S')} "
                f"(in {int(remaining//60)}m {int(remaining%60)}s)"
            )
            return

    items = fetch_seller_items(domain)
    logger.info(f"[{domain}] Found {len(items)} listings")

    rows = []
    for it in items:
        rows.append({
            "source": domain,
            "external_id": it["external_id"],
            "title": it["title"],
            "price_current": it["price_value"],
            "bids_count": None,
            "end_time": None,
            "url": it["url"],
            "detail_url": it["url"],
            "sale_type": "auction",
            "roi_estimate": None,
            "max_bid": None,
            "notes": None,
            "source_id": source_id,
            "model_key": None,
            "time_left_s": None,
            "status": "live",
        })

    if rows:
        bulk_upsert_auction_listings(rows)
        with connection.cursor() as cur:
            cur.execute("UPDATE sources SET last_scraped_at = now() WHERE id = %s", (source_id,))
        connection.commit()
    else:
        logger.info(f"[{domain}] No listings found to insert")

    for i, it in enumerate(items, 1):
        print(f"[{i}] '{it['title']}'")
        print(f"    item_id={it['external_id']}")
        print(f"    price={it['price_raw']}")
        print(f"    url={it['url']}")
        print("-" * 40)


if __name__ == "__main__":
    main()
