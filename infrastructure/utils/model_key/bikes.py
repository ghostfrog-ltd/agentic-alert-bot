from __future__ import annotations

import re
from typing import Optional, Mapping, Any

# ---------------------------------------
# Lightweight helpers for MotoMine bikes
# ---------------------------------------

# We only care about realistic bike CC values
_CC_NUMBERS = (
    "50|80|90|100|110|125|150|200|250|300|350|400|450|500|600|650|700|750|800|850|900|"
    "950|1000|1100|1200|1300"
)
_CC_RE = re.compile(rf"\b({_CC_NUMBERS})\s*cc\b", re.IGNORECASE)
_CC_BARE_RE = re.compile(rf"\b({_CC_NUMBERS})\b")
_YEAR_RE = re.compile(r"\b(19[7-9]\d|200[0-9]|201[0-9]|202[0-9])\b")


def _normalise_slug(value: str) -> str:
    """
    Normalise manufacturer / model into an uppercase slug with only [A-Z0-9].

    Examples:
        'Harley Davidson'   -> 'HARLEYDAVIDSON'
        'GSX-R 600'         -> 'GSXR600'
        '  Mt-07   '        -> 'MT07'
    """
    value = (value or "").strip().upper()
    # Replace '&' with 'AND' so it is stable
    value = value.replace("&", "AND")
    # Remove everything that isn't A–Z or 0–9
    value = re.sub(r"[^A-Z0-9]+", "", value)
    return value


def _first_str(attrs: Optional[Mapping[str, Any]], *keys: str) -> Optional[str]:
    """
    Return the first non-empty string value for the given keys from attrs.
    Safe if attrs is None or not a Mapping.
    """
    if not attrs or not isinstance(attrs, Mapping):
        return None

    for key in keys:
        if key in attrs and attrs[key] not in (None, ""):
            return str(attrs[key])
    return None


def _extract_cc_from_value(raw: str) -> Optional[str]:
    """
    Extract a plausible CC from a freeform string, e.g.:

      '600 cc'  -> '600'
      '1043cc'  -> '1043'
      'GSXR 600' (with no 'cc') -> '600'
    """
    if not raw:
        return None

    m = _CC_RE.search(raw)
    if m:
        return m.group(1)

    m = _CC_BARE_RE.search(raw)
    if m:
        return m.group(1)

    return None


def _resolve_cc(attrs: Optional[Mapping[str, Any]], title: Optional[str] = None) -> Optional[str]:
    """
    Resolve engine capacity in cc using structured attrs first, then the title.
    Priority:
      1) Capacity (cc)
      2) Engine Size
      3) Parsed from title (if provided)
    """
    raw_cc = _first_str(attrs, "Capacity (cc)", "Engine Size")
    if raw_cc:
        cc = _extract_cc_from_value(raw_cc)
        if cc:
            return cc

    if title:
        cc = _extract_cc_from_value(title)
        if cc:
            return cc

    return None


def _extract_year_from_value(raw: str) -> Optional[str]:
    """
    Extract a plausible year from a freeform string.
    """
    if not raw:
        return None
    m = _YEAR_RE.search(raw)
    return m.group(1) if m else None


def _resolve_year(attrs: Optional[Mapping[str, Any]], title: Optional[str] = None) -> Optional[str]:
    """
    Resolve year using structured attrs first, then the title.
      - Year
      - Date of 1st Registration
      - Fallback to title
    """
    raw_year = _first_str(attrs, "Year", "Date of 1st Registration")
    if raw_year:
        year = _extract_year_from_value(raw_year)
        if year:
            return year

    if title:
        year = _extract_year_from_value(title)
        if year:
            return year

    return None


def bike_model_key(
    attrs: Optional[Mapping[str, Any]],
    title: Optional[str] = None,
) -> Optional[str]:
    """
    Build a MotoMine bike model_key from structured attributes.

    Expected attrs keys (case-sensitive, as per your scraped JSON):
      - 'Manufacturer'
      - 'Model'
      - 'Submodel'            (optional)
      - 'Capacity (cc)'
      - 'Engine Size'
      - 'Year'
      - 'Date of 1st Registration' (optional alt-year)

    Returns strings like:
        'SUZUKI-GSXR600-600-2024'
        'YAMAHA-MT07-689-2021'
        'LEXMOTO-ZSB-125-2018'
        'PIAGGIO-ZIP-2015'        (if CC missing)

    If we don't have enough info (no manufacturer or model), returns None.
    """
    # If attrs is missing or garbage, bail cleanly
    if not attrs or not isinstance(attrs, Mapping):
        return None

    # Core bits: make + model
    raw_make = _first_str(attrs, "Manufacturer", "Make")
    raw_model = _first_str(attrs, "Model", "Submodel")

    if not raw_make or not raw_model:
        return None

    make_slug = _normalise_slug(raw_make)
    model_slug = _normalise_slug(raw_model)

    cc = _resolve_cc(attrs, title=title)
    year = _resolve_year(attrs, title=title)

    parts: list[str] = [make_slug, model_slug]
    if cc:
        parts.append(cc)
    if year:
        parts.append(year)

    return "-".join(parts) if len(parts) >= 2 else None
