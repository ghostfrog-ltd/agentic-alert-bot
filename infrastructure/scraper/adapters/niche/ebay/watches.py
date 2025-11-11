from __future__ import annotations
from typing import Any
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-watches"

    CATEGORY_IDS = [
        31387,  # Wristwatches
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        t = (row.get("title") or "").lower()

        # obvious noise we don't want
        bad_words = (
            "strap", "band", "bracelet", "buckle", "link",
            "case only", "box only", "spares", "repair",
            "movement only", "dial only", "battery", "tool", "holder",
        )
        if any(k in t for k in bad_words):
            return False

        # likely actual watches
        good_words = (
            "watch", "chrono", "chronograph", "automatic", "mechanical", "diver", "smartwatch",
        )
        if not any(k in t for k in good_words):
            return False

        # whitelist known brands
        brands = (
            "seiko", "citizen", "casio", "omega", "rolex", "tag", "heuer", "tissot",
            "oris", "longines", "breitling", "hamilton", "rado", "garmin", "suunto",
            "apple watch", "samsung watch"
        )
        if any(b in t for b in brands):
            return True

        # fallback: keep if model_key is clearly a watch
        mk = (row.get("model_key") or "").lower()
        return mk.startswith("seiko_") or mk.startswith("casio_") or mk.startswith("watch_")
