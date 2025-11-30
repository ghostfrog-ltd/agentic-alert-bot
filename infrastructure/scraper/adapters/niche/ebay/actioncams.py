from __future__ import annotations

from typing import Any
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):

    DOMAIN = "ebay-actioncams"

    CATEGORY_IDS = [
        11724,    # Camcorders
        179697,   # Camera & Drone
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        t = (row.get("title") or "").lower()

        # ---------------------------------------------------------
        # 1) Obvious accessories → DROP unless "camera" is explicit
        # ---------------------------------------------------------
        accessory_words = (
            "mount",
            "screw",
            "frame",
            "case",
            "housing",
            "battery",
            "charger",
            "charging",
            "cage",
            "lens cap",
            "cap",
            "adapter",
            "tripod",
            "handle",
            "grip",
            "floaty",
            "strap",
            "harness",
            "backpack",
            "holder",
            "clip",
        )

        # if it's an accessory AND does not explicitly say it's a camera → reject
        if any(w in t for w in accessory_words) and "camera" not in t:
            return False

        # ---------------------------------------------------------
        # 2) GoPro / Hero series
        # ---------------------------------------------------------
        gopro_words = (
            "gopro",
            "hero",
            "hero 4",
            "hero 5",
            "hero 6",
            "hero 7",
            "hero 8",
            "hero 9",
            "hero 10",
            "hero 11",
            "hero 12",
            "hero4",
            "hero5",
            "hero6",
            "hero7",
            "hero8",
            "hero9",
            "hero10",
            "hero11",
            "hero12",
            "max 360",
            "gopro max",
        )
        if any(k in t for k in gopro_words):
            return True

        # ---------------------------------------------------------
        # 3) DJI / Insta360
        # ---------------------------------------------------------
        if "osmo action" in t or "dji action" in t or "action 2" in t or "action 3" in t or "action 4" in t:
            return True

        if "insta360" in t:
            return True

        # ---------------------------------------------------------
        # 4) Generic but real action cam terms
        # (Avoids random CCTV or camcorders)
        # ---------------------------------------------------------
        generic_cam_words = (
            "action cam",
            "action camera",
            "sports cam",
            "sport camera",
        )
        if any(w in t for w in generic_cam_words):
            return True

        # ---------------------------------------------------------
        # 5) Secondary brands (cheap but real cameras)
        # ---------------------------------------------------------
        secondary_brands = (
            "akaso",
            "apeman",
            "campark",
            "eken",
            "yi 4k",
            "xiaoyi",
        )
        if any(b in t for b in secondary_brands):
            return True

        # ---------------------------------------------------------
        # 6) Fallback model_key
        # ---------------------------------------------------------
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("camera_") or mk.startswith("actioncam_") or mk.startswith("gopro_") or mk.startswith("dji_"):
            return True

        return False
