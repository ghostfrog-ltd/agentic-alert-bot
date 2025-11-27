from __future__ import annotations

from typing import Mapping, Any, Optional
from typing import Dict, Set

from infrastructure.utils.condition import _derive_condition_grade

# DONT DELETE CONSOLE VARIANTS PLS
CONSOLE_VARIANTS: Dict[str, Set[str]] = {
    # -----------------
    # SONY PLAYSTATION
    # -----------------
    "ps1": {
        "original",
        "fat",
        "chu-1000",
        "scph-1002",
        "ps one",
        "psone",
        "mini",
        "compact",
    },
    "ps2": {
        "fat",
        "original",
        "slim",
        "scph",
        "silver",
        "black",
    },
    "ps3": {
        "fat",
        "phat",
        "slim",
        "super slim",
        "60gb",
        "80gb",
        "120gb",
        "160gb",
        "250gb",
        "320gb",
        "500gb",
        "black",
        "white",
    },
    "ps4": {
        "original",
        "fat",
        "slim",
        "pro",
        # storage
        "500gb",
        "1tb",
        "2tb",
        # colours
        "jet black",
        "black",
        "glacier white",
        "white",
        "limited edition",
        # bundles / specials
        "bundle",
        "vr bundle",
        "days of play",
    },
    "ps5": {
        "standard",
        "disc",
        "digital",
        "digital edition",
        "slim",
        # storage
        "825gb",
        "1tb",
        "2tb",
        # colours / covers
        "black",
        "white",
        "midnight black",
        "cosmic red",
        "limited edition",
        "special edition",
        # packs
        "bundle",
        "spider-man bundle",
        "fc24 bundle",
    },

    # -------------
    # XBOX FAMILY
    # -------------
    "xbox_original": {
        "original",
        "classic",
        "crystal",
        "limited edition",
        "green",
        "black",
    },
    "xbox_360": {
        "core",
        "arcade",
        "pro",
        "elite",
        "slim",
        "e",
        # storage
        "20gb",
        "60gb",
        "120gb",
        "250gb",
        "320gb",
        # colours
        "black",
        "white",
        "matte",
        "gloss",
        "halo edition",
    },
    "xbox_one": {
        "original",
        "fat",
        "one s",
        "s",
        "one x",
        "x",
        "all digital",
        "sad edition",
        # storage
        "500gb",
        "1tb",
        "2tb",
        # colours
        "black",
        "white",
        "robot white",
        "matte black",
        # specials
        "day one edition",
        "limited edition",
        "bundle",
    },
    "xbox_series": {
        # two main families
        "series s",
        "series x",
        "s",
        "x",
        # storage
        "512gb",
        "1tb",
        "2tb",
        # colours
        "black",
        "white",
        "carbon black",
        "robot white",
        "limited edition",
        # packs
        "bundle",
        "fortnite bundle",
        "game pass bundle",
    },

    # -----------------
    # NINTENDO HOME
    # -----------------
    "wii": {
        "wii",
        "mini",
        "family edition",
        "red",
        "black",
        "white",
        "blue",
        "sports resort bundle",
        "mario kart bundle",
    },
    "wii_u": {
        "basic",
        "premium",
        "deluxe",
        "8gb",
        "32gb",
        "white",
        "black",
        "mario kart bundle",
        "splatoon bundle",
    },
    "switch": {
        # base identifiers
        "v1",
        "v2",
        "mariko",
        # main variants
        "oled",
        "lite",
        # colours / editions
        "neon",
        "grey",
        "gray",  # US spelling appears in titles
        "turquoise",
        "yellow",
        "coral",
        "animal crossing",
        "pokemon edition",
        "zelda edition",
        "limited edition",
        # bundles
        "bundle",
        "mario kart bundle",
    },

    # -----------------
    # NINTENDO HANDHELDS
    # -----------------
    "3ds": {
        "original",
        "xl",
        "new 3ds",
        "new 3ds xl",
        "2ds",
        "new 2ds",
        "new 2ds xl",
        # colours
        "black",
        "blue",
        "red",
        "pink",
        "white",
        "limited edition",
        "pokemon edition",
        "zelda edition",
    },
    "ds": {
        "ds lite",
        "dsi",
        "dsi xl",
        "lite",
        "xl",
        # colours
        "black",
        "white",
        "pink",
        "blue",
        "silver",
        "red",
    },

    # -----------------
    # SONY HANDHELDS
    # -----------------
    "psp": {
        "1000",
        "2000",
        "3000",
        "street",
        "go",
        # colours
        "black",
        "white",
        "silver",
        "red",
        "blue",
        # specials
        "limited edition",
    },
    "ps_vita": {
        "1000",
        "2000",
        "slim",
        "oled",
        "wifi",
        "3g",
        # colours
        "black",
        "white",
        "aqua blue",
        # specials
        "limited edition",
    },

    # -------------
    # SEGA
    # -------------
    "mega_drive": {
        "mega drive",
        "megadrive",
        "genesis",
        "model 1",
        "model 2",
        "mini",
        "mini 2",
        "asian version",
        "japanese version",
        "pal",
        "ntsc",
    },
    "saturn": {
        "model 1",
        "model 2",
        "pal",
        "ntsc",
        "white",
        "grey",
        "gray",
    },
    "dreamcast": {
        "pal",
        "ntsc",
        "japanese version",
        "white",
        "black",
        "limited edition",
    },

    # -----------------
    # RETRO MINI /
    # MICRO-CONSOLES
    # -----------------
    "mini_classic": {
        "nes mini",
        "snes mini",
        "playstation classic",
        "mega drive mini",
        "mini",
        "classic mini",
        "console only",
        "boxed",
    },
}


