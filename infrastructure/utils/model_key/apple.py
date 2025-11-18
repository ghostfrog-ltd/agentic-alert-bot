from __future__ import annotations

from typing import Mapping, Any, Optional


def _clean(v: Any) -> str:
    if not v:
        return ""
    return str(v).strip().lower().replace(" ", "-")


def _num(v: Any) -> str:
    """Simple digit extractor (used for things like mm sizes)."""
    if not v:
        return ""
    return "".join(ch for ch in str(v) if ch.isdigit())


def _capacity_token(v: Any) -> str:
    """
    Turn '256 GB', '512GB', '1 TB', '2tb', etc. into:
      - '256gb'
      - '512gb'
      - '1tb'
      - '2tb'
    If we can't make sense of it, return "".
    """
    if not v:
        return ""
    s = str(v).strip().lower().replace(" ", "")
    if not s:
        return ""

    # Grab the first number we see
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        elif num:
            break

    if not num:
        return ""

    if "tb" in s:
        return f"{num}tb"
    return f"{num}gb"


def apple_model_key(attrs: Mapping[str, Any], title: str = "") -> Optional[str]:
    """
    ebay-apple boring version:

    - Attrs only, ignore title.
    - Brand must contain 'apple'.
    - MacBooks: apple-macbook[-air|-pro]-<ram>-<storage>
    - iPhones: apple-<iphone-model>-<storage>
    - iPads: apple-<ipad-model>-<generation?>-<storage>
    - Watches: apple-watch-...
    - AirPods: apple-airpods / apple-airpodspro
    - AirTag / Apple TV / HomePod: simple keys.
    - Non-Apple -> None (let other helpers try).
    - If nothing matches -> 'unknown'.
    """

    if not attrs:
        return "unknown"

    # ------------------------------------------------------------------ #
    # Brand gate
    # ------------------------------------------------------------------ #
    brand_raw = (
        attrs.get("Brand")
        or attrs.get("Marca")
        or attrs.get("brand")
    )
    brand = _clean(brand_raw)
    if "apple" not in brand:
        return None

    # ------------------------------------------------------------------ #
    # Core fields
    # ------------------------------------------------------------------ #
    series = _clean(attrs.get("Series") or "")
    product_line = _clean(attrs.get("Product Line") or "")
    model = _clean(attrs.get("Model") or "")
    product_family = _clean(attrs.get("Product Family") or "")

    # "family" = best general category string
    family = series or product_line or product_family

    storage_token = _capacity_token(
        attrs.get("Storage Capacity")
        or attrs.get("storage")
        or attrs.get("SSD Capacity")
        or attrs.get("Hard Drive Capacity")
        or attrs.get("Capacity")
    )

    ram_token = _capacity_token(
        attrs.get("RAM")
        or attrs.get("RAM Size")
        or attrs.get("Memory")
        or attrs.get("ram")
    )

    # chipset is still available if we ever want it, but we no longer
    # put it in keys for MacBooks to avoid over-fragmentation
    chipset = _clean(
        attrs.get("Chipset Model")
        or attrs.get("Processor")
        or attrs.get("CPU")
        or attrs.get("Processor Model")
    )

    def _strip_apple_prefix(s: str) -> str:
        return s[len("apple-"):] if s.startswith("apple-") else s

    def _any_contains(needle: str) -> bool:
        return (
            needle in model
            or needle in family
            or needle in product_line
            or needle in series
            or needle in product_family
        )

    # ------------------------------------------------------------------ #
    # MACBOOKS  (no CPU in key, just family + RAM + storage)
    # ------------------------------------------------------------------ #
    if "macbook" in family or "macbook" in model or "macbook" in product_family:
        txt = "-".join(x for x in (family, model, product_family) if x)

        # Family
        if "macbook-air" in txt:
            line = "macbook-air"
        elif "macbook-pro" in txt:
            line = "macbook-pro"
        else:
            line = "macbook"

        # Chipset family (m1/m2/m3 OR intel)
        chip = chipset.lower()
        chip_token = ""

        if "m1" in chip:
            chip_token = "m1"
        elif "m2" in chip:
            chip_token = "m2"
        elif "m3" in chip:
            chip_token = "m3"
        elif "m4" in chip:
            chip_token = "m4"
        elif "intel" in chip or "core" in chip or "i5" in chip or "i7" in chip or "i9" in chip:
            chip_token = "intel"

        parts = ["apple", line]

        if chip_token:
            parts.append(chip_token)

        if ram_token:
            parts.append(ram_token)

        if storage_token:
            parts.append(storage_token)

        return "-".join(parts)

    # ------------------------------------------------------------------ #
    # APPLE WATCH
    # ------------------------------------------------------------------ #
    if _any_contains("watch"):
        raw_series = _clean(attrs.get("Series") or "")
        case_size = _clean(
            attrs.get("Case Size")
            or attrs.get("Case Size (mm)")
            or attrs.get("case-size")
            or attrs.get("Size")
        )
        size_num = _num(case_size)

        connectivity_source = " ".join(
            [
                str(attrs.get("Connectivity") or ""),
                str(attrs.get("Features") or ""),
                str(attrs.get("Wireless Technology") or ""),
            ]
        ).lower()

        connectivity = ""
        if "cellular" in connectivity_source:
            if "gps" in connectivity_source:
                connectivity = "gps-cellular"
            else:
                connectivity = "cellular"
        elif "gps" in connectivity_source:
            connectivity = "gps"

        parts: list[str] = ["apple", "watch"]

        s = raw_series
        series_token = ""
        if s:
            if s.isdigit():
                series_token = f"series-{s}"
            elif "series" in s:
                series_token = s
            elif s in ("se", "se-2nd-gen", "se-2nd-generation"):
                series_token = "se"
            elif "ultra" in s:
                series_token = "ultra"

        if series_token:
            parts.append(series_token)

        if size_num:
            parts.append(f"{size_num}mm")

        if connectivity:
            parts.append(connectivity)

        return "-".join(parts)

    # ------------------------------------------------------------------ #
    # IPHONE
    # ------------------------------------------------------------------ #
    if _any_contains("iphone"):
        if "iphone" in model:
            line = model
        elif "iphone" in family:
            line = family
        else:
            line = product_line or model or family

        line = _strip_apple_prefix(line)

        parts: list[str] = ["apple", line]

        if storage_token:
            parts.append(storage_token)

        return "-".join(parts)

    # ------------------------------------------------------------------ #
    # IPAD
    # ------------------------------------------------------------------ #
    if _any_contains("ipad"):
        if "ipad" in model:
            line = model
        elif "ipad" in family:
            line = family
        else:
            line = product_line or model or family

        line = _strip_apple_prefix(line)
        gen = _clean(attrs.get("Generation") or attrs.get("generation"))

        parts: list[str] = ["apple", line]

        if gen:
            parts.append(gen)

        if storage_token:
            parts.append(storage_token)

        return "-".join(parts)

    # ------------------------------------------------------------------ #
    # AIRPODS
    # ------------------------------------------------------------------ #
    if _any_contains("airpods"):
        joined = "-".join([model, family, product_line])
        if "pro" in joined:
            return "apple-airpodspro"
        return "apple-airpods"

    # ------------------------------------------------------------------ #
    # AIRTAG
    # ------------------------------------------------------------------ #
    if _any_contains("airtag"):
        return "apple-airtag"

    # ------------------------------------------------------------------ #
    # APPLE TV
    # ------------------------------------------------------------------ #
    if _any_contains("apple-tv") or _any_contains("appletv"):
        gen = _clean(attrs.get("Generation") or attrs.get("generation"))
        parts = ["apple", "appletv"]
        if gen:
            parts.append(gen)
        return "-".join(parts)

    # ------------------------------------------------------------------ #
    # HOMEPOD
    # ------------------------------------------------------------------ #
    if _any_contains("homepod"):
        txt = "-".join([model, family, product_line])
        if "mini" in txt:
            return "apple-homepod-mini"
        return "apple-homepod"


    # ------------------------------------------------------------------ #
    # FALLBACK
    # ------------------------------------------------------------------ #
    return "unknown"
