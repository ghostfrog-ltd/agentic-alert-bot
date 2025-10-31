from __future__ import annotations

import requests
import xml.etree.ElementTree as ET
from typing import Any, Optional

from infrastructure.utils.logger import get_logger
from infrastructure.ebay.auth import get_auth
from infrastructure.utils.usage_tracker import increment_api_usage  # ✅ NEW

logger = get_logger(__name__)

# -----------------
# Endpoints / const
# -----------------
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item/"
EBAY_TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
EBAY_SITE_ID = "3"            # UK
EBAY_COMPAT_LEVEL = "967"     # compat level suitable for GetItem


class EbayApiRateLimited(Exception):
    pass


class EbayApiTempError(Exception):
    pass


# -----------------
# Helpers
# -----------------
def _xml_text(node: Optional[ET.Element], path: str) -> Optional[str]:
    """
    Safe text fetch from an XML element.
    Tries namespaced and non-namespaced lookup.
    """
    if node is None:
        return None

    ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}
    found = node.find(path, ns)
    if found is not None and found.text:
        return found.text.strip()

    # fallback dumb walk without namespace
    cur = node
    for part in path.strip("./").split("/"):
        if cur is None:
            return None
        cur = cur.find(part)
    if cur is not None and cur.text:
        return cur.text.strip()

    return None


def _call_trading_getitem(item_id: str) -> Optional[ET.Element]:
    """
    Call eBay Trading API GetItem (XML) to get final sale info for ended listings.

    Returns <Item> node or None.
    Raises EbayApiRateLimited / EbayApiTempError on network/rate issues.
    """
    auth = get_auth()
    # get_auth() in your project returns an object with .get_token() in some places,
    # and sometimes just the token. We'll support both defensively:
    token = auth.get_token() if hasattr(auth, "get_token") else auth

    headers = {
        "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": "GetItem",
        "X-EBAY-API-SITEID": EBAY_SITE_ID,
        "X-EBAY-API-COMPATIBILITY-LEVEL": EBAY_COMPAT_LEVEL,
        "X-EBAY-API-IAF-TOKEN": token,
    }

    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{item_id}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
  <IncludeWatchCount>true</IncludeWatchCount>
