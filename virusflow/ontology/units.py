from __future__ import annotations

from enum import Enum


class Unit(str, Enum):
    DIMENSIONLESS = "1"
    ADU = "adu"
    ELECTRON = "electron"
    ELECTRON_PER_SECOND = "electron / s"
    ELECTRON_VARIANCE = "electron2"
    PIXEL = "pixel"
    ANGSTROM = "Angstrom"
    SECOND = "s"


UNKNOWN_UNIT = "unknown"

