# agent/actions/close/ended.py
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Sequence, List, Tuple

import requests

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import get_connection
from infrastructure.utils.timez import now_utc
from infrastructure.utils.usage_tracker import increment_api_usage

logger = get_logger(__name__)

EBAY_TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
EBAY_SITE_ID = "3"  # UK
EBAY_COMPAT_LEVEL = "967"  # compat level for GetItem


@dataclass
class TradingSnapshot:
    listing_status: str
    selling_state: str
    bid_count: int
    qty_sold: int
    final_price: Optional[float]
    currency: Optional[str]
    ended: bool
    sold_flag: bool
    ebay_end_time: Optional[str]  # raw EndTime from Trading (ISO string)


def _xml_text(node: Optional[ET.Element], path: str, ns: dict[str, str]) -> Optional[str]:
    """
    Safe helper to extract text from an XML node with or without namespace.
    """
    if node is None:
        return None

    # Try namespaced lookup first
    found = node.find(path, ns)
    if found is not None and found.text:
        return found.text.strip()

    # Fallback: walk the plain tag path with no namespace
    cur = node
    for part in path.strip("./").split("/"):
        if cur is None:
            return None
        cur = cur.find(part)
    if cur is not None and cur.text:
        return cur.text.strip()

    return None


def _call_trading_get_item(external_id: str) -> requests.Response:
    """
    Direct Trading API GetItem using EBAY_TRADING_TOKEN (Auth'n'Auth style).
    This function does NOT touch the DB; it only does HTTP and logs.
    """
    trading_token = os.getenv("EBAY_TRADING_TOKEN", "").strip()
    if not trading_token:
        raise RuntimeError("EBAY_TRADING_TOKEN is not set in env")

    headers = {
        "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": "GetItem",
        "X-EBAY-API-SITEID": EBAY_SITE_ID,
        "X-EBAY-API-COMPATIBILITY-LEVEL": EBAY_COMPAT_LEVEL,
        # Trading API wants the Auth'n'Auth token here
        "X-EBAY-API-IAF-TOKEN": trading_token,
    }

    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{external_id}</ItemID>
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

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        logger.warning(
            "[close.ended] Trading HTTP error status=%s for external_id=%s: %s",
            resp.status_code,
            external_id,
            e,
        )

    return resp


def _parse_trading_get_item(resp: requests.Response) -> Optional[TradingSnapshot]:
    """
    Namespace-aware parse of Trading GetItem response.

    Returns a TradingSnapshot that tells us:
      - is it ended?
      - did it sell?
      - what is the final price (if any)?
      - what does Trading think the EndTime is?
    """
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        logger.error("[close.ended] XML parse fail: %s", e)
        return None

    ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}

    ack = _xml_text(root, "./ns:Ack", ns)
    if not ack or ack.strip().lower() != "success":
        short_msg = _xml_text(root, "./ns:Errors/ns:ShortMessage", ns) or ""
        long_msg = _xml_text(root, "./ns:Errors/ns:LongMessage", ns) or ""
        code = _xml_text(root, "./ns:Errors/ns:ErrorCode", ns) or ""
        logger.warning(
            "[close.ended] Trading error ack=%s code=%s short=%s long=%s",
            ack,
            code,
            short_msg,
            long_msg,
        )
        return None

    item_node = root.find(".//ns:Item", ns)
    if item_node is None:
        logger.error("[close.ended] No <Item> node in successful Trading response")
        return None

    # Listing / selling state
    listing_status = (
        _xml_text(item_node, "./ns:ListingStatus", ns)
        or _xml_text(item_node, "./ns:SellingStatus/ns:ListingStatus", ns)
        or ""
    ).lower()

    selling_state = (
        _xml_text(item_node, "./ns:SellingStatus/ns:SellingState", ns)
        or ""
    ).lower()

    bid_count_txt = _xml_text(item_node, "./ns:SellingStatus/ns:BidCount", ns)
    qty_sold_txt = _xml_text(item_node, "./ns:SellingStatus/ns:QuantitySold", ns)

    try:
        bid_count = int(bid_count_txt) if bid_count_txt else 0
    except ValueError:
        bid_count = 0

    try:
        qty_sold = int(qty_sold_txt) if qty_sold_txt else 0
    except ValueError:
        qty_sold = 0

    # Price + currency
    price_node = item_node.find("./ns:SellingStatus/ns:CurrentPrice", ns)
    final_price: Optional[float] = None
    currency: Optional[str] = None

    if price_node is not None:
        price_text = price_node.text or ""
        currency = price_node.attrib.get("currencyID")
        try:
            final_price = float(price_text) if price_text else None
        except ValueError:
            final_price = None

    # Trading's own EndTime (when it expects listing to end)
    ebay_end_time = _xml_text(item_node, "./ns:ListingDetails/ns:EndTime", ns)

    # Work out ended vs live
    if "active" in listing_status or "active" in selling_state:
        ended = False
    else:
        ended = True

    # Work out if it actually sold
    sold_flag = False
    # Typical successful sold outcome
    if "endedwithsales" in selling_state:
        sold_flag = True
    # Fixed-price with quantity sold
    elif qty_sold > 0:
        sold_flag = True
    # Completed auctions – if there were bids and a final price
    elif (
        ("completed" in listing_status or "completed" in selling_state)
        and bid_count > 0
        and final_price is not None
        and final_price > 0
    ):
        sold_flag = True

    logger.debug(
        "[close.ended] parsed listing_status=%s selling_state=%s bid_count=%s "
        "qty_sold=%s final_price=%s currency=%s ended=%s sold_flag=%s ebay_end_time=%s",
        listing_status,
        selling_state,
        bid_count,
        qty_sold,
        final_price,
        currency,
        ended,
        sold_flag,
        ebay_end_time,
    )

    return TradingSnapshot(
        listing_status=listing_status,
        selling_state=selling_state,
        bid_count=bid_count,
        qty_sold=qty_sold,
        final_price=final_price,
        currency=currency,
        ended=ended,
        sold_flag=sold_flag,
        ebay_end_time=ebay_end_time,
    )