</GetItemRequest>
"""

    resp = requests.post(
        EBAY_TRADING_ENDPOINT,
        data=body.encode("utf-8"),
        headers=headers,
        timeout=(4, 6),
    )

    # We count *successful* calls in usage. Only increment after status 200 parse succeeds.

    if resp.status_code == 429:
        raise EbayApiRateLimited("429 from Trading API GetItem")

    if resp.status_code >= 500:
        raise EbayApiTempError(f"Trading API {resp.status_code} (5xx)")

    if resp.status_code != 200:
        logger.warning(
            "[eBayAPI] Trading GetItem HTTP %s for %s body=%s",
            resp.status_code, item_id, resp.text[:400]
        )
        raise EbayApiTempError(f"Trading GetItem HTTP {resp.status_code}")

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise EbayApiTempError(f"Trading XML parse fail: {e}")

    item_node = (
        root.find(".//{urn:ebay:apis:eBLBaseComponents}Item")
        or root.find(".//Item")
    )
    if item_node is None:
        return None

    # ✅ success means we actually hit eBay Trading API and parsed it
    increment_api_usage("ebay")

    return item_node


def _interpret_trading_item(item_node: ET.Element) -> dict:
    """
    Convert Trading API <Item> node into the unified snapshot dict.
    """
    listing_status = (
        _xml_text(item_node, "./ListingStatus")
        or _xml_text(item_node, "./SellingStatus/ListingStatus")
        or ""
    ).lower()

    selling_state = (
        _xml_text(item_node, "./SellingStatus/SellingState")
        or ""
    ).lower()

    bid_count_txt = _xml_text(item_node, "./SellingStatus/BidCount")
    current_price_txt = _xml_text(item_node, "./SellingStatus/CurrentPrice")

    # live vs ended
    if "active" in listing_status or "active" in selling_state:
        live = True
        ended = False
    else:
        live = False
        ended = True

    # sold?
    sold = False
    if "endedwithsales" in selling_state:
        sold = True
    elif "completed" in listing_status or "completed" in selling_state:
        try:
            bc = int(bid_count_txt) if bid_count_txt else 0
        except ValueError:
            bc = 0
        if bc > 0:
            sold = True
    elif "ended" in selling_state and "withsales" in selling_state:
        sold = True

    # parse final price (this is the ONLY source of truth once ended)
    final_price = None
    if current_price_txt:
        try:
            final_price = float(current_price_txt)
        except ValueError:
            final_price = None

    # If it's still live, we shouldn't treat current price as a final sale value.
    if live:
        final_price_to_report = None
    else:
        final_price_to_report = final_price

    snapshot = {
        "ended": ended,
        "sold": sold,
        "final_price": final_price_to_report,
        "live": live,
    }

    logger.info(
        "[eBayAPI] Trading snapshot item live=%s ended=%s sold=%s final=%s",
        live,
        ended,
        sold,
        final_price_to_report,
    )

    return snapshot


def _call_browse(item_id: str) -> Optional[dict]:
    """
    Hit the Browse API (JSON). Good for live listings, often 404 for ended ones.
    Returns parsed dict or None if not available.
    Raises EbayApiRateLimited / EbayApiTempError if we got rate/5xx.
    """
    auth = get_auth()
    token = auth.get_token() if hasattr(auth, "get_token") else auth

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
    }

    url = f"{EBAY_BROWSE_URL}{item_id}"

    resp = requests.get(url, headers=headers, timeout=(4, 6))

    # We only increment usage on 2xx below.

    if resp.status_code == 429:
        raise EbayApiRateLimited("429 from Browse API")
    if resp.status_code >= 500:
        raise EbayApiTempError(f"Browse API {resp.status_code} (5xx)")

    if resp.status_code == 404:
        # Classic "ended so we hide it" case.
        return None

    if resp.status_code != 200:
        # Could be "not allowed", weird category, policy, etc.
        logger.debug(
            "[eBayAPI] Browse API non-200 (%s) for %s body=%s",
            resp.status_code,
            item_id,
            resp.text[:300],
        )
        return None

    # ✅ success, count this
    increment_api_usage("ebay")

    try:
        data = resp.json()
    except Exception as e:
        raise EbayApiTempError(f"Browse JSON parse fail: {e}")

    return data


def _interpret_browse_json(data: dict, row: dict) -> dict:
    """
    Convert Browse API JSON into partial snapshot.
    This is mainly for *live* listings. Browse often doesn't tell us final sale.
    """
    price = None
    bid_count = None

    # Try auction bid price first
    if "currentBidPrice" in data and isinstance(data["currentBidPrice"], dict):
        val = data["currentBidPrice"].get("value")
        if val is not None:
            try:
                price = float(val)
            except Exception:
                pass

    # Fallback: regular price (BIN or current listing price)
    if price is None and "price" in data and isinstance(data["price"], dict):
        val = data["price"].get("value")
        if val is not None:
            try:
                price = float(val)
            except Exception:
                pass

    # bidCount if they give it
    if "bidCount" in data:
        try:
            bid_count = int(data["bidCount"])
        except Exception:
            bid_count = None

    # Heuristic: Browse is for active stuff, so default live=True/ended=False here.
    snapshot = {
        "ended": False,
        "sold": False,
        "final_price": None,  # Browse can't be trusted for true final
        "live": True,
        "current_price": price,
        "bid_count": bid_count,
        "market_price": row.get("market_price"),
    }

    logger.info(
        "[eBayAPI] Browse snapshot listing=%s live_price=£%s bids=%s market=£%s",
        row.get("id"),
        price,
        bid_count,
        row.get("market_price"),
    )

    return snapshot


# -----------------
# Public: fetch_live_snapshot
# -----------------
def fetch_live_snapshot(row: dict) -> dict:
    """
    Unified snapshot getter used by your closer / watchlist / heartbeat.

    1. Try Browse API (JSON). Great for live items, cheap, fast.
       - If we get data: return snapshot with live price.

    2. If Browse returns 404 / hidden / no data:
       Hit Trading API GetItem (XML) to get the FINAL sale price.
       - That gives us ended/sold/final_price even after eBay hides it publicly.

    Return dict ALWAYS shaped like:
    {
        "ended": bool,
        "sold": bool,
        "final_price": float|None,    # final GBP price if ended
        "live": bool,
        "current_price": float|None,  # for live items
        "bid_count": int|None,
        "market_price": float|None,   # whatever we had in DB
    }
    """

    # --- pull item_id from row ---
    item_id = row.get("item_id") or row.get("external_id") or row.get("ebay_id")
    if not item_id:
        logger.warning("[eBayAPI] no item_id for listing id=%s", row.get("id"))
        # Fallback: we return a "can't tell" snapshot
        return {
            "ended": False,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": row.get("current_price"),
            "bid_count": row.get("bid_count"),
            "market_price": row.get("market_price"),
        }

    # --- Step 1: Try Browse API (good for active listings) ---
    browse_data = None
    try:
        browse_data = _call_browse(item_id)
    except EbayApiRateLimited as e:
        logger.warning("[eBayAPI] Browse rate-limited for %s: %s", item_id, e)
    except EbayApiTempError as e:
        logger.warning("[eBayAPI] Browse temp error for %s: %s", item_id, e)
    except requests.RequestException as e:
        logger.warning("[eBayAPI] Browse network fail for %s: %s", item_id, e)

    if browse_data:
        # We got JSON back from Browse => almost certainly still live.
        snap = _interpret_browse_json(browse_data, row)
        return snap

    # --- Step 2: Fallback to Trading API (final truth for ended stuff) ---
    try:
        item_node = _call_trading_getitem(item_id)
    except EbayApiRateLimited as e:
        logger.warning("[eBayAPI] Trading rate-limited for %s: %s", item_id, e)
        # If we're rate limited here, we can't know final; ask caller to retry later.
        return {
            "ended": False,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": row.get("current_price"),
            "bid_count": row.get("bid_count"),
            "market_price": row.get("market_price"),
        }
    except EbayApiTempError as e:
        logger.warning("[eBayAPI] Trading temp error for %s: %s", item_id, e)
        return {
            "ended": False,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": row.get("current_price"),
            "bid_count": row.get("bid_count"),
            "market_price": row.get("market_price"),
        }
    except requests.RequestException as e:
        logger.warning("[eBayAPI] Trading network fail for %s: %s", item_id, e)
        return {
            "ended": False,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": row.get("current_price"),
            "bid_count": row.get("bid_count"),
            "market_price": row.get("market_price"),
        }

    if item_node is None:
        # Couldn’t get item details even from Trading API (nuked listing etc.)
        return {
            "ended": True,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": None,
            "bid_count": row.get("bid_count"),
            "market_price": row.get("market_price"),
        }

    trading_snap = _interpret_trading_item(item_node)

    # Merge Trading snapshot (which has ended/sold/final_price/live)
    # with some of our row context (market_price etc.)
    out = {
        "ended": trading_snap["ended"],
        "sold": trading_snap["sold"],
        "final_price": trading_snap["final_price"],
        "live": trading_snap["live"],
        # For ended listings final_price is truth; for live ones we don't treat
        # current bid as final. We'll just return None here unless you want both.
        "current_price": None,
        "bid_count": row.get("bid_count"),
        "market_price": row.get("market_price"),
    }

    return out
