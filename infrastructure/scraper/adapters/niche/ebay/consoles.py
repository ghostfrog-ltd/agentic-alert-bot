from __future__ import annotations

from typing import Any
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-consoles"
    CATEGORY_IDS = [139971, 54968, 139973]
    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Only accept actual consoles.
        We rely on attrs first, title second, model_key fallback third.
        """

        # -----------------------------
        # 1) Primary check: structured attrs
        # -----------------------------
        item_type = (row.get("attr_type") or "").lower()
        brand     = (row.get("attr_brand") or "").lower()
        model     = (row.get("attr_model") or "").lower()

        # if type explicitly says "console" → accept
        if item_type == "console":
            return True

        # If brand+model look like a console (e.g., ps1, ps2, ps3, ps4, ps5, xbox series s/x, switch)
        # You already said "read the CSV" meaning attrs are clean now.
        known_console_brands = ("sony", "microsoft", "nintendo", "sega")
        if brand in known_console_brands and model:
            # Model being present is already enough — accessories usually won't have model fields
            return True

        # -----------------------------
        # 2) Fallback: title heuristics
        # -----------------------------
        t = (row.get("title") or "").lower()

        title_markers = (
            "ps1", "ps2", "ps3", "ps4", "ps5",
            "xbox", "series x", "series s", "one x", "one s",
            "nintendo switch", "switch oled", "switch lite",
            "wii", "wii u",
            "gamecube",
            "mega drive", "genesis", "dreamcast",
            "snes", "super nintendo", "nes", "n64",
            "master system",
        )

        if any(k in t for k in title_markers):
            return True

        # -----------------------------
        # 3) Fallback: model_key already set as console_*
        # -----------------------------
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("console_"):
            return True

        # -----------------------------
        # 4) Otherwise irrelevant
        # -----------------------------
        return False
