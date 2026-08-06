from __future__ import annotations

"""Weighted linear source extraction over physical fiber coupling.

Implements the small weighted linear inverse described in
``docs/architecture/spatial-psf-dar-coupling-resource-pycharm.md``: given the
physical, unnormalized fiber coupling ``C(exposure, fiber, wavelength)``, solve
for a point source (optionally plus local background) at each wavelength via
``beta_hat = (X^T W X)^-1 X^T W y`` with ``W = 1 / variance``.
"""

import numpy as np

from ..core.algo_result import AlgoResult


EXTRACTION_VERSION = "weighted-linear-design-matrix-1.0"
APERTURE_SUM_VERSION = "unweighted-fiber-sum-1.0"
OBSERVATION_COMBINE_VERSION = "inverse-variance-observation-combine-1.0"

INVALID_SOLVE_BIT = np.uint16(1)
INCONSISTENT_WAVELENGTH_BIT = np.uint16(2)


def select_source_fibers(fiber_x, fiber_y, source_x, source_y, *, max_distance_arcsec, fiber_mask=None) -> np.ndarray:
    """Boolean exclusion mask: fibers farther than ``max_distance_arcsec`` from the
    source, or already excluded by ``fiber_mask``, are excluded.

    Distance is a fiber-selection optimization, not part of the physical
    coupling definition; excluded fibers simply contribute zero rows to the
    design matrix and never renormalize the retained coupling.
    """

    fx = np.asarray(fiber_x, dtype=float)
    fy = np.asarray(fiber_y, dtype=float)
    if fx.shape != fy.shape or fx.ndim != 1:
        raise ValueError("fiber_x and fiber_y must be matched 1D arrays")
    distance = np.hypot(fx - float(source_x), fy - float(source_y))
    excluded = distance > float(max_distance_arcsec)
    if fiber_mask is not None:
        excluded = excluded | np.asarray(fiber_mask, dtype=bool)
    return excluded


def sum_aperture_flux(flux, variance, *, fiber_mask=None) -> AlgoResult:
    """Direct, unweighted per-wavelength fiber-sum extraction.

    Provided for comparison against the physical-coupling PSF extraction, not
    for production use: it makes no correction for finite aperture capture.
    """

    y = np.asarray(flux, dtype=float)
    var = np.asarray(variance, dtype=float)
    if y.shape != var.shape or y.ndim != 2:
        raise ValueError("flux and variance must be matched 2D (fiber, wavelength) arrays")
    n_fiber, n_wave = y.shape
    excluded = np.zeros((n_fiber,), dtype=bool) if fiber_mask is None else np.asarray(fiber_mask, dtype=bool)
    usable = (~excluded)[:, None] & np.isfinite(y) & np.isfinite(var) & (var > 0.0)
    amplitude = np.where(usable, y, 0.0).sum(axis=0)
    variance_sum = np.where(usable, var, 0.0).sum(axis=0)
    usable_fiber_count = usable.sum(axis=0)
    amplitude = np.where(usable_fiber_count > 0, amplitude, np.nan)
    variance_sum = np.where(usable_fiber_count > 0, variance_sum, np.nan)
    return AlgoResult(
        kind="aperture_sum_extraction",
        version=APERTURE_SUM_VERSION,
        arrays={
            "amplitude": amplitude,
            "variance": variance_sum,
            "usable_fiber_count": usable_fiber_count.astype(np.int64),
        },
        scalars={"fiber_count": int(n_fiber)},
    )


