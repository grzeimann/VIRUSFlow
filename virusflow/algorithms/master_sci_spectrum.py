"""Compatibility wrapper for Master Science spectrum extraction."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult
from .master_spectrum import extract_master_spectrum


def extract_master_sci_spectrum(
    master_sci: np.ndarray,
    fiber_trace_map: np.ndarray,
    *,
    aperture_width: float = 5.0,
) -> AlgoResult:
    """Apply :func:`extract_master_spectrum` to a Master Science image."""

    return extract_master_spectrum(
        master_sci,
        fiber_trace_map,
        result_kind="extracted_master_sci_spectrum",
        aperture_width=aperture_width,
    )
