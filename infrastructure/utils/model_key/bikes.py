# agent/model_keys/bikes.py
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


def _normalise_model(raw: Any) -> str:
    """
    Normalise the Model into a compact, bucketable token.

    Rules:
    - Use Model only (ignore Street Name etc.)
    - Strip parentheses and their contents
    - Replace slashes and multiple spaces
    - Remove non-alphanumeric characters from tokens
    - Join tokens together, all lowercase
    - If result is "0" or empty → treat as missing
    """
    s = _clean(raw)
    if not s:
        return ""

    # Kill any bracketed junk codes
    s = _strip_parentheses(s)

    # Normalise separators
    s = s.replace("/", " ")
    s = s.replace("\\", " ")
    s = s.replace("-", " ")
    s = " ".join(s.split())  # collapse multiple spaces

    if not s:
        return ""

    tokens = []
    for tok in s.split():
        # Strip non-alphanumerics from each token
        alnum = "".join(ch for ch in tok if ch.isalnum())
        if not alnum:
            continue
        tokens.append(alnum.lower())

    if not tokens:
        return ""

    model = "".join(tokens)  # e.g. ["gsx", "r600", "k5"] → "gsxr600k5"
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
        suzuki-gsxr600-600cc
        honda-cmx500-500cc
        ktm-125duke-125cc
        bmw-r1200gs-1200cc

    Rules:
    - brand: from Manufacturer, normalised
    - model: from Model, normalised
    - capacity: from Capacity (cc) or Engine Size, appended as "{cc}cc"
    - We DELIBERATELY IGNORE Year to avoid fragmenting comps
      (2005 vs 2006 vs 2007 of the same bike should usually share a bucket)
    - If brand or model missing/invalid → return "unknown"
    - If capacity is missing or 0 → no capacity suffix
    - `title` arg is accepted for compatibility but ignored.
    """
    brand = _normalise_brand(attrs.get("Manufacturer"))
    model = _normalise_model(attrs.get("Model"))
    capacity_cc = _parse_capacity_cc(attrs)

    if not brand or not model:
        return UNKNOWN_KEY

    parts: list[str] = [f"{brand}-{model}"]

    if capacity_cc is not None:
        parts.append(f"{capacity_cc}cc")

    return "-".join(parts) if parts else UNKNOWN_KEY
