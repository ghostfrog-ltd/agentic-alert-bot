# agent/model_keys/tools.py
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
        "Dewalt DCF899N-XJ (Body Only)" -> "Dewalt DCF899N-XJ "
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
        "DEWALT"     -> "dewalt"
        "Makita"     -> "makita"
        "Pro-Max Professional Quality Tools" -> "promaxprofessionalqualitytools"
    """
    s = _clean(raw)
    if not s:
        return ""

    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def _is_garbage_model(s: str) -> bool:
    """
    Heuristics for useless model strings we should treat as missing.
    """
    low = s.lower()
    if not low:
        return True

    bad_exact = {
        "n/a",
        "na",
        "unknown",
        "does not apply",
        "doesn't apply",
        "doesnt apply",
        "see description",
        "see descriptions",
        "see pictures",
        "as the description shows",
        "other",
    }
    if low in bad_exact:
        return True

    if "does not apply" in low:
        return True

    return False


def _tokenise_model_like(s: str) -> list[str]:
    """
    Common tokenisation logic for Model/MPN/Type:
    - strip parentheses
    - normalise separators (/, \, -) to spaces
    - collapse multiple spaces
    - split, strip non-alphanumerics per token
    - lowercase
    """
    s = _strip_parentheses(s)

    s = s.replace("/", " ")
    s = s.replace("\\", " ")
    s = s.replace("-", " ")
    s = " ".join(s.split())

    if not s:
        return []

    tokens: list[str] = []
    for tok in s.split():
        alnum = "".join(ch for ch in tok if ch.isalnum())
        if not alnum:
            continue
        tokens.append(alnum.lower())

    return tokens


def _normalise_model_from_model(raw_model: Any, raw_brand: Any) -> str:
    """
    Normalise the Model into a compact, bucketable token.

    Priority path:
    - Use attrs["Model"], cleaned
    - Drop leading brand token if it repeats Brand
      e.g. Brand="DEWALT", Model="DEWALT DCF899N-XJ" -> "dcf899nxj"
    - Return "" if it's garbage/unusable.
    """
    s = _clean(raw_model)
    if not s or _is_garbage_model(s):
        return ""

    tokens = _tokenise_model_like(s)
    if not tokens:
        return ""

    # Try to drop leading brand word if it matches
    brand_clean = _clean(raw_brand)
    brand_tokens = brand_clean.split()
    brand_first = brand_tokens[0].lower() if brand_tokens else ""

    if brand_first and tokens and tokens[0] == brand_first.lower():
        tokens = tokens[1:]

    if not tokens:
        return ""

    return "".join(tokens)


def _normalise_model_from_mpn(raw_mpn: Any) -> str:
    """
    Fallback: build a model-like token from MPN if Model was useless/missing.
    """
    s = _clean(raw_mpn)
    if not s or _is_garbage_model(s):
        return ""

    tokens = _tokenise_model_like(s)
    if not tokens:
        return ""

    return "".join(tokens)


def _normalise_model_from_type(raw_type: Any) -> str:
    """
    Second fallback: use Type as the model-like token (angle grinder, planer, etc.)
    """
    s = _clean(raw_type)
    if not s or _is_garbage_model(s):
        return ""

    tokens = _tokenise_model_like(s)
    if not tokens:
        return ""

    return "".join(tokens)


def tools_model_key(
    attrs: Mapping[str, Any],
    title: str = "",
) -> Optional[str]:
    """
    Build a canonical model key for power tools (source='ebay-tools') using ONLY attrs.

    New output style (console-style with grade):
        {brand}-{model}_{grade}

    Examples (given your attributes):
        Brand="DEWALT", Model="DEWALT DCF899N-XJ"
            -> "dewalt-dcf899nxj_B"

        Brand="DEWALT", Model="DCS565N"
            -> "dewalt-dcs565n_B"

        Brand="Makita", Model="DHS680Z"
            -> "makita-dhs680z_A"

        Brand="Bosch", Model="Bosch PSA 700 E"
            -> "bosch-psa700e_B"

        Brand="Terratek", Model="Terratek Rotary Multi Tool 150 pcs"
            -> "terratek-rotarymultitool150pcs_B"

    Fallbacks:
        - If Model is missing/garbage, use MPN.
        - If MPN is missing/garbage, use Type.
        - If Brand missing OR all candidates for model are missing/garbage → "unknown".

    `title` is ignored for model selection but passed into _derive_condition_grade
    to keep grading consistent with other categories.
    """
    raw_brand = attrs.get("Brand")
    raw_model = attrs.get("Model")
    raw_mpn = attrs.get("MPN")
    raw_type = attrs.get("Type")

    brand = _normalise_brand(raw_brand)
    if not brand:
        return UNKNOWN_KEY

    # 1) Primary: Model
    model = _normalise_model_from_model(raw_model, raw_brand)

    # 2) Fallback: MPN
    if not model:
        model = _normalise_model_from_mpn(raw_mpn)

    # 3) Fallback: Type
    if not model:
        model = _normalise_model_from_type(raw_type)

    if not model:
        return UNKNOWN_KEY

    base_key = f"{brand}-{model}"
    grade = _derive_condition_grade(attrs, title)

    return f"{base_key}_{grade}"
