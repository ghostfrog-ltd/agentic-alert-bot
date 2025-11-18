from __future__ import annotations

import re
from typing import Mapping, Any, Optional


def _clean(s: Any) -> str:
    if not s:
        return ""
    s = str(s).strip().upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def _extract_brand(attrs: Mapping[str, Any], title: Optional[str]) -> Optional[str]:
    # Prefer structured fields
    for key in ("Brand", "Manufacturer", "Maker"):
        b = _clean(attrs.get(key))
        if b:
            return b

    # Fallback: sniff from title
    if title:
        t = title.upper()
        for kb in [
            "DEWALT", "MAKITA", "BOSCH", "MILWAUKEE", "RYOBI", "HILTI", "FESTOOL",
            "HIKOKI", "METABO", "AEG", "BLACK+DECKER", "BLACK & DECKER",
            "PARKSIDE", "WORX", "EINHELL", "MAC ALLISTER", "STANLEY"
        ]:
            if kb in t:
                return _clean(kb)

    return None


def _extract_action(attrs: Mapping[str, Any], title: Optional[str]) -> Optional[str]:
    # Prefer structured action / type
    for key in ("Action", "Tool Type", "Type"):
        a = _clean(attrs.get(key))
        if a:
            return a

    # Fallback: infer from title with common patterns
    if title:
        t = title.upper()
        patterns = {
            "COMBI": "COMBIDRILL",
            "HAMMER": "HAMMERDRILL",
            "IMPACT": "IMPACTDRIVER",
            "SDS": "SDSPLUS",
            "CIRCULAR": "CIRCULARSAW",
            "JIGSAW": "JIGSAW",
            "RECIP": "RECIPSAW",
            "GRINDER": "ANGLEGRINDER",
            "MULTI": "MULTITOOL",
            "ROUTER": "ROUTER",
            "PLANER": "PLANER",
            "MITRE": "MITRE",
        }
        for needle, out in patterns.items():
            if needle in t:
                return out

    return None


def _extract_power(attrs: Mapping[str, Any], title: Optional[str]) -> Optional[str]:
    power_fields = [
        "Power", "Voltage", "Volts", "Wattage", "W", "V"
    ]

    # Prefer structured power info
    for key in power_fields:
        v = attrs.get(key)
        if v:
            return _normalise_power(str(v))

    # Fallback from title
    if title:
        t = title.upper()

        # 18V, 20V, 230V, etc
        m = re.search(r"\b(\d+)\s*V\b", t)
        if m:
            return f"{m.group(1)}V"

        # 710W, 1200W, etc
        m = re.search(r"\b(\d+)\s*W\b", t)
        if m:
            return f"{m.group(1)}W"

    return None


def _normalise_power(raw: str) -> Optional[str]:
    raw = raw.upper()

    # extract number + V/W
    m = re.search(r"(\d+)\s*(V|W)", raw)
    if not m:
        return None
    num, unit = m.group(1), m.group(2)
    return f"{num}{unit}"


def _extract_model(attrs: Mapping[str, Any], title: Optional[str]) -> Optional[str]:
    for key in ("Model", "MPN", "Manufacturer Part Number", "PartNumber", "Part Number"):
        v = _clean(attrs.get(key))
        if v and v not in {"NONE", "NA", "DOESNOTAPPLY"}:
            return v

    # Fallback: scrape model-like token from title (optional)
    if title:
        t = title.upper()
        m = re.findall(r"\b[A-Z0-9]{3,}\b", t)
        # Only return a model if it's code-like
        for tok in m:
            if any(c.isdigit() for c in tok):
                return _clean(tok)

    return None


def tools_model_key(
    attrs: Mapping[str, Any],
    title: Optional[str] = None,
) -> Optional[str]:
    """
    Build a canonical key for tools:
      ACTION-POWER-BRAND[-MODEL]
    """

    action = _extract_action(attrs, title)
    power = _extract_power(attrs, title)
    brand = _extract_brand(attrs, title)
    model = _extract_model(attrs, title)

    if not action or not power or not brand:
        return None  # missing fundamentals

    key = f"{action}-{power}-{brand}"

    # model optional
    if model:
        key += f"-{model}"

    return key
