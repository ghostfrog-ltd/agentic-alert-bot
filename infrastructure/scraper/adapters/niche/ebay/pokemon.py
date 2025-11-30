from __future__ import annotations
from typing import Any
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-pokemon"

    CATEGORY_IDS = [
        2611,      # Pokémon individual cards
        183454,    # Pokémon sealed products
        183454,    # Sealed TCG products (boxes, ETBs, tins)
        183448,    # Card lots
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        title = (row.get("title") or "").lower().strip()

        # ---------------------------------------------------
        # 1) HARD FILTER: junk we never want
        # ---------------------------------------------------
        trash_words = (
            "sleeve", "sleeves",
            "binder",
            "portfolio",
            "folder",
            "playmat", "play mat",
            "mat only",
            "empty tin", "tin only",
            "coin",
            "dice",
            "token",
            "digital", "code card", "online code",
            "sticker",
            "fake", "proxy",
            "toy", "figure", "plush",
            "deck box", "storage box",
            "manual", "rulebook", "rule book",
        )
        if any(w in title for w in trash_words):
            return False

        # ---------------------------------------------------
        # 2) Strong Pokémon signals
        # ---------------------------------------------------
        poke_words = (
            "pokemon",
            "pokémon",
            "pkmn",
            "tcg",
            "booster",
            "etb",
            "elite trainer box",
            "trainer box",
            "ultra premium",
        )
        if any(w in title for w in poke_words):
            return True

        # ---------------------------------------------------
        # 3) Graded card signals (PSA/BGS/CGC)
        # ---------------------------------------------------
        graded_words = (
            "psa ",
            "psa-",
            "bgs ",
            "cgc ",
            "sgc ",
            "graded",
            "slab",
        )
        if any(w in title for w in graded_words):
            # Must still be Pokémon-related
            if "pokemon" in title or "pokémon" in title or "pkmn" in title:
                return True

        # ---------------------------------------------------
        # 4) Fallback numeric detection – set/collector numbers
        # This catches titles like:
        #   "Charizard 4/102"
        #   "Mewtwo 10/130"
        # ---------------------------------------------------
        import re
        if re.search(r"\b\d+\/\d+\b", title) and "pokemon" in title:
            return True

        # ---------------------------------------------------
        # 5) model_key fallback
        # ---------------------------------------------------
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("pokemon_") or mk.startswith("pkmn_"):
            return True

        return False
