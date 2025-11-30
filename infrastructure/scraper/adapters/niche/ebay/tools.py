from __future__ import annotations

from typing import Any

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-tools"

    CATEGORY_IDS = [
        3247,  # Power Tools
    ]

    # Same as your other modern niches
    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Keep only listings that are very likely actual power tools.
        Conservative:
          - must look like a tool (tool word or strong brand+voltage)
          - aggressively drop obvious accessories/consumables if no tool word
        """
        title = (row.get("title") or "").lower()

        # Common power-tool brands
        brand_keywords = (
            "makita",
            "dewalt",
            "bosch",
            "milwaukee",
            "ryobi",
            "hilti",
            "hitachi",
            "metabo",
            "einhell",
            "festool",
            "parkside",
            "black+decker",
            "black and decker",
        )

        # Generic tool-type hints (actual tools)
        tool_keywords = (
            "drill",
            "driver",
            "impact driver",
            "impact wrench",
            "sds",
            "hammer drill",
            "combi drill",
            "angle grinder",
            "grinder",
            "circular saw",
            "jigsaw",
            "reciprocating saw",
            "recip saw",
            "multitool",
            "multi tool",
            "nail gun",
            "nailer",
            "rotary hammer",
            "planer",
            "router",
            "heat gun",
            "sds+",
            "sds plus",
        )

        # Things we consider "just accessories/consumables"
        accessory_keywords = (
            "drill bit",
            "drill bits",
            "bit set",
            "driver bits",
            "screwdriver bits",
            "blade",
            "blades",
            "saw blade",
            "saw blades",
            "cutting disc",
            "cutting discs",
            "grinding disc",
            "grinding discs",
            "sanding disc",
            "sanding discs",
            "sanding sheets",
            "hole saw",
            "hole saws",
            "holesaw",
            "holesaws",
            "battery",
            "batteries",
            "charger",
            "chargers",
            "tool bag",
            "toolbox",
            "tool box",
            "carry case",
            "case only",
            "stacking case",
            "tstak",
            "l-boxx",
            "l boxx",
            "sortimo",
            "insert tray",
            "foam insert",
        )

        voltage_keywords = (
            "10.8v",
            "12v",
            "14.4v",
            "18v",
            "20v",
            "36v",
            "40v",
            "54v",
            "cordless",
        )

        has_brand = any(k in title for k in brand_keywords)
        has_tool_word = any(k in title for k in tool_keywords)
        has_accessory_word = any(k in title for k in accessory_keywords)
        has_voltage_hint = any(k in title for k in voltage_keywords)

        # 1) If it looks like pure accessory/consumable and has no tool word → drop
        if has_accessory_word and not has_tool_word:
            return False

        # 2) If there's a clear tool word → keep it
        if has_tool_word:
            return True

        # 3) Brand + voltage (e.g. "DeWalt 18V DCD796") → probably a bare tool even
        #    if "drill" isn't in the title text
        if has_brand and has_voltage_hint and not has_accessory_word:
            return True

        # 4) Fallback on model_key (if your pipeline sets something for tools)
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("tool_") or mk.startswith("powertool_"):
            return True

        # 5) Everything else is probably junk / accessories / mismatch
        return False
