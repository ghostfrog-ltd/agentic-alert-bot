import re
from typing import Optional

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

# --- shared bike helpers ---
_CC_NUMBERS = (
    "50|80|90|100|110|125|150|200|250|300|350|400|450|500|600|650|700|750|800|850|900|"
    "950|1000|1100|1200|1300"
)
CC = rf"\b({_CC_NUMBERS})\s*cc\b"
CC_BARE = rf"\b({_CC_NUMBERS})\b"
YEAR       = r"\b(200[0-9]|201[0-9]|202[0-6])\b"
BIKE_WORDS = r"(motorcycle|motorbike|bike|scooter|moped)"
CAR_WORDS  = r"(car|estate|saloon|hatchback|coupe|convertible|tourer|touring|mpv|suv|van)"

# Expanded bike makes – includes learner / commuter brands etc.
BIKE_MAKES = (
    "honda", "yamaha", "kawasaki", "suzuki", "ktm",
    "ducati", "triumph", "bmw", "harley", "vespa",
    "aprilia", "husqvarna", "enfield",
    "lexmoto", "sinnis", "benelli", "piaggio", "peugeot",
    "sym", "zontes", "keeway", "neco", "mgb", "bullit",
    "cfmoto", "kymco", "hyosung", "voge", "um", "hanway", "bsa",
)

