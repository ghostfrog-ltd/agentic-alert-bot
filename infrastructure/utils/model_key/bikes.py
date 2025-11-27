from __future__ import annotations

from typing import Mapping, Any

UNKNOWN_KEY = "unknown"


def _clean(s: Any) -> str:
    """
    Basic string cleaner:
    - Convert None → ""
    - Strip whitespace
    """
    if s is None:
        return ""
    return str(s).strip()


def _normalise_brand(raw: Any) -> str:
    """
    Normalise manufacturer → brand segment for the key.

    Rules:
    - Use Manufacturer only
    - Lowercase
    - Remove spaces and non-alphanumeric chars
    """
    s = _clean(raw)
    if not s:
        return ""

    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def _strip_parentheses(s: str) -> str:
    """
    Remove anything inside parentheses, including the parentheses themselves.
    Example:
        "YZF R6 (YZF600)" -> "YZF R6 "
    """
    cleaned = []
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            if depth > 0:
                depth -= 1
            continue
        if depth == 0:
            cleaned.append(ch)
    return "".join(cleaned)

def _normalise_capacity(cc: int | None) -> int | None:
    """
    Normalise small displacement differences into standard bike classes.
    Only adjusts values within plausible ranges; otherwise returns original cc.
    """
    if cc is None:
        return None

    # 125 Class
    if 120 <= cc <= 129:
        return 125

    # 115-ish scooters
    if 110 <= cc <= 119:
        return 115

    # 232 / 250 Class variants
    if 230 <= cc <= 239:
        return 232
    if 240 <= cc <= 255:
        return 250

    # 300 Class
    if 298 <= cc <= 305:
        return 300

    # 320–350 Class
    if 306 <= cc <= 324:
        return 320
    if 325 <= cc <= 350:
        return 350

    # 400 Class
    if 395 <= cc <= 404:
        return 400

    # 600–650 Class
    if 595 <= cc <= 609:
        return 600
    if 643 <= cc <= 655:
        return 650

    # 690 / 700 Class
    if 670 <= cc <= 699:
        return 690
    if 695 <= cc <= 705:
        return 700

    # 750 Class
    if 730 <= cc <= 760:
        return 750

    # 800 Class
    if 795 <= cc <= 810:
        return 800

    # 1000 Class
    if 995 <= cc <= 1010:
        return 1000

    return cc


def _normalise_model(raw: Any) -> str:
    """
    Normalise a bike model into a stable token.

    - Remove parentheses and junk
    - Collapse separators
    - Remove 'abs' token (MT-07 ABS → mt07)
    - Remove trailing year-like suffixes (e.g. 21/22/23)
    - Preserve significant variant codes (k5, k7, r6 etc.)
    """
    s = _clean(raw)
    if not s:
        return ""

    s = _strip_parentheses(s)
    s = s.replace("/", " ")
    s = s.replace("\\", " ")
    s = s.replace("-", " ")
    s = " ".join(s.split())

    if not s:
        return ""

    tokens = []
    for tok in s.split():
        alnum = "".join(ch for ch in tok if ch.isalnum())
        if not alnum:
            continue
        lower = alnum.lower()
        # Drop ABS
        if lower == "abs":
            continue
        tokens.append(lower)

    if not tokens:
        return ""

    model = "".join(tokens)

    # Strip suffix year tokens (15-25), but only if model remains meaningful
    if len(model) > 3 and model[-2:].isdigit():
        yy = int(model[-2:])
        if 15 <= yy <= 25:  # bikes from 2015–2025
            model = model[:-2]

    if model == "0":
        return ""

    return model



def _parse_capacity_cc(attrs: Mapping[str, Any]) -> int | None:
    """
    Parse capacity (cc) from attrs.

    Priority:
    1) Capacity (cc)
    2) Engine Size

    Rules:
    - Convert to int if possible
    - If value is 0 or negative → skip (treat as missing)
    """
    for key in ("Capacity (cc)", "Engine Size"):
        raw = attrs.get(key)
        s = _clean(raw)
        if not s:
            continue
        try:
            cc = int(float(s))
        except ValueError:
            continue
        # zero-cc is to be skipped
        if cc <= 0:
            continue
        return cc
    return None


def bike_model_key(attrs: Mapping[str, Any], title: str = "") -> str:
    """
    Build a canonical model key for bikes (motomine, etc.) using ONLY attrs.

    Output format (examples):
        suzuki-gsxr600-600cc_b
        honda-cmx500-500cc_b
        ktm-125duke-125cc_b
        bmw-r1200gs-1200cc_b

    Rules:
    - brand: from Manufacturer, normalised
    - model: from Model, normalised
    - capacity: from Capacity (cc) or Engine Size, appended as "{cc}cc"
    - We IGNORE Year to avoid fragmenting comps
    - Every valid key ends "_b" (forced baseline grade)
    - If brand or model missing/invalid → return "unknown" (no suffix)
    - If capacity is missing or 0 → no capacity suffix
    - `title` accepted but ignored
    """
    brand = _normalise_brand(attrs.get("Manufacturer"))
    model = _normalise_model(attrs.get("Model"))
    capacity_cc = _parse_capacity_cc(attrs)

    if not brand or not model:
        return UNKNOWN_KEY

    parts: list[str] = [f"{brand}-{model}"]

    if capacity_cc is not None:
        parts.append(f"{capacity_cc}cc")

    base_key = "-".join(parts)

    # Force grade B on all bikes
    return f"{base_key}_b"

