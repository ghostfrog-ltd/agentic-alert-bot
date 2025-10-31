from __future__ import annotations

from infrastructure.scraper.adapters.ebay_base import EbayAdapterBase


class Adapter(EbayAdapterBase):
    """
    Action camera / moto vlog niche.

    Targets:
    - GoPro Hero bodies & bundles
    - DJI Osmo Action / Action 2 / 3 / 4 / Action-style mini cams
    - Insta360 (X2/X3/X4, Ace Pro, Go 3, One RS, etc.)
    - Good accessory kits (mounts, batteries, cages, mic adapters)

    This adapter will register/resolve sources.domain = "ebay-actioncams".
    """

    # This is what will show in logs and sources.domain
    DOMAIN = "ebay-actioncams"

    # Broad-ish eBay category IDs where action cams + kits usually live.
    # These WILL vary by site (UK vs US) and eBay sometimes shuffles them,
    # so treat these as placeholders until you confirm against the live API.
    #
    # You want:
    # - Action cameras
    # - Action camera accessories / mounts / bundles
    # - "Camcorders" / "Digital Camcorders" because sellers dump GoPros there
    #
    # TODO: replace these with the real category IDs you are actually querying.
    CATEGORY_IDS = [
        11724,  # Camcorders
        31388,  # Digital Cameras
        15200,  # Camera & Drone
    ]

    # We want both fixed-price (instant flip potential) AND auctions
    # (late snipes under market).
    SALE_TYPE = ["bin", "auction"]

    # -------------------------
    # Keyword groups / heuristics
    # -------------------------
    #
    # These sets help us classify/score listings, or sanity check that the item
    # is in our niche and not, like, a kid's pink "4K sports cam 1080p lol".
    #

    # GoPro ecosystem
    GOPRO_KEYWORDS = [
        "gopro",
        "hero 5", "hero5",
        "hero 6", "hero6",
        "hero 7", "hero7",
        "hero 8", "hero8",
        "hero 9", "hero9",
        "hero 10", "hero10",
        "hero 11", "hero11",
        "hero 12", "hero12",
        "gopro max", "max 360",
        "media mod", "mic adapter", "skeleton housing",
    ]

    # DJI action cams / pocket cams that bikers vlog with
    DJI_KEYWORDS = [
        "dji action",
        "osmo action", "osmo action 2", "osmo action2",
        "action 2", "action2",
        "action 3", "action3",
        "action 4", "action4",
        "osmo pocket", "osmo pocket 2",
        "mic adapter dji",
    ]

    # Insta360 ecosystem
    INSTA_KEYWORDS = [
        "insta360", "insta 360",
        "x2", "x3", "x4",
        "ace pro", "acepro",
        "go 2", "go2",
        "go 3", "go3",
        "one rs", "1-inch", "1 inch",
        "360 cam", "360 camera",
    ]

    # Accessory bundle tells (useful for cheap flips: full kit = value)
    ACCESSORY_KEYWORDS = [
        "helmet mount",
        "chin mount",
        "chest mount", "chesty",
        "handlebar mount",
        "vlog kit",
        "bundle", "kit bundle",
        "battery pack", "dual charger", "fast charger",
        "cage", "housing", "underwater housing",
        "nd filter", "lens protector",
    ]

    # Catch-all "is this in our world?" list.
    # Good for fast niche check or downstream tagging.
    ACTIONCAM_KEYWORDS = (
            GOPRO_KEYWORDS
            + DJI_KEYWORDS
            + INSTA_KEYWORDS
            + ACCESSORY_KEYWORDS
    )
