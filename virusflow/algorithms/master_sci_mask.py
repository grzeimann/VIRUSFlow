"""Construct the fiber-by-spectral-sample mask from Master Science spectra."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult
from .utils.masks import (
    build_model_spectra,
    coarse_self_normalization,
    make_spectral_mask,
)


ALGO_VERSION = "master-sci-spectral-mask-1.0"


def build_master_sci_spectral_mask(
    spectrum: np.ndarray,
    wavelength_map: np.ndarray,
    *,
    fiber_normalization: np.ndarray | None = None,
    coarse_bins: int = 32,
    model_bins: int = 2000,
    minimum_wavelength_finite_fraction: float = 0.8,
    amplifier_fibers: int = 112,
    very_bad_threshold: float = 10.0,
) -> AlgoResult:
    """Normalize, model in wavelength space, and mask spectral residuals.

    A supplied twilight-derived normalization is preferred.  When it is absent,
    a slowly varying relative response is estimated from coarse wavelength bins
    in the Master Science spectra themselves.  The normalization is retained as
    evidence alongside the model and mask.
    """

    observed = np.asarray(spectrum, dtype=float)
    wavelength = np.asarray(wavelength_map, dtype=float)
    if observed.ndim != 2 or wavelength.shape != observed.shape:
        raise ValueError("spectrum and wavelength_map must be matching 2D arrays")
    if not 0.0 < float(minimum_wavelength_finite_fraction) <= 1.0:
        raise ValueError("minimum_wavelength_finite_fraction must be in (0, 1]")

    if fiber_normalization is None:
        normalization = coarse_self_normalization(
            observed, wavelength, nbins=int(coarse_bins)
        )
        normalization_mode = "coarse_self_normalization"
    else:
        normalization = np.asarray(fiber_normalization, dtype=float)
        if normalization.shape != observed.shape:
            raise ValueError("fiber_normalization must match spectrum shape")
        normalization_mode = "twilight_fiber_normalization"

    usable_normalization = np.isfinite(normalization) & (normalization > 0.0)
    normalized = np.full(observed.shape, np.nan, dtype=float)
    np.divide(observed, normalization, out=normalized, where=usable_normalization)
    finite_fraction = np.isfinite(wavelength).sum(axis=1) / wavelength.shape[1]
    good_solutions = finite_fraction > float(minimum_wavelength_finite_fraction)
    interpolator, _, _, _ = build_model_spectra(
        normalized,
        wavelength,
        good_solutions,
        nbins=int(model_bins),
        normalize_per_fiber=False,
    )
    model = np.asarray(interpolator(wavelength), dtype=float)
    mask = make_spectral_mask(
        normalized,
        model,
        amp_rows=int(amplifier_fibers),
        very_bad_thresh=float(very_bad_threshold),
    ).astype(np.uint8)
    # A sample without a usable coordinate or normalization cannot be safely
    # interpreted, even if the archival residual heuristic did not flag it.
    mask[~np.isfinite(wavelength) | ~usable_normalization] = 1

    return AlgoResult(
        kind="fiber_wavelength_spectral_mask",
        version=ALGO_VERSION,
        arrays={
            "mask": mask,
            "spectral_model": np.asarray(model, dtype=np.float32),
            "normalization": np.asarray(normalization, dtype=np.float32),
            "good_wavelength_solution": good_solutions.astype(np.uint8),
        },
        scalars={
            "masked_fraction": float(mask.mean()),
            "good_wavelength_solution_count": int(good_solutions.sum()),
            "fiber_count": int(observed.shape[0]),
        },
        meta={
            "mask_shape": list(mask.shape),
            "normalization_mode": normalization_mode,
            "coarse_normalization_bins": int(coarse_bins),
            "spectral_model_bins": int(model_bins),
            "minimum_wavelength_finite_fraction": float(
                minimum_wavelength_finite_fraction
            ),
            "amplifier_fibers": int(amplifier_fibers),
            "very_bad_threshold": float(very_bad_threshold),
            "mask_semantics": "1=unusable spectral sample, 0=usable spectral sample",
        },
    )
