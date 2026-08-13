"""Calibration-time fiber response from complementary LDLS and twilight data."""

from __future__ import annotations

import warnings

import numpy as np
from scipy.interpolate import interp1d

from ..core.algo_result import AlgoResult
from .utils.masks import build_model_spectra
from .robust import chunked_biweight_location


FIBER_RESPONSE_VERSION = "exposure-ldls-twilight-factorization-3.2"


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
                binned[:, index] = chunked_biweight_location(values[:, columns].T)
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
            centers[good],
            row[good],
            kind=kind,
            bounds_error=False,
            fill_value="extrapolate",
            assume_sorted=True,
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


def fit_exposure_fiber_response(
    ldls_spectra: list[np.ndarray],
    twilight_spectra: list[np.ndarray],
    wavelengths: list[np.ndarray],
    *,
    science_spectrum: np.ndarray | None = None,
    common_model_bins: int = 3000,
    broad_ldls_bins: int = 5,
    twilight_residual_bins: int = 25,
    minimum_wavelength_finite_fraction: float = 0.8,
) -> AlgoResult:
    """Factor an exposure response into within-amplifier, 1-D, and 0-D terms.

    Exposure-wide common models ``M_L`` and ``M_T`` remove LDLS and twilight
    source spectra.  ``R_L = L / M_L`` is split into an LDLS broad continuum
    ``B_L`` and unit-normalized fine response ``F_L = R_L / B_L``.  LDLS is
    therefore the sole source of sharp wavelength-dependent response.

    Twilight supplies only a broad illumination envelope: ``R_T = T / M_T``,
    ``Q = R_T / F_L``, and ``B_T = continuum(Q)``.  The hybrid response is
    ``H = F_L * B_T`` (``twilight_scaled_ldls_response``).  For each amplifier,
    ``R_a = median_i(H_ai)`` is the full pre-scalar response and
    ``F_ai = H_ai / R_a`` is the unit-median within-amplifier component.
    In linear space, ``g_a = median_lambda(R_a)`` and ``A_a = R_a / g_a``;
    after an exposure reference scalar, the published normalization is
    ``F_ai * A_a * g_a_rel``.

    Raw twilight residuals (including sharp solar/LSF structure) and optional
    science residuals are diagnostics only. Neither is fed back into the fit.
    """

    if not (len(ldls_spectra) == len(twilight_spectra) == len(wavelengths)):
        raise ValueError("LDLS, twilight, and wavelength amplifier counts must agree")
    if not ldls_spectra:
        raise ValueError("at least one participating amplifier is required")
    ldls_parts = [np.asarray(value, dtype=float) for value in ldls_spectra]
    twilight_parts = [np.asarray(value, dtype=float) for value in twilight_spectra]
    wave_parts = [np.asarray(value, dtype=float) for value in wavelengths]
    shapes = {part.shape for part in ldls_parts}
    if len(shapes) != 1 or len(next(iter(shapes))) != 2:
        raise ValueError("all participating LDLS spectra must have one common 2D shape")
    if any(
        twilight.shape != ldls.shape or wave.shape != ldls.shape
        for ldls, twilight, wave in zip(ldls_parts, twilight_parts, wave_parts)
    ):
        raise ValueError("LDLS, twilight, and wavelength must be matching 2D arrays")
    ldls = np.concatenate(ldls_parts)
    twilight = np.concatenate(twilight_parts)
    wave = np.concatenate(wave_parts)
    amplifier_count = len(ldls_parts)
    fibers_per_amplifier, samples = ldls_parts[0].shape

    science = (
        None if science_spectrum is None else np.asarray(science_spectrum, dtype=float)
    )
    if science is not None and science.shape != ldls.shape:
        raise ValueError("science_spectrum must match concatenated calibration spectra")

    usable = np.isfinite(wave) & np.isfinite(ldls) & np.isfinite(twilight)
    good_solutions = np.mean(usable, axis=1) > float(minimum_wavelength_finite_fraction)
    if not np.any(good_solutions):
        raise ValueError("no fiber has sufficient finite calibration coverage")

    # Estimate common spectral shapes after removing fiber-to-fiber
    # normalization differences in _common_spectrum().
    ldls_common_spectrum = _common_spectrum(
        ldls, wave, good_solutions, nbins=common_model_bins
    )
    twilight_common_spectrum = _common_spectrum(
        twilight, wave, good_solutions, nbins=common_model_bins
    )

    # Both common spectra are deliberately fitted once from every usable fiber
    # in the exposure, rather than once per amplifier.
    ldls_relative_response = _safe_divide(ldls, ldls_common_spectrum)

    # LDLS supplies only the fine wavelength-dependent fiber response.
    ldls_broad_response = get_continuum(ldls_relative_response, nbins=broad_ldls_bins)
    ldls_fine_response = _safe_divide(ldls_relative_response, ldls_broad_response)

    # Force each fine LDLS response to unit median so LDLS carries no
    # broadband fiber normalization.
    fine_valid = (
        good_solutions[:, None]
        & np.isfinite(ldls_fine_response)
        & (ldls_fine_response > 0.0)
    )
    ldls_fine_scale = chunked_biweight_location(
        np.where(fine_valid, ldls_fine_response, np.nan).T
    )
    good_fine_scale = np.isfinite(ldls_fine_scale) & (ldls_fine_scale > 0.0)
    ldls_fine_response = _safe_divide(ldls_fine_response, ldls_fine_scale[:, None])

    # Remove the common twilight spectrum and fine LDLS response. The
    # remaining smooth structure contains the broad fiber response plus
    # the overall amplifier twilight level.
    twilight_relative_response = _safe_divide(twilight, twilight_common_spectrum)
    twilight_without_ldls_fine = _safe_divide(
        twilight_relative_response, ldls_fine_response
    )
    twilight_broad_response = get_continuum(
        twilight_without_ldls_fine, nbins=broad_ldls_bins
    )

    # H = F_L * B_T: fine structure from LDLS, broad envelope from twilight.
    twilight_scaled_ldls_response = ldls_fine_response * twilight_broad_response
    valid = (
        good_solutions[:, None]
        & good_fine_scale[:, None]
        & np.isfinite(wave)
        & np.isfinite(twilight_scaled_ldls_response)
        & (twilight_scaled_ldls_response > 0.0)
    )
    within_amplifier_response = np.full_like(twilight_scaled_ldls_response, np.nan)
    amplifier_total_response = np.full((amplifier_count, samples), np.nan, dtype=float)
    amplifier_response = np.full_like(amplifier_total_response, np.nan)
    amplifier_scalar = np.full(amplifier_count, np.nan, dtype=float)
    for amplifier in range(amplifier_count):
        start, stop = (
            amplifier * fibers_per_amplifier,
            (amplifier + 1) * fibers_per_amplifier,
        )
        local_valid = valid[start:stop]
        # Keep the median convention here: it enforces the documented unit
        # median of each within-amplifier response component.
        amplifier_total = np.nanmedian(
            np.where(local_valid, twilight_scaled_ldls_response[start:stop], np.nan),
            axis=0,
        )
        amplifier_total_response[amplifier] = amplifier_total
        within_amplifier_response[start:stop] = _safe_divide(
            twilight_scaled_ldls_response[start:stop], amplifier_total[None, :]
        )
        scalar = float(
            np.nanmedian(
                amplifier_total[np.isfinite(amplifier_total) & (amplifier_total > 0.0)]
            )
        )
        if not np.isfinite(scalar) or scalar <= 0.0:
            raise ValueError("invalid amplifier response scalar")
        amplifier_scalar[amplifier] = scalar
        amplifier_response[amplifier] = amplifier_total / scalar
    reference_scalar = float(np.nanmedian(amplifier_scalar))
    if not np.isfinite(reference_scalar) or reference_scalar <= 0.0:
        raise ValueError("invalid exposure amplifier reference")
    amplifier_scalar /= reference_scalar
    normalization = (
        within_amplifier_response
        * amplifier_response.repeat(fibers_per_amplifier, axis=0)
        * np.repeat(amplifier_scalar, fibers_per_amplifier)[:, None]
    )

    valid &= np.isfinite(within_amplifier_response) & (within_amplifier_response > 0.0)
    valid &= np.isfinite(normalization) & (normalization > 0.0)
    normalization[~valid] = np.nan
    within_amplifier_response[~valid] = np.nan

    # Retain the twilight residual as diagnostic evidence rather than
    # fitting it back into the normalization.
    predicted_twilight = twilight_common_spectrum * normalization * reference_scalar
    twilight_residual_raw = _safe_divide(twilight, predicted_twilight) - 1.0
    twilight_residual_raw[~valid] = np.nan

    twilight_residual_correction = get_continuum(
        twilight_residual_raw, nbins=twilight_residual_bins
    )
    twilight_residual_correction[~valid] = np.nan

    arrays = {
        "raw_ratio": twilight_relative_response.astype(np.float32),
        "normalization": normalization.astype(np.float32),
        "valid_mask": valid.astype(np.uint8),
        "common_ldls": ldls_common_spectrum.astype(np.float32),
        "common_twilight": twilight_common_spectrum.astype(np.float32),
        "within_amplifier_response": within_amplifier_response.astype(np.float32),
        "amplifier_response": amplifier_response.astype(np.float32),
        "amplifier_scalar": amplifier_scalar.astype(np.float32),
        "amplifier_common_response": amplifier_total_response.astype(np.float32),
        "fiber_amplifier_index": np.repeat(
            np.arange(amplifier_count, dtype=np.int32), fibers_per_amplifier
        ),
        "ftf_ldls": ldls_fine_response.astype(np.float32),
        "twilight_broad_correction": twilight_broad_response.astype(np.float32),
        "twilight_residual_correction": twilight_residual_correction.astype(np.float32),
        "wavelength": wave.astype(np.float32),
    }
    if science is not None:
        science_model = _common_spectrum(
            science, wave, good_solutions, nbins=common_model_bins
        )
        science_model_with_response = science_model * normalization
        science_scale_samples = _safe_divide(science, science_model_with_response)
        science_scale_valid = (
            valid
            & np.isfinite(science_scale_samples)
            & (science_scale_samples > 0.0)
        )
        science_scalar = float(
            np.nanmedian(np.where(science_scale_valid, science_scale_samples, np.nan))
        )
        if not np.isfinite(science_scalar) or science_scalar <= 0.0:
            raise ValueError("invalid science reference electron scale")

        predicted_science = science_model_with_response * science_scalar
        science_relative_residual = _safe_divide(
            science - predicted_science, science_scalar
        )
        science_relative_residual[~(valid & np.isfinite(science))] = np.nan
        arrays["science_residual_scaled"] = science_relative_residual.astype(np.float32)

    return AlgoResult(
        kind="exposure_fiber_response",
        version=FIBER_RESPONSE_VERSION,
        arrays=arrays,
        scalars={
            "common_model_bins": int(common_model_bins),
            "broad_ldls_bins": int(broad_ldls_bins),
            "twilight_residual_bins": int(twilight_residual_bins),
            "good_wavelength_solution_count": int(np.count_nonzero(good_solutions)),
            "valid_fraction": float(np.mean(valid)),
            "amplifier_count": amplifier_count,
            "fibers_per_amplifier": fibers_per_amplifier,
            "amplifier_reference_scalar": reference_scalar,
            **(
                {"science_reference_electrons": science_scalar}
                if science is not None
                else {}
            ),
        },
        meta={
            "response_factorization": "within_amplifier_response * amplifier_response * amplifier_scalar",
            "fine_structure_source": "master_ldls",
            "large_scale_anchor": "master_twilight",
            "twilight_reference_mode": "exposure_wide_robust_common_fiber_spectrum",
            "amplifier_response_representation": "linear",
            "amplifier_common_response_role": "full_pre_scalar_amplifier_response",
            "twilight_residual_role": "diagnostic_only",
            "science_role": (
                "validation_only" if science is not None else "not_available"
            ),
            "science_residual_definition": (
                "(science - predicted_science) / science_reference_electrons"
                if science is not None
                else "not_available"
            ),
            "scattered_light_treatment": (
                "paired_physical_ccd_gap_model_subtracted_before_extraction"
            ),
        },
    )
