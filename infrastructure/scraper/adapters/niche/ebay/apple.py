from __future__ import annotations

from typing import Any

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-apple"

    CATEGORY_IDS = [
        9355,    # Mobile Phones & Smartphones
        171485,  # Tablets & eBook Readers
        111422,  # Laptops & Netbooks
        179,     # Desktop PCs
        15032,   # iPods & MP3 players / some Apple audio
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Keep only listings that are very likely Apple *devices*:
        iPhone / iPad / Mac / Watch / AirPods / AirTag / iPod / Apple TV.
        Drop obvious accessories like cases, screen protectors, cables, etc.
        """
        title = (row.get("title") or "").lower()

        # -----------------------------
        # 1) Obvious accessory / junk words
        # -----------------------------
        accessory_keywords = (
            "case",
            "cover",
            "bumper",
            "folio",
            "wallet",
            "screen protector",
            "tempered glass",
            "glass protector",
            "protector",
            "film",
            "skin",
            "sticker",
            "decal",
            "housing",
            "shell",
            "frame",
            "bezel",
            "back glass",
            "front glass",
            "digitizer",

            "strap",
            "band",
            "loop",
            "bracelet",

            "cable",
            "lead",
            "charger",
            "charging cable",
            "charging lead",
            "power adapter",
            "power adaptor",
            "plug",
            "dock",
            "docking station",
            "stand",
            "holder",
            "mount",

            "keyboard cover",
            "keycaps",
            "skin for macbook",
            "skin for iphone",

            "box only",
            "empty box",
            "retail box",
            "dummy phone",
            "display phone",
            "non working display",
        )

        has_accessory_word = any(k in title for k in accessory_keywords)

        # if it's clearly just an accessory and not explicitly "airpods"/"airtag" themselves → drop
        # (AirPods & AirTag are high-value devices we *do* want)
        core_small_device_words = ("airpods", "airpod", "air tag", "airtag")
        if has_accessory_word and not any(w in title for w in core_small_device_words):
            return False

        # -----------------------------
        # 2) Structured brand check
        # -----------------------------
        brand = (row.get("attr_brand") or "").lower()
        if brand == "apple" and not has_accessory_word:
            return True

        # -----------------------------
        # 3) Strong Apple / device-family hints
        # -----------------------------
        device_keywords = (
            # phones
            "iphone",
            # tablets
            "ipad",
            # laptops / desktops
            "macbook",
            "mac book",
            "macbook pro",
            "macbook air",
            "imac",
            "mac mini",
            "mac pro",
            # audio / small stuff
            "airpods",
            "airpod",
            "airpods pro",
            "airpods max",
            "airtag",
            "air tag",
            "ipod",
            # watch
            "apple watch",
            "watch series",
            "watch se",
            # tv
            "apple tv",
        )

        has_device_word = any(k in title for k in device_keywords)

        # "apple" by itself is noisy (e.g. "apple iphone case"),
        # but we already dropped accessory-only above.
        if "apple" in title and not has_accessory_word:
            return True

        if has_device_word and not has_accessory_word:
            return True

        # For AirPods / AirTag we allow them even with accessory words above,
        # because the main thing can still be the device (e.g. "AirPods Pro with case").
        if any(w in title for w in core_small_device_words):
            return True

        # -----------------------------
        # 4) Fallback: model_key already classified as Apple device
        # -----------------------------
        mk = (row.get("model_key") or "").lower()
        if (
            mk.startswith("iphone_")
            or mk.startswith("ipad_")
            or mk.startswith("macbook_")
            or mk.startswith("imac_")
            or mk.startswith("macmini_")
            or mk.startswith("macpro_")
            or mk.startswith("watch_")
            or mk.startswith("airpods_")
            or mk.startswith("airtag_")
            or mk.startswith("ipod_")
            or mk.startswith("apple_tv_")
        ):
            return True

        # -----------------------------
        # 5) Everything else → not relevant
        # -----------------------------
        return False
