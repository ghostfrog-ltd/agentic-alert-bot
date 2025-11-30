from __future__ import annotations
from typing import Any
from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    DOMAIN = "ebay-retro-pc"

    CATEGORY_IDS = [
        11189,   # Vintage Computing
        27386,   # Graphics / Video Cards
        44980,   # Sound Cards (Internal)
        51197,   # Motherboards
        164,     # CPUs
        170083,  # RAM
        165,     # HDDs
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        t = (row.get("title") or "").lower().strip()

        # -----------------------------------------------------------
        # 🚫 1. HARD FILTER – Anything obviously modern → DROP
        # -----------------------------------------------------------
        MODERN_TRASH = (
            "rtx", "gtx", "rx ", "ddr4", "ddr5", "ryzen",
            "intel i9", "intel i7", "intel i5", "intel i3",
            "3060", "3070", "3080", "3090",
            "4060", "4070", "4080", "4090",
            "nvidia 16", "nvidia 20", "nvidia 30", "nvidia 40",
            "m2", "nvme", "pcie 4", "pcie 5",
            "usb 3", "rgb", "aio cooler", "gaming pc",
            "z490", "z590", "b550", "x570", "lga1200", "lga1700",
        )
        if any(k in t for k in MODERN_TRASH):
            return False

        # -----------------------------------------------------------
        # ✅ 2. STRONG RETRO SIGNALS
        # -----------------------------------------------------------
        RETRO_WORDS = (
            # GPUs
            "3dfx", "voodoo", "tnt2", "rage 128", "rage pro", "geforce2", "geforce 2",
            "geforce3", "geforce 3", "geforce4", "geforce 4",
            "matrox", "g200", "g400", "g450", "g550", "s3 virge", "s3 trio",
            "cirrus logic", "tseng", "et4000",
            # CPUs
            "pentium", "pentium ii", "pentium iii", "486", "386",
            "athlon", "k6", "k6-2", "k6-3", "duron",
            "slot 1", "slot a", "socket 7", "socket a", "socket 370",
            # Sound
            "sound blaster", "awe64", "sb16", "sb32", "gravis", "adlib",
            # Motherboards
            "isa", "v lb", "vlb", "agp", "baby at", "at motherboard",
            # Complete systems
            "retro pc", "retro computer", "dos pc", "ms-dos",
            "windows 95", "win95", "windows 98", "win98",
            "old pc", "classic pc", "vintage computer",
        )

        if any(k in t for k in RETRO_WORDS):
            return True

        # -----------------------------------------------------------
        # 👍 3. CATEGORY 11189 = Vintage Computing → whitelist lightly
        # -----------------------------------------------------------
        category_id = row.get("category_id")
        if category_id == 11189:
            # As long as it isn't modern trash, keep it
            return True

        # -----------------------------------------------------------
        # 👍 4. Fallback: model_key
        # -----------------------------------------------------------
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("retro_") or mk.startswith("gpu_") or mk.startswith("soundcard_") or mk.startswith("cpu_"):
            return True

        # -----------------------------------------------------------
        # ❌ 5. Otherwise irrelevant
        # -----------------------------------------------------------
        return False
