import os
import requests
import xml.etree.ElementTree as ET
from pprint import pprint

from infrastructure.ebay.auth import get_auth

EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item/"
EBAY_TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
EBAY_SITE_ID = "3"          # UK
EBAY_COMPAT_LEVEL = "967"   # compat level for GetItem

# 👇 change this to any ended listing you care about
# TEST_ITEM_ID = "306554242371"
TEST_ITEM_ID = "306554242452"


def _xml_text(node, path: str):
    """
    Safe helper to extract text from an XML node with or without namespace.
    """
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


def call_browse(item_id: str):
    """
    Direct Browse API call using the OAuth token you already had working.
    """
    auth_obj = get_auth()
    browse_token = auth_obj.get_token() if hasattr(auth_obj, "get_token") else auth_obj

    headers = {
        "Authorization": f"Bearer {browse_token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
    }

    url = f"{EBAY_BROWSE_URL}{item_id}"
    resp = requests.get(url, headers=headers, timeout=(4, 6))

    print("=== BROWSE API ====================================")
    print("URL:", url)
    print("STATUS:", resp.status_code)
    try:
        body_json = resp.json()
        print("BODY (as JSON):")
        pprint(body_json)
    except Exception:
        print("BODY (raw text):")
        print(resp.text[:2000])
    print("====================================================\n")

    return resp


def call_trading_raw(item_id: str):
    """
    Direct Trading API GetItem using your new EBAY_TRADING_TOKEN (Auth'n'Auth style).
    We do NOT rely on get_auth() here.
    """
    trading_token = os.getenv("EBAY_TRADING_TOKEN", "").strip()
    if not trading_token:
        raise RuntimeError("EBAY_TRADING_TOKEN is not set in env")

    headers = {
        "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": "GetItem",
        "X-EBAY-API-SITEID": EBAY_SITE_ID,
        "X-EBAY-API-COMPATIBILITY-LEVEL": EBAY_COMPAT_LEVEL,
        # IMPORTANT: Trading API wants the Auth'n'Auth token here
        "X-EBAY-API-IAF-TOKEN": trading_token,
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

    print("=== TRADING API (GetItem) ==========================")
    print("URL:", EBAY_TRADING_ENDPOINT)
    print("STATUS:", resp.status_code)
    print("HEADERS SENT (sanitised):")
    print("  Content-Type:", headers["Content-Type"])
    print("  X-EBAY-API-CALL-NAME:", headers["X-EBAY-API-CALL-NAME"])
    print("  X-EBAY-API-SITEID:", headers["X-EBAY-API-SITEID"])
    print("  X-EBAY-API-COMPATIBILITY-LEVEL:", headers["X-EBAY-API-COMPATIBILITY-LEVEL"])
    print("  X-EBAY-API-IAF-TOKEN: <redacted>")
    print("\nREQUEST BODY SENT:")
    print(body)

    print("\nRESPONSE TEXT (XML):")
    print(resp.text[:4000])
    print("====================================================\n")

    return resp


def interpret_trading_response(resp: requests.Response):
    """
    Namespace-aware parse of Trading GetItem response.
    Dumps SellingStatus so we can see QuantitySold, BidCount, CurrentPrice, etc.
    """
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        print(f"[parse] XML parse fail: {e}")
        return

    ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}

    ack_el = root.find("./ns:Ack", ns)
    ack = ack_el.text.strip() if ack_el is not None and ack_el.text else None
    print(f"[parse] Ack: {ack}")

    if (ack or "").lower() != "success":
        # dump Trading error block
        short_msg = root.findtext("./ns:Errors/ns:ShortMessage", default="", namespaces=ns)
        long_msg = root.findtext("./ns:Errors/ns:LongMessage", default="", namespaces=ns)
        code = root.findtext("./ns:Errors/ns:ErrorCode", default="", namespaces=ns)
        print(f"[parse] Trading error {code}: {short_msg} / {long_msg}")
        return

    item_node = root.find(".//ns:Item", ns)
    if item_node is None:
        print("[parse] No <Item> node in successful response (weird)")
        return

    # dump SellingStatus raw XML for visibility
    selling_status_node = item_node.find("./ns:SellingStatus", ns)
    if selling_status_node is not None:
        print("\n=== RAW <SellingStatus> XML =======================")
        print(ET.tostring(selling_status_node, encoding="unicode"))
        print("===================================================\n")
    else:
        print("[parse] No <SellingStatus> node found")

    # pull structured fields with namespace-aware lookup
    listing_status = (
        item_node.findtext("./ns:ListingStatus", default="", namespaces=ns)
        or item_node.findtext("./ns:SellingStatus/ns:ListingStatus", default="", namespaces=ns)
        or ""
    ).lower()

    selling_state = (
        item_node.findtext("./ns:SellingStatus/ns:SellingState", default="", namespaces=ns)
        or ""
    ).lower()

    bid_count_txt = item_node.findtext("./ns:SellingStatus/ns:BidCount", default="", namespaces=ns)
    qty_sold_txt = item_node.findtext("./ns:SellingStatus/ns:QuantitySold", default="", namespaces=ns)
    current_price_txt = item_node.findtext("./ns:SellingStatus/ns:CurrentPrice", default="", namespaces=ns)

    try:
        bid_count = int(bid_count_txt) if bid_count_txt else 0
    except ValueError:
        bid_count = 0

    try:
        qty_sold = int(qty_sold_txt) if qty_sold_txt else 0
    except ValueError:
        qty_sold = 0

    final_price = None
    if current_price_txt:
        try:
            final_price = float(current_price_txt)
        except ValueError:
            final_price = None

    if "active" in listing_status or "active" in selling_state:
        live = True
        ended = False
    else:
        live = False
        ended = True

    sold_flag = False
    if "endedwithsales" in selling_state:
        sold_flag = True
    elif qty_sold > 0:
        sold_flag = True
    elif ("completed" in listing_status or "completed" in selling_state) and bid_count > 0 and final_price and final_price > 0:
        sold_flag = True

    print("=== INTERPRETED FIELDS ====================")
    print("ended:", ended)
    print("live:", live)
    print("listing_status:", listing_status)
    print("selling_state:", selling_state)
    print("qty_sold:", qty_sold)
    print("bid_count:", bid_count)
    print("final_price:", final_price)
    print("sold_flag:", sold_flag)
    print("===========================================")


def main():
    item_id = TEST_ITEM_ID

    # 1. Browse (will 404 on ended auctions)
    call_browse(item_id)

    # 2. Trading with seller token
    trading_resp = call_trading_raw(item_id)

    # 3. Now: parse with namespace-aware logic
    interpret_trading_response(trading_resp)



def main():
    item_id = TEST_ITEM_ID

    # 1. Browse (will 404 on ended auctions, that's normal)
    call_browse(item_id)

    # 2. Trading with your new token
    trading_resp = call_trading_raw(item_id)

    # 3. Show parsed meaning (did it sell? for how much?)
    interpret_trading_response(trading_resp)


if __name__ == "__main__":
    main()
