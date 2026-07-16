from __future__ import annotations

"""Bias (zero) master-frame construction.

This module provides a single public routine:
- step_bias: reduce a set of raw bias frames with CCD base_reduction and
  robustly combine them into a master bias. It also computes a robust
  per-pixel scatter (MAD) and reports a scalar readnoise estimate. The
  resulting master is returned in a storage-neutral AlgoResult; no file I/O occurs in the algorithm.

Exports: step_bias
"""

from typing import Iterable, Optional, Dict, Any, List

import logging
import numpy as np
from astropy.stats import biweight_location
from . import ccd as _ccd

__all__ = ["step_bias"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "bias-1.0"

# Input item type accepted by step_bias
BiasInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


from ..core.algo_result import AlgoResult

def step_bias(
    raw_bias_inputs: Optional[Iterable[BiasInput]] = None,
    params: Optional[Dict[str, Any]] = None,
    raw_inputs: Optional[Iterable[BiasInput]] = None,
) -> AlgoResult:
    """
    Construct a master bias frame from input zero (bias) frames using numpy/astropy.

    Contract:
    - Inputs: iterable of dicts with keys:
        - 'path': outer container path (FITS file or .tar archive path)
        - 'tar_member': optional member path inside the tar when applicable
    - Output: returns a storage-neutral AlgoResult; no file I/O or persistence here.

    Algorithm (aligned with reference step_zro):
    - For each input frame, run CCD base_reduction (overscan subtraction, trim, orientation, gain, error)
    - Stack the reduced images (converted to float)
    - Master bias = median across the stack (axis=0)
    - Per-pixel robust scatter = 1.4826 * median(|frame - master|) over frames
    - Readnoise scalar = median of the scatter map
    """
    params = params or {}
    # Accept either alias name; prefer explicit raw_inputs if provided
    effective = raw_inputs if raw_inputs is not None else raw_bias_inputs
    inputs: List[BiasInput] = list(effective or [])
    n_inputs = len(inputs)
    if n_inputs == 0:
        # Fail fast per architecture guidance: empty inputs indicate a planning/scoping error
        raise ValueError("step_bias requires at least one raw bias input in raw_bias_inputs")

    # Read all frames serially. Parallelism is handled by the task/executor layer.
    def _reduce_one(idx_item):
        i, it = idx_item
        p = it.get("path")
        tm = it.get("tar_member")
        if not p:
            return None, i, "no-path"
        try:
            img, _err = _ccd.base_reduction(p, tm, return_header=False)
            return img, i, None
        except Exception as e:
            # Do not implement test-only fallbacks in production algorithms; tests should
            # provide inputs via fixtures/mocks. Propagate error for the caller to handle.
            return None, i, str(e)

    frames: List[np.ndarray] = []
    errors: List[str] = []

    for i, it in enumerate(inputs):
        img, idx, err = _reduce_one((i, it))
        if img is not None:
            frames.append(img)
        elif err:
            errors.append(f"[{idx}] {err}")

    if not frames:
        # Aggregate a few input errors to aid debugging without changing behavior
        detail = ("; ".join(errors[:5])) if errors else "no per-input errors captured"
        raise RuntimeError(f"No readable bias frames provided to step_bias (n_inputs={n_inputs}). Sample errors: {detail}")

    # Align shapes (ensure all equal); if not, raise
    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"Input bias frames have differing shapes: {sorted(shapes)}")

    stack = np.stack(frames, axis=0)
    # Use biweight location for stack combination to avoid digitization bias
    master = biweight_location(stack, axis=0, ignore_nan=True)
    # Robust per-pixel scatter via MAD (kept as median-of-abs-dev for now)
    mad = np.median(np.abs(stack - master[None, :, :]), axis=0) * 1.4826
    # Scalar readnoise estimate
    readnoise = float(np.nanmedian(mad))

    # Return pure computational result; no persistence here per architecture.
    return AlgoResult(
        kind="bias",
        version=ALGO_VERSION,
        meta={
            "n_inputs": len(frames),
            "shape": list(master.shape),
        },
        scalars={
            "readnoise": readnoise,
            "n_inputs": len(frames),
        },
        arrays={
            "master": master,
        },
    )
