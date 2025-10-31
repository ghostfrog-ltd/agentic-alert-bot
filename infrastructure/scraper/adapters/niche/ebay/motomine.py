from __future__ import annotations
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):
    DOMAIN = "motomine"

    # Pull ONLY from this specific seller (Moto Mine / Surrey Motorcycle Salvage).
    FETCH_MODE = "seller"
    SELLER_USERNAME = "motomine"  # critical fix

    # We only care about live bidding inventory.
    # No BIN / fixed price stock.
    SALE_TYPE = ["auction"]

    # Don't try to force categories.
    CATEGORY_IDS: list[int] = []

    # We're not classifying retro vs modern from title keywords (for now).
    RETRO_KEYWORDS: list[str] = []
    MODERN_KEYWORDS: list[str] = []
