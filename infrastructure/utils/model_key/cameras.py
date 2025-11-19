# agent/model_keys/cameras.py
from __future__ import annotations

from typing import Mapping, Any, Optional

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


def _strip_parentheses(s: str) -> str:
    """
    Remove anything inside parentheses, including the parentheses themselves.
    Example:
        "GoPro HERO 13 Black (Creator Edition)" -> "GoPro HERO 13 Black "
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


def _normalise_brand(raw: Any) -> str:
    """
    Normalise Brand into a compact token for the key.

    Rules:
    - Use Brand only
    - Lowercase
    - Remove spaces and non-alphanumeric chars
    Examples:
        "GoPro" -> "gopro"
        "Insta360 & Mavic2" -> "insta360mavic2"
        "GoPro Westcoast" -> "goprowestcoast"
    """
    s = _clean(raw)
    if not s:
        return ""

    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def _normalise_model(raw_model: Any, raw_brand: Any) -> str:
    """
    Normalise the Model into a compact, bucketable token.

    Rules:
    - Prefer Model (attrs["Model"])
    - Strip worthless values ("does not apply", "as the description shows")
    - Strip parentheses and their contents
    - Replace slashes & hyphens with spaces, collapse multiple spaces
    - Strip leading brand token if it repeats the Brand field
      (e.g. Brand=GoPro, Model="GoPro HERO 13 Black" → "HERO 13 Black")
    - Strip non-alphanumerics from tokens, lowercase everything
    - Join tokens into a single identifier
    - If result is empty → treat as missing
    """
    s = _clean(raw_model)
    if not s:
        return ""

    low = s.lower()
    if "does not apply" in low or low in {"as the description shows", "camcorders"}:
        return ""

    # Kill any bracketed junk
    s = _strip_parentheses(s)

    # Normalise separators
    s = s.replace("/", " ")
    s = s.replace("\\", " ")
    s = s.replace("-", " ")
    s = " ".join(s.split())  # collapse multiple spaces

    if not s:
        return ""

    # Tokenise
    raw_tokens = s.split()

    # Try to remove leading brand token (e.g. "GoPro HERO 13 Black")
    brand_clean = _clean(raw_brand)
    brand_tokens = brand_clean.split()
    brand_first = brand_tokens[0].lower() if brand_tokens else ""

    if brand_first and raw_tokens:
        if raw_tokens[0].lower() == brand_first:
            raw_tokens = raw_tokens[1:]

    tokens: list[str] = []
    for tok in raw_tokens:
        alnum = "".join(ch for ch in tok if ch.isalnum())
        if not alnum:
            continue
        tokens.append(alnum.lower())

    if not tokens:
        return ""

    # Examples:
    #   ["hero", "13", "black"] -> "hero13black"
    #   ["osmo", "action", "4"] -> "osmoaction4"
    #   ["x3"] -> "x3"
    model = "".join(tokens)
    return model


def camera_drone_model_key(
    attrs: Mapping[str, Any],
    title: str = "",
) -> Optional[str]:
    """
    Build a canonical model key for camera/drone-style listings
    (e.g. source='ebay-actioncams', other camera/drone sources) using ONLY attrs.

    Output format:
        {brand}-{model}

    Examples:
        Brand="GoPro", Model="GoPro HERO 13 Black"
            -> "gopro-hero13black"

        Brand="GoPro", Model="HERO8"
            -> "gopro-hero8"

        Brand="DJI", Model="DJI Osmo Action 4 Adventure"
            -> "dji-osmoaction4adventure"

        Brand="Insta360", Model="Insta360 X3"
            -> "insta360-x3"

        Brand="AKASO", Model="Akaso Ek7000 Pro"
            -> "akaso-ek7000pro"

    Rules:
    - Uses attrs["Brand"] and attrs["Model"]
    - Ignores `title` completely (kept only for call-site compatibility)
    - If no usable Brand or Model → returns UNKNOWN_KEY ("unknown")
    """
    raw_brand = attrs.get("Brand")
    raw_model = attrs.get("Model")

    brand = _normalise_brand(raw_brand)
    model = _normalise_model(raw_model, raw_brand)

    if not brand or not model:
        return UNKNOWN_KEY

    return f"{brand}-{model}"
