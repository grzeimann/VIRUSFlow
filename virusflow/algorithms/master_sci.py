"""Canonical detector-coordinate Master Science aggregation."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult
from .inputs import array_frames
from .robust import chunked_biweight_location

ALGO_VERSION = "master-sci-2.0"


def build_master_sci(raw_inputs=None, params=None) -> AlgoResult:
    """Combine eligible, base-reduced science frames without doing storage I/O."""

    frames = [np.asarray(value, dtype=np.float32) for value in array_frames(raw_inputs or [])]
    if not frames:
        raise ValueError("master_sci requires at least one eligible frame")
    stack = np.stack(frames, axis=0)
    master = np.asarray(chunked_biweight_location(stack, axis=0), dtype=np.float32)
    illumination = float(np.nanmedian(np.abs(master)))
    return AlgoResult(
        kind="master_sci", version=ALGO_VERSION,
        arrays={"master_sci": master},
        scalars={
            "n_inputs": len(frames),
            "robust_illumination": illumination,
            "finite_fraction": float(np.isfinite(master).mean()),
        },
        meta={
            "image_shape": list(master.shape),
            "combination_estimator": "chunked fixed-center biweight_location",
        },
    )
