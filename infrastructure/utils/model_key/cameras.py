from __future__ import annotations

import re
from typing import Optional

# ------------- shared camera / drone helpers -------------

CAMERA_WORDS = r"(camera|camcorder|action\s+camera|body\s+cam|vlogging\s+camera)"
DRONE_WORDS  = r"(drone|quadcopter|quad\s*cop(t)?er|fpv)"

CAM_BRANDS = (
    "gopro", "insta360", "dji", "akaso", "wolfang",
    "blackmagic", "canon", "sony", "panasonic", "brifield",
    "techalogic", "hoverair", "potensic", "chubory",
)

DRONE_BRANDS = (
    "dji", "potensic", "hoverair", "chubory", "mjx",
)

# -------- specific rules: action cams --------
_RULES_ACTIONCAMS: list[tuple[str, str]] = [
    # GoPro HERO series
    (r"\bgopro\b.*\bhero\s*12\b", "cam_gopro_hero12"),
    (r"\bgopro\b.*\bhero\s*11\b", "cam_gopro_hero11"),
    (r"\bgopro\b.*\bhero\s*10\b", "cam_gopro_hero10"),
    (r"\bgopro\b.*\bhero\s*9\b",  "cam_gopro_hero9"),
    (r"\bgopro\b.*\bhero\s*8\b",  "cam_gopro_hero8"),
    (r"\bgopro\b.*\bhero\s*7\b",  "cam_gopro_hero7"),
    (r"\bgopro\b.*\bhero\s*6\b",  "cam_gopro_hero6"),
    (r"\bgopro\b.*\bhero\s*5\b",  "cam_gopro_hero5"),
    (r"\bgopro\b.*\bhero\b",      "cam_gopro_hero"),

    # Insta360 – X / One X / Go lines
    (r"\binsta\s*360\b.*\bx4\b",           "cam_insta360_x4"),
    (r"\binsta\s*360\b.*\bx3\b",           "cam_insta360_x3"),
    (r"\binsta\s*360\b.*\bx2\b",           "cam_insta360_x2"),
    (r"\binsta\s*360\b.*\bone\s*x2\b",     "cam_insta360_one_x2"),
    (r"\binsta\s*360\b.*\bone\s*x3\b",     "cam_insta360_one_x3"),
    (r"\binsta\s*360\b.*\bgo\s*3\b",       "cam_insta360_go3"),
    (r"\binsta\s*360\b.*\bgo\s*2\b",       "cam_insta360_go2"),
    (r"\binsta\s*360\b",                   "cam_insta360_generic"),

    # DJI Osmo Action
    (r"\bdji\b.*\bosmo\s+action\s*5\b.*\bpro\b", "cam_dji_osmo_action5_pro"),
    (r"\bdji\b.*\bosmo\s+action\s*4\b",          "cam_dji_osmo_action4"),
    (r"\bdji\b.*\bosmo\s+action\s*3\b",          "cam_dji_osmo_action3"),
    (r"\bdji\b.*\bosmo\s+action\b",              "cam_dji_osmo_action"),

    # DJI Pocket
    (r"\bdji\b.*\bosmo\s+pocket\s*3\b", "cam_dji_osmo_pocket3"),
    (r"\bdji\b.*\bosmo\s+pocket\s*2\b", "cam_dji_osmo_pocket2"),
    (r"\bdji\b.*\bpocket\s*3\b",        "cam_dji_pocket3"),
    (r"\bdji\b.*\bpocket\s*2\b",        "cam_dji_pocket2"),

    # Blackmagic Pocket Cinema
    (r"\bblackmagic\b.*\bpocket\s+cinema\s+camera\s*6k\b", "cam_blackmagic_pcc6k"),
    (r"\bblackmagic\b.*\bpocket\s+cinema\s+camera\s*4k\b", "cam_blackmagic_pcc4k"),
    (r"\bblackmagic\b.*\bpocket\s+cinema\s+camera\b",      "cam_blackmagic_pcc"),

    # Generic brand-level action cams
    (r"\bakaso\b.*(action\s+camera|4k|sport\s+camera)",    "cam_akaso_action"),
    (r"\bwolfang\b.*(action\s+camera|4k|sport\s+camera)",  "cam_wolfang_action"),
    (r"\bbrifield\b.*body\s+cam",                          "cam_brifield_body"),
    (r"\btechalogic\b.*(helmet|hat)\s+camera",             "cam_techalogic_helmet"),
]

