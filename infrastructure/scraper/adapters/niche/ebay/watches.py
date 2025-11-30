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

        # -------------------------------------------------
        # 1) Obvious non-watch / accessory junk
        #    But allow titles that clearly say "watch"
        #    to avoid killing "watch + strap" style bundles.
        # -------------------------------------------------
        bad_words = (
            "strap", "band", "bracelet", "buckle", "links", "link",
            "case only", "box only", "empty box",
            "spares", "repair", "parts only",
            "movement only", "dial only",
            "battery", "tool", "holder",
        )
        has_bad_word = any(k in t for k in bad_words)

        if has_bad_word and "watch" not in t:
            # "strap", "band", "empty box", etc. *without* "watch" → accessory
            return False

        # -------------------------------------------------
        # 2) Must look like an actual watch
        # -------------------------------------------------
        good_words = (
            "watch",
            "chrono",
            "chronograph",
            "automatic",
            "mechanical",
            "diver",
            "smartwatch",
            "field watch",
            "dress watch",
            "pilot watch",
            "gmt",
        )
        has_good_word = any(k in t for k in good_words)

        if not has_good_word:
            # no hint it's even a watch
            return False

        # -------------------------------------------------
        # 3) Brand whitelist – bias to stuff we care about
        # -------------------------------------------------
        brands = (
            "seiko",
            "citizen",
            "casio",
            "g-shock",
            "g shock",
            "omega",
            "rolex",
            "tag heuer",
            "tag-heuer",
            "heuer",
            "tissot",
            "oris",
            "longines",
            "breitling",
            "hamilton",
            "rado",
            "bulova",
            "panerai",
            "patek",
            "audemars",
            "swatch",
            "garmin",
            "suunto",
            "apple watch",
            "samsung watch",
            "vostok",
        )
        has_brand = any(b in t for b in brands)

        if has_brand:
            return True

        # -------------------------------------------------
        # 4) Fallback: model_key saying it's a watch
        # -------------------------------------------------
        mk = (row.get("model_key") or "").lower()

        if (
            mk.startswith("seiko_")
            or mk.startswith("casio_")
            or mk.startswith("citizen_")
            or mk.startswith("omega_")
            or mk.startswith("rolex_")
            or mk.startswith("watch_")
        ):
            return True

        # -------------------------------------------------
        # 5) Anything else is probably junk/mismatch
        # -------------------------------------------------
        return False
