from __future__ import annotations

"""Bias (zero) master-frame construction.

This module provides a single public routine:
- step_bias: reduce a set of raw bias frames with CCD reduce_raw_amplifier_frame and
  robustly combine them into a master bias. It also computes a robust
  per-pixel scatter (MAD) and reports a scalar readnoise estimate. The
  resulting master is returned in a storage-neutral AlgoResult; no file I/O occurs in the algorithm.

Exports: step_bias
"""

from typing import Iterable, Optional, Dict, Any, List

import logging
import numpy as np
from .inputs import array_frames
from .robust import chunked_biweight_location

__all__ = ["step_bias"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "bias-1.1"

# Input item type accepted by step_bias
BiasInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


from ..core.algo_result import AlgoResult

def step_bias(
    raw_inputs: Optional[Iterable[BiasInput]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> AlgoResult:
    """
    Construct a master bias frame from input zero (bias) frames using numpy/astropy.

    Contract:
    - Inputs: iterable of dicts with keys:
        - 'path': outer container path (FITS file or .tar archive path)
        - 'tar_member': optional member path inside the tar when applicable
    - Output: returns a storage-neutral AlgoResult; no file I/O or persistence here.

    Algorithm (aligned with reference step_zro):
    - For each input frame, run CCD reduce_raw_amplifier_frame (overscan subtraction, trim, orientation, gain, error)
    - Stack the reduced images (converted to float)
    - Master bias = median across the stack (axis=0)
    - Per-pixel robust scatter = 1.4826 * median(|frame - master|) over frames
    - Read noise scalar = median of the scatter map
    """
    params = params or {}
    frames = array_frames(raw_inputs or [])

    stack = np.stack(frames, axis=0)
    # Use biweight location for stack combination to avoid digitization bias
    master = chunked_biweight_location(stack, axis=0)
    # Robust per-pixel scatter via MAD (kept as median-of-abs-dev for now)
    mad = np.median(np.abs(stack - master[None, :, :]), axis=0) * 1.4826
    # Scalar read-noise estimate
    read_noise = float(np.nanmedian(mad))

    # Return pure computational result; no persistence here per architecture.
    return AlgoResult(
        kind="bias",
        version=ALGO_VERSION,
        meta={
            "n_inputs": len(frames),
            "image_shape": list(master.shape),
        },
        scalars={
            "read_noise": read_noise,
            "n_inputs": len(frames),
        },
        arrays={
            "master": master,
            "per_pixel_bias_scatter": mad,
        },
    )
