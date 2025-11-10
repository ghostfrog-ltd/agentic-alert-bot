from __future__ import annotations

import re
from typing import Optional

# -------- shared Apple helpers --------

# Storage in GB
STORAGE_GB = re.compile(r"\b(16|32|64|128|256|512|1024)\s*gb\b")

# Apple silicon / chip hints (very rough, but useful for Macs/iPads)
CHIP = re.compile(r"\b(m1|m2|m3)\b")

# Screen sizes (mostly for iPad Pro / MacBook)
SIZE_INCH = re.compile(
    r"\b(10\.9|11|12\.9|13|14|15|16)\s*(?:\"|inch|in|inches)?\b"
)

# Watch series
WATCH_SERIES = re.compile(r"\bseries\s*(\d+)\b")

# Generic "gen" pattern (e.g. "2nd gen", "3rd generation")
GEN = re.compile(r"\b(1st|2nd|3rd|4th)\s+gen(?:eration)?\b")

# AirTag pack size, e.g. "4 pack", "4pk", "4 set", "4 bundle"
PACK_RE = re.compile(r"\b(\d{1,2})\s*(?:pack|pk|set|bundle)\b")

# -------- Apple-specific RULES --------
# Order matters: more specific → more generic.

_RULES_APPLE: list[tuple[str, str]] = [
    # ==== iPhone (newer models first) ====
    (r"\biphone\s*15\s*pro\s*max\b", "apple_iphone_15_pro_max"),
    (r"\biphone\s*15\s*pro\b",      "apple_iphone_15_pro"),
    (r"\biphone\s*15\s*plus\b",     "apple_iphone_15_plus"),
    (r"\biphone\s*15\b",            "apple_iphone_15"),

    (r"\biphone\s*14\s*pro\s*max\b", "apple_iphone_14_pro_max"),
    (r"\biphone\s*14\s*pro\b",       "apple_iphone_14_pro"),
    (r"\biphone\s*14\s*plus\b",      "apple_iphone_14_plus"),
    (r"\biphone\s*14\b",             "apple_iphone_14"),

    (r"\biphone\s*13\s*pro\s*max\b", "apple_iphone_13_pro_max"),
    (r"\biphone\s*13\s*pro\b",       "apple_iphone_13_pro"),
    (r"\biphone\s*13\s*mini\b",      "apple_iphone_13_mini"),
    (r"\biphone\s*13\b",             "apple_iphone_13"),

    (r"\biphone\s*12\s*pro\s*max\b", "apple_iphone_12_pro_max"),
    (r"\biphone\s*12\s*pro\b",       "apple_iphone_12_pro"),
    (r"\biphone\s*12\s*mini\b",      "apple_iphone_12_mini"),
    (r"\biphone\s*12\b",             "apple_iphone_12"),

    (r"\biphone\s*11\s*pro\s*max\b", "apple_iphone_11_pro_max"),
    (r"\biphone\s*11\s*pro\b",       "apple_iphone_11_pro"),
    (r"\biphone\s*11\b",             "apple_iphone_11"),

    (r"\biphone\s*xs\s*max\b", "apple_iphone_xs_max"),
    (r"\biphone\s*xs\b",      "apple_iphone_xs"),
    (r"\biphone\s*xr\b",      "apple_iphone_xr"),
    (r"\biphone\s*x\b",       "apple_iphone_x"),

    (r"\biphone\s*se\b",      "apple_iphone_se"),
    (r"\biphone\s*8\s*plus\b","apple_iphone_8_plus"),
    (r"\biphone\s*8\b",       "apple_iphone_8"),
    (r"\biphone\s*7\s*plus\b","apple_iphone_7_plus"),
    (r"\biphone\s*7\b",       "apple_iphone_7"),

    # Generic iPhone fallback
    (r"\biphone\b", "apple_iphone"),

    # ==== iPad ====
    (r"\bipad\s+pro\s*12\.?9\b", "apple_ipad_pro_12_9"),
    (r"\bipad\s+pro\s*11\b",     "apple_ipad_pro_11"),
    (r"\bipad\s+pro\b",          "apple_ipad_pro"),
    (r"\bipad\s+air\b",          "apple_ipad_air"),
    (r"\bipad\s+mini\b",         "apple_ipad_mini"),
    (r"\bipad\b",                "apple_ipad"),

    # ==== Macs ====
    (r"\bmacbook\s+pro\b", "apple_macbook_pro"),
    (r"\bmacbook\s+air\b", "apple_macbook_air"),
    (r"\bmacbook\b",       "apple_macbook"),
    (r"\bimac\b",          "apple_imac"),
    (r"\bmac\s+mini\b",    "apple_mac_mini"),
    (r"\bmac\s+pro\b",     "apple_mac_pro"),

    # ==== Audio / accessories ====
    (r"\bairpods\s+pro\b", "apple_airpods_pro"),
    (r"\bairpods\s+max\b", "apple_airpods_max"),
    (r"\bairpods\b",       "apple_airpods"),

    (r"\bapple\s+watch\s+ultra\b", "apple_watch_ultra"),
    (r"\bapple\s+watch\s+se\b",    "apple_watch_se"),
    (r"\bapple\s+watch\b",         "apple_watch"),

    (r"\bapple\s+pencil\b",   "apple_pencil"),
    (r"\bmagic\s+mouse\b",    "apple_magic_mouse"),
    (r"\bmagic\s+keyboard\b", "apple_magic_keyboard"),
    (r"\bmagic\s+trackpad\b", "apple_magic_trackpad"),

    # AirTags
    (r"\bair\s*tags?\b", "apple_airtag"),

    (r"\bipod\b", "apple_ipod"),
]


