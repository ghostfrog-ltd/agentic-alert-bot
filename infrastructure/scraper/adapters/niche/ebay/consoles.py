from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-consoles"

    # consoles / accessories / games (your list)
    CATEGORY_IDS = [139971, 54968, 139973]

    SALE_TYPE = ["auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        t = (row.get("title") or "").lower()

        # anything that obviously contains the brand name "playstation", "xbox", etc.
        if "playstation" in t or "ps5" in t or "ps4" in t or "ps3" in t:
            return True
        if "xbox" in t:
            return True
        if "nintendo switch" in t or "switch oled" in t or "wii" in t or "gamecube" in t:
            return True

        # retro consoles / Sega
        retro_console_words = (
            "mega drive",
            "genesis",
            "dreamcast",
            "snes",
            "super nintendo",
            "nes",
            "n64",
            "master system",
        )
        if any(k in t for k in retro_console_words):
            return True

        # Fallback: if the model_key already looks like a console_* classification, keep it
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("console_"):
            return True

        # Otherwise it's probably games/accessories/PC junk in the same category → drop
        return False