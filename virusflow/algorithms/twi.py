from __future__ import annotations

"""Twilight (continuum) master-frame construction.

This module exposes a single public routine:
- step_twi: reduce raw twilight exposures with CCD base_reduction and combine
  them robustly (biweight) into a master twilight frame for tracing/extraction
  support. The result is persisted via artifacts.io_fits.write_array_fits with an explicit sidecar.

Exports: step_twi
"""

from typing import Iterable, Optional, Dict, Any, List

import numpy as np
from astropy.stats import biweight_location

from .ccd import base_reduction
# persistence removed per architecture

# Algorithm version string for this module
ALGO_VERSION = "twi-1.0"

# Input item type accepted by step_twi (same structure as bias/dark/flat)
TwiInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


from ..core.algo_result import AlgoResult

def step_twi(
    raw_twi_inputs: Optional[Iterable[TwiInput]] = None,
    output_path: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    raw_inputs: Optional[Iterable[TwiInput]] = None,
) -> AlgoResult:
    """Construct a master twilight (twi) frame from input twilight frames.

    Storage-neutral: compute and return AlgoResult only. No persistence here.
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

    return AlgoResult(
        kind="twi",
        version=ALGO_VERSION,
        meta={
            "shape": list(master.shape),
        },
        scalars={
            "n_inputs": len(frames),
        },
        arrays={
            "master": master,
        },
    )
