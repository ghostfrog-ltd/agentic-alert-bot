from __future__ import annotations

import re
from typing import Optional, List, Tuple

# -------- platform regex snippets --------
SWITCH   = r"(nintendo\s*switch|nsw\b|\bswitch\b)"
PS5      = r"\bps5\b"
PS4      = r"\bps4\b"
PS3      = r"\bps3\b|\bplaystation\s*3\b"
PS2      = r"\bps2\b|\bplaystation\s*2\b"
SERIES_X = r"\bxbox\s*series\s*x\b|\bxsx\b|\bx\s*series\b(?!\s*s)"
SERIES_S = r"\bxbox\s*series\s*s\b|\bxss\b|\bs\s*series\b"
XONE     = r"\bxbox\s*one\b|\bxone\b"
X360     = r"\bxbox\s*360\b|\bx360\b|\b360\b"
WII      = r"\bwii\b"
HANDHELD = r"\br36s\b|\bpowkiddy\b|\banbernic\b|\brg\d+\b|\bretroid\b|\bmiu\b|\btrimui\b"

# Retro / older platforms
PS1        = r"\bps1\b|\bpsx\b|\bplaystation\s*(1|one)\b"
PSP        = r"\bpsp\b"
VITA       = r"\bps\s*vita\b|\bplaystation\s*vita\b|\bvita\b"
WIIU       = r"\bwii\s*u\b"
GAMECUBE   = r"\b(gamecube|gc)\b"
N64        = r"\bn64\b|\bnintendo\s*64\b"
SNES       = r"\bsnes\b|\bsuper\s+nintendo\b"
NES        = r"\bnes\b|\bnintendo\s+entertainment\s+system\b"
MEGADRIVE  = r"\bmega\s*drive\b|\bgenesis\b"
DREAMCAST  = r"\bdreamcast\b"
SATURN     = r"\bsaturn\b"
GBA        = r"\bgame\s*boy\s*advance\b|\bgba\b"
GB         = r"\b(game\s*boy|gb)\b"
NDS        = r"\bnintendo\s*ds\b|\bnds\b"
N3DS       = r"\bnintendo\s*3ds\b|\b3ds\b"

GAME_OR_MEDIA = (
    r"(?:\b("
    r"game|disc|dvd|blu[-\s]?ray|steelbook|soundtrack|collection|trilogy|anthology|"
    r"edition|goty|hits|classics|essentials|platinum|cartridge|cart|"
    r"digital\s+code|download\s+code|download|dlc|season\s+pass|expansion|add[-\s]?on"
    r")s?\b)"
)

ACCESSORY = (
    r"(?:\b("
    r"controller|pad|joy[-\s]?con|joycon|pro\s*controller|"
    r"steering\s*wheel|wheel\s*and\s*pedals|fight\s*stick|arcade\s*stick|joystick|"
    r"headset|headphones|earbuds?|microphone|camera|eye\s*camera|webcam|"
    r"charger|charging\s*(dock|stand|station)|dock|stand|base\s*station|"
    r"battery\s*pack|power\s*supply|psu|adapter|cable|lead|hdmi|av\s*cable|power\s*lead|"
    r"sensor\s*bar|remote|nunchuk|nunchuck|"
    r"case|carry\s*case|bag|backpack|skin|grip|faceplate|shell|"
    r"amiibo|skylanders|disney\s*infinity|lego\s*dimensions|portal\s*of\s*power|"
    r"racing\s*wheel|flightstick|flight\s*stick"
    r")s?\b)"
)

CONSOLE_WORDS = r"(console|system|bundle|set|handheld|hand\s*held)"

# -------- console-specific RULES --------
# NOTE: ACCESSORY rules come *before* GAME rules so that
# "Box Protector PS5 PS4 PS3 Blu Ray Game ... Case" -> ps5_accessory, not ps5_game.
_RULES_CONSOLES: List[Tuple[str, str]] = [
    (rf"{SWITCH}.*{ACCESSORY}", "switch_accessory"),
    (rf"{SWITCH}.*{GAME_OR_MEDIA}", "switch_game"),
    (rf"{PS5}.*{ACCESSORY}", "ps5_accessory"),
    (rf"{PS5}.*{GAME_OR_MEDIA}", "ps5_game"),
    (rf"{PS4}.*{ACCESSORY}", "ps4_accessory"),
    (rf"{PS4}.*{GAME_OR_MEDIA}", "ps4_game"),
    (rf"{XONE}.*{ACCESSORY}", "xbox_one_accessory"),
    (rf"{XONE}.*{GAME_OR_MEDIA}", "xbox_one_game"),
    (rf"{X360}.*{ACCESSORY}", "xbox_360_accessory"),
    (rf"{X360}.*{GAME_OR_MEDIA}", "xbox_360_game"),
    (rf"{SERIES_X}.*{ACCESSORY}", "xbox_series_x_accessory"),
    (rf"{SERIES_X}.*{GAME_OR_MEDIA}", "xbox_series_x_game"),
    (rf"{SERIES_S}.*{ACCESSORY}", "xbox_series_s_accessory"),
    (rf"{SERIES_S}.*{GAME_OR_MEDIA}", "xbox_series_s_game"),
    (rf"{WII}.*{ACCESSORY}", "wii_accessory"),
    (rf"{WII}.*{GAME_OR_MEDIA}", "wii_game"),
]

