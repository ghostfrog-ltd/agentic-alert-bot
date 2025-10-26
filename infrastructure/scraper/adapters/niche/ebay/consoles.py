# infrastructure/scraper/adapters/niche/ebay/consoles.py
from __future__ import annotations
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-consoles"
    CATEGORY_IDS = [139971, 54968, 139973]  # consoles / accessories / games
    MAX_PAGES_PER_CAT = 8
    SALE_TYPE = "bin"

    # optional categorisation per niche
    RETRO_KEYWORDS = [
        "ps1", "playstation 1", "ps2", "gamecube", "n64",
        "gba", "game boy", "snes", "nes", "dreamcast", "sega",
    ]
    MODERN_KEYWORDS = [
        "ps3", "ps4", "ps5", "xbox one", "xbox series", "switch", "steam deck",
    ]
