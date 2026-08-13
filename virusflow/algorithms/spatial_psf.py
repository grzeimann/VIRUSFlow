from __future__ import annotations

"""Wavelength-local VIRUS spatial (Moffat PSF) measurement and coupling.

Implements the concrete first step of the coupling term in the canonical
equation, ``C_{e,f}(theta, lambda)``, as described in
``docs/architecture/spatial-psf-dar-coupling-resource-pycharm.md``: a circular
Moffat PSF integrated over the actual circular fiber apertures, a bounded
robust local fit per wavelength interval seeded by the DAR model, and a smooth
polynomial residual chromatic model fitted only where VIRUS measurements
constrain it.
"""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from ..core.algo_result import AlgoResult


MOFFAT_COUPLING_VERSION = "circular-moffat-aperture-integrated-1.0"
PSF_FIT_VERSION = "bounded-robust-moffat-fit-1.0"
CHROMATIC_PSF_VERSION = "polynomial-residual-chromatic-psf-1.0"


def moffat_psf_value(dx, dy, fwhm, *, beta: float = 3.5):
    """Circular Moffat profile normalized to unit two-dimensional integral."""

    fwhm_array = np.asarray(fwhm, dtype=float)
    if np.any(fwhm_array <= 0.0) or not np.all(np.isfinite(fwhm_array)):
        raise ValueError("Moffat FWHM must be finite and positive")
    alpha = fwhm_array / (2.0 * np.sqrt(2.0 ** (1.0 / beta) - 1.0))
    norm = (beta - 1.0) / (np.pi * alpha ** 2)
    r2 = np.asarray(dx, dtype=float) ** 2 + np.asarray(dy, dtype=float) ** 2
    return norm * (1.0 + r2 / alpha ** 2) ** (-beta)


def integrate_moffat_over_apertures(
    fiber_x,
    fiber_y,
    fiber_radius,
    centroid_x,
    centroid_y,
    fwhm,
    *,
    beta: float = 3.5,
    grid_half_points: int = 12,
) -> np.ndarray:
    """Unnormalized physical coupling: the Moffat PSF integrated over each
    fiber's actual circular aperture using fixed-resolution disk quadrature.

    The result is not normalized to sum to one across fibers; it is the
    predicted fraction of unit total source flux captured by each fiber.
    """

    fx = np.asarray(fiber_x, dtype=float)
    fy = np.asarray(fiber_y, dtype=float)
    if fx.shape != fy.shape or fx.ndim != 1:
        raise ValueError("fiber_x and fiber_y must be matched 1D arrays")
    radius = float(fiber_radius)
    if radius <= 0.0:
        raise ValueError("fiber_radius must be positive")
    n = int(grid_half_points)
    if n < 1:
        raise ValueError("grid_half_points must be at least 1")

    offsets = np.linspace(-radius, radius, 2 * n + 1)
    grid_x, grid_y = np.meshgrid(offsets, offsets, indexing="ij")
    inside = (grid_x ** 2 + grid_y ** 2) <= radius ** 2
    grid_x = grid_x[inside]
    grid_y = grid_y[inside]
    cell_area = (offsets[1] - offsets[0]) ** 2

    dx = (fx[:, None] + grid_x[None, :]) - float(centroid_x)
    dy = (fy[:, None] + grid_y[None, :]) - float(centroid_y)
    values = moffat_psf_value(dx, dy, fwhm, beta=beta)
    coupling = values.sum(axis=1) * cell_area
    return np.clip(coupling.astype(float), 0.0, None)


def build_wavelength_intervals(wavelength_min: float, wavelength_max: float, count: int) -> np.ndarray:
    """Return ``count`` equal-width ``[low, high]`` wavelength interval edges."""

    low = float(wavelength_min)
    high = float(wavelength_max)
    n = int(count)
    if n < 1:
        raise ValueError("wavelength interval count must be at least 1")
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        raise ValueError("wavelength_min must be finite and less than wavelength_max")
    edges = np.linspace(low, high, n + 1)
    return np.column_stack((edges[:-1], edges[1:]))


