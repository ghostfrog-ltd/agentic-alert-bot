from __future__ import annotations

import re
from typing import Mapping, Any, Optional


_STOPWORDS = {
    "BLACK", "WHITE", "SILVER", "GREY", "GRAY",
    "BLUE", "RED", "GREEN", "YELLOW", "ORANGE",
    "LIMITED", "EDITION", "KIT", "BUNDLE", "COMBO", "PACK",
    "ADVENTURE", "CREATOR", "CREATOREDITION", "REFURB", "RENEWED",
    "CAMERA",
}


def _clean(s: Any) -> str:
    if not s:
        return ""
    return str(s).strip().upper()


def _clean_brand(raw: Any) -> Optional[str]:
    b = _clean(raw)
    if not b or b == "UNBRANDED":
        return None
    return re.sub(r"[^A-Z0-9]", "", b) or None


def _extract_brand(attrs: Mapping[str, Any], title: str) -> Optional[str]:
    # 1) From attrs
    brand = attrs.get("Brand") or attrs.get("Manufacturer")
    brand_norm = _clean_brand(brand)
    if brand_norm:
        return brand_norm

    # 2) From title (best-effort)
    t = title.upper()

    for known in ["GOPRO", "DJI", "INSTA360", "DRIFT", "AKASO", "APEMAN", "SONY", "CANON", "NIKON"]:
        if known in t:
            return known

    return None


def _model_core(brand: str, model_raw: Any) -> Optional[str]:
    if not model_raw:
        return None

    m = _clean(model_raw)

    # Strip "DOES NOT APPLY" type nonsense
    if m in {"DOES NOT APPLY", "N/A", "NOT APPLICABLE"}:
        return None

    # Strip leading brand again (duplicate)
    if m.startswith(brand + " "):
        m = m[len(brand) + 1 :]

    # Tokenise
    tokens = re.split(r"[ \-/]+", m)

    core = []
    for tok in tokens:
        tok = re.sub(r"[^A-Z0-9+]", "", tok)
        if not tok:
            continue
        if tok in _STOPWORDS:
            continue
        core.append(tok)

    if not core:
        return None

    # DJI Osmo Action special merge
    if brand == "DJI":
        if len(core) >= 2 and core[0] == "OSMO" and core[1] == "ACTION":
            core = ["OSMOACTION"] + core[2:]

    # GoPro HERO 3+ => HERO3PLUS
    for i, tok in enumerate(core):
        if tok.endswith("+"):
            core[i] = tok.replace("+", "PLUS")

    return "".join(core) or None


def camera_drone_model_key(
    attrs: Mapping[str, Any],
    title: str
) -> Optional[str]:
    """
    Canonical camera/drone model key.
    Uses attrs first, falls back to title.
    Example outputs:
      GOPRO-HERO12
      DJI-OSMOACTION4
      INSTA360-X3
      DRIFT-GHOSTXL
    """
    brand = _extract_brand(attrs, title)
    if not brand:
        return None

    # Prefer model from attrs, fallback to title
    raw_model = attrs.get("Model")
    if not raw_model:
        raw_model = title

    core = _model_core(brand, raw_model)
    if not core:
        return None

    return f"{brand}-{core}"