def solve_source_design_matrix(flux, variance, design_matrix, *, fiber_mask=None) -> AlgoResult:
    """Weighted linear least-squares solve for one wavelength sample.

    Returns ``success=False`` (rather than raising) when there are fewer
    usable fibers than design-matrix columns or ``X^T W X`` is singular.
    """

    y = np.asarray(flux, dtype=float)
    var = np.asarray(variance, dtype=float)
    design = np.asarray(design_matrix, dtype=float)
    if y.ndim != 1 or var.shape != y.shape:
        raise ValueError("flux and variance must be matched 1D arrays")
    if design.ndim != 2 or design.shape[0] != y.shape[0]:
        raise ValueError("design_matrix must have one row per fiber")

    n_columns = design.shape[1]
    excluded = np.zeros(y.shape, dtype=bool) if fiber_mask is None else np.asarray(fiber_mask, dtype=bool)
    usable = np.isfinite(y) & np.isfinite(var) & (var > 0.0) & ~excluded & np.all(np.isfinite(design), axis=1)
    usable_count = int(usable.sum())

    def failure() -> AlgoResult:
        return AlgoResult(
            kind="point_source_extraction",
            version=EXTRACTION_VERSION,
            arrays={
                "amplitude": np.full(n_columns, np.nan, dtype=np.float64),
                "covariance": np.full((n_columns, n_columns), np.nan, dtype=np.float64),
            },
            scalars={
                "chi2": float("nan"),
                "dof": 0,
                "usable_fiber_count": usable_count,
                "condition_number": float("nan"),
                "success": False,
            },
        )

    if usable_count < n_columns:
        return failure()

    x_used = design[usable]
    y_used = y[usable]
    weights = 1.0 / var[usable]

    xtw = x_used.T * weights
    xtwx = xtw @ x_used
    try:
        condition_number = float(np.linalg.cond(xtwx))
        xtwx_inverse = np.linalg.inv(xtwx)
    except np.linalg.LinAlgError:
        return failure()
    if not np.all(np.isfinite(xtwx_inverse)):
        return failure()

    amplitude = xtwx_inverse @ (xtw @ y_used)
    residual = y_used - x_used @ amplitude
    chi2 = float(np.sum(residual ** 2 * weights))
    dof = usable_count - n_columns

    return AlgoResult(
        kind="point_source_extraction",
        version=EXTRACTION_VERSION,
        arrays={
            "amplitude": amplitude.astype(np.float64),
            "covariance": xtwx_inverse.astype(np.float64),
        },
        scalars={
            "chi2": chi2,
            "dof": dof,
            "usable_fiber_count": usable_count,
            "condition_number": condition_number,
            "success": True,
        },
    )


def extract_source_spectrum(flux, variance, coupling, *, background=False, fiber_mask=None) -> AlgoResult:
    """Per-wavelength weighted linear source extraction over fiber coupling."""

    y = np.asarray(flux, dtype=float)
    var = np.asarray(variance, dtype=float)
    c = np.asarray(coupling, dtype=float)
    if not (y.shape == var.shape == c.shape) or y.ndim != 2:
        raise ValueError("flux, variance, and coupling must be matched 2D (fiber, wavelength) arrays")
    n_fiber, n_wave = y.shape
    excluded = np.zeros((n_fiber,), dtype=bool) if fiber_mask is None else np.asarray(fiber_mask, dtype=bool)
    if excluded.shape != (n_fiber,):
        raise ValueError("fiber_mask must have one entry per fiber")

    amplitude = np.zeros(n_wave, dtype=np.float64)
    amplitude_variance = np.zeros(n_wave, dtype=np.float64)
    background_amplitude = np.zeros(n_wave, dtype=np.float64) if background else None
    chi2 = np.zeros(n_wave, dtype=np.float64)
    dof = np.zeros(n_wave, dtype=np.int64)
    usable_fiber_count = np.zeros(n_wave, dtype=np.int64)
    mask = np.zeros(n_wave, dtype=np.uint16)
    captured_fraction = np.nansum(np.where(~excluded[:, None], c, 0.0), axis=0)

    for w in range(n_wave):
        column = c[:, w : w + 1]
        design_matrix = column if not background else np.hstack([column, np.ones_like(column)])
        result = solve_source_design_matrix(y[:, w], var[:, w], design_matrix, fiber_mask=excluded)
        success = bool(result.scalars["success"])
        chi2[w] = result.scalars["chi2"]
        dof[w] = result.scalars["dof"]
        usable_fiber_count[w] = result.scalars["usable_fiber_count"]
        if not success:
            mask[w] |= INVALID_SOLVE_BIT
            amplitude[w] = np.nan
            amplitude_variance[w] = np.nan
            if background:
                background_amplitude[w] = np.nan
            continue
        amplitude[w] = result.get_array("amplitude")[0]
        amplitude_variance[w] = result.get_array("covariance")[0, 0]
        if background:
            background_amplitude[w] = result.get_array("amplitude")[1]

    arrays = {
        "amplitude": amplitude,
        "variance": amplitude_variance,
        "mask": mask,
        "captured_fraction": captured_fraction.astype(np.float64),
        "usable_fiber_count": usable_fiber_count,
        "chi2": chi2,
        "dof": dof,
    }
    if background:
        arrays["background"] = background_amplitude

    return AlgoResult(
        kind="point_source_extraction",
        version=EXTRACTION_VERSION,
        arrays=arrays,
        scalars={
            "fit_background": bool(background),
            "design_matrix_identity": (
                "columns=[coupling, background]" if background else "columns=[coupling]"
            ),
        },
    )