# -------- specific RULES (kept light) --------
_RULES: list[tuple[str, str]] = [
    # Consoles with explicit game/accessory words
    (rf"{SWITCH}.*{GAME_OR_MEDIA}", "switch_game"),
    (rf"{SWITCH}.*{ACCESSORY}", "switch_accessory"),
    (rf"{PS5}.*{GAME_OR_MEDIA}", "ps5_game"),
    (rf"{PS5}.*{ACCESSORY}", "ps5_accessory"),
    (rf"{PS4}.*{GAME_OR_MEDIA}", "ps4_game"),
    (rf"{PS4}.*{ACCESSORY}", "ps4_accessory"),
    (rf"{XONE}.*{GAME_OR_MEDIA}", "xbox_one_game"),
    (rf"{XONE}.*{ACCESSORY}", "xbox_one_accessory"),
    (rf"{X360}.*{GAME_OR_MEDIA}", "xbox_360_game"),
    (rf"{X360}.*{ACCESSORY}", "xbox_360_accessory"),
    (rf"{SERIES_X}.*{GAME_OR_MEDIA}", "xbox_series_x_game"),
    (rf"{SERIES_S}.*{GAME_OR_MEDIA}", "xbox_series_s_game"),
    (rf"{WII}.*{GAME_OR_MEDIA}", "wii_game"),
    (rf"{WII}.*{ACCESSORY}", "wii_accessory"),

    # ========================
    # Bikes (specific models first)
    # ========================
    # Honda
    (r"\bhonda\b.*\bcbr\s*125\b", "bike_honda_cbr125"),
    (r"\bhonda\b.*\bcbr\s*650\b", "bike_honda_cbr650"),
    (r"\bhonda\b.*\bcbr\b", "bike_honda_cbr"),
    (r"\bhonda\b.*\bcrf\b", "bike_honda_crf"),
    (r"\bhonda\b.*\bcbf\b", "bike_honda_cbf"),
    (r"\bhonda\b.*\bpcx\b", "bike_honda_pcx"),
    (r"\bhonda\b.*\bforza\b", "bike_honda_forza"),
    (r"\bhonda\b.*\bgrom\b", "bike_honda_grom"),
    (rf"\bhonda\b.*{BIKE_WORDS}", "bike_honda"),

    # Yamaha
    (r"\byamaha\b.*\bmt[- ]?07\b", "bike_yamaha_mt07"),
    (r"\byamaha\b.*\bmt[- ]?09\b", "bike_yamaha_mt09"),
    (r"\byamaha\b.*\byzf[- ]?r7\b", "bike_yamaha_yzf_r7"),
    (r"\byamaha\b.*\byzf\b", "bike_yamaha_yzf"),
    (r"\byamaha\b.*\br6\b", "bike_yamaha_r6"),
    (r"\byamaha\b.*\br1\b", "bike_yamaha_r1"),
    (r"\byamaha\b.*\btmax\b", "bike_yamaha_tmax"),
    (r"\byamaha\b.*\bxmax\b", "bike_yamaha_xmax"),
    (r"\byamaha\b.*\bnmax\b", "bike_yamaha_nmax"),
    (rf"\byamaha\b.*{BIKE_WORDS}", "bike_yamaha"),

    # Kawasaki
    (r"\bkawasaki\b.*\bzx[- ]?6r\b", "bike_kawasaki_zx6r"),
    (r"\bkawasaki\b.*\bzx[- ]?9r\b", "bike_kawasaki_zx9r"),
    (r"\bkawasaki\b.*\bzx[- ]?10r\b", "bike_kawasaki_zx10r"),
    (r"\bkawasaki\b.*\bninja\b", "bike_kawasaki_ninja"),
    (rf"\bkawasaki\b.*{BIKE_WORDS}", "bike_kawasaki"),

    # Suzuki / KTM / Ducati / Triumph / BMW etc
    (r"\bsuzuki\b.*\bgsxr\b", "bike_suzuki_gsxr"),
    (rf"\bsuzuki\b.*{BIKE_WORDS}", "bike_suzuki"),
    (r"\bktm\b.*\bduke\b", "bike_ktm_duke"),
    (r"\bktm\b.*\badventure\b", "bike_ktm_adventure"),
    (rf"\bktm\b.*{BIKE_WORDS}", "bike_ktm"),
    (r"\bducati\b.*\bpanigale\b", "bike_ducati_panigale"),
    (r"\bducati\b.*\bscrambler\b", "bike_ducati_scrambler"),
    (r"\bducati\b.*\bmultistrada\b", "bike_ducati_multistrada"),
    (rf"\bducati\b.*{BIKE_WORDS}", "bike_ducati"),
    (r"\btriumph\b.*\btiger\b", "bike_triumph_tiger"),
    (r"\btriumph\b.*\bbonneville\b", "bike_triumph_bonneville"),
    (rf"\btriumph\b.*{BIKE_WORDS}", "bike_triumph"),
    (r"\bbmw\b.*\bs1000rr\b", "bike_bmw_s1000rr"),
    (r"\bbmw\b.*\br\s*\d{3,4}\b", "bike_bmw_r_series"),
    (rf"\bharley(-|\s)?davidson\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_harley"),
    (rf"\bvespa\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_vespa"),
    (rf"\b(enfield|royal\s+enfield)\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_enfield"),
    (rf"\baprilia\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_aprilia"),
    (rf"\bhusqvarna\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_husqvarna"),


    # MV Agusta
    (r"\bmv\s+agusta\b.*\bbrutale\b", "bike_mv_agusta_brutale"),
    (r"\bmv\s+agusta\b.*\bdragster\b", "bike_mv_agusta_dragster"),
    (r"\bmv\s+agusta\b.*\bturismo\s+veloce\b", "bike_mv_agusta_turismo_veloce"),
    (r"\bmv\s+agusta\b.*\bf3\b", "bike_mv_agusta_f3"),
    (r"\bmv\s+agusta\b.*\bf4\b", "bike_mv_agusta_f4"),
    (rf"\bmv\s+agusta\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_mv_agusta"),

    # Moto Guzzi
    (r"\bmoto\s+guzzi\b.*\bv7\b", "bike_moto_guzzi_v7"),
    (r"\bmoto\s+guzzi\b.*\bv9\b", "bike_moto_guzzi_v9"),
    (rf"\bmoto\s+guzzi\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_moto_guzzi"),

    # Indian
    (r"\bindian\b.*\bscout\b", "bike_indian_scout"),
    (r"\bindian\b.*\bchief\b", "bike_indian_chief"),
    (r"\bindian\b.*\bbobber\b", "bike_indian_bobber"),
    (rf"\bindian\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_indian"),

    # Norton
    (rf"\bnorton\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_norton"),

    # Fantic / Herald / Mash / Mutt etc. – generic brand-level keys
    (rf"\bfantic\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_fantic"),
    (rf"\bherald\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_herald"),
    (rf"\bmash\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_mash"),
    (rf"\bmutt\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_mutt"),

    # Generic bike when we see bike-ish term + cc/year
    (rf"\b({BIKE_WORDS})\b.*({CC}|{YEAR})", "bike_generic"),
    (r"\b(motocross|enduro|mx|quad|atv)\b", "bike_offroad"),


    # Electric commuters
    (rf"\bsuper\s+soco\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_super_soco"),
    (rf"\bniu\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_niu"),
    (rf"\bzero\b.*({BIKE_WORDS}|{CC}|{YEAR})", "bike_zero"),
]

# ------- special CC guessers -------
SPECIAL_CC_CODES = [
    (r"\bzx[- ]?6r\b",  "600"),
    (r"\bzx[- ]?9r\b",  "900"),
    (r"\bzx[- ]?10r\b", "1000"),
    (r"\byzf[- ]?r6\b", "600"),
    (r"\byzf[- ]?r1\b", "1000"),
    (r"\byzf[- ]?r7\b", "700"),
]

# ------- bike enrichment helpers -------

def _parse_cc(text: str) -> Optional[str]:
    m = re.search(CC, text)
    if m:
        return m.group(1)
    m = re.search(CC_BARE, text)
    if m:
        return m.group(1)
    for pattern, guess in SPECIAL_CC_CODES:
        if re.search(pattern, text):
            return guess
    return None


