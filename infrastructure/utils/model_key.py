import re
from typing import Optional

# -------- platform regex snippets --------
SWITCH   = r"(nintendo\s*switch|nsw\b|\bswitch\b)"
PS5      = r"\bps5\b"
PS4      = r"\bps4\b"
PS3      = r"\bps3\b"
PS2      = r"\bps2\b|\bplaystation\s*2\b"
SERIES_X = r"\bxbox\s*series\s*x\b|\bxsx\b|\bx\s*series\b(?!\s*s)"
SERIES_S = r"\bxbox\s*series\s*s\b|\bxss\b|\bs\s*series\b"
XONE     = r"\bxbox\s*one\b|\bxone\b"
X360     = r"\bxbox\s*360\b|\bx360\b|\b360\b"
WII      = r"\bwii\b"
HANDHELD = r"\br36s\b|\bpowkiddy\b|\banbernic\b|\brg\d+\b|\bretroid\b|\bmiu\b|\btrimui\b"

# -------- helpers for bikes/cars --------
CC   = r"\b(50|80|90|100|110|125|150|200|250|300|350|400|450|500|600|650|700|750|800|850|900|950|1000|1100|1200|1300)\s*cc\b"
YEAR = r"\b(200[0-9]|201[0-9]|202[0-6])\b"

