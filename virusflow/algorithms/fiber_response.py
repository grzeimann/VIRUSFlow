"""Calibration-time fiber response from complementary LDLS and twilight data."""

from __future__ import annotations

import warnings

import numpy as np
from astropy.stats import biweight_location, mad_std
from scipy.interpolate import interp1d

from ..core.algo_result import AlgoResult
from .utils.masks import build_model_spectra


FIBER_RESPONSE_VERSION = "ldls-fine-twilight-anchor-1.1"


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    output = np.full(np.asarray(numerator).shape, np.nan, dtype=float)
    usable = np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0.0)
    np.divide(numerator, denominator, out=output, where=usable)
    return output


def get_continuum(spectra: np.ndarray, *, nbins: int) -> np.ndarray:
    """Fit the legacy robust, per-fiber detector-column continuum.

    Each detector-column bin is reduced with a biweight location and the bin
    values are interpolated back to every column.  This is the operation used
    by the older Remedy response algorithm for both its broad twilight anchor
    and its finer residual correction.
    """

    values = np.asarray(spectra, dtype=float)
    if values.ndim != 2:
        raise ValueError("spectra must be a fiber-by-dispersion-pixel array")
    if values.shape[1] < 2:
        raise ValueError("continuum fitting requires at least two spectral samples")
    bin_count = min(values.shape[1], max(2, int(nbins)))
    chunks = np.array_split(np.arange(values.shape[1]), bin_count)
    centers = np.asarray([np.mean(chunk) for chunk in chunks], dtype=float)
    binned = np.full((values.shape[0], bin_count), np.nan, dtype=float)
    for index, columns in enumerate(chunks):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                binned[:, index] = biweight_location(
                    values[:, columns], axis=1, ignore_nan=True
                )
            except (TypeError, ValueError):
                binned[:, index] = np.nanmedian(values[:, columns], axis=1)

    continuum = np.full(values.shape, np.nan, dtype=float)
    columns = np.arange(values.shape[1], dtype=float)
    for fiber, row in enumerate(binned):
        good = np.isfinite(row)
        if np.count_nonzero(good) < max(2, bin_count // 2 + 1):
            continue
        kind = "quadratic" if np.count_nonzero(good) >= 3 else "linear"
        continuum[fiber] = interp1d(
            centers[good], row[good], kind=kind, bounds_error=False,
            fill_value="extrapolate", assume_sorted=True,
        )(columns)
    return continuum


def _common_spectrum(
    spectrum: np.ndarray,
    wavelength: np.ndarray,
    good_solutions: np.ndarray,
    *,
    nbins: int,
) -> np.ndarray:
    interpolator, _, _, _ = build_model_spectra(
        spectrum, wavelength, good_solutions, nbins=int(nbins)
    )
    return np.asarray(interpolator(wavelength), dtype=float)


def fit_within_amplifier_response(
    ldls_spectrum: np.ndarray,
    twilight_spectrum: np.ndarray,
    wavelength: np.ndarray,
    *,
    science_spectrum: np.ndarray | None = None,
    common_model_bins: int = 3000,
    broad_ldls_bins: int = 5,
    twilight_residual_bins: int = 25,
    minimum_wavelength_finite_fraction: float = 0.8,
) -> AlgoResult:
    """Anchor a fine LDLS response to a twilight-determined broad response.

    LDLS is divided by its robust common spectrum, and the resulting ratio is
    itself divided by its own broad continuum (``broad_ldls_bins``) so that
    LDLS contributes only fine, wavelength-dependent structure with unit
    median per fiber.  Twilight is independently divided by its common
    spectrum and by that fine LDLS response; the smooth remainder defines the
    broadband within-amplifier fiber response, so broad-scale normalization is
    determined by twilight rather than by LDLS's own broad illumination
    shape.  The final normalization is the fine LDLS response times this
    twilight-derived broad response.

    Fitting the LDLS ratio and a fitted twilight residual back into the
    normalization can reintroduce broadband illumination structure and noise
    into the within-amplifier fiber response, making the decomposition less
    physically identifiable.  The remaining twilight residual after applying
    the normalization is therefore binned (``twilight_residual_bins``) and
    retained only as diagnostic evidence; it is never multiplied back into
    the fitted response.  Master Science, when supplied, is evaluated only as
    validation evidence and never changes the fitted response.
    """

    ldls = np.asarray(ldls_spectrum, dtype=float)
    twilight = np.asarray(twilight_spectrum, dtype=float)
    wave = np.asarray(wavelength, dtype=float)

    if ldls.ndim != 2 or twilight.shape != ldls.shape or wave.shape != ldls.shape:
        raise ValueError("LDLS, twilight, and wavelength must be matching 2D arrays")

    science = None if science_spectrum is None else np.asarray(science_spectrum, dtype=float)
    if science is not None and science.shape != ldls.shape:
        raise ValueError("science_spectrum must match the calibration spectrum shape")

    usable = np.isfinite(wave) & np.isfinite(ldls) & np.isfinite(twilight)
    finite_fraction = np.mean(usable, axis=1)
    good_solutions = finite_fraction > float(minimum_wavelength_finite_fraction)

    if np.count_nonzero(good_solutions) < 1:
        raise ValueError("no fiber has sufficient finite calibration coverage")

    # Estimate common spectral shapes after removing fiber-to-fiber
    # normalization differences in _common_spectrum().
    ldls_model = _common_spectrum(ldls, wave, good_solutions, nbins=common_model_bins)
    twilight_model = _common_spectrum(twilight, wave, good_solutions, nbins=common_model_bins)

    # Remove the common LDLS spectrum.
    ldls_ratio = _safe_divide(ldls, ldls_model)

    # LDLS supplies only the fine wavelength-dependent fiber response.
    ldls_broad = get_continuum(ldls_ratio, nbins=broad_ldls_bins)
    ldls_fine = _safe_divide(ldls_ratio, ldls_broad)

    # Force each fine LDLS response to unit median so LDLS carries no
    # broadband fiber normalization.
    fine_valid = good_solutions[:, None] & np.isfinite(ldls_fine) & (ldls_fine > 0.0)
    ldls_fine_scale = np.nanmedian(np.where(fine_valid, ldls_fine, np.nan), axis=1)
    good_fine_scale = np.isfinite(ldls_fine_scale) & (ldls_fine_scale > 0.0)
    ldls_fine = _safe_divide(ldls_fine, ldls_fine_scale[:, None])

    # Remove the common twilight spectrum and fine LDLS response. The
    # remaining smooth structure contains the broad fiber response plus
    # the overall amplifier twilight level.
    twilight_ratio = _safe_divide(twilight, twilight_model)
    twilight_for_broad = _safe_divide(twilight_ratio, ldls_fine)
    twilight_broad = get_continuum(twilight_for_broad, nbins=broad_ldls_bins)

    # Separate the amplifier-wide twilight level from the relative
    # within-amplifier broadband fiber response.
    broad_valid = (
        good_solutions[:, None]
        & good_fine_scale[:, None]
        & np.isfinite(wave)
        & np.isfinite(twilight_broad)
        & (twilight_broad > 0.0)
    )
    amplifier_twilight_level = float(np.nanmedian(np.where(broad_valid, twilight_broad, np.nan)))

    if not np.isfinite(amplifier_twilight_level) or amplifier_twilight_level <= 0.0:
        raise ValueError("invalid amplifier twilight level")

    twilight_broad = twilight_broad / amplifier_twilight_level

    # The within-amplifier normalization now has unit amplifier-wide scale.
    # The amplifier level is retained separately for amp-to-amp calibration.
    normalization = ldls_fine * twilight_broad

    valid = (
        good_solutions[:, None]
        & good_fine_scale[:, None]
        & np.isfinite(wave)
        & np.isfinite(ldls_fine)
        & (ldls_fine > 0.0)
        & np.isfinite(twilight_broad)
        & (twilight_broad > 0.0)
        & np.isfinite(normalization)
        & (normalization > 0.0)
    )
    normalization[~valid] = np.nan

    # Retain the twilight residual as diagnostic evidence rather than
    # fitting it back into the normalization.
    predicted_twilight = twilight_model * amplifier_twilight_level * normalization
    twilight_residual_raw = _safe_divide(twilight, predicted_twilight) - 1.0
    twilight_residual_raw[~valid] = np.nan

    twilight_residual_correction = get_continuum(
        twilight_residual_raw, nbins=twilight_residual_bins
    )
    twilight_residual_correction[~valid] = np.nan

    arrays = {
        # Compatibility names retained for existing Product consumers.
        "raw_ratio": twilight_ratio.astype(np.float32),
        "normalization": normalization.astype(np.float32),
        "valid_mask": valid.astype(np.uint8),
        "common_twilight": twilight_model.astype(np.float32),
        # Explicit factorization and wavelength evidence.
        "ftf_ldls": ldls_fine.astype(np.float32),
        "twilight_broad_correction": twilight_broad.astype(np.float32),
        # Diagnostic only: not applied back into the fitted normalization.
        "twilight_residual_correction": twilight_residual_correction.astype(np.float32),
        "wavelength": wave.astype(np.float32),
        "amplifier_twilight_level": np.asarray(
            [amplifier_twilight_level], dtype=np.float32
        ),
    }
    if science is not None:
        science_model = _common_spectrum(
            science, wave, good_solutions, nbins=common_model_bins
        )
        ftf_science = _safe_divide(science, science_model)
        science_residual = _safe_divide(ftf_science, normalization) - 1.0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            arrays["science_residual_per_fiber"] = np.asarray(
                mad_std(science_residual, ignore_nan=True, axis=1), dtype=np.float32
            )

    return AlgoResult(
        kind="within_amplifier_normalization",
        version=FIBER_RESPONSE_VERSION,
        arrays=arrays,
        scalars={
            "common_model_bins": int(common_model_bins),
            "broad_ldls_bins": int(broad_ldls_bins),
            "twilight_residual_bins": int(twilight_residual_bins),
            "good_wavelength_solution_count": int(np.count_nonzero(good_solutions)),
            "valid_fraction": float(np.mean(valid)),
            "amplifier_twilight_level": amplifier_twilight_level,
        },
        meta={
            "response_factorization": (
                "ftf_ldls * twilight_broad_correction"
            ),
            "fine_structure_source": "master_ldls",
            "large_scale_anchor": "master_twilight",
            "twilight_reference_mode": "robust_common_fiber_spectrum",
            "twilight_residual_role": "diagnostic_only",
            "science_role": "validation_only" if science is not None else "not_available",
            "scattered_light_treatment": (
                "paired_physical_ccd_gap_model_subtracted_before_extraction"
            ),
        },
    )