# ------- console fallback helpers -------

CONSOLE_PLATFORMS: list[tuple[str, str]] = [
    ("switch", SWITCH),
    ("ps5", PS5),
    ("ps4", PS4),
    ("ps3", PS3),
    ("ps2", PS2),
    ("ps1", PS1),
    ("psp", PSP),
    ("vita", VITA),
    ("xbox_series_x", SERIES_X),
    ("xbox_series_s", SERIES_S),
    ("xbox_one", XONE),
    ("xbox_360", X360),
    ("wiiu", WIIU),
    ("wii", WII),
    ("gamecube", GAMECUBE),
    ("n64", N64),
    ("snes", SNES),
    ("nes", NES),
    ("megadrive", MEGADRIVE),
    ("dreamcast", DREAMCAST),
    ("saturn", SATURN),
    ("gba", GBA),
    ("gb", GB),
    ("nds", NDS),
    ("n3ds", N3DS),
    ("handheld", HANDHELD),
]


def _fallback_console_key(text: str) -> Optional[str]:
    """
    Generic console detector:
    - find any known platform
    - classify as *_accessory / *_console / *_game / *_other
      (in that priority order)
    """
    for plat_key, plat_regex in CONSOLE_PLATFORMS:
        if re.search(plat_regex, text):
            # Prefer accessories (controllers, cases, box protectors, etc.)
            if re.search(ACCESSORY, text):
                suffix = "accessory"
            # Then plain hardware consoles / systems / bundles
            elif re.search(CONSOLE_WORDS, text):
                suffix = "console"
            # Then games/media
            elif re.search(GAME_OR_MEDIA, text):
                suffix = "game"
            else:
                suffix = "other"
            return f"{plat_key}_{suffix}"
    return None


# ------- extra non-console game/accessory helpers -------

RUNESCAPE_GOLD = re.compile(r"\b(runescape|osrs)\b", re.I)
FIFA_COINS = re.compile(r"\b(fc|fut)\b.*\bcoins\b|\bcoins\b.*\b(fc|fut)\b", re.I)

RETRO_COMPUTERS: list[tuple[str, str]] = [
    ("c64", r"\bcommodore\s*64\b|\bc64\b"),
    ("spectrum", r"\b(zx\s*)?spectrum\b"),
    ("amiga", r"\bamiga\b"),
    ("atari", r"\batari\b"),
]

PC_WORDS = r"\b(pc|steam|origin|uplay|battlenet|battle\.net|epic\s+games)\b"
GAMING_WORD = r"\bgaming\b"


def _classify_non_console_game_or_accessory(text: str) -> Optional[str]:
    """Classify remaining UNKNOWNs that look like games/accessories but
    don't mention a specific console platform we recognise."""

    # Virtual currencies / game accounts
    if RUNESCAPE_GOLD.search(text):
        return "runescape_virtual_item"
    if FIFA_COINS.search(text):
        return "fifa_coins_virtual_item"

    has_game_word = bool(re.search(GAME_OR_MEDIA, text))
    has_console_word = bool(re.search(CONSOLE_WORDS, text))

    # Generic consoles with no known platform:
    # e.g. "Steam Deck ... Handheld Console", "PC Engine Console", "Neo Geo CD Console",
    # "Retro Game Stick 64GB with 2 Controllers"
    if has_console_word:
        if "retro" in text or "tv" in text or "stick" in text or "handheld" in text:
            return "generic_retro_console"
        return "generic_console"

    # Retro computer games (C64 / Spectrum / Amiga / Atari)
    for key, pat in RETRO_COMPUTERS:
        if re.search(pat, text) and has_game_word:
            return f"{key}_game"
    '''
    # PC-ish games (Steam / Origin keys etc.)
    if has_game_word and re.search(PC_WORDS, text):
        return "pc_game"

    # Generic game / media (no platform)
    if has_game_word:
        return "generic_game"

    # Gaming accessories with no explicit console platform.
    if re.search(ACCESSORY, text):
        return "generic_accessory"
    '''

    return None


def console_or_game_model_key(text: str) -> Optional[str]:
    """
    Consoles / games / accessories / retro / PC classifier.

    Expects `text` already lowercased (normalise_model() does this).
    Returns e.g. ps5_game, ps5_console, ps5_accessory, generic_game, pc_game, etc.,
    or None.
    """
    # 1) Direct console rules (platform + game/accessory words)
    for pat, key in _RULES_CONSOLES:
        if re.search(pat, text):
            return key

    # 2) Generic console fallback
    console_key = _fallback_console_key(text)
    if console_key:
        return console_key

    # 3) Non-console games/accessories (PC, C64, Amiga, generic, retro sticks)
    other = _classify_non_console_game_or_accessory(text)
    if other:
        return other

    return None
