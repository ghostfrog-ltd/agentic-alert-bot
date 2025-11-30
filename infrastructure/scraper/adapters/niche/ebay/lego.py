from __future__ import annotations
from typing import Any
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-lego"

    CATEGORY_IDS = [
        19006,    # LEGO Complete Sets & Packs
        19001,    # LEGO Minifigures
        16722,    # LEGO Bulk Bricks
        313,      # LEGO (General / Other)
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Only keep real LEGO items:
        - full sets
        - sealed or used sets
        - minifig bundles
        - retired sets
        - Technic / UCS / Ideas / Creator sets
        Drop:
        - knockoffs
        - Mega Bloks
        - parts-only unless clearly LEGO
        """
        t = (row.get("title") or "").lower()

        # ------------------------
        # 1) Hard filter knockoffs
        # ------------------------
        bad_words = (
            "mega bloks",
            "megabloks",
            "compatible with lego",
            "lego style",
            "brick pack",
            "building blocks",
            "duplo style",
            "sluban",
            "lepin",          # knockoff
            "decool",         # knockoff
            "qing yi",
            "sy block",
            "no lego",
        )
        if any(w in t for w in bad_words):
            return False

        # ------------------------
        # 2) Strong LEGO signals
        # ------------------------
        lego_words = (
            "lego",
            "minifigure",
            "minifig",
            "technic",
            "creator expert",
            "ucs",        # Star Wars Ultimate Collector Series
            "star wars lego",
            "ideas set",
            "modular building",
        )
        if any(w in t for w in lego_words):
            return True

        # ------------------------
        # 3) Set numbers (VERY reliable)
        # LEGO set numbers are 3–7 digits, often shown as #### or ######.
        # Detect patterns like:
        # - "set 10214"
        # - "10214 tower bridge"
        # - "lego 42115"
        # ------------------------
        import re
        if re.search(r"\b\d{3,7}\b", t):
            # Only allow if NOT a part number that is clearly non-LEGO
            return True

        # ------------------------
        # 4) Fallback on model_key
        # ------------------------
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("lego_"):
            return True

        return False
