from __future__ import annotations

from typing import Optional

from .bikes import bike_model_key
from .consoles import console_or_game_model_key
from .cameras import camera_drone_model_key
from .watches import watch_model_key
from .apple import apple_model_key
from .tools import tools_model_key

def normalise_model(title: str) -> Optional[str]:
    """
    High-level classifier used everywhere.

    Order matters:
    - Try to classify as a bike first (bike_* keys)
    - Then consoles / games / accessories / retro / PC / generic
    """
    t = title.lower()

    # 1) Bikes
    key = bike_model_key(t)
    if key:
        return key

    # 2) Apple
    key = apple_model_key(t)
    if key:
        return key

    # 3) Watches
    key = watch_model_key(t)
    if key:
        return key

    # 4) Consoles
    key = console_or_game_model_key(t)
    if key:
        return key

    # 5) Cameras
    key = camera_drone_model_key(t)
    if key:
        return key

    # 6) Tools
    key = tools_model_key(t)
    if key:
        return key

    # Unknown
    return None
