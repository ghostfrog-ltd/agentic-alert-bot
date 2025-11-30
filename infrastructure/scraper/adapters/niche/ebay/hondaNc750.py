# honda_nc750_adapter.py
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase
from typing import Any

class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-honda-nc750"

    # 6024 (Motors) is causing 400s with Browse API → drop it.
    CATEGORY_IDS = [179753]   # "Motorcycles & Scooters" – works

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        title = (row.get("title") or "").lower()

        if "honda" not in title:
            return False

        markers = (
            "nc750", "nc 750", "nc750s", "nc 750 s",
            "nc750x", "nc 750 x",
            # optional shared bits:
            "nc700", "nc 700", "nc700s", "nc700x",
        )
        return any(m in title for m in markers)
