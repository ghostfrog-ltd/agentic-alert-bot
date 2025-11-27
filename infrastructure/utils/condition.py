
from __future__ import annotations

from typing import Mapping, Any, Optional
from typing import Dict, Set


def _as_text(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip().lower()

def _derive_condition_grade(attrs: Mapping[str, Any], title: str) -> str:
    """
    Map raw attributes + title into a coarse condition grade:

        A = new / sealed / like new
        B = default used / unknown (safe mid-grade)
        C = poorer refurb / rough
        D = clearly faulty / for parts / box-only style listings

    Heuristics are intentionally simple and easy to tweak.
    """

    # 1) Normalised text from title + attributes
    title_text = _as_text(title)

    all_attr_parts = []
    for _, v in attrs.items():
        if isinstance(v, (list, tuple, set)):
            all_attr_parts.extend(_as_text(x) for x in v)
        else:
            all_attr_parts.append(_as_text(v))

    attr_text = " ".join(p for p in all_attr_parts if p)
    blob = f"{title_text} {attr_text}".strip()

    # ----------------
    # Grade D: faulty / spares / box-only
    # ----------------
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

        # Spares/repairs flags
        "for spares", "for parts", "spares or repairs", "spares & repairs",
        "repair only", "needs repair", "for repair", "parts only",
        "non working", "non-working", "untested", "sold as seen", "as is", "as-is",
        "not tested", "unable to test",

        # Box/accessory-only
        "box only", "boxes only", "package only",
        "case only", "charger only", "battery only", "shell only",
        "housing only", "empty box", "empty packaging",
        "no console included", "no phone included", "no tablet included",

        # Warnings / scam flags
        "see description", "read full listing", "please read",

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

    if any(term in blob for term in faulty_terms):
        return "D"

    # ----------------
    # Explicit grade / condition fields (if present)
    # ----------------
    grade_val = _as_text(
        attrs.get("Grade")
        or attrs.get("Product Grade")
        or attrs.get("Condition")
    )
    if grade_val:
        if "grade c" in grade_val or "c+" in grade_val or "c " in grade_val:
            return "C"
        if "grade b" in grade_val or "b+" in grade_val or "b " in grade_val:
            return "B"
        if "grade a" in grade_val or "a+" in grade_val or "a " in grade_val:
            return "A"

        # eBay-style condition text
        if "for parts" in grade_val or "not working" in grade_val:
            return "D"
        if "used" in grade_val or "seller refurbished" in grade_val or "refurbished" in grade_val:
            return "B"
        if "new" in grade_val or "brand new" in grade_val or "sealed" in grade_val:
            return "A"

    # Some sellers encode USED in the model code itself
    model_val = _as_text(attrs.get("Model"))
    if "used" in model_val:
        return "B"

    # ----------------
    # Grade A: clearly new-ish
    # ----------------
    new_terms = [
        "brand new", "new & sealed", "new and sealed",
        "factory sealed", "sealed", "unopened",
        "unused", "like new", "open box",
    ]
    if any(term in blob for term in new_terms):
        return "A"

    # ----------------
    # Grade C: rough but working
    # ----------------
    rough_terms = [
        "heavy wear", "heavily used", "scratches",
        "cosmetic damage", "cosmetically poor",
    ]
    if any(term in blob for term in rough_terms):
        return "C"

    # Default: B (generic used / unknown)
    return "B"
