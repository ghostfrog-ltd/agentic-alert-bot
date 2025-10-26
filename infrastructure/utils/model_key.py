import re

_RULES = [
    (r"nintendo\s*switch\s*(v2|oled)?\s*(\d+gb|\d+g|1tb)?", "switch_oled" ),
    (r"nintendo\s*switch", "switch_v2"),
    (r"ps4\s*pro\s*1tb", "ps4_pro_1tb"),
    (r"ps4\s*slim\s*500\s*gb", "ps4_slim_500gb"),
    (r"ps4\s*slim\s*1tb", "ps4_slim_1tb"),
    (r"ps4", "ps4_unknown"),
    (r"xbox\s*one\s*s\s*1tb", "xbox_one_s_1tb"),
    (r"xbox\s*one\s*s", "xbox_one_s"),
    (r"xbox\s*one\s*x", "xbox_one_x"),
    (r"xbox\s*series\s*s", "xbox_series_s"),
    (r"xbox\s*series\s*x", "xbox_series_x"),
]

def normalise_model(title: str) -> str | None:
    t = title.lower()
    for pat, key in _RULES:
        if re.search(pat, t):
            return key
    return None