# -------- RULES --------
_RULES: list[tuple[str, str]] = [
    # --- Consoles ---
    (rf"{SWITCH}.*\boled\b",                     "switch_oled"),
    (rf"{SWITCH}.*\blite\b",                     "switch_lite"),
    (rf"{SWITCH}.*\bv2\b",                       "switch_v2"),
    (rf"{SWITCH}(?!.*(oled|lite))",              "switch_standard"),

    (rf"{PS5}.*\b(disc|digital)\s*edition\b",    "ps5_standard"),
    (rf"{PS5}.*\bconsole\b",                     "ps5_standard"),
    (rf"{PS5}(?!.*(game|sealed|pegi|dlc|code|steelbook))\b", "ps5_standard"),

    (rf"{PS4}.*\bpro\b.*\b1\s*tb\b",             "ps4_pro_1tb"),
    (rf"{PS4}.*\bpro\b",                         "ps4_pro"),
    (rf"{PS4}.*\bslim\b.*\b1\s*tb\b",            "ps4_slim_1tb"),
    (rf"{PS4}.*\bslim\b.*\b500\s*gb\b",          "ps4_slim_500gb"),
    (rf"{PS4}.*\bslim\b",                        "ps4_slim"),
    (rf"{PS4}.*\bconsole\b",                     "ps4_unknown"),
    (rf"{PS4}(?!.*(game|sealed|pegi|dlc|code|steelbook))\b", "ps4_unknown"),

    (rf"{SERIES_X}",                             "xbox_series_x"),
    (rf"{SERIES_S}",                             "xbox_series_s"),

    (rf"{XONE}.*\bone\s*x\b|\bxox\b",            "xbox_one_x"),
    (rf"{XONE}.*\bone\s*s\b|\bxos\b",            "xbox_one_s"),
    (rf"{XONE}.*\bconsole\b",                    "xbox_one"),
    (rf"{XONE}(?!.*(game|sealed|pegi|dlc|code|steelbook))", "xbox_one"),

    (rf"{X360}",                                 "xbox_360"),
    (rf"{WII}",                                  "wii"),
    (rf"{PS3}",                                  "ps3"),
    (rf"{PS2}",                                  "ps2"),

    (rf"{HANDHELD}",                             "retro_handheld"),
    (r"\b(retro|arcade|tv)\s+(game|console)\b",  "retro_tv_box"),

    # --- Accessory false positives for bikes ---
    (r"\b(bike|bicycle)\s+(rack|mount|carrier)\b", "accessory_bike_rack"),
    (r"\b(exercise|spin)\s+bike\b",               "accessory_exercise_bike"),
    (r"\bmotorbike\s+(cover|lock|chain)\b",       "accessory_bike_misc"),

    # --- Bikes: Honda ---
    (r"\bhonda\b.*\bcbr125\b",                   "bike_honda_cbr125"),
    (r"\bhonda\b.*\bcbr\b",                      "bike_honda_cbr"),
    (r"\bhonda\b.*\bcrf\b",                      "bike_honda_crf"),
    (r"\bhonda\b.*\bcbf\b",                      "bike_honda_cbf"),
    (r"\bhonda\b.*\bpcx\b",                      "bike_honda_pcx"),
    (r"\bhonda\b.*\bforza\b",                    "bike_honda_forza"),
    (r"\bhonda\b.*\bgrom\b",                     "bike_honda_grom"),
    (r"\bhonda\b.*(motorcycle|motorbike|bike|scooter|moped)", "bike_honda"),
    (rf"\bhonda\b.*({CC}|{YEAR})",               "bike_honda"),

    # --- Yamaha ---
    (r"\byamaha\b.*\bmt[- ]?07\b",               "bike_yamaha_mt07"),
    (r"\byamaha\b.*\bmt[- ]?09\b",               "bike_yamaha_mt09"),
    (r"\byamaha\b.*\byzf\b",                     "bike_yamaha_yzf"),
    (r"\byamaha\b.*\br6\b",                      "bike_yamaha_r6"),
    (r"\byamaha\b.*\br1\b",                      "bike_yamaha_r1"),
    (r"\byamaha\b.*\btmax\b",                    "bike_yamaha_tmax"),
    (r"\byamaha\b.*\bxmax\b",                    "bike_yamaha_xmax"),
    (r"\byamaha\b.*\bnmax\b",                    "bike_yamaha_nmax"),
    (r"\byamaha\b.*(motorcycle|motorbike|bike|scooter|moped)", "bike_yamaha"),
    (rf"\byamaha\b.*({CC}|{YEAR})",              "bike_yamaha"),

    # --- Kawasaki ---
    (r"\bkawasaki\b.*\bzx6r\b",                  "bike_kawasaki_zx6r"),
    (r"\bkawasaki\b.*\bzx10r\b",                 "bike_kawasaki_zx10r"),
    (r"\bkawasaki\b.*\bninja\b",                 "bike_kawasaki_ninja"),
    (r"\bkawasaki\b.*(motorcycle|motorbike|bike)", "bike_kawasaki"),
    (rf"\bkawasaki\b.*({CC}|{YEAR})",            "bike_kawasaki"),

    # --- Suzuki ---
    (r"\bsuzuki\b.*\bgsxr\b",                    "bike_suzuki_gsxr"),
    (r"\bsuzuki\b.*(motorcycle|motorbike|bike)", "bike_suzuki"),
    (rf"\bsuzuki\b.*({CC}|{YEAR})",              "bike_suzuki"),

    # --- KTM ---
    (r"\bktm\b.*\bduke\b",                       "bike_ktm_duke"),
    (r"\bktm\b.*\badventure\b",                  "bike_ktm_adventure"),
    (r"\bktm\b.*(motorcycle|motorbike|bike)",    "bike_ktm"),
    (rf"\bktm\b.*({CC}|{YEAR})",                 "bike_ktm"),

    # --- Ducati ---
    (r"\bducati\b.*\bpanigale\b",                "bike_ducati_panigale"),
    (r"\bducati\b.*\bscrambler\b",               "bike_ducati_scrambler"),
    (r"\bducati\b.*\bmultistrada\b",             "bike_ducati_multistrada"),
    (r"\bducati\b.*(motorcycle|motorbike|bike)", "bike_ducati"),
    (rf"\bducati\b.*({CC}|{YEAR})",              "bike_ducati"),

    # --- Triumph ---
    (r"\btriumph\b.*\btiger\b",                  "bike_triumph_tiger"),
    (r"\btriumph\b.*\bbonneville\b",             "bike_triumph_bonneville"),
    (r"\btriumph\b.*(motorcycle|motorbike|bike)", "bike_triumph"),
    (rf"\btriumph\b.*({CC}|{YEAR})",             "bike_triumph"),

    # --- BMW ---
    (r"\bbmw\b.*\bs1000rr\b",                    "bike_bmw_s1000rr"),
    (r"\bbmw\b.*\br(1200|1250)\b",               "bike_bmw_r_series"),
    (r"\bbmw\b.*(motorcycle|motorbike|bike)",    "bike_bmw"),
    (rf"\bbmw\b.*({CC}|{YEAR})",                 "bike_bmw"),

    # --- Other brands ---
    (r"\bharley(-|\s)?davidson\b",               "bike_harley"),
    (r"\bharley\b",                              "bike_harley"),
    (r"\bvespa\b",                               "bike_vespa"),
    (r"\b(enfield|royal\s+enfield)\b",           "bike_enfield"),
    (r"\baprilia\b",                             "bike_aprilia"),
    (r"\bhusqvarna\b",                           "bike_husqvarna"),

    # --- Generic two-wheeler ---
    (rf"\b(motorcycle|motorbike|bike|scooter|moped)\b.*({CC}|{YEAR})", "bike_generic"),
    (r"\b(motocross|enduro|mx|quad|atv)\b",      "bike_offroad"),

    # --- Cars ---
    (r"\bford\b.*\bfiesta\b",                    "car_ford_fiesta"),
    (r"\bford\b.*\bfocus\b",                     "car_ford_focus"),
    (r"\bvw\b.*\bgolf\b|\bvolkswagen\b.*\bgolf\b", "car_vw_golf"),
    (r"\bbmw\b.*\b3\s*series\b",                 "car_bmw_3series"),
    (r"\baudi\b.*\ba3\b",                        "car_audi_a3"),
    (r"\baudi\b.*\ba4\b",                        "car_audi_a4"),
    (r"\bmercedes\b.*\bc[-\s]?class\b|\bmercedes[-\s]?benz\b.*\bc[-\s]?class\b", "car_mercedes_cclass"),
    (r"\bskoda\b.*\boctavia\b",                  "car_skoda_octavia"),
    (r"\btoyota\b.*\bcorolla\b",                 "car_toyota_corolla"),
    (r"\bnissan\b.*\bqashqai\b",                 "car_nissan_qashqai"),

    # --- Car brands ---
    (r"\bford\b",                                "car_ford"),
    (r"\bvw\b|\bvolkswagen\b",                   "car_vw"),
    (r"\bbmw\b",                                 "car_bmw"),
    (r"\baudi\b",                                "car_audi"),
    (r"\bmercedes\b|\bmercedes[-\s]?benz\b",     "car_mercedes"),
    (r"\btoyota\b",                              "car_toyota"),
    (r"\bnissan\b",                              "car_nissan"),
    (r"\bskoda\b",                               "car_skoda"),
    (r"\bseat\b",                                "car_seat"),
    (r"\bpeugeot\b",                             "car_peugeot"),
    (r"\bcitroen\b",                             "car_citroen"),
    (r"\brenault\b",                             "car_renault"),
    (r"\bfiat\b",                                "car_fiat"),
    (r"\blexus\b",                               "car_lexus"),
    (r"\bsubaru\b",                              "car_subaru"),
    (r"\btesla\b",                               "car_tesla"),
    (r"\bjaguar\b",                              "car_jaguar"),
    (r"\bmini\b",                                "car_mini"),
]

def normalise_model(title: str) -> Optional[str]:
    t = title.lower()
    for pat, key in _RULES:
        if re.search(pat, t):
            return key
    return None
