import re
from typing import Optional

# ---------------------------------------------------------------------
# Watch model classifier
# ---------------------------------------------------------------------
# Normalises watch titles into stable model_keys for ROI/comps.
#
# Examples:
#   "Seiko SKX007 Auto Diver"       → "seiko_skx007"
#   "Seiko 5 Automatic"             → "seiko_5"
#   "Omega Seamaster 300M"          → "omega_seamaster"
#   "Casio G-Shock GA-2100"         → "casio_gshock_ga2100"
#   "Michael Kors MK5605"           → "michaelkors_generic"
#   "EMPORIO ARMANI AR5920"         → "emporioarmani_generic"
# ---------------------------------------------------------------------

BRAND_PATTERNS: dict[str, list[tuple[str, callable]]] = {
    "seiko": [
        # Seiko 5 / Presage / Prospex / etc.
        (r"\bseiko\s*(5|presage|prospex|samurai|turtle|alpinist)\b",
         lambda m: f"seiko_{m.group(1).lower()}"),
        # SRPxxxx references
        (r"\bseiko\s*(srp\d{3,4})\b",
         lambda m: f"seiko_{m.group(1).lower()}"),
        # SKX007 etc
        (r"\bseiko\s*(skx\d{3})\b",
         lambda m: f"seiko_{m.group(1).lower()}"),
        # SNK809 / SNZH etc
        (r"\bseiko\s*(sn[a-z]\d{3})\b",
         lambda m: f"seiko_{m.group(1).lower()}"),
        # belt-and-braces for "Seiko Alpinist"
        (r"\bseiko\s*alpinist\b",
         lambda m: "seiko_alpinist"),
    ],

    "casio": [
        # G-Shock GA-2100 etc
        (r"\bg[\-\s]?shock\s*(ga|gw|gd|gm|dw)\-?(\d{2,4}[a-z]?)\b",
         lambda m: f"casio_gshock_{m.group(1).lower()}{m.group(2).lower()}"),
        (r"\bcasio\s*f\-?91w\b",
         lambda m: "casio_f91w"),
        (r"\bcasio\s*a168w?\b",
         lambda m: "casio_a168w"),
        (r"\bca\-?53w\b",
         lambda m: "casio_ca53w"),
        (r"\bae\-?1200w?[hd]?\b",
         lambda m: "casio_ae1200"),
        (r"\bcasio\s*royale\b",
         lambda m: "casio_royale"),
    ],

    "citizen": [
        (r"\bcitizen\s*eco[\-\s]?drive\b",
         lambda m: "citizen_ecodrive"),
        (r"\bpromaster\b",
         lambda m: "citizen_promaster"),
        (r"\bnighthawk\b",
         lambda m: "citizen_nighthawk"),
    ],

    "omega": [
        (r"\bseamaster\b",
         lambda m: "omega_seamaster"),
        (r"\bspeedmaster\b",
         lambda m: "omega_speedmaster"),
        (r"\bde\s*ville\b",
         lambda m: "omega_deville"),
        (r"\bconstellation\b",
         lambda m: "omega_constellation"),
    ],

    "tag_heuer": [
        (r"\btag[\s\-]*heuer\s*(carrera|aquaracer|monaco|formula\s*1|link)\b",
         lambda m: "tagheuer_" + re.sub(r"\s+", "", m.group(1).lower())),
    ],

    "rolex": [
        (r"\brolex\s*(submariner|daytona|datejust|gmt\-?master|explorer|yacht\-?master)\b",
         lambda m: "rolex_" + re.sub(r"[\s\-]+", "", m.group(1).lower())),
    ],

    "tissot": [
        (r"\btissot\b.*\bprx\b",
         lambda m: "tissot_prx"),
        (r"\btissot\b.*\bseastar\b",
         lambda m: "tissot_seastar"),
        (r"\ble\s*locle\b",
         lambda m: "tissot_lelocle"),
    ],

    "hamilton": [
        (r"\bkhaki\s*(field|aviation|navy)\b",
         lambda m: f"hamilton_khaki_{m.group(1).lower()}"),
    ],

    "swatch": [
        (r"\bmoon\s*swatch\b|\bmoonswatch\b",
         lambda m: "swatch_moonswatch"),
    ],

    # fashion brands – mostly generic keys
    "michael_kors": [
        (r"\bmichael\s*kors\b", lambda m: "michaelkors_generic"),
    ],
    "emporio_armani": [
        (r"\bemporio\s*armani\b", lambda m: "emporioarmani_generic"),
    ],
    "timex": [
        (r"\btimex\b.*\bmarlin\b", lambda m: "timex_marlin"),
        (r"\btimex\b", lambda m: "timex_generic"),
    ],
    "bulova": [
        (r"\bbulova\b", lambda m: "bulova_generic"),
    ],
}

# Brand-level fallback – this is where we "cover the world" reasonably.
# If any of these brands is seen and no model-rule hit, we return brand_generic.
BRAND_FALLBACKS: dict[str, str] = {
    # mainstream / luxury
    "seiko": "seiko_generic",
    "grand seiko": "grandseiko_generic",
    "casio": "casio_generic",
    "g-shock": "casio_gshock_generic",
    "citizen": "citizen_generic",
    "omega": "omega_generic",
    "rolex": "rolex_generic",
    "tag heuer": "tagheuer_generic",
    "tissot": "tissot_generic",
    "hamilton": "hamilton_generic",
    "longines": "longines_generic",
    "iwc": "iwc_generic",
    "oris": "oris_generic",
    "breitling": "breitling_generic",
    "panerai": "panerai_generic",
    "patek philippe": "patekphilippe_generic",
    "audemars piguet": "audemarspiguet_generic",
    "vacheron constantin": "vacheronconstantin_generic",
    "zenith": "zenith_generic",
    "hublot": "hublot_generic",
    "tudor": "tudor_generic",
    "rado": "rado_generic",
    "breguet": "breguet_generic",

    # fashion / high volume eBay stuff
    "michael kors": "michaelkors_generic",
    "emporio armani": "emporioarmani_generic",
    "armani exchange": "armaniexchange_generic",
    "fossil": "fossil_generic",
    "diesel": "diesel_generic",
    "guess": "guess_generic",
    "skagen": "skagen_generic",
    "daniel wellington": "danielwellington_generic",
    "invicta": "invicta_generic",
    "bulova": "bulova_generic",
    "swatch": "swatch_generic",
    "garmin": "garmin_generic",
    "suunto": "suunto_generic",
    "vivienne westwood": "viviennewestwood_generic",
    "kate spade": "katespade_generic",
    "seagull": "seagull_generic",
    "skmei": "skmei_generic",
    "felca": "felca_generic",
    "talis": "talis_generic",
    "spacecube": "spacecube_generic",
    # add more as/when you see them in data
}


def watch_model_key(title: str) -> Optional[str]:
    t = title.lower()

    # 1) Model-level rules first (more specific)
    for _brand, patterns in BRAND_PATTERNS.items():
        for pat, fn in patterns:
            m = re.search(pat, t)
            if m:
                return fn(m)

    # 2) Brand-level fallback: check longer brand names first
    for brand, key in sorted(BRAND_FALLBACKS.items(), key=lambda kv: -len(kv[0])):
        pattern = r"\b" + re.escape(brand).replace(r"\ ", r"\s+") + r"\b"
        if re.search(pattern, t):
            return key

    # 3) No idea
    return None
