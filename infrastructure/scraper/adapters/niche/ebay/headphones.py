from __future__ import annotations

from typing import Any

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-headphones"

    CATEGORY_IDS = [
        112529,  # Headphones
        # add more audio/headset cats if you use them
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Keep actual headphones / earphones / earbuds from good brands.
        Drop obvious accessories: cases, cables, pads, stands, etc.
        """
        title = (row.get("title") or "").lower().strip()

        # ------------------------------------
        # 1) obvious accessory / junk
        # ------------------------------------
        accessory_keywords = (
            "case",
            "carry case",
            "carrying case",
            "hard case",
            "soft case",
            "pouch",
            "bag",
            "travel bag",
            "storage bag",

            "ear pad",
            "earpad",
            "earpads",
            "ear pads",
            "ear cushion",
            "ear cushions",
            "ear tips",
            "eartips",
            "ear tip",
            "foam tips",
            "replacement pads",
            "replacement cushions",

            "headband cover",
            "headband cushion",
            "headband pad",

            "cable",
            "lead",
            "aux cable",
            "audio cable",
            "extension cable",
            "replacement cable",
            "adapter",
            "adaptor",
            "jack adapter",
            "jack adaptor",

            "stand",
            "hanger",
            "hook",

            "box only",
            "empty box",
            "retail box",
            "shell only",
        )
        has_accessory_word = any(k in title for k in accessory_keywords)

        # ------------------------------------
        # 2) headphone-ish words
        # ------------------------------------
        headphone_keywords = (
            "headphone",
            "headphones",
            "headset",
            "gaming headset",
            "earphone",
            "earphones",
            "earbud",
            "earbuds",
            "in-ear",
            "in ear",
            "on-ear",
            "on ear",
            "over-ear",
            "over ear",
            "wireless earbuds",
            "true wireless",
            "noise cancelling",
            "noise-cancelling",
            "anc",
        )
        has_headphone_word = any(k in title for k in headphone_keywords)

        # if it clearly looks like just an accessory and doesn't mention headphones/earbuds → bin
        if has_accessory_word and not has_headphone_word:
            return False

        # ------------------------------------
        # 3) brand whitelist
        # ------------------------------------
        brand = (row.get("attr_brand") or "").lower()

        brand_keywords = (
            "sony",
            "bose",
            "sennheiser",
            "beats",
            "jbl",
            "beyerdynamic",
            "beyer",
            "akg",
            "shure",
            "bang & olufsen",
            "bang&olufsen",
            "b&o",
            "marshall",
            "audio-technica",
            "audiotechnica",
            "skullcandy",
            "an ker",     # covers some typo-ish variants
            "soundcore",
            "anker",
            "steelseries",
            "razer",
            "logitech",
        )

        has_brand_in_title = any(b in title for b in brand_keywords)

        # structured brand is best
        if brand in ("sony", "bose", "sennheiser", "beats", "beyerdynamic", "akg", "shure", "jbl", "bang & olufsen", "audio-technica"):
            if not has_accessory_word:
                return True

        # title-based brand + headphone keyword
        if has_brand_in_title and has_headphone_word and not has_accessory_word:
            return True

        # allow titles like "Sony WH-1000XM4" even if "headphones" missing, as long as no accessory word
        model_tokens = (
            "wh-1000xm3",
            "wh-1000xm4",
            "wh-1000xm5",
            "wf-1000xm3",
            "wf-1000xm4",
            "wf-1000xm5",
            "qc35",
            "qc 35",
            "qc45",
            "qc 45",
            "quietcomfort 35",
            "quietcomfort 45",
            "700 headphones",
            "momentum 3",
            "momentum 4",
            "hd 560s",
            "hd560s",
            "hd 599",
            "hd599",
        )
        has_iconic_model = any(tok in title for tok in model_tokens)
        if (has_iconic_model or ("sony" in title or "bose" in title or "sennheiser" in title)) and not has_accessory_word:
            return True

        # ------------------------------------
        # 4) fallback on model_key
        # ------------------------------------
        mk = (row.get("model_key") or "").lower()
        if (
            mk.startswith("hp_")
            or mk.startswith("headphone_")
            or mk.startswith("headset_")
            or mk.startswith("earbud_")
            or mk.startswith("sony_")
            or mk.startswith("bose_")
            or mk.startswith("sennheiser_")
        ):
            return True

        # ------------------------------------
        # 5) otherwise not relevant
        # ------------------------------------
        return False
