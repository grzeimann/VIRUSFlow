from __future__ import annotations

"""Science (typical exposure) diagnostic master-frame construction.

This module exposes a single public routine:
- build_master_science: reduce raw science exposures with CCD reduce_raw_amplifier_frame
  and robustly combine them (biweight) into a master science frame used for
  detector/fiber diagnostics at typical science exposure levels.

Architecture: algorithms are storage-neutral. build_master_science performs
computation only and returns an AlgoResult; it does not perform persistence,
publication, or QA. Do not compute generic QA statistics (percentiles, histograms)
inside the algorithm per the revised implementation plan.

Exports: build_master_science
"""

from typing import Iterable, Optional, Dict, Any, List

import logging
import numpy as np
from astropy.stats import biweight_location

from .inputs import array_frames
from ..core.algo_result import AlgoResult

__all__ = ["build_master_science"]
logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "sci-1.0"

# Input item type accepted by build_master_science (same structure as bias/dark/flat)
SciInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


def build_master_science(
    raw_inputs: Optional[Iterable[SciInput]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> AlgoResult:
    """Construct a master science diagnostic frame from input science frames.

    Storage-neutral: compute and return AlgoResult only. No persistence here.

    Parameters
    ----------
    raw_inputs : Optional[Iterable[SciInput]]
        Iterable of raw science frame references (path, tar_member).
    params : Optional[Dict[str, Any]]
        Algorithm tuning parameters (reserved; none currently used).
    """
    params = params or {}
    frames = array_frames(raw_inputs or [])

    stack = np.stack(frames, axis=0)
    master = biweight_location(stack, axis=0, ignore_nan=True)

    return AlgoResult(
        kind="sci",
        version=ALGO_VERSION,
        meta={
            "image_shape": list(master.shape),
        },
        scalars={
            "n_inputs": len(frames),
        },
        arrays={
            "master_science": master,
        },
    )
