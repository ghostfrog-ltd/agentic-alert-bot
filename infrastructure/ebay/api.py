from __future__ import annotations

import os
import requests
import xml.etree.ElementTree as ET
from typing import Any, Optional

from infrastructure.utils.logger import get_logger
from infrastructure.ebay.auth import get_auth
from infrastructure.utils.usage_tracker import increment_api_usage  # ✅ counts successful calls

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
    if node is None:
        return None

    ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}
    found = node.find(path, ns)
    if found is not None and found.text:
        return found.text.strip()

    cur = node
    for part in path.strip("./").split("/"):
        if cur is None:
            return None
        cur = cur.find(part)
    if cur is not None and cur.text:
        return cur.text.strip()

    return None


def _get_trading_token() -> str:
    trading_token = os.getenv("EBAY_TRADING_TOKEN", "").strip()
    if not trading_token:
        raise RuntimeError("EBAY_TRADING_TOKEN is not set in env")
    return trading_token


def _call_trading_getitem(item_id: str) -> Optional[ET.Element]:
    token = _get_trading_token()

    headers = {
        "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": "GetItem",
        "X-EBAY-API-SITEID": EBAY_SITE_ID,
        "X-EBAY-API-COMPATIBILITY-LEVEL": EBAY_COMPAT_LEVEL,
        "X-EBAY-API-IAF-TOKEN": token,
    }

    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{token}</eBayAuthToken>
  </RequesterCredentials>
  <ItemID>{item_id}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
  <IncludeWatchCount>true</IncludeWatchCount>
