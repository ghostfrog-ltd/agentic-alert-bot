from __future__ import annotations

import re
from typing import Mapping, Any, Optional

from infrastructure.utils.condition import _derive_condition_grade

UNKNOWN_KEY = "unknown"


def _clean_alnum(s: Any) -> str:
    """
    Uppercase, strip, remove non-alphanumeric.
    Used for brand / reference / model core tokens.
    """
    if s is None:
        return ""
    s = str(s).strip().upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def _clean_brand(raw: Any) -> str:
    """
    Normalise brand to a lowercase alphanumeric token.
    Examples:
        "Seiko"  -> "seiko"
        "G-SHOCK" -> "gshock"
    """
    b = _clean_alnum(raw)
    return b.lower() if b else ""


def _extract_reference(attrs: Mapping[str, Any]) -> Optional[str]:
    """
    Use 'Reference Number' if present.
    Sometimes it's a list like ['2F70-5330', '2F70'] – pick the longest.

    Returns a lowercase alphanumeric token, or None if not useful.
    """
    ref = attrs.get("Reference Number") or attrs.get("ReferenceNumber")
    if not ref:
        return None

    if isinstance(ref, list):
        candidates = [_clean_alnum(r) for r in ref if _clean_alnum(r)]
        if not candidates:
            return None
        # take the longest cleaned ref (most specific)
        best = max(candidates, key=len)
    else:
        best = _clean_alnum(ref)

    if best in {"NONE", "NA", "N/A", "NOTAPPLICABLE"}:
        return None

    best = best.strip()
    return best.lower() or None


def _extract_model_core(
    attrs: Mapping[str, Any],
    brand_norm: str,
) -> Optional[str]:
    """
    Fallback when we don't have a good reference number.

    Use Model (or Watch Model) only — no title fallback.

    - Strip brand prefix if duplicated, e.g. "SEIKO 5" -> "5"
    - Split on space / dash / slash
    - Remove non-alphanumeric characters per token
    - Drop generic words like WATCH/MENS/WOMENS/UNISEX
    - Glue remaining tokens together

    Returns a lowercase alphanumeric string, or None.
    """
    raw_model = attrs.get("Model") or attrs.get("Watch Model")
    if not raw_model:
        return None

    s = str(raw_model).strip().upper()

    # remove obvious brand prefix if present: "SEIKO 5" -> "5"
    if brand_norm:
        bn = brand_norm.upper()
        if s.startswith(bn + " "):
            s = s[len(bn) + 1 :]

    tokens = re.split(r"[ \-/]+", s)
    pieces: list[str] = []
    for tok in tokens:
        c = re.sub(r"[^A-Z0-9]", "", tok)
        if not c:
            continue
        # don't keep generic words that add nothing
        if c in {"WATCH", "WRISTWATCH", "MENS", "MEN", "WOMENS", "WOMEN", "UNISEX"}:
            continue
        pieces.append(c)

    if not pieces:
        return None

    # Return lowercase joined token, e.g. ["F", "91W"] -> "f91w"
    return "".join(pieces).lower()


def watch_model_key(
    attrs: Mapping[str, Any],
    title: Optional[str] = None,  # kept for call-site compatibility
) -> str:
    """
    Canonical key for watches (attrs-only, no title for model).

    Priority:
      1) brand-ref         (when 'Reference Number' present)
      2) brand-modelcore   (fallback using Model / Watch Model)

    New output style (console-style with grade):
      brand-ref_<grade>
      brand-modelcore_<grade>

    Examples:
      Brand="Seiko", Reference="6222-8000"
        -> "seiko-62228000_B"

      Brand="Seiko", Model="Seiko 5"
        -> "seiko-5_B"

      Brand="Casio", Model="F-91W"
        -> "casio-f91w_B"

      Brand="G-SHOCK", Model="Mudman GW-9500"
        -> "gshock-gw9500_A"

    If we can't classify, returns "unknown" (no grade suffix).
    """
    attrs = attrs or {}
    brand = _clean_brand(attrs.get("Brand"))
    if not brand:
        return UNKNOWN_KEY

    # 1) Try reference number (most specific)
    ref = _extract_reference(attrs)
    if ref:
        base_key = f"{brand}-{ref}"
        grade = _derive_condition_grade(attrs, title or "")
        return f"{base_key}_{grade}"

    # 2) Fall back to model
    model_core = _extract_model_core(attrs, brand)
    if model_core:
        base_key = f"{brand}-{model_core}"
        grade = _derive_condition_grade(attrs, title or "")
        return f"{base_key}_{grade}"

    return UNKNOWN_KEY
