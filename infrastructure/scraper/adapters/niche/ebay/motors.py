from __future__ import annotations

from typing import Any

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    """
    Cars + motorbikes + commercial vans (full vehicles only).
    """

    DOMAIN = "ebay-motors"

    CATEGORY_IDS = [
        # TODO: swap these for the exact Motors leaf categories you want.
        # Using generic Motors/Car categories as placeholders.
        9801,   # Cars (legacy ID – update if you've migrated)
        # Add your specific Motors subcategory IDs here, e.g.:
        179981,  # Motorcycles & Scooters
        '9801x',   # Vans / Commercial Vehicles
    ]

    SALE_TYPE = ["bin", "auction"]

    def _is_relevant(self, row: dict[str, Any]) -> bool:
        """
        Keep only *vehicles* (cars / motorbikes / vans), not parts or accessories.
        Salvage / "spares or repair" is allowed because it's still a whole vehicle.
        """
        title = (row.get("title") or "").lower().strip()

        # ------------------------------------
        # 1) Structured type hints (if present)
        # ------------------------------------
        veh_type = (
            (row.get("attr_vehicle_type") or "")
            or (row.get("attr_type") or "")
        ).lower()

        if veh_type in {
            "car",
            "vehicle",
            "van",
            "commercial vehicle",
            "motorcycle",
            "motorbike",
            "bike",
            "scooter",
            "pickup",
            "truck",
        }:
            return True

        # ------------------------------------
        # 2) Kill obvious NON-vehicle stuff
        # ------------------------------------
        # a) Model/toy / RC / promo items
        toy_words = (
            "diecast",
            "die-cast",
            "1:18",
            "1/18",
            "1:24",
            "1/24",
            "1:43",
            "rc car",
            "r/c car",
            "remote control car",
            "remote controlled car",
            "model car",
            "model bike",
            "slot car",
            "hot wheels",
            "matchbox",
            "corgi",
            "shell tanker",
            "lego",
            "technic",
            "poster",
            "print",
            "photograph",
            "press photo",
        )
        if any(w in title for w in toy_words):
            return False

        # b) Plates, manuals, brochures, merch
        non_vehicle_words = (
            "number plate",
            "reg plate",
            "registration plate",
            "private plate",
            "cherished plate",
            "show plate",
            "keyring",
            "key ring",
            "brochure",
            "sales brochure",
            "handbook",
            "owners manual",
            "owner's manual",
            "workshop manual",
            "haynes manual",
            "service book",
            "service history book",
            "press pack",
            "dealer pack",
            "jacket",
            "t-shirt",
            "t shirt",
            "cap",
            "hat",
            "mug",
            "sticker",
            "decal",
        )
        if any(w in title for w in non_vehicle_words):
            return False

        # c) Parts / accessories
        # (Note: we *don't* include "spares" or "repair" here on purpose.)
        parts_words = (
            "alloy wheel",
            "alloy wheels",
            "steel wheel",
            "wheels and tyres",
            "wheels & tyres",
            "wheel & tyre",
            "tyre",
            "tyres",
            "hubcap",
            "hub caps",
            "mirror",
            "wing mirror",
            "door mirror",
            "headlight",
            "headlamp",
            "tail light",
            "taillight",
            "rear light",
            "indicator",
            "rear bumper",
            "front bumper",
            "bumper",
            "bonnet",
            "bootlid",
            "tailgate",
            "door handle",
            "grille",
            "radiator",
            "intercooler",
            "turbo",
            "exhaust",
            "back box",
            "muffler",

            "brake disc",
            "brake discs",
            "brake pad",
            "brake pads",
            "caliper",
            "calipers",
            "shock absorber",
            "shock absorbers",
            "damper",
            "strut",
            "spring",
            "coilover",
            "suspension arm",
            "wishbone",

            "seat",
            "seats",
            "steering wheel",
            "airbag",
            "air bag",
            "dashboard",
            "dash board",
            "cluster",
            "instrument cluster",

            "engine",
            "gearbox",
            "gear box",
            "clutch",
            "flywheel",
            "ecu",
            "control unit",
            "starter motor",
            "alternator",

            "tow bar",
            "towbar",
            "roof rack",
            "roof bars",
            "roof box",

            "car mat",
            "car mats",
            "floor mats",
            "seat cover",
            "seat covers",
            "car cover",
            "boot liner",
            "dog guard",
            "sun visor",
            "wiper blade",
            "wiper blades",
            "head unit",
            "stereo",
            "radio",
            "cd player",
            "dash cam",
        )

        # If the title is clearly just a part / accessory, and there is
        # no strong vehicle phrase, drop it.
        vehicle_words_soft = (
            "car",
            "van",
            "motorbike",
            "motor bike",
            "motorcycle",
            "bike",
            "scooter",
            "pickup",
            "pick up",
            "pick-up",
            "truck",
            "4x4",
            "lcv",
            "mpv",
            "estate",
            "saloon",
            "coupe",
            "hatchback",
        )
        has_part_word = any(w in title for w in parts_words)
        has_vehicle_word = any(w in title for w in vehicle_words_soft)

        if has_part_word and not has_vehicle_word:
            return False

        # ------------------------------------
        # 3) Positive signals: makes + models
        # ------------------------------------
        car_makes = (
            "ford",
            "vauxhall",
            "opel",
            "bmw",
            "audi",
            "mercedes",
            "mercedes-benz",
            "mercedes benz",
            "vw",
            "volkswagen",
            "seat",
            "skoda",
            "toyota",
            "honda",
            "nissan",
            "mazda",
            "hyundai",
            "kia",
            "renault",
            "peugeot",
            "citroen",
            "fiat",
            "mini",
            "volvo",
            "jaguar",
            "land rover",
            "range rover",
            "mitsubishi",
            "subaru",
            "suzuki",
            "lexus",
            "alfa romeo",
            "alfa-romeo",
            "dacia",
            "tesla",
        )

        bike_makes = (
            "honda",
            "yamaha",
            "kawasaki",
            "suzuki",
            "ktm",
            "ducati",
            "triumph",
            "harley",
            "harley-davidson",
            "harley davidson",
            "bmw",
            "aprilia",
            "piaggio",
            "vespa",
            "royal enfield",
            "royal-enfield",
        )

        van_models = (
            "transit",
            "tourneo",
            "custom",
            "sprinter",
            "crafter",
            "ducato",
            "boxer",
            "jumper",
            "trafic",
            "vivaro",
            "movano",
            "combo",
            "doblo",
            "berlingo",
            "partner",
            "caddy",
            "kangoo",
            "expert",
            "dispatch",
            "proace",
            "nv200",
            "nv300",
            "nv400",
        )

        all_vehicle_tokens = car_makes + bike_makes + van_models
        has_make_or_model = any(tok in title for tok in all_vehicle_tokens)

        # If it looks like a vehicle (make/model or vehicle word) and we
        # didn't already kill it as toy/plate/manual/part-only → keep it.
        if has_make_or_model or has_vehicle_word:
            return True

        # ------------------------------------
        # 4) Fallback on model_key classification
        # ------------------------------------
        mk = (row.get("model_key") or "").lower()
        if (
            mk.startswith("car_")
            or mk.startswith("bike_")
            or mk.startswith("motorbike_")
            or mk.startswith("motorcycle_")
            or mk.startswith("van_")
            or mk.startswith("vehicle_")
        ):
            return True

        # ------------------------------------
        # 5) Everything else → not a vehicle we care about
        # ------------------------------------
        return False
