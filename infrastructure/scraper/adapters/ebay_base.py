# infrastructure/scraper/adapters/ebay_base.py
from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from bs4 import BeautifulSoup

from core.contracts import AuctionAdapter
from infrastructure.db.schema import (
    upsert_auction_listing,
    resolve_source_id,
    resolve_source_field,
    append_price_history,
    mark_listing_seen,
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

    def __init__(self):
        self.session = build_session()
        self._source_name, self._source_id = self._resolve_source()

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

    def parse_auction(self, url: str) -> bool:
        try:
            try:
                self.session.get("https://www.ebay.co.uk/", timeout=15)
            except Exception:
                pass

            fetch_url = url if "nordt=true" in url else (url.split("?")[0] + "?nordt=true")
            r = refetch_get(fetch_url, self.session)
            html = r.text
            soup = BeautifulSoup(html, "lxml")

            html_lower = html.lower()
            from infrastructure.utils.http import is_ended_listing as _is_ended  # local import to avoid cycles
            if _is_ended(html_lower):
                logger.info(f"[{self.DOMAIN}] ended/invalid listing, skipping (ok): {url}")
                save_cookies(self.session)
                return False

            if looks_like_interstitial(soup, html):
                r = refetch_get(url, self.session)
                html = r.text
                soup = BeautifulSoup(html, "lxml")
                html_lower = html.lower()
                if _is_ended(html_lower):
                    logger.info(f"[{self.DOMAIN}] ended/invalid listing (retry), skipping (ok): {url}")
                    save_cookies(self.session)
                    return False

            if not is_valid_item_page(soup, html):
                warn_blocked(self.DOMAIN, url, r, reason="no title/price markers")
                save_cookies(self.session)
                return False

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

            upsert_auction_listing(
                source=self._source_name,
                external_id=external_id,
                title=title[:255],
                price_current=price_current,
                bids_count=0,
                end_time=end_time,        # naive UTC (per helper)
                url=url_clean,
                detail_url=detail_url,
                sale_type=self.SALE_TYPE,
                roi_estimate=None,
                max_bid=None,
                notes=category_hint,
                source_id=self._source_id,
                model_key=model_key,
                time_left_s=time_left_s,
                status="live",
            )

            try:
                mark_listing_seen(
                    external_id=external_id,
                    price_current=price_current,
                    bids_count=0,
                    end_time=end_time,
                    time_left_s=time_left_s,
                    model_key=model_key,
                    status="live",
                )
            except Exception as _e:
                logger.warning(f"[{self.DOMAIN}] mark_listing_seen failed: {_e}")

            if price_current and price_current > 0:
                try:
                    append_price_history(external_id=external_id, price=price_current, bids_count=0)
                except Exception as _e:
                    logger.warning(f"[{self.DOMAIN}] price_history append failed: {_e}")

            save_cookies(self.session)
            return True

        except Exception as _e:
            logger.warning(f"[{self.DOMAIN}] parse_auction failed: {_e}")
            save_cookies(self.session)
            return False
