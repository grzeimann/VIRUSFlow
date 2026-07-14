from __future__ import annotations

"""Flat-field master-frame construction.

This module focuses on building a master flat used for tracing and pixelmasking:
- step_flt: reduce raw flat exposures with CCD base_reduction and combine them
  robustly (biweight) into a master flat; compute a flat-specific pixel mask
  using a median-filter deviation rule and simple column heuristics; persist
  via core.artifacts.save_master_flat.

Exports: step_flt
"""

from typing import Iterable, Optional, Dict, Any, List

import logging
import numpy as np
from astropy.stats import biweight_location
from scipy.signal import medfilt

from .ccd import base_reduction
from ..artifacts.io_fits import write_array_fits

__all__ = ["step_flt"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "flat-1.0"

# Input item type accepted by step_flt (same structure as bias/dark)
FlatInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


def _compute_flat_pixelmask(image: np.ndarray) -> np.ndarray:
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


def step_flt(
    raw_flt_inputs: Optional[Iterable[FlatInput]] = None,
    output_path: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    raw_inputs: Optional[Iterable[FlatInput]] = None,
) -> Dict[str, Any]:
    """Construct a master flat frame from input flat (continuum) frames.

    Contract:
    - Inputs: iterable of dicts with keys:
        - 'path': outer container path (FITS file or .tar archive path)
        - 'tar_member': optional member path inside the tar when applicable
    - Output: write a master-flat FITS file to output_path, and return metadata.

    Algorithm (similar to dark/bias):
    - For each input frame, run CCD base_reduction (overscan subtraction, trim, orientation, gain, error)
    - Stack the reduced images robustly via biweight location to produce the master flat
    - Compute flat pixel mask using the ported get_pixelmask_flt logic
    """
    params = params or {}
    # Prefer explicit raw_inputs if provided
    effective = raw_inputs if raw_inputs is not None else raw_flt_inputs
    inputs: List[FlatInput] = list(effective or [])
    if len(inputs) == 0:
        raise ValueError("step_flt requires at least one raw flat input in raw_flt_inputs")

    def _reduce_one(idx_item):
        i, it = idx_item
        p = it.get("path")
        tm = it.get("tar_member")
        if not p:
            return None, i, "no-path"
        try:
            img, _err = base_reduction(p, tm, return_header=False)
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
        raise RuntimeError("No readable flat frames provided to step_flt")

    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"Input flat frames have differing shapes: {sorted(shapes)}")

    stack = np.stack(frames, axis=0)
    master = biweight_location(stack, axis=0, ignore_nan=True)

    flat_mask = _compute_flat_pixelmask(master)
    n_bad = int(flat_mask.sum())
    frac_bad = float(n_bad) / float(flat_mask.size)

    if output_path is not None:
        write_array_fits(
            output_path,
            data=master,
            n_inputs=len(frames),
            algo_version=ALGO_VERSION,
            extra_primary_cards={"BADFRAC": float(frac_bad)},
            mask=flat_mask,
            mask_name="FLATMASK",
            sidecar={
                "kind": "master_flat",
                "role": "calibration",
                "payload_type": "array",
                "storage_format": "fits",
                "bad_fraction": float(frac_bad),
            },
        )

    return {
        "n_inputs": len(frames),
        "shape": list(master.shape),
        "n_bad": n_bad,
        "bad_fraction": frac_bad,
        "output_path": output_path,
        "algo": "algorithms.flat.step_flt",
        "version": ALGO_VERSION,
    }
