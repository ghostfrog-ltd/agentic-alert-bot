from __future__ import annotations

from typing import Any

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-tools"

    CATEGORY_IDS = [
        3247,  # Power Tools
    ]

    # Same as your other modern niches
    SALE_TYPE = ["auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Keep only listings that are very likely power tools.
        Super conservative: brand or obvious tool words in title.
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
        )

        # Generic tool-type hints
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
        )

        return any(k in title for k in brand_keywords) or any(k in title for k in tool_keywords)
