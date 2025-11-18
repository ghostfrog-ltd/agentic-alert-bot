from __future__ import annotations

from typing import Mapping, Any, Optional


def _clean(s: Any) -> str:
    if not s:
        return ""
    return str(s).strip().upper()


def console_or_game_model_key(
        attrs: Mapping[str, Any],
        title: str,
) -> Optional[str]:
    """
    FINAL RULESET:
    -----------------
    - We ONLY use raw_attrs
    - Title is ignored
    - If Platform, Type, AND Model all exist -> return {PLATFORM}-{TYPE}-{MODEL}
    - If ANY missing -> return "UNKNOWN"
    -----------------
    """

    if not attrs:
        return "unknown"

    # Hard extract.attrs (ONLY attrs matter)
    platform = _clean(attrs.get("Platform"))
    type_ = _clean(attrs.get("Type"))
    model = _clean(attrs.get("Model"))

    # If ANY missing → UNKNOWN
    if not platform or not type_ or not model:
        return "unknown"

    # All 3 present → deterministic key
    return f"{platform}-{type_}-{model}"
