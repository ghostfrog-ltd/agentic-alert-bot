from __future__ import annotations

from typing import Optional, Mapping, Any

from .bikes import bike_model_key
from .apple import apple_model_key
from .watches import watch_model_key
from .consoles import console_or_game_model_key
from .cameras import camera_drone_model_key
from .tools import tools_model_key

def _canonicalise_key(key: Optional[str]) -> Optional[str]:
    """
    Standardise model_key formatting:
      - strip whitespace
      - lowercase
      - collapse blank → None
    """
    if not key:
        return None

    key = key.strip().lower()
    if not key:
        return None

    return key


def normalise_model(
    title: str,
    attrs: Optional[Mapping[str, Any]] = None,
    source: str = "",
) -> Optional[str]:
    """
    High-level classifier used everywhere.

    We route by `source` so each domain uses its own specialist:

      - ebay-consoles      -> consoles / games
      - motomine           -> bikes
      - ebay-apple         -> Apple
      - ebay-watches       -> watches
      - ebay-actioncams    -> cameras / drones
      - ebay-tools         -> tools

    `attrs`:
      - For structured sources (MotoMine, Trading, etc.) pass the raw attributes dict.
      - For title-only cases (plain Browse listings), you can omit it and we'll
        treat it as an empty dict.
    """
    if not title:
        return None

    source = (source or "").strip().lower()
    safe_attrs: Mapping[str, Any] = attrs or {}

    if source == "ebay-consoles":
        return _canonicalise_key(
            console_or_game_model_key(attrs=safe_attrs, title=title)
        )

    if source == "motomine":
        return _canonicalise_key(
            bike_model_key(attrs=safe_attrs, title=title)
        )

    if source == "ebay-apple":
        return _canonicalise_key(
            apple_model_key(attrs=safe_attrs, title=title)
        )

    if source == "ebay-watches":
        return _canonicalise_key(
            watch_model_key(attrs=safe_attrs, title=title)
        )

    if source == "ebay-actioncams":
        return _canonicalise_key(
            camera_drone_model_key(attrs=safe_attrs, title=title)
        )

    if source == "ebay-tools":
        return _canonicalise_key(
            tools_model_key(attrs=safe_attrs, title=title)
        )

    # Unknown source → no classification
    return None
