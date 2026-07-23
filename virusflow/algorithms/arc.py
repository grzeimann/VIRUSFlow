"""Compose separately averaged Hg and Cd masters for wavelength calibration."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult

ALGO_VERSION = "hg-plus-cd-1.0"


def compose_master_arc(master_hg: np.ndarray, master_cd: np.ndarray) -> AlgoResult:
    hg = np.asarray(master_hg)
    cd = np.asarray(master_cd)
    if hg.shape != cd.shape:
        raise ValueError(f"Hg and Cd master shapes differ: {hg.shape} != {cd.shape}")
    combined = np.asarray(hg, dtype=np.float32) + np.asarray(cd, dtype=np.float32)
    return AlgoResult(
        kind="cmp", version=ALGO_VERSION,
        arrays={"master_comparison_lamp": combined},
        scalars={"n_inputs": 2},
        meta={"image_shape": list(combined.shape), "composition": "master_hg + master_cd"},
    )
