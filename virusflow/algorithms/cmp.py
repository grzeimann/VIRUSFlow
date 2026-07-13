from __future__ import annotations

from typing import Iterable, Optional, Dict, Any, List

import numpy as np
from astropy.stats import biweight_location

import logging
from .ccd import base_reduction, repair_masked_columns
from ..core.artifacts import save_master_cmp, build_union_pixelmask

logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "cmp-1.0"

# Input item type accepted by step_cmp (same structure as bias/dark/flat)
CmpInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


def step_cmp(
    raw_cmp_inputs: Optional[Iterable[CmpInput]] = None,
    output_path: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    raw_inputs: Optional[Iterable[CmpInput]] = None,
) -> Dict[str, Any]:
    """Construct a master comparison (cmp) frame from input comparison frames.

    Contract:
    - Inputs: iterable of dicts with keys:
        - 'path': outer container path (FITS file or .tar archive path)
        - 'tar_member': optional member path inside the tar when applicable
    - Output: write a master-cmp FITS file to output_path, and return metadata.

    Algorithm (mirrors twilight/flat stacking without mask computation):
    - For each input frame, run CCD base_reduction (overscan subtraction, trim, orientation, gain, error)
    - Stack the reduced images robustly via biweight location to produce the master comparison frame
    - Persist the master using save_master_cmp (no mask)
    """
    params = params or {}
    # Prefer explicit raw_inputs if provided
    effective = raw_inputs if raw_inputs is not None else raw_cmp_inputs
    inputs: List[CmpInput] = list(effective or [])
    if len(inputs) == 0:
        raise ValueError("step_cmp requires at least one raw comparison input in raw_cmp_inputs")

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
        raise RuntimeError("No readable comparison frames provided to step_cmp")

    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"Input comparison frames have differing shapes: {sorted(shapes)}")

    stack = np.stack(frames, axis=0)
    master = biweight_location(stack, axis=0, ignore_nan=True)

    # Optional: build a current pixelmask = union(flat_mask, dark_mask) if paths/artifacts are provided
    params = params or {}
    flat_path = params.get("master_flat_path") or (params.get("master_flat_artifact") or {}).get("path") if isinstance(params.get("master_flat_artifact"), dict) else params.get("master_flat_path")
    dark_path = params.get("master_dark_path") or (params.get("master_dark_artifact") or {}).get("path") if isinstance(params.get("master_dark_artifact"), dict) else params.get("master_dark_path")

    union_mask = None
    frac_mask = 0.0
    try:
        union_mask, frac_mask = build_union_pixelmask(flat_path=flat_path, dark_path=dark_path,
                                                      flat_artifact=params.get("master_flat_artifact") if isinstance(params.get("master_flat_artifact"), dict) else None,
                                                      dark_artifact=params.get("master_dark_artifact") if isinstance(params.get("master_dark_artifact"), dict) else None)
        if union_mask is not None and union_mask.shape != master.shape:
            logger.warning("Union mask shape %s does not match master CMP shape %s; ignoring mask",
                           getattr(union_mask, 'shape', None), master.shape)
            union_mask = None
    except Exception as e:
        logger.warning("Failed to build union pixel mask: %s", e)
        union_mask = None

    # Attempt to repair masked columns using the union mask
    repaired = master
    if union_mask is not None and np.any(union_mask):
        try:
            repaired = repair_masked_columns(master, np.asarray(union_mask, dtype=bool), sigma=1.0)
            logger.info("Applied repair_masked_columns to CMP using union mask (bad_fraction=%.4f)", float(frac_mask))
        except Exception as e:
            logger.warning("repair_masked_columns failed; proceeding with unmodified master: %s", e)

    if output_path is not None:
        # Persist the repaired image (if repairs were applied) as the master CMP
        save_master_cmp(output_path, repaired, n_inputs=len(frames), algo_version=ALGO_VERSION)

    if errors:
        logger.warning("step_cmp encountered %d reduction errors; proceeding with %d good frames", len(errors), len(frames))

    return {
        "n_inputs": len(frames),
        "shape": list(master.shape),
        "bad_fraction_mask": float(frac_mask),
        "output_path": output_path,
        "algo": "algorithms.cmp.step_cmp",
        "version": ALGO_VERSION,
    }