# -------- Apple enrichment helpers --------

def _parse_storage_gb(text: str) -> Optional[str]:
    m = STORAGE_GB.search(text)
    if not m:
        return None
    return f"{m.group(1)}gb"


def _parse_chip(text: str) -> Optional[str]:
    m = CHIP.search(text)
    if not m:
        return None
    return m.group(1)  # m1 / m2 / m3


def _parse_size_inches(text: str) -> Optional[str]:
    m = SIZE_INCH.search(text)
    if not m:
        return None
    # Normalise 12.9 → 12_9 etc.
    raw = m.group(1)
    return raw.replace(".", "_")  # "12.9" -> "12_9"


def _parse_watch_series(text: str) -> Optional[str]:
    m = WATCH_SERIES.search(text)
    if not m:
        return None
    return f"s{m.group(1)}"  # "series 7" -> "s7"


def _parse_gen(text: str) -> Optional[str]:
    m = GEN.search(text)
    if not m:
        return None
    # "2nd" -> "2nd_gen"
    return f"{m.group(1)}_gen"


def _parse_pack_size(text: str) -> Optional[str]:
    """
    For AirTags etc, detect pack size: "4 pack", "4pk", "4 set", "4 bundle".
    """
    m = PACK_RE.search(text)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    if n <= 0:
        return None
    return f"{n}pack"


def _refine_apple_key(base_key: str, text: str) -> str:
    """
    Turn e.g. 'apple_iphone_13' into 'apple_iphone_13_128gb'
    or 'apple_ipad_pro_12_9_256gb', or 'apple_macbook_air_m1_256gb',
    or 'apple_airtag_4pack'.
    """
    t = text.lower()

    parts = base_key.split("_")
    if not parts:
        return base_key

    device_type = parts[1] if len(parts) >= 2 else None  # iphone / ipad / macbook / watch / airpods / airtag

    key_parts = parts[:]  # start from base

    # Storage is relevant for phones, tablets, iPods, and Macs
    if device_type in {"iphone", "ipad", "ipod", "macbook", "imac", "mac", "macbookpro", "macbookair"}:
        storage = _parse_storage_gb(t)
        if storage and storage not in key_parts:
            key_parts.append(storage)

    # Chip is mainly for Macs / some iPads
    if device_type in {"macbook", "imac", "mac", "macbookpro", "macbookair", "ipad"}:
        chip = _parse_chip(t)
        if chip and chip not in key_parts:
            key_parts.append(chip)

    # Screen size helps distinguish some iPads / MacBooks
    if device_type in {"ipad", "macbook"} or "ipad_pro" in base_key:
        size = _parse_size_inches(t)
        if size and size not in key_parts:
            key_parts.append(size)

    # Watch series
    if device_type == "watch" or base_key.startswith("apple_watch"):
        series = _parse_watch_series(t)
        if series and series not in key_parts:
            key_parts.append(series)

    # Generic gen for things like "2nd gen AirPods"
    gen = _parse_gen(t)
    if gen and gen not in key_parts:
        key_parts.append(gen)

    # AirTag pack size (apple_airtag, apple_airtag_* etc.)
    if "airtag" in base_key:
        pack = _parse_pack_size(t)
        if pack and pack not in key_parts:
            key_parts.append(pack)

    return "_".join(key_parts)


def _guess_apple_base_from_title(text: str) -> Optional[str]:
    """
    Fallback for things that clearly contain an Apple family word,
    but didn't hit a specific rule above.
    """
    t = text.lower()

    if "iphone" in t:
        return "apple_iphone"
    if "ipad" in t:
        return "apple_ipad"
    if "macbook pro" in t:
        return "apple_macbook_pro"
    if "macbook air" in t:
        return "apple_macbook_air"
    if "macbook" in t:
        return "apple_macbook"
    if "imac" in t:
        return "apple_imac"
    if "mac mini" in t:
        return "apple_mac_mini"
    if "mac pro" in t:
        return "apple_mac_pro"
    if "airpods" in t:
        return "apple_airpods"
    if "apple watch" in t:
        return "apple_watch"
    if "apple pencil" in t:
        return "apple_pencil"
    if "magic mouse" in t:
        return "apple_magic_mouse"
    if "magic keyboard" in t:
        return "apple_magic_keyboard"
    if "magic trackpad" in t:
        return "apple_magic_trackpad"
    if "airtag" in t or "air tag" in t:
        return "apple_airtag"
    if "ipod" in t:
        return "apple_ipod"

    return None


def apple_model_key(text: str) -> Optional[str]:
    """
    Apple-only classifier.

    Expects `text` already lowercased (normalise_model() does this).
    Returns an apple_* model_key or None.
    """
    # 1) Specific rules
    for pat, key in _RULES_APPLE:
        if re.search(pat, text):
            return _refine_apple_key(key, text)

    # 2) Fallback Apple detection
    base_key = _guess_apple_base_from_title(text)
    if base_key:
        return _refine_apple_key(base_key, text)

    return None