def bin_flux_by_wavelength_interval(wavelength, flux, variance, mask, interval):
    """Inverse-variance-weighted per-fiber mean flux within one wavelength interval.

    ``wavelength``, ``flux``, ``variance``, and ``mask`` are matched ``(fiber,
    pixel)`` arrays; ``mask`` is nonzero where a sample is invalid. Fibers with
    no valid samples inside the interval return ``nan`` flux/uncertainty so
    downstream fitting excludes them via its own finite-value checks.
    """

    wave = np.asarray(wavelength, dtype=float)
    flux_array = np.asarray(flux, dtype=float)
    variance_array = np.asarray(variance, dtype=float)
    mask_array = np.asarray(mask)
    if not (wave.shape == flux_array.shape == variance_array.shape == mask_array.shape) or wave.ndim != 2:
        raise ValueError("wavelength, flux, variance, and mask must be matched 2D (fiber, pixel) arrays")
    low, high = float(interval[0]), float(interval[1])

    valid = (
        (wave >= low) & (wave < high)
        & (mask_array == 0)
        & np.isfinite(flux_array) & np.isfinite(variance_array) & (variance_array > 0.0)
    )
    weight = np.where(valid, 1.0 / np.where(variance_array > 0.0, variance_array, np.inf), 0.0)
    weight_sum = weight.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        binned_flux = np.where(weight_sum > 0.0, (weight * np.where(valid, flux_array, 0.0)).sum(axis=1) / weight_sum, np.nan)
        binned_uncertainty = np.where(weight_sum > 0.0, 1.0 / np.sqrt(weight_sum), np.nan)
    return binned_flux, binned_uncertainty


