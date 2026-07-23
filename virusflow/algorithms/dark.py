from __future__ import annotations

"""Dark-current master-frame construction.

This module provides:
- step_dark: reduce raw dark frames with CCD reduce_raw_amplifier_frame and robustly combine
  them (biweight) into a master dark; derive a dark pixel mask using
  sigma-clipped residual logic and simple full-column heuristics; returns a storage-neutral AlgoResult (no file I/O here).

Exports: step_dark
"""

from typing import Iterable, Optional, Dict, Any, List

import logging
import numpy as np
from astropy.stats import sigma_clipped_stats

from .inputs import array_frames
from .robust import chunked_biweight_location

__all__ = ["step_dark"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "dark-1.1"

# Input item type accepted by step_dark (same structure as bias)
DarkInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


def detect_dark_current_outliers(dark: np.ndarray) -> np.ndarray:
    """Compute a dark pixel mask following reference/fiber_utils.get_pixelmask.

    Steps:
    - Subtract column-wise median, then row-wise median
    - Compute sigma-clipped stats of the residuals
    - Flag pixels with |residual| > 5*sigma
    - Additionally, if residual > 100 at (y,x) and the next row (y+1,x) is also
      flagged, then flag the entire column x.
    Returns an integer mask (0/1) with the same shape as input.
    """
    img = np.asarray(dark, dtype=float)
    # Subtract column median then row median
    y1 = img - np.median(img, axis=0)[np.newaxis, :]
    y1 = y1 - np.median(y1, axis=1)[:, np.newaxis]
    _m, _med, s = sigma_clipped_stats(y1)
    # Base mask: 5-sigma outliers in absolute value
    mask = np.abs(y1) > 5.0 * float(s)
    # If bright outliers > 100 ADU and the next row is also masked, flag column
    yind, xind = np.where(y1 > 100)
    for yi, xi in zip(yind, xind):
        if yi + 2 < mask.shape[0]:
            if mask[yi + 1, xi]:
                mask[:, xi] = True
    return mask.astype(np.uint8)


from ..core.algo_result import AlgoResult

def step_dark(
    raw_inputs: Optional[Iterable[DarkInput]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> AlgoResult:
    """Construct a master dark frame from input dark frames.

    Storage-neutral implementation: compute and return AlgoResult only. No persistence.

    Parameters
    ----------
    raw_inputs : Optional[Iterable[DarkInput]]
        Iterable of raw dark frame references (path, tar_member).
    params : Optional[Dict[str, Any]]
        Algorithm tuning parameters (reserved; none currently used).
    """
    params = params or {}
    frames = array_frames(raw_inputs or [])

    stack = np.stack(frames, axis=0)
    master = chunked_biweight_location(stack, axis=0)

    dark_mask = detect_dark_current_outliers(master)
    n_bad = int(dark_mask.sum())
    frac_bad = float(n_bad) / float(dark_mask.size)

    return AlgoResult(
        kind="dark",
        version=ALGO_VERSION,
        meta={
            "image_shape": list(master.shape),
        },
        scalars={
            "n_inputs": len(frames),
            "bad_fraction": float(frac_bad),
        },
        arrays={
            "master_dark": master,
            "dark_pixel_mask": dark_mask,
        },
    )
