"""Shared detector/additive correction for science and calibration inputs."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult


ALGORITHM_VERSION = "response-calibration-detector-1.1"
DARK_BIAS_CONVENTION = "included_in_electron_master"


def correct_response_calibration_frames(
    images,
    variances,
    exposure_times,
    *,
    master_bias,
    master_bias_scatter,
    master_dark,
    dark_pixel_mask,
    dark_reference_exposure_time: float,
    dark_bias_convention: str,
) -> AlgoResult:
    """Apply the shared science/response bias and dark correction.

    The retained dark representation includes bias, so the additive dark
    prediction is ``master_bias + scale * (master_dark-master_bias)``.  Its
    reference exposure time and bias convention must come from the selected
    ``master_dark`` Product.
    """

    data = np.asarray(images, dtype=np.float32)
    variance = np.asarray(variances, dtype=np.float32)
    times = np.asarray(exposure_times, dtype=np.float32)
    bias = np.asarray(master_bias, dtype=np.float32)
    bias_scatter = np.asarray(master_bias_scatter, dtype=np.float32)
    dark = np.asarray(master_dark, dtype=np.float32)
    dark_mask = np.asarray(dark_pixel_mask, dtype=np.uint8)
    reference = float(dark_reference_exposure_time)
    convention = str(dark_bias_convention)
    if data.ndim != 3 or variance.shape != data.shape:
        raise ValueError("response calibration images and variances must be shape-matched stacks")
    if times.shape != (data.shape[0],):
        raise ValueError("one exposure time is required for every response calibration frame")
    if any(array.shape != data.shape[1:] for array in (bias, bias_scatter, dark, dark_mask)):
        raise ValueError("response calibration detector Products must match the input image shape")
    if not np.isfinite(reference) or reference <= 0.0:
        raise ValueError("master_dark requires a finite positive reference exposure time")
    if convention != DARK_BIAS_CONVENTION:
        raise ValueError(
            "master_dark requires bias_convention="
            f"{DARK_BIAS_CONVENTION!r}; received {convention!r}"
        )
    if np.any(~np.isfinite(times)) or np.any(times <= 0.0):
        raise ValueError("response calibration exposure times must be finite and positive")

    scales = times / np.float32(reference)
    dark_residual = dark - bias
    corrected = data - bias[None, :, :] - scales[:, None, None] * dark_residual[None, :, :]
    corrected_variance = variance + np.square(bias_scatter, dtype=np.float32)[None, :, :]
    masks = np.broadcast_to(dark_mask, data.shape).copy()
    masks |= (~np.isfinite(corrected) | ~np.isfinite(corrected_variance)).astype(np.uint8)
    return AlgoResult(
        kind="response_calibration_detector_state",
        version=ALGORITHM_VERSION,
        arrays={
            "corrected_images": corrected.astype(np.float32),
            "corrected_variances": corrected_variance.astype(np.float32),
            "pixel_masks": masks.astype(np.uint8),
            "dark_scales": scales.astype(np.float32),
        },
        scalars={
            "frame_count": int(data.shape[0]),
            "dark_reference_exposure_time_seconds": reference,
            "minimum_dark_scale": float(np.min(scales)),
            "maximum_dark_scale": float(np.max(scales)),
        },
        meta={
            "detector_correction_policy": "bias_plus_exptime_scaled_dark_residual-1",
            "dark_representation": "electron_master_including_bias",
            "dark_bias_convention": convention,
        },
    )


__all__ = [
    "ALGORITHM_VERSION", "DARK_BIAS_CONVENTION",
    "correct_response_calibration_frames",
]
