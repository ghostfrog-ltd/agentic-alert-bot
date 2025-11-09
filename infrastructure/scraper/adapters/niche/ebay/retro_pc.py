from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):

    DOMAIN = "ebay-retro-pc"

    CATEGORY_IDS = [
        11189,  # Vintage Computing
        27386,  # Graphics / Video Cards
        44980,  # Sound Cards (Internal)
        51197,  # Motherboards
        164,  # CPUs / Processors
        170083,  # ram
        165,  # hdds
    ]

    SALE_TYPE = ["bin", "auction"] # "bin" == Buy It Now / fixed price
