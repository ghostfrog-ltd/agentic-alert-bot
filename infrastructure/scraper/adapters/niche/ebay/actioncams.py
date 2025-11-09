from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):

    DOMAIN = "ebay-actioncams"

    CATEGORY_IDS = [
        11724,  # Camcorders
        179697,  # Camera & Drone
    ]

    SALE_TYPE = ["bin", "auction"]