# -------- specific rules: drones --------
_RULES_DRONES: list[tuple[str, str]] = [
    # DJI Mini line
    (r"\bdji\b.*\bmini\s*5\b.*\bpro\b", "drone_dji_mini5_pro"),
    (r"\bdji\b.*\bmini\s*4\b.*\bpro\b", "drone_dji_mini4_pro"),
    (r"\bdji\b.*\bmini\s*4\b",          "drone_dji_mini4"),
    (r"\bdji\b.*\bmini\s*3\b.*\bpro\b", "drone_dji_mini3_pro"),
    (r"\bdji\b.*\bmini\s*3\b",          "drone_dji_mini3"),
    (r"\bdji\b.*\bmini\s*2\b.*\bse\b",  "drone_dji_mini2_se"),
    (r"\bdji\b.*\bmini\s*2\b",          "drone_dji_mini2"),
    (r"\bdji\b.*\bmini\b",              "drone_dji_mini"),

    # Avata / Neo
    (r"\bdji\b.*\bavata\s*2\b", "drone_dji_avata2"),
    (r"\bdji\b.*\bavata\b",     "drone_dji_avata"),
    (r"\bdji\b.*\bneo\s*2\b",   "drone_dji_neo2"),
    (r"\bdji\b.*\bneo\b",       "drone_dji_neo"),

    # Mavic etc.
    (r"\bdji\b.*\bmavic\s*air\b",          "drone_dji_mavic_air"),
    (r"\bdji\b.*\bmavic\s*pro\b",          "drone_dji_mavic_pro"),
    (r"\bdji\b.*\bmavic\b",                "drone_dji_mavic"),
    (r"\bdji\b.*\bfpv\b.*\bdrone\b",       "drone_dji_fpv"),
    (r"\bdji\b.*\bspark\b",                "drone_dji_spark"),

    # HoverAir X1 / X1 Pro / Pro Max
    (r"\bhoverair\b.*x1\s*pro\s*max\b", "drone_hoverair_x1_pro_max"),
    (r"\bhoverair\b.*x1\s*pro\b",      "drone_hoverair_x1_pro"),
    (r"\bhoverair\b.*x1\b",            "drone_hoverair_x1"),

    # Potensic Atom
    (r"\bpotensic\b.*\batom\s*2\b", "drone_potensic_atom2"),
    (r"\bpotensic\b.*\batom\b",     "drone_potensic_atom"),

    # Generic branded drones
    (r"\bpotensic\b.*drone\b", "drone_potensic_generic"),
    (r"\bmjx\b.*bugs\b",       "drone_mjx_bugs"),
    (r"\bchubory\b.*drone\b",  "drone_chubory_generic"),
]

# Combine for single pass
_RULES_ALL: list[tuple[str, str]] = _RULES_ACTIONCAMS + _RULES_DRONES


# ---------- brand detection helpers ----------

def _detect_brand(text: str) -> Optional[str]:
    for make in CAM_BRANDS:
        if re.search(rf"\b{re.escape(make)}\b", text):
            return make
    return None


def _is_droneish(text: str) -> bool:
    return bool(re.search(DRONE_WORDS, text))


def _is_camerish(text: str) -> bool:
    return bool(re.search(CAMERA_WORDS, text))


# ---------- main entrypoint ----------

def camera_drone_model_key(text: str) -> Optional[str]:
    """
    Camera/drone-only classifier.

    Expects `text` already lowercased (same as bike_model_key()).
    Returns cam_* or drone_* model_key or None.
    """
    # 1) Specific rules first
    for pat, key in _RULES_ALL:
        if re.search(pat, text):
            return key

    # 2) Fallback: brand + type (camera vs drone)
    brand = _detect_brand(text)
    if not brand:
        return None

    '''
    if _is_droneish(text):
        return f"drone_{brand}_generic"

    if _is_camerish(text):
        return f"cam_{brand}_generic"
    '''

    return None
