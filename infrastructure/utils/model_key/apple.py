from __future__ import annotations

from typing import Mapping, Any, Optional
import re

from infrastructure.utils.condition import _derive_condition_grade


def _clean(v: Any) -> str:
    if not v:
        return ""
    return str(v).strip().lower().replace(" ", "-")


def _num(v: Any) -> str:
    """Simple digit extractor (used for things like mm sizes)."""
    if not v:
        return ""
    return "".join(ch for ch in str(v) if ch.isdigit())


def _extract_ipad_gen_token(attrs: Mapping[str, Any], title: str) -> str:
    """
    Detect iPad generations like '2nd Gen', '3rd Generation' etc.

    Returns tokens 'gen1', 'gen2', ... or '' if nothing obvious is found.
    We deliberately *don't* map years to gens – only explicit 'Xth gen'
    phrases to avoid overfitting.
    """
    txt = " ".join(
        s
        for s in (
            str(attrs.get("Model") or ""),
            str(attrs.get("Product Line") or ""),
            str(attrs.get("Series") or ""),
            title or "",
        )
        if s
    ).lower()

    if "ipad" not in txt:
        return ""

    # Normalise a bit
    txt = txt.replace("-", " ")

    patterns = [
        (r"\b(1st|first)\s+gen(eration)?\b", "gen1"),
        (r"\b(2nd|second)\s+gen(eration)?\b", "gen2"),
        (r"\b(3rd|third)\s+gen(eration)?\b", "gen3"),
        (r"\b(4th|fourth)\s+gen(eration)?\b", "gen4"),
        (r"\b(5th|fifth)\s+gen(eration)?\b", "gen5"),
        (r"\b(6th|sixth)\s+gen(eration)?\b", "gen6"),
    ]
    for pattern, token in patterns:
        if re.search(pattern, txt):
            return token

    return ""


def _extract_iphone_se_gen_suffix(attrs: Mapping[str, Any], title: str) -> str:
    """
    Very small helper: distinguish iPhone SE generations, since they share a name.

    Returns suffixes like '-2016', '-2020', '-2022' or '' if unsure.
    """
    txt = " ".join(
        s
        for s in (
            str(attrs.get("Model") or ""),
            str(attrs.get("Product Line") or ""),
            str(attrs.get("Series") or ""),
            title or "",
        )
        if s
    ).lower()

    if "iphone se" not in txt:
        return ""

    # Year hints first (most sellers include the year)
    if "2022" in txt or re.search(r"\b(3rd|third)\s+gen", txt):
        return "-2022"
    if "2020" in txt or re.search(r"\b(2nd|second)\s+gen", txt):
        return "-2020"
    if "2016" in txt or re.search(r"\b(1st|first)\s+gen", txt):
        return "-2016"

    return ""


