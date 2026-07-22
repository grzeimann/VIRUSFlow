from __future__ import annotations

"""Twilight (continuum) master-frame construction.

This module exposes a single public routine:
- step_twi: reduce raw twilight exposures with CCD reduce_raw_amplifier_frame and combine
  them robustly (biweight) into a master twilight frame for tracing/extraction
  support. Returns a storage-neutral AlgoResult; no persistence or file I/O occurs here.

Exports: step_twi
"""

from typing import Iterable, Optional, Dict, Any, List

import numpy as np
from astropy.stats import biweight_location

from .inputs import array_frames
# persistence removed per architecture

# Algorithm version string for this module
ALGO_VERSION = "twi-1.0"

# Input item type accepted by step_twi (same structure as bias/dark/flat)
TwiInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


from ..core.algo_result import AlgoResult

def step_twi(
    raw_inputs: Optional[Iterable[TwiInput]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> AlgoResult:
    """Construct a master twilight (twi) frame from input twilight frames.

    Storage-neutral: compute and return AlgoResult only. No persistence here.

    Parameters
    ----------
    raw_inputs : Optional[Iterable[TwiInput]]
        Iterable of raw twilight frame references (path, tar_member).
    params : Optional[Dict[str, Any]]
        Algorithm tuning parameters (reserved; none currently used).
    """
    params = params or {}
    frames = array_frames(raw_inputs or [])

    stack = np.stack(frames, axis=0)
    master = biweight_location(stack, axis=0, ignore_nan=True)

    return AlgoResult(
        kind="twi",
        version=ALGO_VERSION,
        meta={
            "image_shape": list(master.shape),
        },
        scalars={
            "n_inputs": len(frames),
        },
        arrays={
            "master_twilight": master,
        },
    )
