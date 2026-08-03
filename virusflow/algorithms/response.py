from __future__ import annotations

"""Fiber-to-fiber and amplifier-to-amplifier response normalization."""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter

from ..core.algo_result import AlgoResult


NORMALIZATION_VERSION = "twilight-within-and-amplifier-1.0"
RESPONSE_VERSION = "relative-response-factorized-2.0"


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
        amp_index = np.asarray(self.fiber_identity)[:, 0].astype(int)
        for index in range(wave.shape[0]):
            output[index] = np.interp(
                wave[index], self.wavelength_knots[index], self.within_amp_knots[index],
                left=np.nan, right=np.nan,
            )
        output *= self.amplifier_factors[amp_index, None]
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
    common = np.nanmedian(twilight, axis=0)
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


def amplifier_normalization(amplifier_twilight_levels) -> AlgoResult:
    """Place amplifiers in one exposure-wide robust twilight reference frame."""

    levels = np.asarray(amplifier_twilight_levels, dtype=float)
    positive = np.isfinite(levels) & (levels > 0)
    reference = float(np.nanmedian(levels[positive])) if positive.any() else float("nan")
    factors = np.full(levels.shape, np.nan, dtype=float)
    factors[positive] = levels[positive] / reference
    return AlgoResult(
        kind="amplifier_normalization",
        version=NORMALIZATION_VERSION,
        arrays={"amplifier_factors": factors.astype(np.float32)},
        scalars={"reference_level": reference},
    )


def normalize_amplifier_spectrum(spectrum, variance, within_normalization, amplifier_factor) -> AlgoResult:
    """Combine within-amplifier and amplifier-to-amplifier response into one divisor."""

    spec = np.asarray(spectrum, dtype=float)
    var = np.asarray(variance, dtype=float)
    within = np.asarray(within_normalization, dtype=float)
    final_response = within * amplifier_factor
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
        scalars={"amplifier_factor": float(amplifier_factor)},
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


def baseline_relative_response(wavelength, *, version: str) -> AlgoResult:
    """Explicit provisional identity response used until a measured curve exists."""

    wave = np.asarray(wavelength)
    response = np.ones(wave.shape, dtype=np.float32)
    return AlgoResult(
        kind="baseline_relative_response",
        version=version,
        arrays={"wavelength": wave, "response": response},
        scalars={"response_median": 1.0},
    )
