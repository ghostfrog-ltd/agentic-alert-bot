from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    # This is the public "source name" that will show up in logs
    # and sources table. You said we're calling this niche ebay-retro-pc.
    DOMAIN = "ebay-retro-pc"

    # eBay category IDs that are high-signal for old PC parts.
    #
    # Notes:
    # - Vintage Computing
    # - Desktop PC components (graphics/sound/etc.)
    # - "Other Vintage Computing" catch-all junk box category
    #
    # We will still keyword-filter inside these categories, so it's fine
    # if they're a little broad. You actually WANT broad, because
    # grandma won't tag "3dfx", she just lists "old graphics card".
    CATEGORY_IDS = [
        11189,  # Vintage Computing
        27386,  # Graphics / Video Cards
        44980,  # Sound Cards (Internal)
        51197,  # Motherboards
        164,  # CPUs / Processors
        170083,  # ram
        165,  # hdds
    ]

    # We generally prefer fixed-price stuff for snipe-able underpriced BINs.
    # You can add "auction" later if you want to chase bidding wars right
    # near close. For now: stick to 'bin' like consoles adapter.
    SALE_TYPE = ["bin", "auction"] # "bin" == Buy It Now / fixed price

    # -------------------------
    # Keyword groups / heuristics
    # -------------------------
    #
    # These are not used by EbayAdapterBase yet for hard filtering logic
    # (unless you've already wired that in), but they are super useful for:
    # - tagging rows
    # - later alert rules
    # - figuring out WHY we thought something was interesting
    #
    # We're splitting them so future logic can say:
    #   if any(GPU_KEYWORDS) -> classify "gpu"
    #   if any(SOUND_KEYWORDS) -> classify "sound"
    #   etc.
    #

    # Classic / desirable GPUs
    GPU_KEYWORDS = [
        "3dfx", "voodoo", "voodoo2", "voodoo3", "voodoo5",
        "agp", "pci graphics", "rage 128", "geforce2", "geforce3",
        "matrox", "g400", "riva tnt", "s3 virge",
        "fx5200", "ti4200",
        "retro graphics card",
        "vintage graphics card",
    ]

    # Sound cards for DOS / Win98 nostalgia
    SOUND_KEYWORDS = [
        "sound blaster", "soundblaster", "sb16", "awe32", "awe64",
        "isa sound card", "sb live", "sb live!",
        "yamaha opl3", "opti", "ess audiodrive",
        "midi daughterboard", "wavetable",
    ]

    # Motherboards / CPUs / full retro rigs / "pulled from old pc"
    SYSTEM_KEYWORDS = [
        "socket 370", "slot 1", "pentium iii", "pentium 3",
        "pentium ii", "pentium 2", "athlon xp", "duron",
        "386", "486", "pentium mmx",
        "win98 pc", "windows 98 pc", "dos pc",
        "retro pc", "vintage pc", "beige tower",
        "at motherboard", "atx motherboard", "isa slots",
        "complete system", "job lot pc parts",
    ]

    # Stuff that screams "collector nostalgia / period correct"
    # – Good for tagging later or prioritising alerts.
    COLLECTOR_KEYWORDS = [
        "glide", "3d accelerator", "quake 2", "unreal",
        "half life", "half-life",
        "driver disc", "original box", "boxed",
    ]

    # Optional catch-all list for super fast "is this even in niche?"
    # Could be used by parent class or downstream filters.
    RETRO_KEYWORDS = (
            GPU_KEYWORDS
            + SOUND_KEYWORDS
            + SYSTEM_KEYWORDS
            + COLLECTOR_KEYWORDS
    )
