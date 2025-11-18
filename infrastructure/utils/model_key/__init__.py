from __future__ import annotations

from typing import Optional, Mapping, Any

from .bikes import bike_model_key
from .apple import apple_model_key
from .watches import watch_model_key
from .consoles import console_or_game_model_key
from .cameras import camera_drone_model_key
from .tools import tools_model_key


def _block_faulty(title: Optional[str]) -> bool:
    """
    Return True if the title clearly indicates a faulty / for-parts item.
    """
    if not title:
        return False

    t = title.lower()

    faulty_terms = [
        # Core faults
        "faulty", "not working", "no power", "won't power", "wont power",
        "won't turn on", "wont turn on", "does not turn on", "doesn't turn on",
        "no display", "no video", "no sound", "dead", "powers on then off",
        "boot loop", "does not charge", "won't charge", "wont charge",
        "overheating", "water damaged", "liquid damaged", "liquid damage",
        "screen damaged", "screen cracked", "cracked screen", "screen issues",
        "screen fault", "display fault", "display issue", "faulty battery",
        "bad battery", "battery issue", "battery fault", "charging port issue",
        "charging fault", "screen flickers", "read description", "empty",
        "packaging only", "damaged", "broken screen", "spares and repairs",

        # Spares/repairs flags        "screen fault", "display fault", "display issue", "faulty battery",
        "for spares", "for parts", "spares or repairs", "spares & repairs",
        "repair only", "needs repair", "for repair", "parts only",
        "non working", "non-working", "untested", "sold as seen", "as is", "as-is",
        "not tested", "unable to test",

        # Box/accessory-only
        "box only", "boxes only", "packaging only", "package only",
        "case only", "charger only", "battery only", "shell only",
        "housing only", "empty box", "empty packaging",
        "no console included", "no phone included", "no tablet included",

        # Warnings / scam flags
        "read description", "see description", "read full listing", "please read",

        # Tools-specific
        "no blade", "no battery", "bare unit", "body only", "body-only",
        "no charger", "no accessories", "missing parts", "missing screws",

        # Camera/drone
        "lens error", "lens fault", "gimbal error", "gimbal fault",
        "motor overload", "crash damage", "crashed",

        # Console-specific
        "drift issue", "stick drift", "joystick drift", "controller drift",
        "no hdmi output", "av output fault",
    ]

    for val in faulty_terms:
        if val in t:
            return True

    return False


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

    # Drop obviously faulty/for-parts items for all sources except MotoMine
    if _block_faulty(title) and source != "motomine":
        return None

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
