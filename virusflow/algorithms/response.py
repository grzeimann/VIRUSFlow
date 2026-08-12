from __future__ import annotations

"""Fiber-to-fiber and amplifier-to-amplifier response normalization."""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter

from ..core.algo_result import AlgoResult
from .robust import chunked_biweight_location


NORMALIZATION_VERSION = "twilight-within-and-amplifier-1.0"
RESPONSE_VERSION = "relative-response-factorized-3.0"


@dataclass(frozen=True)
class FiberResponseModel:
    wavelength_knots: np.ndarray
    within_amp_knots: np.ndarray
    amplifier_factors: np.ndarray
    illumination_factors: np.ndarray
    fiber_identity: np.ndarray

    def evaluate(self, wavelength: np.ndarray) -> np.ndarray:
        wave = np.asarray(wavelength, dtype=float)
        if wave.shape[0] != self.within_amp_knots.shape[0]:
            raise ValueError("response model and wavelength fiber counts differ")
        output = np.empty(wave.shape, dtype=np.float32)
        for index in range(wave.shape[0]):
            output[index] = np.interp(
                wave[index], self.wavelength_knots[index], self.within_amp_knots[index],
                left=np.nan, right=np.nan,
            )
        factors = np.asarray(self.amplifier_factors, dtype=np.float32)
        if factors.shape != output.shape:
            raise ValueError("amplifier factors must be sampled for every fiber and wavelength")
        output *= factors
        output *= self.illumination_factors[:, None]
        return output


def compact_fiber_response(
    wavelength: np.ndarray,
    within_amp_response: np.ndarray,
    amplifier_factors: np.ndarray,
    illumination_factors: np.ndarray,
    fiber_identity: np.ndarray,
    *,
    knot_stride: int = 16,
) -> FiberResponseModel:
    wave = np.asarray(wavelength, dtype=np.float32)
    response = np.asarray(within_amp_response, dtype=np.float32)
    if wave.shape != response.shape or wave.ndim != 2:
        raise ValueError("wavelength and within-amplifier response must be matched 2D arrays")
    indices = np.unique(np.r_[np.arange(0, wave.shape[1], max(1, int(knot_stride))), wave.shape[1] - 1])
    return FiberResponseModel(
        wave[:, indices], response[:, indices],
        np.asarray(amplifier_factors, dtype=np.float32),
        np.asarray(illumination_factors, dtype=np.float32),
        np.asarray(fiber_identity, dtype=np.int32),
    )


