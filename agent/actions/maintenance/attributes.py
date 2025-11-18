# agent/actions/maintenance/attributes.py

from __future__ import annotations

import os
import json
from typing import Optional, List, Tuple, Dict, Any

import requests
import xml.etree.ElementTree as ET

from infrastructure.db.schema import get_connection
from infrastructure.utils.usage_tracker import increment_api_usage
from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

EBAY_TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
EBAY_SITE_ID = "3"          # UK
EBAY_COMPAT_LEVEL = "967"   # compat level for GetItem


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def _extract_numeric_item_id(raw_id: str | None) -> Optional[str]:
    if not raw_id:
        return None

    if raw_id.startswith(("v1|", "v2|")):
        parts = raw_id.split("|")
        if len(parts) >= 2 and parts[1].isdigit():
            return parts[1]

    if "|" in raw_id:
        for p in raw_id.split("|"):
            if p.isdigit():
                return p

    if raw_id.isdigit():
        return raw_id

    return None


def _call_trading_get_item(item_id: str) -> Optional[str]:
    token = os.getenv("EBAY_TRADING_TOKEN", "").strip()
    if not token:
        logger.error("[maint.attributes] EBAY_TRADING_TOKEN not set")
        return None

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
  <IncludeItemSpecifics>true</IncludeItemSpecifics>
</GetItemRequest>
"""

    logger.info(f"[maint.attributes] Calling Trading GetItem for item_id={item_id}")
    try:
        resp = requests.post(
            EBAY_TRADING_ENDPOINT,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=(4, 8),
        )
    except Exception as e:
        logger.error(f"[maint.attributes] Trading request error for {item_id}: {e}")
        return None

    logger.info(f"[maint.attributes] HTTP {resp.status_code} for item_id={item_id}")

    if resp.status_code != 200:
        logger.error(resp.text[:1000])
        return None

    try:
        increment_api_usage("ebay")
    except Exception as e:
        logger.warning(f"[maint.attributes] increment_api_usage failed: {e}")

    return resp.text


def _parse_storage_gb(raw_val: Optional[str]) -> Optional[int]:
    if not raw_val:
        return None
    txt = str(raw_val).lower()
    digits = "".join(ch for ch in txt if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _extract_from_trading(xml_text: str) -> tuple[Dict[str, Optional[Any]], Dict[str, Any]]:
    attrs: Dict[str, Optional[Any]] = {
        "brand": None,
        "model_name": None,
        "storage_gb": None,
        "colour": None,
        "epid": None,
    }
    raw_map: Dict[str, Any] = {}

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"[maint.attributes] XML parse error: {e}")
        return attrs, raw_map

    ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}

    item_node = root.find(".//ns:Item", ns) or root.find(".//Item")
    if item_node is None:
        logger.error("[maint.attributes] No <Item> node found in Trading response")
        return attrs, raw_map

    # --- ProductListingDetails (for ePID + Brand) --------------------
    pld = item_node.find("./ns:ProductListingDetails", ns)
    if pld is not None:
        # ePID
        for pid in pld.findall("./ns:ProductID", ns):
            text = (pid.text or "").strip()
            if text:
                attrs["epid"] = text
                raw_map["epid"] = text
                break

        # Brand from BrandMPN
        bmpn_brand = pld.find("./ns:BrandMPN/ns:Brand", ns)
        if bmpn_brand is not None and bmpn_brand.text:
            brand_text = bmpn_brand.text.strip()
            attrs["brand"] = attrs["brand"] or brand_text
            raw_map.setdefault("Brand", brand_text)

    # --- ItemSpecifics -----------------------------------------------
    specs_node = item_node.find("./ns:ItemSpecifics", ns)
    if specs_node is not None:
        for nvl in specs_node.findall("./ns:NameValueList", ns):
            name_el = nvl.find("./ns:Name", ns)
            if name_el is None or not name_el.text:
                continue
            name_raw = name_el.text.strip()
            if not name_raw:
                continue

            name_lower = name_raw.lower()

            values = [
                (v.text or "").strip()
                for v in nvl.findall("./ns:Value", ns)
                if v.text
            ]
            if not values:
                continue

            val_single = values[0]
            joined_vals = ", ".join(values)

            raw_map[name_raw] = values if len(values) > 1 else val_single

            if name_lower == "brand" and not attrs["brand"]:
                attrs["brand"] = val_single

            elif name_lower == "model" and not attrs["model_name"]:
                attrs["model_name"] = val_single

            elif name_lower in ("colour", "color") and not attrs["colour"]:
                attrs["colour"] = val_single

            elif name_lower in ("storage capacity", "storage", "hard drive capacity", "memory"):
                if attrs["storage_gb"] is None:
                    attrs["storage_gb"] = _parse_storage_gb(val_single or joined_vals)

    return attrs, raw_map


def _load_candidates(limit: int) -> List[Tuple[int, str]]:


    sql = """
        SELECT id, external_id
        FROM auction_listings
        WHERE source LIKE 'ebay%%'
          AND status IN ('live', 'OPEN')
          AND external_id IS NOT NULL
          AND raw_attrs IS NULL
        ORDER BY id DESC
        LIMIT %s
    """

    sql = """
        SELECT id, external_id
        FROM auction_listings
        WHERE 1=1
        AND raw_attrs IS NULL
        ORDER BY id DESC
        LIMIT %s
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
        conn.commit()

    return rows


