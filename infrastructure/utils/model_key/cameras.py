# agent/model_keys/cameras.py
from __future__ import annotations

from typing import Mapping, Any, Optional

from infrastructure.utils.condition import _derive_condition_grade

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


def _compress_model_tokens(tokens: list[str]) -> str:
    """
    Take cleaned model tokens and collapse them into a coarse "family" token.

    Goal: hero13black bundle combo -> hero13
          osmo action 4 adventure combo -> osmoaction4
          x3 creator kit -> x3
          a7 iii body -> a7iii

    Strategy:
      - Always keep the first token.
      - Keep the next tokens until we hit a "stop word" like colour/bundle/edition,
        or we already have enough info (name + number).
      - Join chosen tokens together.
    """
    if not tokens:
        return ""

    STOP_TOKENS = {
        # colours
        "black", "white", "silver", "grey", "gray", "green", "blue", "red",
        "yellow", "orange", "purple", "gold", "rose", "pink",
        # bundle / packaging
        "bundle", "combo", "kit", "set", "pack", "gift", "creator", "creatoredition",
        "adventure", "adventureedition", "edition", "special", "limited",
        # generic fluff
        "camera", "cam", "actioncam", "hd", "uhd",
        "4k", "5k", "6k", "8k", "1080p", "2k",
        "body", "bodyonly",
    }

    out: list[str] = []
    have_number = False

    for tok in tokens:
        if not out:
            # always keep the first token
            out.append(tok)
            if tok.isdigit():
                have_number = True
            continue

        # stop if token is obviously just colour/bundle/etc
        if tok in STOP_TOKENS:
            break

        # digits → usually generation / model number, good to keep
        if tok.isdigit():
            out.append(tok)
            have_number = True
            continue

        # if we already have a number and at least 2 tokens, we likely
        # have enough to identify the family (e.g. osmo + 4)
        if have_number and len(out) >= 2:
            break

        # keep a couple of name tokens max (e.g. "osmo action", "eos r5")
        if len(out) >= 3:
            break

        out.append(tok)

    return "".join(out)


def _normalise_model(raw_model: Any, raw_brand: Any) -> str:
    """
    Normalise the Model into a compact, bucketable token.

    This is a more "compressed" version than before – we intentionally
    throw away variants (colour, kit/bundle, edition) to reduce key count.

    Steps:
      - Start from attrs["Model"]
      - Strip worthless values ("does not apply", "as the description shows")
      - Strip parentheses and their contents
      - Replace slashes & hyphens with spaces, collapse spaces
      - Strip leading brand token if it repeats the Brand field
      - Strip non-alphanumerics from tokens, lowercase everything
      - Compress tokens into a short "family" via _compress_model_tokens()
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

    # Collapse to a family-like core (hero13, osmoaction4, x3, a7iii, etc.)
    model_core = _compress_model_tokens(tokens)
    return model_core


def camera_drone_model_key(
    attrs: Mapping[str, Any],
    title: str = "",
) -> Optional[str]:
    """
    Build a canonical model key for camera/drone-style listings
    (e.g. source='ebay-actioncams', other camera/drone sources).

    NEW Output format (console-style):

        {brand}-{family}_{grade}

    Examples:
        Brand="GoPro", Model="GoPro HERO 13 Black (Creator Edition)"
            -> "gopro-hero13_B"

        Brand="GoPro", Model="HERO8 Black"
            -> "gopro-hero8_B"

        Brand="DJI", Model="DJI Osmo Action 4 Adventure Combo"
            -> "dji-osmoaction4_B"

        Brand="Insta360", Model="Insta360 X3 Creator Kit"
            -> "insta360-x3_B"

    Rules:
    - Uses attrs["Brand"] and attrs["Model"]
    - Ignores `title` for model, but passes it to _derive_condition_grade
    - If no usable Brand or Model → returns UNKNOWN_KEY ("unknown")
    """
    raw_brand = attrs.get("Brand")
    raw_model = attrs.get("Model")

    brand = _normalise_brand(raw_brand)
    model_core = _normalise_model(raw_model, raw_brand)

    if not brand or not model_core:
        return UNKNOWN_KEY

    base_key = f"{brand}-{model_core}"
    grade = _derive_condition_grade(attrs, title)

    return f"{base_key}_{grade}"