</GetItemRequest>
"""

    resp = requests.post(
        EBAY_TRADING_ENDPOINT,
        data=body.encode("utf-8"),
        headers=headers,
        timeout=(6, 8),
    )

    if resp.status_code == 429:
        raise EbayApiRateLimited("429 from Trading API GetItem")
    if resp.status_code >= 500:
        raise EbayApiTempError(f"Trading API {resp.status_code} (5xx)")

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise EbayApiTempError(f"Trading XML parse fail: {e}")

    # debug dump
    try:
        os.makedirs("/tmp/ebay_debug", exist_ok=True)
        with open(f"/tmp/ebay_debug/{item_id}.xml", "w") as f:
            f.write(resp.text)
    except Exception:
        pass

    item_node = (
        root.find(".//{urn:ebay:apis:eBLBaseComponents}Item")
        or root.find(".//Item")
    )
    if item_node is None:
        logger.warning("[eBayAPI] Trading returned no <Item> node for %s", item_id)
        return None

    increment_api_usage("ebay")
    return item_node


def _interpret_trading_item(item_node: ET.Element) -> dict:
    ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}

    item_id = (
        item_node.findtext("./ns:ItemID", default="", namespaces=ns)
        or "?"
    )

    listing_status_raw = (
        item_node.findtext("./ns:ListingStatus", default="", namespaces=ns)
        or item_node.findtext("./ns:SellingStatus/ns:ListingStatus", default="", namespaces=ns)
        or ""
    )
    listing_status = listing_status_raw.lower()

    selling_state_raw = (
        item_node.findtext("./ns:SellingStatus/ns:SellingState", default="", namespaces=ns)
        or ""
    )
    selling_state = selling_state_raw.lower()

    qty_sold_txt = item_node.findtext("./ns:SellingStatus/ns:QuantitySold", default="", namespaces=ns)
    bid_count_txt = item_node.findtext("./ns:SellingStatus/ns:BidCount", default="", namespaces=ns)
    current_price_txt = item_node.findtext("./ns:SellingStatus/ns:CurrentPrice", default="", namespaces=ns)

    try:
        qty_sold = int(qty_sold_txt) if qty_sold_txt else 0
    except ValueError:
        qty_sold = 0

    try:
        bid_count = int(bid_count_txt) if bid_count_txt else 0
    except ValueError:
        bid_count = 0

    try:
        final_price = float(current_price_txt) if current_price_txt else None
    except ValueError:
        final_price = None

    live = ("active" in listing_status) or ("active" in selling_state)
    ended = not live

    sold_flag = False
    if "endedwithsales" in selling_state:
        sold_flag = True
    elif qty_sold > 0:
        sold_flag = True
    elif (
        ("completed" in listing_status or "completed" in selling_state)
        and bid_count > 0
        and final_price
        and final_price > 0
    ):
        sold_flag = True

    snapshot = {
        "item_id": item_id,
        "ended": ended,
        "live": live,
        "sold": sold_flag,
        "final_price": (final_price if ended else None),
        "bid_count": bid_count,
        "qty_sold": qty_sold,
        "listing_status": listing_status,
        "selling_state": selling_state,
    }

    logger.info(
        "=== INTERPRETED FIELDS ====================\n"
        "item_id: %s\n"
        "ended: %s\n"
        "live: %s\n"
        "listing_status: %s\n"
        "selling_state: %s\n"
        "qty_sold: %s\n"
        "bid_count: %s\n"
        "final_price: %s\n"
        "sold_flag: %s\n"
        "===========================================",
        item_id,
        ended,
        live,
        listing_status,
        selling_state,
        qty_sold,
        bid_count,
        final_price,
        sold_flag,
    )

    return snapshot


def _call_browse(item_id: str) -> Optional[dict]:
    auth = get_auth()
    token = auth.get_token() if hasattr(auth, "get_token") else auth

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
    }

    url = f"{EBAY_BROWSE_URL}{item_id}"

    resp = requests.get(url, headers=headers, timeout=(4, 6))

    if resp.status_code == 429:
        raise EbayApiRateLimited("429 from Browse API")
    if resp.status_code >= 500:
        raise EbayApiTempError(f"Browse API {resp.status_code} (5xx)")

    if resp.status_code == 404:
        return None

    if resp.status_code != 200:
        logger.debug(
            "[eBayAPI] Browse API non-200 (%s) for %s body=%s",
            resp.status_code,
            item_id,
            resp.text[:300],
        )
        return None

    increment_api_usage("ebay")

    try:
        data = resp.json()
    except Exception as e:
        raise EbayApiTempError(f"Browse JSON parse fail: {e}")

    return data


def _interpret_browse_json(data: dict, row: dict) -> dict:
    price = None
    bid_count = None

    if "currentBidPrice" in data and isinstance(data["currentBidPrice"], dict):
        val = data["currentBidPrice"].get("value")
        if val is not None:
            try:
                price = float(val)
            except Exception:
                pass

    if price is None and "price" in data and isinstance(data["price"], dict):
        val = data["price"].get("value")
        if val is not None:
            try:
                price = float(val)
            except Exception:
                pass

    if "bidCount" in data:
        try:
            bid_count = int(data["bidCount"])
        except Exception:
            bid_count = None

    snapshot = {
        "ended": False,
        "sold": False,
        "final_price": None,
        "live": True,
        "current_price": price,
        "bid_count": bid_count,
        "qty_sold": None,
        "listing_status": None,
        "selling_state": None,
        "market_price": row.get("market_price"),
    }

    logger.debug(
        "[eBayAPI] Browse snapshot listing=%s live_price=£%s bids=%s market=£%s",
        row.get("id"),
        price,
        bid_count,
        row.get("market_price"),
    )

    return snapshot


def fetch_live_snapshot(row: dict) -> dict:
    """
    Normal path for stuff that's still basically fresh / maybe still live.
    Browse first, fallback to Trading.
    """
    item_id = (
        row.get("item_id")
        or row.get("external_id")
        or row.get("ebay_id")
    )
    if not item_id:
        logger.warning("[eBayAPI] no item_id for listing id=%s", row.get("id"))
        return {
            "ended": False,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": row.get("current_price"),
            "bid_count": row.get("bid_count"),
            "qty_sold": None,
            "listing_status": None,
            "selling_state": None,
            "market_price": row.get("market_price"),
        }

    # Try Browse for live stuff
    try:
        browse_data = _call_browse(item_id)
        if browse_data:
            return _interpret_browse_json(browse_data, row)
    except (EbayApiRateLimited, EbayApiTempError, requests.RequestException) as e:
        logger.warning("[eBayAPI] Browse error for %s: %s", item_id, e)

    # Fallback to Trading for ended stuff
    try:
        item_node = _call_trading_getitem(item_id)
    except (EbayApiRateLimited, EbayApiTempError, requests.RequestException) as e:
        logger.warning("[eBayAPI] Trading error for %s: %s", item_id, e)
        return {
            "ended": False,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": row.get("current_price"),
            "bid_count": row.get("bid_count"),
            "qty_sold": None,
            "listing_status": None,
            "selling_state": None,
            "market_price": row.get("market_price"),
        }

    if item_node is None:
        # Trading says no item -> ended, no sale info
        return {
            "ended": True,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": None,
            "bid_count": row.get("bid_count"),
            "qty_sold": None,
            "listing_status": None,
            "selling_state": None,
            "market_price": row.get("market_price"),
        }

    trading_snap = _interpret_trading_item(item_node)

    return {
        "ended": trading_snap["ended"],
        "sold": trading_snap["sold"],
        "final_price": trading_snap["final_price"],
        "live": trading_snap["live"],
        "current_price": None,
        "bid_count": trading_snap["bid_count"],
        "qty_sold": trading_snap["qty_sold"],
        "listing_status": trading_snap["listing_status"],
        "selling_state": trading_snap["selling_state"],
        "market_price": row.get("market_price"),
    }


def fetch_trading_only(item_id: str) -> dict:
    """
    HARD MODE.
    Skip Browse completely. Ask Trading right now.
    Used when the auction is definitely over (e.g. >=30 mins past end_time).
    Returns same shape as fetch_live_snapshot().
    """
    try:
        item_node = _call_trading_getitem(item_id)
    except EbayApiRateLimited:
        # caller will treat this as "we couldn't confirm yet"
        raise
    except EbayApiTempError:
        raise
    except requests.RequestException as e:
        raise EbayApiTempError(str(e))

    if item_node is None:
        # ended but Trading isn't giving us sale info
        return {
            "ended": True,
            "sold": False,
            "final_price": None,
            "live": False,
            "current_price": None,
            "bid_count": None,
            "qty_sold": None,
            "listing_status": None,
            "selling_state": None,
        }

    snap = _interpret_trading_item(item_node)

    return {
        "ended": snap["ended"],
        "sold": snap["sold"],
        "final_price": snap["final_price"],
        "live": snap["live"],
        "current_price": None,
        "bid_count": snap["bid_count"],
        "qty_sold": snap["qty_sold"],
        "listing_status": snap["listing_status"],
        "selling_state": snap["selling_state"],
    }
