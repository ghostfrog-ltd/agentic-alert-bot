from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from infrastructure.db.schema import (
    get_open_auctions,
    get_open_auctions_ending_before,
    mark_status,
    finalize_auction,
    get_recent_max_price,
)
from infrastructure.utils.logger import get_logger
from infrastructure.utils.http import get as http_get, head  # use our wrappers

logger = get_logger(__name__)

GRACE_WINDOW = timedelta(minutes=5)        # mark ENDING_SOON
BURST_WINDOW = timedelta(minutes=3)        # high-frequency polling window
BURST_INTERVAL_SECONDS = 12                # how often you poll in the burst
CLOSE_DELAY = timedelta(seconds=90)        # wait after end_time for snipes/page lag


# --- time helpers (normalize to aware UTC) ---
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _should_burst(now: datetime, end_time: datetime) -> bool:
    end_time = _to_aware_utc(end_time) or end_time
    return (end_time - BURST_WINDOW) <= now <= (end_time + CLOSE_DELAY)


def _mark_ending_soon_if_needed():
    now = _now_utc()
    for a in get_open_auctions(now):
        end_time = _to_aware_utc(a.get("end_time"))
        if not end_time:
            continue
        if a.get("status") != "ENDING_SOON" and now >= (end_time - GRACE_WINDOW):
            mark_status(a["id"], "ENDING_SOON")
            logger.info(f"[close] Auction {a['id']} -> ENDING_SOON")


def _fetch_detail_snapshot(detail_url: str) -> tuple[bool, float | None, str | None]:
    """
    Returns (ended, price, sale_type)
    If ended is True but price is None, price was hidden/unavailable.
    """
    # 1) Cheap HEAD probe (don’t follow redirects)
    try:
        hr = head(detail_url, allow_redirects=False, timeout=15)
    except Exception:
        hr = None

    # If we got a redirect, follow once (usually to "this listing has ended")
    if hr is not None and 300 <= hr.status_code < 400 and hr.headers.get("Location"):
        loc = hr.headers["Location"]
        if loc.startswith("/"):
            loc = urljoin(detail_url, loc)
        r = http_get(loc, timeout=25)  # follow normally now
    else:
        # No redirect (or HEAD skipped) → fetch the page
        r = http_get(detail_url, timeout=25)

    html = r.text
    soup = BeautifulSoup(html, "lxml")  # faster/more lenient than html.parser

    # --- Heuristics:
    text = soup.get_text(" ", strip=True).lower()
    ended = ("this listing has ended" in text) or ("ended" in text and "listing" in text)

    price: float | None = None
    sale_type: str | None = None

    # Try common price containers on ended pages
    cand = soup.select_one(
        "#prcIsum, .x-price-primary, .vi-price, .display-price, "
        ".vi-VR-cvipPrice, .notranslate"
    )
    if cand:
        import re
        m = re.search(r"([0-9]+[0-9,]*\.?[0-9]*)", cand.get_text())
        if m:
            try:
                price = float(m.group(1).replace(",", ""))
            except Exception:
                price = None

    # Infer sale type with weak heuristics
    if "best offer accepted" in text:
        sale_type = "best_offer"
    elif "buy it now" in text:
        sale_type = "bin"
    elif "bids" in text or "bid" in text:
        sale_type = "auction"

    return ended, price, sale_type


def _close_due_auctions():
    now = _now_utc()
    due = get_open_auctions_ending_before(now + CLOSE_DELAY)  # include those within the close delay
    for a in due:
        auction_id = a["id"]
        detail_url = a.get("detail_url")
        end_time = _to_aware_utc(a.get("end_time"))

        if not end_time:
            continue
        if now < (end_time + CLOSE_DELAY):
            # not yet past the close delay
            continue

        # Final detail fetch attempt for accurate close
        try:
            if detail_url:
                ended, final_price, sale_type = _fetch_detail_snapshot(detail_url)
            else:
                logger.warning(f"[close] Auction {auction_id} missing detail_url; using fallback")
                ended, final_price, sale_type = True, None, None  # fallback path

            if ended:
                if final_price is not None:
                    finalize_auction(auction_id, final_price, "HIGH", "ENDED_CONFIRMED", sale_type)
                    logger.info(f"[close] Auction {auction_id} ENDED_CONFIRMED @ {final_price}")
                else:
                    # No visible price (best offer / unsold / cancelled)
                    recent = get_recent_max_price(auction_id, window_minutes=15)
                    finalize_auction(auction_id, recent, "LOW", "ENDED_TENTATIVE", sale_type)
                    logger.info(f"[close] Auction {auction_id} ENDED_TENTATIVE @ {recent} (no final price visible)")
            else:
                # Edge: page says not ended yet; skip and try next tick
                logger.info(f"[close] Auction {auction_id} not ended per detail page; will retry")
        except Exception as e:
            # Network or parse failure — be safe, record tentative using recent observed max
            recent = get_recent_max_price(auction_id, window_minutes=15)
            finalize_auction(auction_id, recent, "LOW", "ENDED_TENTATIVE", None)
            logger.warning(f"[close] Auction {auction_id} tentative close (error): {e}")


def _burst_polling_hook(poll_callback):
    """
    Optional: if you want to integrate a scheduler that hits detail URLs every BURST_INTERVAL_SECONDS
    within BURST_WINDOW. Your scraper loop could call this with a roster of ending-soon auctions.
    """
    pass  # You can integrate with your existing scrape loop if desired.


def tick():
    """
    Call this every heartbeat tick, e.g., right after or before your regular scraping run.
    """
    _mark_ending_soon_if_needed()
    _close_due_auctions()
