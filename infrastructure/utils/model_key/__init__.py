from __future__ import annotations

from typing import Optional

from .bikes import bike_model_key
from .consoles_and_games import console_or_game_model_key


def normalise_model(title: str) -> Optional[str]:
    """
    High-level classifier used everywhere.

    Order matters:
    - Try to classify as a bike first (bike_* keys)
    - Then consoles / games / accessories / retro / PC / generic
    """
    t = title.lower()

    # 1) Bikes (we consider this "done" and don't want to break it)
    bike_key = bike_model_key(t)
    if bike_key:
        return bike_key

    # 2) Consoles / games / accessories / retro / PC
    cg_key = console_or_game_model_key(t)
    if cg_key:
        return cg_key

    # 3) Unknown – caller can map None -> "unknown" if needed
    return None
