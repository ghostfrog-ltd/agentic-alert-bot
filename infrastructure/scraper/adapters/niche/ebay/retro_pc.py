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
        164,     # CPUs / Processors
        170083,  # RAM
        165,     # HDDs
    ]

    SALE_TYPE = ["auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        t = (row.get("title") or "").lower()

        # 🚫 obvious non-retro junk
        if any(k in t for k in (
            "rtx", "gtx", "rx ", "ddr4", "ddr5", "ryzen", "intel i9", "intel i7", "intel i5",
            "3060", "3070", "3080", "3090", "4070", "4080", "4090",
            "nvidia 10", "nvidia 20", "nvidia 30", "nvidia 40",
            "m2", "nvme", "pcie 4", "pcie 5",
            "usb 3", "rgb", "gaming pc", "case fan", "aio cooler"
        )):
            return False

        # ✅ key retro / collectible terms
        retro_words = (
            # iconic GPUs
            "3dfx", "voodoo", "tnt2", "rage 128", "geforce 2", "geforce 3", "geforce 4",
            "matrox", "g400", "g450", "g550", "ati", "s3 trio", "cirrus logic", "tseng",
            # CPUs
            "pentium", "486", "386", "celeron", "athlon", "duron", "slot 1", "slot a", "socket 370",
            # sound cards
            "sound blaster", "awe64", "sb16", "gravis", "adlib", "isa sound",
            # motherboards
            "socket 7", "socket a", "socket 370", "isa slot", "agp", "pci", "at motherboard", "baby at",
            # systems / cases
            "386 pc", "486 pc", "pentium pc", "retro pc", "dos pc", "windows 95", "windows 98",
        )
        if any(k in t for k in retro_words):
            return True

        # ✅ catch "vintage computing" items generically
        if "vintage computing" in t or "retro computer" in t or "old pc" in t:
            return True

        # ✅ fallback on model_key
        mk = (row.get("model_key") or "").lower()
        if mk.startswith("retro_") or mk.startswith("gpu_") or mk.startswith("soundcard_"):
            return True

        # else drop it
        return False
