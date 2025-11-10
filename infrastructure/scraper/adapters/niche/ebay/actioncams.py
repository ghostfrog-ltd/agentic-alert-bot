from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase

class Adapter(EbayAdapterBase):

    DOMAIN = "ebay-actioncams"

    CATEGORY_IDS = [
        11724,  # Camcorders
        179697,  # Camera & Drone
    ]

    SALE_TYPE = ["auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        t = (row.get("title") or "").lower()

        # GoPro & Hero series
        gopro_words = (
            "gopro",
            "hero 5",
            "hero 6",
            "hero 7",
            "hero 8",
            "hero 9",
            "hero 10",
            "hero 11",
            "hero 12",
            "hero11",
            "hero10",
        )
        if any(k in t for k in gopro_words):
            return True

        # DJI and Insta360
        if "osmo action" in t or "dji action" in t:
            return True
        if "insta360" in t:
            return True

        # Generic "action camera" words
        if "action cam" in t or "action camera" in t:
            return True

        # Fallback on model_key classification if you’ve got camera-specific keys
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("camera_") or mk.startswith("actioncam_"):
            return True

        return False