def _load_candidates(limit: int, grace_minutes: int) -> Sequence[tuple]:
    """
    Grab the next N auctions that have ended (with a small grace window)
    and are not yet finalized.

    This function opens and closes its own DB connection so we can
    safely do Trading + usage tracking afterwards without nested contexts.
    """
    cutoff = now_utc() - timedelta(minutes=grace_minutes)
    logger.info(
        "[close.ended] selecting up to %d candidates with end_time <= %s",
        limit,
        cutoff,
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, external_id, source, end_time
                FROM auction_listings
                WHERE finalized = FALSE
                  AND end_time IS NOT NULL
                  AND end_time <= %s
                ORDER BY end_time ASC
                LIMIT %s
                """,
                (cutoff, limit),
            )
            rows = cur.fetchall()
        conn.commit()

    return rows


def _apply_updates(updates: List[Tuple[int, Optional[float], int]]) -> None:
    """
    Apply finalization updates in a single DB transaction.

    updates: list of (auction_id, final_price or None, bid_count)
    """
    if not updates:
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            for auction_id, final_price, bid_count in updates:
                if final_price is None:
                    # Ended but unsold
                    cur.execute(
                        """
                        UPDATE auction_listings
                        SET finalized = TRUE,
                            status = 'ended',
                            bids_count = %s
                        WHERE id = %s
                        """,
                        (bid_count, auction_id),
                    )
                else:
                    # Ended and sold
                    cur.execute(
                        """
                        UPDATE auction_listings
                        SET finalized = TRUE,
                            final_price = %s,
                            status = 'sold',
                            bids_count = %s
                        WHERE id = %s
                        """,
                        (final_price, bid_count, auction_id),
                    )
        conn.commit()


def run(limit: int = 10, grace_minutes: int = 30) -> None:
    """
    Close the next `limit` ended auctions in ascending end_time order.

    Flow:
      1) Load candidate rows from DB (separate connection).
      2) For each:
         - Call Trading GetItem
         - increment_api_usage("ebay_trading")
         - Interpret ended / sold / final price / ebay_end_time
      3) Apply DB updates in a single transaction.
    """
    logger.info("[close.ended] run(limit=%d, grace_minutes=%d) starting", limit, grace_minutes)

    rows = _load_candidates(limit=limit, grace_minutes=grace_minutes)

    if not rows:
        logger.info("[close.ended] no ended auctions to finalize")
        return

    logger.info("[close.ended] found %d candidate auctions", len(rows))

    # now includes bid_count in the tuple
    updates: List[Tuple[int, Optional[float], int]] = []

    for row in rows:
        auction_id, external_id, source, end_time = row
        logger.info(
            "[close.ended] processing auction_id=%s external_id=%s source=%s end_time=%s",
            auction_id,
            external_id,
            source,
            end_time,
        )

        try:
            resp = _call_trading_get_item(str(external_id))
        except Exception as e:
            logger.error(
                "[close.ended] Trading call failed for auction_id=%s external_id=%s: %s",
                auction_id,
                external_id,
                e,
            )
            continue

        # Track usage OUTSIDE any DB connection used by this action
        try:
            increment_api_usage("ebay")
        except Exception as e:
            logger.warning(
                "[close.ended] increment_api_usage failed for external_id=%s: %s",
                external_id,
                e,
            )

        snapshot = _parse_trading_get_item(resp)
        if snapshot is None:
            logger.error(
                "[close.ended] could not parse Trading response for auction_id=%s external_id=%s",
                auction_id,
                external_id,
            )
            continue

        if not snapshot.ended:
            # If Trading says it's still active, and we have a better EndTime from Trading,
            # update our DB end_time so this listing stops being selected
            if snapshot.ebay_end_time:
                try:
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE auction_listings
                                SET end_time = %s
                                WHERE id = %s
                                """,
                                (snapshot.ebay_end_time, auction_id),
                            )
                        conn.commit()
                    logger.info(
                        "[close.ended] refreshed end_time from Trading for auction_id=%s "
                        "external_id=%s new_end_time=%s",
                        auction_id,
                        external_id,
                        snapshot.ebay_end_time,
                    )
                except Exception as e:
                    logger.warning(
                        "[close.ended] failed to refresh end_time for auction_id=%s external_id=%s: %s",
                        auction_id,
                        external_id,
                        e,
                    )

            # Safety: if Trading still thinks it's active, don't finalize it.
            logger.info(
                "[close.ended] Trading says item still active; skipping finalize "
                "auction_id=%s external_id=%s (listing_status=%s selling_state=%s)",
                auction_id,
                external_id,
                snapshot.listing_status,
                snapshot.selling_state,
            )
            continue

        # Only set final_price if we are confident it actually sold
        final_price = (
            snapshot.final_price
            if snapshot.sold_flag and snapshot.final_price is not None
            else None
        )

        # include bid_count in the update tuple
        updates.append((auction_id, final_price, snapshot.bid_count))

        logger.info(
            "[close.ended] prepared finalize auction_id=%s external_id=%s final_price=%s "
            "(listing_status=%s selling_state=%s)",
            auction_id,
            external_id,
            final_price,
            snapshot.listing_status,
            snapshot.selling_state,
        )

    # Apply DB updates in a separate, clean transaction
    _apply_updates(updates)

    logger.info("[close.ended] run complete")


if __name__ == "__main__":
    # Handy manual runner:
    #   python -m agent.actions.close.ended
    run()
