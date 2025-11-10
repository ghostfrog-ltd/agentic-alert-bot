from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-apple"

    CATEGORY_IDS = [
        9355,    # Mobile Phones & Smartphones
        171485,  # Tablets & eBook Readers
        111422,  # Laptops & Netbooks
        179,     # Desktop PCs
        15032,   # iPods & MP3 players / some Apple audio
    ]

    SALE_TYPE = ["auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Keep only listings that are very likely Apple gear.
        """
        title = (row.get("title") or "").lower()

        # Strong Apple hints
        if "apple" in title:
            return True

        # Product-family hints even if 'apple' is missing
        keywords = (
            "iphone",
            "ipad",
            "macbook",
            "imac",
            "mac mini",
            "mac pro",
            "airpods",
            "apple watch",
            "airtag",      # AirTags
            "air tag",     # Air tag spacing variants
            "ipod",
        )
        return any(k in title for k in keywords)
