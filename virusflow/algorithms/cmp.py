from __future__ import annotations

"""Comparison (arc-lamp) master-frame construction.

This module exposes a single public routine:
- step_cmp: reduce a set of raw comparison-lamp exposures using CCD reduce_raw_amplifier_frame
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

from .inputs import array_frames
# persistence and storage-coupled mask logic removed per architecture

__all__ = ["step_cmp"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "cmp-1.0"

# Input item type accepted by step_cmp (same structure as bias/dark/flat)
CmpInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


from ..core.algo_result import AlgoResult

def step_cmp(
    raw_inputs: Optional[Iterable[CmpInput]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> AlgoResult:
    """Construct a master comparison (cmp) frame from input comparison frames.

    Storage-neutral: compute and return AlgoResult only. No persistence here.

    Parameters
    ----------
    raw_inputs : Optional[Iterable[CmpInput]]
        Iterable of raw comparison-lamp frame references (path, tar_member).
    params : Optional[Dict[str, Any]]
        Algorithm tuning parameters (reserved; none currently used).
    """
    params = params or {}
    frames = array_frames(raw_inputs or [])

    stack = np.stack(frames, axis=0)
    master = biweight_location(stack, axis=0, ignore_nan=True)

    # Per architecture, do not read other artifacts or construct masks here.
    # Any masking/repair decisions belong to tasks/persistence policies.

    return AlgoResult(
        kind="cmp",
        version=ALGO_VERSION,
        meta={
            "image_shape": list(master.shape),
        },
        scalars={
            "n_inputs": len(frames),
        },
        arrays={
            "master_comparison_lamp": master,
        },
    )