def _parse_year(text: str) -> Optional[str]:
    m = re.search(YEAR, text)
    return m.group(1) if m else None


def _parse_variant(text: str) -> Optional[str]:
    m = re.search(r"\brr\b", text)
    if m:
        return "rr"
    return None


def _detect_bike_make(text: str) -> Optional[str]:
    """
    Find a known bike make using word boundaries, so e.g. 'sym' does not
    match random substrings by accident.
    """
    for make in BIKE_MAKES:
        if re.search(rf"\b{re.escape(make)}\b", text):
            return make
    return None


def _refine_bike_key(base_key: str, text: str) -> str:
    """Turn bike_honda_cbr into bike_honda_cbr_600_rr_2003 etc."""
    t = text.lower()
    parts = base_key.split("_")
    base_type = parts[0] if parts else "bike"
    base_make = parts[1] if len(parts) >= 2 else None
    base_model = "_".join(parts[2:]) if len(parts) >= 3 else None
    if base_make in ("generic", "offroad"):
        base_make = None
    if base_model in ("generic", "offroad", ""):
        base_model = None

    make = base_make or _detect_bike_make(t)
    model = base_model
    cc = _parse_cc(t)
    variant = _parse_variant(t)
    year = _parse_year(t)

    key_parts = [base_type]
    if make:
        key_parts.append(make)
    if model:
        key_parts.append(model)
    if cc:
        key_parts.append(cc)
    if variant:
        key_parts.append(variant)
    if year:
        key_parts.append(year)
    if len(key_parts) == 1:
        return base_key
    return "_".join(key_parts)


def _guess_bike_base_from_title(text: str) -> Optional[str]:
    """
    Fallback for things like:
      "2009 YAMAHA YBR 125 CUSTOM ..."
      "2013 HONDA CBF 125 ..."
    where we didn't hit a specific rule, but it's clearly a bike:
    - contains a known bike make
    - contains a cc-ish number or year
    """
    make = _detect_bike_make(text)
    if not make:
        return None

    # Need *some* hint it's a vehicle: either CC-like or a year
    if not (re.search(CC, text) or re.search(CC_BARE, text) or re.search(YEAR, text)):
        return None

    # Try to grab a model-ish token directly after the make
    # e.g. "yamaha ybr 125", "honda cbf 600", "suzuki gsxr 750"
    m = re.search(rf"\b{make}\b\s+([a-z0-9\-]{2,8})", text)
    model = None
    if m:
        token = m.group(1).lower()
        # Avoid obvious junk words
        if token not in {
            "motorcycle", "motorbike", "bike", "scooter", "moped",
            "quad", "atv", "spares", "repair", "non-structural",
        }:
            model = token.replace("-", "_")

    base_parts = ["bike", make]
    if model:
        base_parts.append(model)

    return "_".join(base_parts)


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
    - classify as *_game / *_accessory / *_console / *_other
    based on keywords anywhere in the title.
    """
    for plat_key, plat_regex in CONSOLE_PLATFORMS:
        if re.search(plat_regex, text):
            if re.search(ACCESSORY, text):
                suffix = "accessory"
            elif re.search(GAME_OR_MEDIA, text):
                suffix = "game"
            elif re.search(CONSOLE_WORDS, text):
                suffix = "console"
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

    # PC-ish games (Steam / Origin keys etc.)
    if has_game_word and re.search(PC_WORDS, text):
        return "pc_game"

    # Generic game / media (no platform)
    if has_game_word:
        return "generic_game"

    # Gaming accessories with no explicit console platform.
    # At this point we've already failed console + bike detection, so it's safe
    # to be generous and treat all ACCESSORY hits as non-console accessories.
    if re.search(ACCESSORY, text):
        return "generic_accessory"

    return None


# ------- main API -------

def normalise_model(title: str) -> Optional[str]:
    t = title.lower()

    # 1) Direct rules (specific bikes, some console cases)
    for pat, key in _RULES:
        if re.search(pat, t):
            if key.startswith("bike_"):
                return _refine_bike_key(key, t)
            return key

    # 2) Generic console / retro fallback
    console_key = _fallback_console_key(t)
    if console_key:
        return console_key

    # 3) Fallback bikes (any make + cc/year)
    base_bike_key = _guess_bike_base_from_title(t)
    if base_bike_key:
        return _refine_bike_key(base_bike_key, t)

    # 4) Non-console games/accessories (PC, C64, Amiga, generic, retro sticks)
    other = _classify_non_console_game_or_accessory(t)
    if other:
        return other

    # 5) Otherwise unknown – let caller map None -> "unknown" if needed
    return None