def _as_text(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip().lower()


def _is_console_type(type_val: Any) -> bool:
    """
    Decide if this listing is actually a console/handheld,
    not a controller, wheel, stand, etc.
    """
    if not type_val:
        return False

    t = _as_text(type_val)

    console_tokens = [
        "home console",
        "handheld system",
        "games console",
        "console gaming system",
        "home video game console",
        "console",
    ]

    accessory_tokens = [
        "controller",
        "gamepad",
        "racing wheel",
        "wheel",
        "guitar",
        "headset",
        "headphones",
        "gaming accessories",
        "stand",
        "dock",
        "adapter",
        "ac adapter",
        "av adapter",
        "cable",
        "charging",
        "play charge lead",
        "balance board",
        "pedals",
        "loadcell",
    ]

    # Must look like a console/handheld...
    if not any(tok in t for tok in console_tokens):
        return False

    # ...but must NOT look like an accessory
    if any(tok in t for tok in accessory_tokens):
        return False

    return True


def console_or_game_model_key(
        attrs: Mapping[str, Any],
        title: str,
) -> Optional[str]:
    """
    Given eBay-style attributes, return the console family key used
    in CONSOLE_VARIANTS, with a condition suffix:

        "ps4_A", "switch_B", "xbox_one_C", ...

    Returns "unknown" (no suffix) if:
      - it's not a console/handheld (Type says controller, wheel, etc), or
      - we can't confidently map the brand/model/platform.
    """

    if not attrs:
        return "unknown"

    # 1) Check this really is a console, not an accessory
    if not _is_console_type(attrs.get("Type")):
        return "unknown"

    # 2) Build a simple text blob from brand/model/platform/mpn
    brand = _as_text(attrs.get("Brand"))
    model = _as_text(attrs.get("Model"))
    platform = _as_text(attrs.get("Platform"))
    mpn = _as_text(attrs.get("MPN"))

    text_parts = [brand, model, platform, mpn]
    text = " ".join(p for p in text_parts if p)
    text = " ".join(text.split())  # normalise spaces

    if not text:
        return "unknown"

    base_key: Optional[str] = None

    # ---------------
    # SONY / PS FAMILY
    # ---------------
    if (
            "sony" in brand
            or "playstation" in brand
            or "playstation" in model
            or "ps " in model
            or model.startswith("ps")
            or "playstation" in platform
    ):
        if "ps5" in text or "playstation 5" in text:
            base_key = "ps5"
        elif "ps4" in text or "playstation 4" in text:
            base_key = "ps4"
        elif "ps3" in text or "playstation 3" in text:
            base_key = "ps3"
        elif "ps2" in text or "playstation 2" in text:
            base_key = "ps2"
        elif "ps one" in text or "ps1" in text or "playstation 1" in text:
            base_key = "ps1"
        elif "vita" in text:
            base_key = "ps_vita"
        elif "psp" in text:
            base_key = "psp"
        else:
            # could be accessories with weird Type, or odd devkits → bail
            base_key = None

    # ---------------
    # MICROSOFT / XBOX
    # ---------------
    if base_key is None and "xbox" in text:
        # New gen
        if "series s" in text or "series x" in text or "xbox series" in text:
            base_key = "xbox_series"
        # One family
        elif "one s" in text or "one x" in text or "xbox one" in text:
            base_key = "xbox_one"
        # 360 family
        elif "360" in text:
            base_key = "xbox_360"
        # Fallback: OG Xbox
        else:
            base_key = "xbox_original"

    # ---------------
    # NINTENDO FAMILY
    # ---------------
    if base_key is None:
        if "switch" in text:
            base_key = "switch"
        elif "wii u" in text:
            base_key = "wii_u"
        elif "wii" in text:
            base_key = "wii"
        elif "3ds" in text or "2ds" in text or "new 3ds" in text:
            base_key = "3ds"
        elif "ds lite" in text or "dsi" in text:
            base_key = "ds"

    # ---------------
    # SEGA
    # ---------------
    if base_key is None:
        if "mega drive" in text or "megadrive" in text or "genesis" in text:
            base_key = "mega_drive"
        elif "saturn" in text:
            base_key = "saturn"
        elif "dreamcast" in text:
            base_key = "dreamcast"

    # ---------------
    # MINI / CLASSIC
    # ---------------
    if base_key is None:
        if (
                "classic mini" in text
                or "mini console" in text
                or "snes mini" in text
                or "nes mini" in text
                or "playstation classic" in text
        ):
            base_key = "mini_classic"

    if base_key is None:
        return "unknown"

    # At this point we have a console family. Attach condition grade.
    grade = _derive_condition_grade(attrs, title)
    return f"{base_key}_{grade}"