def combine_observation_source_spectra(spectra, *, wavelength_tolerance_angstrom: float = 1.0) -> AlgoResult:
    """Inverse-variance combine per-exposure ``point_source_extraction`` spectra.

    Each element of ``spectra`` is a mapping with ``wavelength``, ``amplitude``,
    ``variance``, ``mask``, and ``captured_fraction`` 1D arrays of matching
    length. Exposures contribute independently at each wavelength using each
    exposure's own retained spatial-model state; this function only combines
    the already-solved per-exposure amplitudes, it does not re-solve coupling.

    Wavelength grids across exposures are expected to already agree (shared
    wavelength-map calibration); if they disagree beyond
    ``wavelength_tolerance_angstrom`` the combination proceeds positionally on
    the first exposure's grid but is marked ``status='degraded'`` rather than
    silently treated as fully consistent.
    """

    entries = list(spectra)
    if not entries:
        raise ValueError("combine_observation_source_spectra requires at least one exposure")

    reference_wavelength = np.asarray(entries[0]["wavelength"], dtype=float)
    n_wave = reference_wavelength.shape[0]
    amplitude = np.zeros((len(entries), n_wave), dtype=float)
    variance = np.zeros((len(entries), n_wave), dtype=float)
    valid = np.zeros((len(entries), n_wave), dtype=bool)
    captured_fraction = np.zeros((len(entries), n_wave), dtype=float)
    wavelength_consistent = True

    for i, entry in enumerate(entries):
        wavelength = np.asarray(entry["wavelength"], dtype=float)
        if wavelength.shape != reference_wavelength.shape:
            raise ValueError("combined exposures must share a compatible spectral sample count")
        if np.nanmax(np.abs(wavelength - reference_wavelength)) > float(wavelength_tolerance_angstrom):
            wavelength_consistent = False
        entry_amplitude = np.asarray(entry["amplitude"], dtype=float)
        entry_variance = np.asarray(entry["variance"], dtype=float)
        entry_mask = np.asarray(entry["mask"])
        amplitude[i] = entry_amplitude
        variance[i] = entry_variance
        captured_fraction[i] = np.asarray(entry["captured_fraction"], dtype=float)
        valid[i] = (
            (entry_mask == 0) & np.isfinite(entry_amplitude) & np.isfinite(entry_variance) & (entry_variance > 0.0)
        )

    weight = np.where(valid, 1.0 / np.where(variance > 0.0, variance, np.inf), 0.0)
    weight_sum = weight.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        combined_amplitude = np.where(weight_sum > 0.0, (weight * np.where(valid, amplitude, 0.0)).sum(axis=0) / weight_sum, np.nan)
        combined_variance = np.where(weight_sum > 0.0, 1.0 / weight_sum, np.nan)
    combined_mask = np.where(weight_sum > 0.0, np.uint16(0), INVALID_SOLVE_BIT).astype(np.uint16)
    if not wavelength_consistent:
        combined_mask = (combined_mask | INCONSISTENT_WAVELENGTH_BIT).astype(np.uint16)
    combined_captured_fraction = np.nanmean(captured_fraction, axis=0)

    return AlgoResult(
        kind="observation_source_spectrum",
        version=OBSERVATION_COMBINE_VERSION,
        arrays={
            "wavelength": reference_wavelength,
            "amplitude": combined_amplitude,
            "variance": combined_variance,
            "mask": combined_mask,
            "captured_fraction": combined_captured_fraction,
        },
        scalars={
            "exposure_count": len(entries),
            "status": "combined" if wavelength_consistent else "degraded",
            "wavelength_consistent": bool(wavelength_consistent),
        },
    )
