from __future__ import annotations
import requests
from infrastructure.utils.logger import get_logger
from infrastructure.ebay.auth import get_auth
from infrastructure.utils.usage_tracker import increment_api_usage  # ✅ NEW

logger = get_logger(__name__)

EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item/"

def fetch_live_snapshot(row: dict) -> dict:
    """
    Ask eBay for the latest state of this specific listing.
    We feed that back into poll_hot_and_alert() to update DB.

    Also increments the per-day API usage counter for 'ebay'.
    """
    item_id = row.get("item_id") or row.get("external_id") or row.get("ebay_id")
    if not item_id:
        logger.warning("[eBayAPI] no item_id for listing id=%s", row.get("id"))
        return {
            "current_price": row.get("current_price"),
            "market_price": row.get("market_price"),
            "bid_count": row.get("bid_count"),
        }

    token = get_auth().get_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
    }

    url = f"{EBAY_BROWSE_URL}{item_id}"

    try:
        # 🌡 Call eBay live API
        r = requests.get(url, headers=headers, timeout=(4, 6))
        r.raise_for_status()

        # 🔢 Track usage AFTER a successful call to eBay
        increment_api_usage("ebay")

        data = r.json()

        price = None
        bid_count = None

        if "currentBidPrice" in data and "value" in data["currentBidPrice"]:
            try:
                price = float(data["currentBidPrice"]["value"])
            except Exception:
                pass
        elif "price" in data and "value" in data["price"]:
            try:
                price = float(data["price"]["value"])
            except Exception:
                pass

        if "bidCount" in data:
            try:
                bid_count = int(data["bidCount"])
            except Exception:
                pass

        # market_price is whatever we currently believe is "fair", from DB
        market_price = row.get("market_price")

        logger.info(
            "[eBayAPI] snapshot listing=%s live=£%s bids=%s market=£%s",
            row.get("id"),
            price,
            bid_count,
            market_price,
        )

        return {
            "current_price": price,
            "market_price": market_price,
            "bid_count": bid_count,
        }

    except requests.RequestException as e:
        logger.error(
            "[eBayAPI] fetch_live_snapshot failed for item_id=%s (id=%s): %s",
            item_id,
            row.get("id"),
            e,
        )
        # We did NOT increment the counter here because the call didn't succeed.
        # If you want to count attempts instead of successes,
        # move increment_api_usage('ebay') to right before requests.get().
        return {
            "current_price": row.get("current_price"),
            "market_price": row.get("market_price"),
            "bid_count": row.get("bid_count"),
        }
