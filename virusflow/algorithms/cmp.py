from __future__ import annotations

"""Comparison (arc-lamp) master-frame construction.

This module exposes a single public routine:
- step_cmp: reduce a set of raw comparison-lamp exposures using CCD base_reduction
  and robustly combine them (biweight) into a master comparison frame.

Architecture: algorithms are storage-neutral. step_cmp performs computation only
and returns an AlgoResult; it does not read previously published artifacts,
construct masks from disk, or perform persistence/registration/QA.

Exports: step_cmp
"""

from typing import Iterable, Optional, Dict, Any, List

import logging
import numpy as np
from astropy.stats import biweight_location

from .ccd import base_reduction
# persistence and storage-coupled mask logic removed per architecture

__all__ = ["step_cmp"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "cmp-1.0"

# Input item type accepted by step_cmp (same structure as bias/dark/flat)
CmpInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


from ..core.algo_result import AlgoResult

def step_cmp(
    raw_cmp_inputs: Optional[Iterable[CmpInput]] = None,
    output_path: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    raw_inputs: Optional[Iterable[CmpInput]] = None,
) -> AlgoResult:
    """Construct a master comparison (cmp) frame from input comparison frames.

    Storage-neutral: compute and return AlgoResult only. No persistence here.
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

    # Per architecture, do not read other artifacts or construct masks here.
    # Any masking/repair decisions belong to tasks/persistence policies.

    if errors:
        logger.warning("step_cmp encountered %d reduction errors; proceeding with %d good frames", len(errors), len(frames))

    return AlgoResult(
        kind="cmp",
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