def fit_wavelength_interval_psf(
    fiber_x,
    fiber_y,
    fiber_radius,
    flux,
    uncertainty,
    *,
    seed_centroid_x: float,
    seed_centroid_y: float,
    wavelength_interval,
    reference_wavelength: float,
    fwhm_bounds=(1.0, 4.0),
    search_radius_arcsec: float = 3.0,
    beta: float = 3.5,
    fit_background: bool = False,
    robust_loss: str = "soft_l1",
    fiber_mask=None,
    grid_half_points: int = 12,
) -> AlgoResult:
    """Bounded, robust local Moffat fit for one wavelength interval.

    A failed or weakly constrained interval retains the seed prediction with
    an explicit ``status`` of ``'degraded'`` and ``valid=False``; it is never
    represented as a successful VIRUS measurement.
    """

    fx = np.asarray(fiber_x, dtype=float)
    fy = np.asarray(fiber_y, dtype=float)
    y = np.asarray(flux, dtype=float)
    sigma = np.asarray(uncertainty, dtype=float)
    if not (fx.shape == fy.shape == y.shape == sigma.shape) or fx.ndim != 1:
        raise ValueError("fiber_x, fiber_y, flux, and uncertainty must be matched 1D arrays")

    excluded = np.zeros(fx.shape, dtype=bool) if fiber_mask is None else np.asarray(fiber_mask, dtype=bool)
    usable = np.isfinite(y) & np.isfinite(sigma) & (sigma > 0.0) & ~excluded
    n_params = 5 if fit_background else 4
    seed_x = float(seed_centroid_x)
    seed_y = float(seed_centroid_y)
    seed_fwhm = float(np.mean(fwhm_bounds))

    def seed_result(status: str) -> AlgoResult:
        coverage = float(
            np.sum(
                integrate_moffat_over_apertures(
                    fx, fy, fiber_radius, seed_x, seed_y, seed_fwhm,
                    beta=beta, grid_half_points=grid_half_points,
                )
            )
        )
        return AlgoResult(
            kind="spatial_psf_measurement",
            version=PSF_FIT_VERSION,
            arrays={
                "centroid_x": np.float32(seed_x),
                "centroid_y": np.float32(seed_y),
                "fwhm": np.float32(seed_fwhm),
                "amplitude": np.float32(np.nan),
                "background": np.float32(0.0),
                "covariance": np.full((n_params, n_params), np.nan, dtype=np.float32),
                "fibers_used": usable,
            },
            scalars={
                "wavelength_interval_min": float(wavelength_interval[0]),
                "wavelength_interval_max": float(wavelength_interval[1]),
                "reference_wavelength": float(reference_wavelength),
                "beta": float(beta),
                "chi2": float("nan"),
                "dof": 0,
                "coverage": coverage,
                "valid": False,
                "status": status,
                "usable_fiber_count": int(usable.sum()),
                "fit_background": bool(fit_background),
                "robust_loss": robust_loss,
            },
        )

    if int(usable.sum()) < n_params + 1:
        return seed_result("degraded")

    used_x = fx[usable]
    used_y = fy[usable]
    used_flux = y[usable]
    used_sigma = sigma[usable]

    def model(params) -> np.ndarray:
        amplitude, cx, cy, fwhm = params[:4]
        coupling = integrate_moffat_over_apertures(
            used_x, used_y, fiber_radius, cx, cy, fwhm, beta=beta, grid_half_points=grid_half_points
        )
        prediction = amplitude * coupling
        if fit_background:
            prediction = prediction + params[4]
        return prediction

    def residual(params) -> np.ndarray:
        return (used_flux - model(params)) / used_sigma

    amplitude0 = max(float(np.nanmax(used_flux)), np.finfo(float).eps)
    x0 = [amplitude0, seed_x, seed_y, seed_fwhm]
    lower = [0.0, seed_x - search_radius_arcsec, seed_y - search_radius_arcsec, float(fwhm_bounds[0])]
    upper = [np.inf, seed_x + search_radius_arcsec, seed_y + search_radius_arcsec, float(fwhm_bounds[1])]
    if fit_background:
        x0.append(0.0)
        lower.append(-np.inf)
        upper.append(np.inf)

    try:
        result = least_squares(residual, x0=x0, bounds=(lower, upper), loss=robust_loss, method="trf")
    except Exception:
        return seed_result("degraded")

    if not result.success:
        return seed_result("degraded")

    dof = int(usable.sum()) - n_params
    chi2 = float(np.sum(residual(result.x) ** 2))
    reduced_chi2 = chi2 / dof if dof > 0 else float("nan")
    try:
        jtj = result.jac.T @ result.jac
        covariance = np.linalg.inv(jtj) * max(reduced_chi2, np.finfo(float).eps)
    except np.linalg.LinAlgError:
        return seed_result("degraded")
    if not np.all(np.isfinite(covariance)):
        return seed_result("degraded")

    amplitude, cx, cy, fwhm = result.x[:4]
    background = float(result.x[4]) if fit_background else 0.0
    coverage = float(
        np.sum(
            integrate_moffat_over_apertures(
                fx, fy, fiber_radius, cx, cy, fwhm, beta=beta, grid_half_points=grid_half_points
            )
        )
    )

    return AlgoResult(
        kind="spatial_psf_measurement",
        version=PSF_FIT_VERSION,
        arrays={
            "centroid_x": np.float32(cx),
            "centroid_y": np.float32(cy),
            "fwhm": np.float32(fwhm),
            "amplitude": np.float32(amplitude),
            "background": np.float32(background),
            "covariance": covariance.astype(np.float32),
            "fibers_used": usable,
        },
        scalars={
            "wavelength_interval_min": float(wavelength_interval[0]),
            "wavelength_interval_max": float(wavelength_interval[1]),
            "reference_wavelength": float(reference_wavelength),
            "beta": float(beta),
            "chi2": chi2,
            "dof": dof,
            "coverage": coverage,
            "valid": True,
            "status": "measured",
            "usable_fiber_count": int(usable.sum()),
            "fit_background": bool(fit_background),
            "robust_loss": robust_loss,
        },
    )


