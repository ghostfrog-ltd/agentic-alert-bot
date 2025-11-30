from __future__ import annotations

from typing import Any

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-samsung"

    CATEGORY_IDS = [
        9355,  # Mobile Phones & Smartphones
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Keep actual Samsung phones (and maybe tablets later),
        not cases, chargers, glass, skins, etc.
        """
        title = (row.get("title") or "").lower()

        # -----------------------------
        # 1) obvious accessory / junk
        # -----------------------------
        accessory_keywords = (
            "case",
            "cover",
            "bumper",
            "wallet",
            "folio",
            "flip cover",
            "screen protector",
            "tempered glass",
            "glass protector",
            "protector",
            "film",
            "skin",
            "sticker",
            "decal",

            "charger",
            "charging cable",
            "charging lead",
            "cable",
            "lead",
            "plug",
            "mains adapter",
            "power adapter",
            "power adaptor",
            "dock",
            "docking station",
            "desk dock",
            "holder",
            "car holder",
            "car mount",
            "bike mount",

            "battery",
            "battery door",
            "back cover",
            "back glass",
            "housing",
            "frame",

            "box only",
            "empty box",
            "retail box",
            "dummy phone",
            "display phone",
            "non-working display",
        )
        has_accessory_word = any(k in title for k in accessory_keywords)

        # if clearly just an accessory → bin it
        if has_accessory_word and "phone" not in title and "galaxy" not in title:
            return False

        # -----------------------------
        # 2) structured brand check
        # -----------------------------
        brand = (row.get("attr_brand") or "").lower()
        if brand == "samsung" and not has_accessory_word:
            return True

        # -----------------------------
        # 3) device-family keywords
        # -----------------------------
        device_keywords = (
            # phones
            "galaxy s",
            "galaxy s20",
            "galaxy s21",
            "galaxy s22",
            "galaxy s23",
            "galaxy s24",
            "s20 ultra",
            "s21 ultra",
            "s22 ultra",
            "s23 ultra",
            "s24 ultra",
            "s20 fe",
            "s21 fe",

            "galaxy note",
            "note 10",
            "note 10+",
            "note 20",
            "note 20 ultra",

            "galaxy z fold",
            "z fold2",
            "z fold 2",
            "z fold3",
            "z fold 3",
            "z fold4",
            "z fold 4",
            "z fold5",
            "z fold 5",
            "z flip",
            "z flip3",
            "z flip 3",
            "z flip4",
            "z flip 4",
            "z flip5",
            "z flip 5",

            # midrange A-series you might still flip
            "galaxy a",
            "a52",
            "a53",
            "a54",
            "a55",

            # in case title is generic but still obviously a Samsung phone
            "samsung galaxy",
            "samsung phone",
            "android smartphone",
        )
        has_device_word = any(k in title for k in device_keywords)

        if ("samsung" in title or has_device_word) and not has_accessory_word:
            return True

        # For safety, we allow some titles where Samsung + phone is clear,
        # even if accessory words exist (e.g. "Samsung Galaxy S23 with box & charger")
        if ("samsung" in title or "galaxy" in title) and "phone" in title:
            return True

        # -----------------------------
        # 4) fallback: model_key says it's a Samsung device
        # -----------------------------
        mk = (row.get("model_key") or "").lower()
        if (
            mk.startswith("samsung_")
            or mk.startswith("galaxy_")
            or mk.startswith("phone_samsung_")
        ):
            return True

        return False
