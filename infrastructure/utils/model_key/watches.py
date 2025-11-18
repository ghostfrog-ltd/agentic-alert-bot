from __future__ import annotations

import re
from typing import Mapping, Any, Optional
from typing import Optional, Dict, Any

def _clean(s: Any) -> str:
    """
    Uppercase, strip, remove non-alphanumeric.
    """
    if s is None:
        return ""
    s = str(s).strip().upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def _clean_brand(raw: Any) -> Optional[str]:
    b = _clean(raw)
    return b or None


def _extract_reference(attrs: Mapping[str, Any]) -> Optional[str]:
    """
    Use 'Reference Number' if present.
    Sometimes it's a list like ['2F70-5330', '2F70'] – pick the longest.
    """
    ref = attrs.get("Reference Number") or attrs.get("ReferenceNumber")
    if not ref:
        return None

    if isinstance(ref, list):
        candidates = [ _clean(r) for r in ref if _clean(r) ]
        if not candidates:
            return None
        # take the longest cleaned ref (most specific)
        best = max(candidates, key=len)
    else:
        best = _clean(ref)

    if best in {"NONE", "NA", "N/A", "NOTAPPLICABLE"}:
        return None

    return best or None


def _extract_model_core(
    attrs: Mapping[str, Any],
    title: Optional[str],
    brand_norm: Optional[str],
) -> Optional[str]:
    """
    Fallback when we don't have a good reference number.

    Use Model first; if that's missing, fall back to the title.
    Strip the brand prefix if duplicated.
    """
    raw_model = attrs.get("Model") or attrs.get("Watch Model")
    source = None

    if raw_model:
        source = str(raw_model)
    elif title:
        source = title
    else:
        return None

    s = source.strip().upper()

    # remove obvious brand prefix if present: "SEIKO 5" -> "5"
    if brand_norm:
        bn = brand_norm
        if s.startswith(bn + " "):
            s = s[len(bn) + 1 :]

    # very light token clean: just remove non-alnum and glue
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

    return "".join(pieces)

def watch_model_key(    attrs: Mapping[str, Any],
    title: Optional[str] = None,
) -> Optional[str]:
    """
    Canonical key for watches.

    Priority:
      1) BRAND-REF         (when Reference Number present)
      2) BRAND-MODELCORE   (fallback using Model / title)

    Examples:
      Seiko Skyliner, ref 6222-8000      -> SEIKO-62228000
      Seiko 5, ref 7S26-0480             -> SEIKO-7S260480
      Casio F-91W                        -> CASIO-F91W
      G-SHOCK Mudman GW-9500            -> GSHOCK-GW9500
      Joubert '1930's Celeb' (no ref)   -> JOUBERT-1930SCELEB
    """
    attrs = attrs or {}
    brand = _clean_brand(attrs.get("Brand"))
    if not brand:
        return None

    # 1) Try reference number (most specific)
    ref = _extract_reference(attrs)
    if ref:
        return f"{brand}-{ref}"

    # 2) Fall back to model / title
    model_core = _extract_model_core(attrs, title, brand)
    if model_core:
        return f"{brand}-{model_core}"

    return None