def apple_model_key(attrs: Mapping[str, Any], title: str = "") -> Optional[str]:
    """
    Apple model_key, console-style:

    - Brand must contain "apple" -> otherwise return None (other helpers can handle).
    - Macs are chip-family specific (M1/M2/M3/M4 vs Intel).
    - iPads now include generation where explicitly stated (e.g. '2nd Gen').
    - iPhone SE gets generation/years where obvious; other iPhones stay as before.

    Examples:

      Mac:
        apple-macbook-pro-m1pro_b
        apple-macbook-air-m2_a
        apple-imac-intel_c
        apple-mac-mini-m1_b
        apple-mac-studio-m1max_a

      iPad:
        apple-ipad-pro-gen2_b
        apple-ipad-pro-m1_a      (if you later choose to wire chip in)
        apple-ipad-air-gen4_b
        apple-ipad-mini_b

      iPhone:
        apple-iphone-13-pro_a
        apple-iphone-se-2020_b

      Watch / AirPods / etc unchanged.

    - Final key is always: <base_family>_<grade> where grade ∈ {A,B,C,D}
    - If clearly Apple but we can't classify → "unknown"
    - If not clearly Apple → None
    """

    if not attrs:
        return "unknown"

    # --------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------
    def _with_grade(base_key: str) -> str:
        grade = _derive_condition_grade(attrs, title)
        return f"{base_key}_{grade}"

    def _brand_is_apple() -> bool:
        brand_raw = (
            attrs.get("Brand")
            or attrs.get("Marca")
            or attrs.get("brand")
        )
        brand = _clean(brand_raw)
        return "apple" in brand

    if not _brand_is_apple():
        # Let non-Apple items fall through to other model_key helpers
        return None

    series = _clean(attrs.get("Series") or "")
    product_line = _clean(attrs.get("Product Line") or "")
    model = _clean(attrs.get("Model") or "")
    product_family = _clean(attrs.get("Product Family") or "")

    family_blob = "-".join(
        x for x in (series, product_line, product_family, model) if x
    )

    chipset = _clean(
        attrs.get("Chipset Model")
        or attrs.get("Processor")
        or attrs.get("CPU")
        or attrs.get("Processor Model")
    )

    # Include title and chipset in a combined chip-source blob
    chip_source = " ".join(
        s for s in (
            chipset,
            attrs.get("Title") or "",
            attrs.get("Item Title") or "",
            title or "",
        )
        if s
    ).lower()

    def _chip_family() -> str:
        """
        For Macs, collapse CPU into:
          - 'm1', 'm1pro', 'm1max', 'm1ultra',
            'm2', 'm2pro', 'm2max', 'm2ultra',
            'm3', 'm3pro', 'm3max',
            'm4', 'm4pro', ...
          - 'intel'
          - 'applesilicon' (generic fallback if we only know "Apple Silicon")
          - '' (unknown)

        We *could* use this for iPads later (e.g. ipad-pro-m1), but for now
        it is wired only into Mac families to avoid exploding key space.
        """
        c = chip_source

        # Specific M-series chip with optional tier: "M1", "M2 Pro", "M3 Max"
        m = re.search(r"\bm(1|2|3|4|5)\s*(pro|max|ultra)?\b", c)
        if m:
            gen = m.group(1)
            tier = (m.group(2) or "").strip().replace(" ", "")
            if tier:
                return f"m{gen}{tier}"   # e.g. m1pro, m2max
            return f"m{gen}"            # e.g. m1, m2, m3, m4

        # Generic "Apple Silicon" mention with no specific chip name
        if ("apple" in c and "silicon" in c) or "applesilicon" in c:
            return "applesilicon"

        # Intel family detection
        if any(tok in c for tok in ("intel", "core i3", "core i5", "core i7", "core i9", "xeon", "core-")):
            return "intel"

        return ""

    chip_family = _chip_family()

    # --------------------------------------------------------------
    # 1) MAC FAMILY (MacBook, iMac, Mac mini, Mac Pro, Mac Studio)
    # --------------------------------------------------------------
    mac_blob = family_blob

    if any(tok in mac_blob for tok in ("macbook", "mac-mini", "macmini", "imac", "mac-pro", "macpro", "mac-studio", "macstudio")):
        # MacBook
        if "macbook" in mac_blob:
            if "air" in mac_blob:
                line = "apple-macbook-air"
            elif "pro" in mac_blob:
                line = "apple-macbook-pro"
            else:
                line = "apple-macbook"
        # iMac
        elif "imac" in mac_blob:
            line = "apple-imac"
        # Mac mini
        elif "mac-mini" in mac_blob or "macmini" in mac_blob:
            line = "apple-mac-mini"
        # Mac Pro
        elif "mac-pro" in mac_blob or "macpro" in mac_blob:
            line = "apple-mac-pro"
        # Mac Studio
        elif "mac-studio" in mac_blob or "macstudio" in mac_blob:
            line = "apple-mac-studio"
        else:
            line = "apple-mac"

        parts = [line]
        if chip_family:
            parts.append(chip_family)

        base_key = "-".join(parts)
        return _with_grade(base_key)

    # --------------------------------------------------------------
    # 2) IPHONE FAMILY (no storage, no RAM; SE gets gen/year)
    # --------------------------------------------------------------
    if "iphone" in family_blob:
        # Try to canonicalise: iphone-13-pro-max, iphone-12-mini, iphone-se, etc.
        def _iphone_line() -> str:
            # family_blob is already hyphen-normalised
            tokens = family_blob.split("-")
            if "iphone" not in tokens and "iphone" not in model:
                return "apple-iphone"

            # Build from first 'iphone' onwards, keeping known tokens
            allowed_suffix_tokens = {
                "se", "plus", "pro", "max", "mini",
                # digits: handled separately
            }
            out = ["iphone"]

            seen_iphone = False
            for t in tokens:
                if t == "iphone":
                    seen_iphone = True
                    continue
                if not seen_iphone:
                    continue

                # stop if we hit unrelated junk
                if not t:
                    break

                # digit = model number: 7, 8, 11, 12, 13, 14, 15, 16, 17...
                if t.isdigit():
                    out.append(t)
                    continue

                if t in allowed_suffix_tokens:
                    out.append(t)
                    continue

                # generation text like '3rd', '4th-generation' etc → skip
                if any(x in t for x in ("gen", "generation", "3rd", "4th", "5th", "6th")):
                    continue

                # anything else likely not part of the marketing name
                break

            return "apple-" + "-".join(out)

        base_key = _iphone_line()

        # Special case: iPhone SE – append gen/year suffix if obvious
        if base_key == "apple-iphone-se":
            se_suffix = _extract_iphone_se_gen_suffix(attrs, title)
            if se_suffix:
                base_key += se_suffix

        return _with_grade(base_key)

    # --------------------------------------------------------------
    # 3) IPAD FAMILY (now with optional generation)
    # --------------------------------------------------------------
    if "ipad" in family_blob:
        # Distinguish major lines only
        line = "apple-ipad"
        if "ipad-air" in family_blob or ("air" in family_blob and "ipad" in family_blob):
            line = "apple-ipad-air"
        elif "ipad-mini" in family_blob or ("mini" in family_blob and "ipad" in family_blob):
            line = "apple-ipad-mini"
        elif "ipad-pro" in family_blob or ("pro" in family_blob and "ipad" in family_blob):
            line = "apple-ipad-pro"

        gen_token = _extract_ipad_gen_token(attrs, title)

        parts = [line]
        if gen_token:
            parts.append(gen_token)

        base_key = "-".join(parts)
        return _with_grade(base_key)

    # --------------------------------------------------------------
    # 4) APPLE WATCH
    # --------------------------------------------------------------
    if "watch" in family_blob:
        raw_series = _clean(attrs.get("Series") or "")
        series_token = ""
        s = raw_series

        # Normalise series / SE / Ultra
        if s:
            if s.isdigit():
                series_token = f"series-{s}"
            elif "series" in s:
                # assume something like series-7
                series_token = s
            elif "ultra" in s:
                series_token = "ultra"
            elif "se" in s:
                series_token = "se"

        # If attrs don't give us a series, try to read from blob
        if not series_token:
            if "ultra" in family_blob:
                series_token = "ultra"
            elif "se" in family_blob:
                series_token = "se"
            else:
                # Last resort: try to spot a simple digit after 'series'
                # but if not found, just "series-unknown"
                if "series-" in family_blob:
                    # e.g. series-7, series-8:
                    for part in family_blob.split("-"):
                        if part.isdigit():
                            series_token = f"series-{part}"
                            break

        parts = ["apple-watch"]
        if series_token:
            parts.append(series_token)

        base_key = "-".join(parts)
        return _with_grade(base_key)

    # --------------------------------------------------------------
    # 5) AIRPODS
    # --------------------------------------------------------------
    if "airpods" in family_blob:
        fb = family_blob
        if "max" in fb:
            base_key = "apple-airpods-max"
        elif "pro" in fb:
            base_key = "apple-airpods-pro"
        else:
            base_key = "apple-airpods"
        return _with_grade(base_key)

    # --------------------------------------------------------------
    # 6) AIRTAG
    # --------------------------------------------------------------
    if "airtag" in family_blob:
        base_key = "apple-airtag"
        return _with_grade(base_key)

    # --------------------------------------------------------------
    # 7) APPLE TV
    # --------------------------------------------------------------
    if "apple-tv" in family_blob or "appletv" in family_blob:
        fb = family_blob
        if "4k" in fb:
            base_key = "apple-appletv-4k"
        else:
            base_key = "apple-appletv"
        return _with_grade(base_key)

    # --------------------------------------------------------------
    # 8) HOMEPOD
    # --------------------------------------------------------------
    if "homepod" in family_blob:
        if "mini" in family_blob:
            base_key = "apple-homepod-mini"
        else:
            base_key = "apple-homepod"
        return _with_grade(base_key)

    # --------------------------------------------------------------
    # FALLBACK FOR APPLE
    # --------------------------------------------------------------
    return "unknown"
