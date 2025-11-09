from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):
    DOMAIN = "watches"

    # consoles / accessories / games (your list)
    CATEGORY_IDS = [
        31387 #wristwatches
    ]

    SALE_TYPE = ["bin", "auction"]