def _apply_attributes(
    auction_id: int,
    raw_attrs: Optional[Any],
    brand: Optional[str],
    model_name: Optional[str],
    storage_gb: Optional[int],
    colour: Optional[str],
    epid: Optional[str],
) -> None:

    fields: List[str] = []
    values: List[Any] = []

    # raw_attrs can be a dict OR the sentinel False -> jsonb 'false'
    if raw_attrs is not None:
        fields.append("raw_attrs = %s")
        values.append(json.dumps(raw_attrs))

    if brand is not None:
        fields.append("brand = %s")
        values.append(brand)

    if model_name is not None:
        fields.append("model_name = %s")
        values.append(model_name)

    if storage_gb is not None:
        fields.append("storage_gb = %s")
        values.append(storage_gb)

    if colour is not None:
        fields.append("colour = %s")
        values.append(colour)

    if epid is not None:
        fields.append("epid = %s")
        values.append(epid)

    if not fields:
        return

    sql = f"""
        UPDATE auction_listings
        SET {", ".join(fields)}
        WHERE id = %s
    """
    values.append(auction_id)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, values)
        conn.commit()


# -------------------------------------------------
# Public entry
# -------------------------------------------------
def run(limit: int = 20, enable_api: bool = True) -> None:
    logger.info(f"[maint.attributes] run(limit={limit}, enable_api={enable_api}) starting")

    if not enable_api:
        logger.info("[maint.attributes] enable_api=False, nothing to do")
        return

    rows = _load_candidates(limit)
    logger.info(f"[maint.attributes] loaded {len(rows)} candidate listings")

    for auction_id, external_id in rows:
        item_id = _extract_numeric_item_id(str(external_id) if external_id else None)
        logger.info(
            f"[maint.attributes] processing auction_id={auction_id} "
            f"external_id={external_id} -> item_id={item_id}"
        )

        if not item_id:
            logger.error(f"[maint.attributes] invalid numeric item_id from {external_id}")
            # mark as permanently unprocessable for attrs
            _apply_attributes(
                auction_id=auction_id,
                raw_attrs=False,   # jsonb 'false'
                brand=None,
                model_name=None,
                storage_gb=None,
                colour=None,
                epid=None,
            )
            continue

        xml_text = _call_trading_get_item(item_id)
        if not xml_text:
            logger.warning(f"[maint.attributes] no XML returned for item_id={item_id}")
            # mark as permanently unprocessable for attrs
            _apply_attributes(
                auction_id=auction_id,
                raw_attrs=False,
                brand=None,
                model_name=None,
                storage_gb=None,
                colour=None,
                epid=None,
            )
            continue

        attrs, raw_map = _extract_from_trading(xml_text)

        logger.info(
            f"[maint.attributes] extracted attrs for id={auction_id}: "
            f"brand={attrs['brand']!r}, model={attrs['model_name']!r}, "
            f"storage={attrs['storage_gb']!r}, colour={attrs['colour']!r}, "
            f"epid={attrs['epid']!r}"
        )
        logger.info(
            f"[maint.attributes] raw_attrs keys for id={auction_id}: {list(raw_map.keys())}"
        )

        if not raw_map and not any(attrs.values()):
            logger.warning(
                f"[maint.attributes] Trading returned no usable attributes for auction_id={auction_id} "
                f"– marking raw_attrs=false"
            )
            _apply_attributes(
                auction_id=auction_id,
                raw_attrs=False,
                brand=None,
                model_name=None,
                storage_gb=None,
                colour=None,
                epid=None,
            )
            continue

        _apply_attributes(
            auction_id=auction_id,
            raw_attrs=raw_map if raw_map else None,
            brand=attrs["brand"],
            model_name=attrs["model_name"],
            storage_gb=attrs["storage_gb"],
            colour=attrs["colour"],
            epid=attrs["epid"],
        )

    logger.info("[maint.attributes] run complete")
