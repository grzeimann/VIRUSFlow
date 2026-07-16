from __future__ import annotations

"""Dark-current master-frame construction.

This module provides:
- step_dark: reduce raw dark frames with CCD base_reduction and robustly combine
  them (biweight) into a master dark; derive a dark pixel mask using
  sigma-clipped residual logic and simple full-column heuristics; returns a storage-neutral AlgoResult (no file I/O here).

Exports: step_dark
"""

from typing import Iterable, Optional, Dict, Any, List

import logging
import numpy as np
from astropy.stats import biweight_location, sigma_clipped_stats

from . import ccd as _ccd

__all__ = ["step_dark"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "dark-1.0"

# Input item type accepted by step_dark (same structure as bias)
DarkInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


def _compute_dark_pixelmask(dark: np.ndarray) -> np.ndarray:
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
    raw_dark_inputs: Optional[Iterable[DarkInput]] = None,
    params: Optional[Dict[str, Any]] = None,
    raw_inputs: Optional[Iterable[DarkInput]] = None,
) -> AlgoResult:
    """Construct a master dark frame from input dark frames.

    Storage-neutral implementation: compute and return AlgoResult only. No persistence.
    """
    params = params or {}
    # Accept either alias name; prefer explicit raw_inputs if provided
    effective = raw_inputs if raw_inputs is not None else raw_dark_inputs
    inputs: List[DarkInput] = list(effective or [])
    n_inputs = len(inputs)
    if n_inputs == 0:
        raise ValueError("step_dark requires at least one raw dark input in raw_dark_inputs")

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
        detail = ("; ".join(errors[:5])) if errors else "no per-input errors captured"
        raise RuntimeError(f"No readable dark frames provided to step_dark (n_inputs={n_inputs}). Sample errors: {detail}")

    # Align shapes (ensure all equal); if not, raise
    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"Input dark frames have differing shapes: {sorted(shapes)}")

    stack = np.stack(frames, axis=0)
    master = biweight_location(stack, axis=0, ignore_nan=True)

    dark_mask = _compute_dark_pixelmask(master)
    n_bad = int(dark_mask.sum())
    frac_bad = float(n_bad) / float(dark_mask.size)

    return AlgoResult(
        kind="dark",
        version=ALGO_VERSION,
        meta={
            "shape": list(master.shape),
        },
        scalars={
            "n_inputs": len(frames),
            "bad_fraction": float(frac_bad),
        },
        arrays={
            "master": master,
            "mask": dark_mask,
        },
    )
