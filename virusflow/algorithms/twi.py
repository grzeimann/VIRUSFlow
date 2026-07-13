from __future__ import annotations

from typing import Iterable, Optional, Dict, Any, List

import numpy as np
from astropy.stats import biweight_location

from .ccd import base_reduction
from ..core.artifacts import save_master_twi

# Algorithm version string for this module
ALGO_VERSION = "twi-1.0"

# Input item type accepted by step_twi (same structure as bias/dark/flat)
TwiInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


def step_twi(
    raw_twi_inputs: Optional[Iterable[TwiInput]] = None,
    output_path: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    raw_inputs: Optional[Iterable[TwiInput]] = None,
) -> Dict[str, Any]:
    """Construct a master twilight (twi) frame from input twilight frames.

    Contract:
    - Inputs: iterable of dicts with keys:
        - 'path': outer container path (FITS file or .tar archive path)
        - 'tar_member': optional member path inside the tar when applicable
    - Output: write a master-twi FITS file to output_path, and return metadata.

    Algorithm (mirrors flat but without pixel mask computation):
    - For each input frame, run CCD base_reduction (overscan subtraction, trim, orientation, gain, error)
    - Stack the reduced images robustly via biweight location to produce the master twilight
    - Persist the master using save_master_twi (no mask)
    """
    params = params or {}
    # Prefer explicit raw_inputs if provided
    effective = raw_inputs if raw_inputs is not None else raw_twi_inputs
    inputs: List[TwiInput] = list(effective or [])
    if len(inputs) == 0:
        raise ValueError("step_twi requires at least one raw twilight input in raw_twi_inputs")

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
        raise RuntimeError("No readable twilight frames provided to step_twi")

    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"Input twilight frames have differing shapes: {sorted(shapes)}")

    stack = np.stack(frames, axis=0)
    master = biweight_location(stack, axis=0, ignore_nan=True)

    if output_path is not None:
        save_master_twi(output_path, master, n_inputs=len(frames), algo_version=ALGO_VERSION)

    return {
        "n_inputs": len(frames),
        "shape": list(master.shape),
        "output_path": output_path,
        "algo": "algorithms.twi.step_twi",
        "version": ALGO_VERSION,
    }
