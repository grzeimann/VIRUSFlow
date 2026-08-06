from __future__ import annotations

from enum import Enum


class CoordinateConvention(str, Enum):
    RAW_AMPLIFIER = "raw_amplifier"
    ORIENTED_AMPLIFIER = "oriented_amplifier_blue_to_red"
    PHYSICAL_CCD_ZERO_INDEXED = "physical_ccd_zero_indexed"
    FIBER_BY_DISPERSION_PIXEL = "fiber_by_dispersion_pixel"
    DETECTOR_ROW_BY_COLUMN = "detector_row_by_column"
    WAVELENGTH_ANGSTROM = "wavelength_angstrom"
    ICRS = "icrs"
    NONE = "none"


# Approved indexed transform for reflected upper amplifiers.  Step 8 will use
# this constant; no 2064-y alternative is retained in the implementation.
UPPER_AMPLIFIER_REFLECTION_INDEX = 2063

