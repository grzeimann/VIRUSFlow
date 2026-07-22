from __future__ import annotations

"""Flat-field master-frame construction.

This module focuses on building a master flat used for tracing and pixelmasking:
- step_flt: reduce raw flat exposures with CCD reduce_raw_amplifier_frame and combine them
  robustly (biweight) into a master flat; compute a flat-specific pixel mask
  using a median-filter deviation rule and simple column heuristics; returns a storage-neutral AlgoResult (no file I/O here).

Exports: step_flt
"""

from typing import Iterable, Optional, Dict, Any, List

import logging
import numpy as np
from astropy.stats import biweight_location
from scipy.signal import medfilt

from .inputs import array_frames
# persistence removed per architecture

__all__ = ["step_flt"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "flat-1.0"

# Input item type accepted by step_flt (same structure as bias/dark)
FlatInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


def detect_flat_response_outliers(image: np.ndarray) -> np.ndarray:
    """Port of reference.get_pixelmask_flt for flat-field images.

    Logic (per reference):
    - For each row, apply a median filter (kernel=17) to get the local continuum.
    - Flag pixels deviating more than 10% from the local median-filtered row.
    - Ignore deviations where the median is low (<200 ADU), likely inter-fiber regions.
    - Ignore detector edges: first/last 8 columns are never masked.
    - Flag entire columns if more than 300 pixels are already flagged in that column.
    Returns uint8 mask with 1 for masked pixels.
    """
    img = np.asarray(image, dtype=float)
    ny, nx = img.shape
    mask = np.zeros((ny, nx), dtype=bool)
    for i in np.arange(ny):
        row_medf = medfilt(img[i], 17)
        # Avoid divide-by-zero; treat near-zero as large denominator
        denom = np.where(row_medf == 0, np.inf, row_medf)
        dev = np.abs((img[i] - row_medf) / denom)
        bad = dev > 0.1
        # Ignore low signal regions
        bad[row_medf < 200] = False
        mask[i] = bad
    # Ignore edges
    mask[:, :8] = False
    mask[:, -8:] = False
    # Flag full columns with many bad pixels
    col_bad = np.sum(mask, axis=0) > 300
    if np.any(col_bad):
        mask[:, col_bad] = True
    return mask.astype(np.uint8)


from ..core.algo_result import AlgoResult

def step_flt(
    raw_inputs: Optional[Iterable[FlatInput]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> AlgoResult:
    """Construct a master flat frame from input flat (continuum) frames.

    Storage-neutral: compute and return AlgoResult only. No persistence here.

    Parameters
    ----------
    raw_inputs : Optional[Iterable[FlatInput]]
        Iterable of raw flat frame references (path, tar_member).
    params : Optional[Dict[str, Any]]
        Algorithm tuning parameters (reserved; none currently used).
    """
    params = params or {}
    frames = array_frames(raw_inputs or [])

    stack = np.stack(frames, axis=0)
    master = biweight_location(stack, axis=0, ignore_nan=True)

    flat_mask = detect_flat_response_outliers(master)
    n_bad = int(flat_mask.sum())
    frac_bad = float(n_bad) / float(flat_mask.size)

    return AlgoResult(
        kind="flat",
        version=ALGO_VERSION,
        meta={
            "image_shape": list(master.shape),
        },
        scalars={
            "n_inputs": len(frames),
            "bad_fraction": float(frac_bad),
        },
        arrays={
            "master_flat": master,
            "flat_response_mask": flat_mask,
        },
    )