def within_amplifier_normalization(twilight_spectrum, *, smooth_pixels: int = 51) -> AlgoResult:
    """Return raw and smoothed fiber/common-twilight response ratios."""

    twilight = np.asarray(twilight_spectrum, dtype=float)
    if twilight.ndim != 2:
        raise ValueError("twilight_spectrum must be fiber by wavelength")
    common = chunked_biweight_location(twilight, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_ratio = twilight / common[None, :]
    size = max(3, int(smooth_pixels))
    if size % 2 == 0:
        size += 1
    filled = raw_ratio.copy()
    row_median = np.nanmedian(filled, axis=1)
    bad = ~np.isfinite(filled)
    filled[bad] = np.broadcast_to(row_median[:, None], filled.shape)[bad]
    smooth = median_filter(filled, size=(1, size), mode="nearest")
    norm = np.nanmedian(smooth, axis=1)
    smooth = smooth / np.where(np.isfinite(norm) & (norm != 0), norm, 1.0)[:, None]
    valid = np.isfinite(raw_ratio) & np.isfinite(smooth) & (smooth > 0)
    return AlgoResult(
        kind="within_amplifier_normalization",
        version=NORMALIZATION_VERSION,
        arrays={
            "raw_ratio": raw_ratio.astype(np.float32),
            "normalization": smooth.astype(np.float32),
            "valid_mask": valid.astype(np.uint8),
            "common_twilight": common.astype(np.float32),
        },
        scalars={"smooth_pixels": int(size)},
    )


def normalize_amplifier_spectrum(spectrum, variance, within_normalization, amplifier_factor) -> AlgoResult:
    """Combine within-amplifier and amplifier-to-amplifier response into one divisor."""

    spec = np.asarray(spectrum, dtype=float)
    var = np.asarray(variance, dtype=float)
    within = np.asarray(within_normalization, dtype=float)
    factor = np.asarray(amplifier_factor, dtype=float)
    if factor.shape not in ((), spec.shape):
        raise ValueError("amplifier_factor must be scalar or match the spectrum shape")
    final_response = within * factor
    normalized = spec / final_response
    normalized_variance = var / np.square(final_response)
    return AlgoResult(
        kind="amplifier_spectrum_normalization",
        version=NORMALIZATION_VERSION,
        arrays={
            "normalized_spectrum": normalized.astype(np.float32),
            "normalized_variance": normalized_variance.astype(np.float32),
            "final_response": final_response.astype(np.float32),
        },
        scalars={"amplifier_factor_median": float(np.nanmedian(factor))},
    )


def measure_exposure_illumination(broadband_flux, sky_mask, amp_indices, amplifier_count) -> AlgoResult:
    """Robust per-amplifier throughput relative to the exposure-wide sky level."""

    broadband = np.asarray(broadband_flux, dtype=float)
    sky = np.asarray(sky_mask, dtype=bool)
    amps = np.asarray(amp_indices, dtype=int)
    amp_sky_level = np.full(int(amplifier_count), np.nan)
    for index in range(int(amplifier_count)):
        selected = (amps == index) & sky
        if selected.any():
            amp_sky_level[index] = np.nanmedian(broadband[selected])
    global_level = float(np.nanmedian(amp_sky_level[np.isfinite(amp_sky_level)]))
    amp_illumination = amp_sky_level / global_level
    fiber_illumination = amp_illumination[amps]
    return AlgoResult(
        kind="exposure_illumination_correction",
        version=RESPONSE_VERSION,
        arrays={
            "amplifier_factor": amp_illumination.astype(np.float32),
            "fiber_factor": fiber_illumination.astype(np.float32),
        },
        scalars={"global_level": global_level},
    )


def baseline_relative_response(
    wavelength, response, uncertainty, mask, *, version: str
) -> AlgoResult:
    """Validate one empirical baseline effective-response payload."""

    wave = np.asarray(wavelength, dtype=float)
    response = np.asarray(response, dtype=float)
    uncertainty = np.asarray(uncertainty, dtype=float)
    mask = np.asarray(mask)
    if wave.ndim != 1 or not (
        response.shape == uncertainty.shape == mask.shape == wave.shape
    ):
        raise ValueError("baseline wavelength, response, uncertainty, and mask must be matched 1D arrays")
    if wave.size < 2 or not np.all(np.isfinite(wave)) or not np.all(np.diff(wave) > 0.0):
        raise ValueError("baseline wavelength must be finite and strictly increasing")
    if mask.dtype.kind not in "uib" or np.any(mask < 0):
        raise ValueError("baseline mask must contain non-negative integral values")
    response_valid = (mask.astype(np.uint16) & 1) == 0
    if np.any(response_valid & (~np.isfinite(response) | (response <= 0.0))):
        raise ValueError("unmasked baseline response samples must be finite and positive")
    uncertainty_unknown = (mask.astype(np.uint16) & 2) != 0
    if np.any(~uncertainty_unknown & (~np.isfinite(uncertainty) | (uncertainty < 0.0))):
        raise ValueError("known baseline uncertainties must be finite and non-negative")
    return AlgoResult(
        kind="baseline_relative_response",
        version=version,
        arrays={
            "wavelength": wave.astype(np.float32),
            "response": response.astype(np.float32),
            "uncertainty": uncertainty.astype(np.float32),
            "mask": mask.astype(np.uint16),
        },
        scalars={
            "response_median": float(np.nanmedian(response[response_valid])),
            "valid_fraction": float(np.mean(response_valid)),
            "uncertainty_unknown_fraction": float(np.mean(uncertainty_unknown)),
        },
    )
