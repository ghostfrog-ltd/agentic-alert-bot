from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-consoles"

    # consoles / accessories / games (your list)
    CATEGORY_IDS = [139971, 54968, 139973]

    # Old code had MAX_PAGES_PER_CAT, not needed now because API mode
    # but we keep SALE_TYPE and the keyword groups.
    SALE_TYPE = ["bin", "auction"]  # only want buy-it-now style / fixed-price style stuff

    # Optional categorisation per niche for notes / classification
    RETRO_KEYWORDS = [
        "ps1", "playstation 1", "ps2", "gamecube", "n64",
        "gba", "game boy", "snes", "nes", "dreamcast", "sega",
    ]

    MODERN_KEYWORDS = [
        "ps3", "ps4", "ps5", "xbox one", "xbox series", "switch", "steam deck",
    ]