@dataclass(frozen=True)
class ChromaticPSFModel:
    """Smooth exposure PSF and chromatic-centroid-residual model.

    The centroid is evaluated as the seed DAR prediction plus a fitted
    polynomial residual, valid only inside the wavelength range actually
    spanned by measured intervals; outside that range the residual is zero
    (falls back to the pure seed) and the returned status marks the sample
    ``prior_only`` rather than measured.
    """

    residual_centroid_coefficients_x: np.ndarray
    residual_centroid_coefficients_y: np.ndarray
    fwhm_coefficients: np.ndarray
    valid_wavelength_min: float
    valid_wavelength_max: float
    beta: float

    def evaluate(self, wavelength, seed_delta_x, seed_delta_y):
        wave = np.asarray(wavelength, dtype=float)
        seed_x = np.asarray(seed_delta_x, dtype=float)
        seed_y = np.asarray(seed_delta_y, dtype=float)
        inside = (wave >= self.valid_wavelength_min) & (wave <= self.valid_wavelength_max)
        residual_x = np.polyval(self.residual_centroid_coefficients_x, wave)
        residual_y = np.polyval(self.residual_centroid_coefficients_y, wave)
        centroid_x = seed_x + residual_x
        centroid_y = seed_y + residual_y
        if np.isfinite(self.valid_wavelength_min) and np.isfinite(self.valid_wavelength_max):
            clipped_wave = np.clip(wave, self.valid_wavelength_min, self.valid_wavelength_max)
        else:
            # No interval ever fitted (prior_only fallback uses
            # valid_wavelength_min=+inf/valid_wavelength_max=-inf sentinels
            # to force `inside` to be False everywhere above). Clipping into
            # that inverted, infinite range would compute 0 * inf inside
            # np.polyval and yield NaN. The fwhm_coefficients in that
            # fallback are wavelength-independent (a single constant
            # coefficient), so evaluating at the unclipped wavelength is
            # equivalent and avoids the NaN.
            clipped_wave = wave
        fwhm = np.polyval(self.fwhm_coefficients, clipped_wave)
        status = np.where(inside, np.uint8(0), np.uint8(1))
        return centroid_x, centroid_y, fwhm, status


def fit_chromatic_psf_model(
    interval_reference_wavelength,
    interval_seed_delta_x,
    interval_seed_delta_y,
    interval_centroid_x,
    interval_centroid_y,
    interval_fwhm,
    interval_valid,
    interval_weight,
    *,
    centroid_degree: int = 2,
    fwhm_degree: int = 1,
    beta: float = 3.5,
) -> AlgoResult:
    """Fit the smooth chromatic residual model only where measurements constrain it."""

    wave = np.asarray(interval_reference_wavelength, dtype=float)
    seed_x = np.asarray(interval_seed_delta_x, dtype=float)
    seed_y = np.asarray(interval_seed_delta_y, dtype=float)
    centroid_x = np.asarray(interval_centroid_x, dtype=float)
    centroid_y = np.asarray(interval_centroid_y, dtype=float)
    fwhm = np.asarray(interval_fwhm, dtype=float)
    valid = np.asarray(interval_valid, dtype=bool)
    weight = np.asarray(interval_weight, dtype=float)

    shapes = {wave.shape, seed_x.shape, seed_y.shape, centroid_x.shape, centroid_y.shape, fwhm.shape, valid.shape, weight.shape}
    if len(shapes) != 1 or wave.ndim != 1:
        raise ValueError("chromatic PSF model inputs must be matched 1D arrays")

    minimum_points = max(centroid_degree, fwhm_degree) + 1
    if int(valid.sum()) < minimum_points:
        raise ValueError("too few valid wavelength intervals to fit a chromatic PSF model")

    fit_wave = wave[valid]
    fit_weight = weight[valid]
    residual_x = (centroid_x - seed_x)[valid]
    residual_y = (centroid_y - seed_y)[valid]

    coefficients_x = np.polyfit(fit_wave, residual_x, centroid_degree, w=fit_weight)
    coefficients_y = np.polyfit(fit_wave, residual_y, centroid_degree, w=fit_weight)
    fwhm_coefficients = np.polyfit(fit_wave, fwhm[valid], fwhm_degree, w=fit_weight)

    model = ChromaticPSFModel(
        residual_centroid_coefficients_x=coefficients_x,
        residual_centroid_coefficients_y=coefficients_y,
        fwhm_coefficients=fwhm_coefficients,
        valid_wavelength_min=float(fit_wave.min()),
        valid_wavelength_max=float(fit_wave.max()),
        beta=float(beta),
    )
    return AlgoResult(
        kind="chromatic_psf_model",
        version=CHROMATIC_PSF_VERSION,
        arrays={
            "residual_centroid_coefficients_x": coefficients_x,
            "residual_centroid_coefficients_y": coefficients_y,
            "fwhm_coefficients": fwhm_coefficients,
        },
        scalars={
            "valid_wavelength_min": model.valid_wavelength_min,
            "valid_wavelength_max": model.valid_wavelength_max,
            "beta": model.beta,
            "fitted_interval_count": int(valid.sum()),
        },
        meta={"model": model},
    )
