from __future__ import annotations

import re
from typing import Optional

# -------- shared TOOL helpers --------

# Voltage / battery rating
VOLTAGE = re.compile(r"\b(10\.8|12|14\.4|16|18|20|24|36|40|54)\s*v\b")

# Tool type hints (useful to enrich the key)
TOOL_TYPE = re.compile(
    r"\b(drill|driver|impact\s*driver|impact\s*wrench|hammer\s*drill|sds|combi\s*drill|angle\s*grinder|grinder|circular\s*saw|jig\s*saw|recip\s*saw|multitool|multi\s*tool|nail\s*gun|nailer)\b",
    flags=re.I,
)

# Model pattern – typical manufacturer codes like DHP458, DCF887, GSB 18V-55, etc.
MODEL_CODE = re.compile(r"\b([A-Z]{2,4}\s*-?\s*\d{2,4}[A-Z]?)\b")

# -------- brand-specific RULES --------
# Simple direct brand triggers → brand base key

_RULES_TOOLS: list[tuple[str, str]] = [
    (r"\bmakita\b", "tools_makita"),
    (r"\bdewalt\b", "tools_dewalt"),
    (r"\bbosch\b", "tools_bosch"),
    (r"\bmilwaukee\b", "tools_milwaukee"),
    (r"\bryobi\b", "tools_ryobi"),
    (r"\bhilti\b", "tools_hilti"),
    (r"\bhitachi\b", "tools_hitachi"),
    (r"\bmetabo\b", "tools_metabo"),
    (r"\beinhell\b", "tools_einhell"),
    (r"\bfestool\b", "tools_festool"),
    (r"\bparkside\b", "tools_parkside"),
]


# -------- enrichment helpers --------

def _parse_voltage(text: str) -> Optional[str]:
    m = VOLTAGE.search(text)
    if not m:
        return None
    return f"{m.group(1).replace('.', '_')}v"


def _parse_tool_type(text: str) -> Optional[str]:
    m = TOOL_TYPE.search(text)
    if not m:
        return None
    return m.group(1).replace(" ", "_").lower()


def _parse_model_code(text: str) -> Optional[str]:
    m = MODEL_CODE.search(text)
    if not m:
        return None
    return m.group(1).replace(" ", "").replace("-", "").upper()


def _refine_tool_key(base_key: str, text: str) -> str:
    """
    Example: tools_makita → tools_makita_DHP458_18v_drill
    """
    t = text.lower()
    parts = [base_key]

    model = _parse_model_code(text)
    if model:
        parts.append(model)

    volt = _parse_voltage(t)
    if volt:
        parts.append(volt)

    ttype = _parse_tool_type(t)
    if ttype:
        parts.append(ttype)

    return "_".join(parts)


def _guess_tool_base_from_title(text: str) -> Optional[str]:
    t = text.lower()
    for pat, base in _RULES_TOOLS:
        if re.search(pat, t):
            return base
    return None


def tools_model_key(text: str) -> Optional[str]:
    """
    Tool-specific model classifier.
    Returns tools_* key or None.
    """
    # 1) Brand-specific hits first
    for pat, key in _RULES_TOOLS:
        if re.search(pat, text):
            return _refine_tool_key(key, text)

    # 2) Fallback: unknown brand but toolish text
    base = "tools_generic"
    if TOOL_TYPE.search(text) or VOLTAGE.search(text):
        return _refine_tool_key(base, text)

    return None
