from __future__ import annotations

"""Memory-bounded robust statistics used by master-frame algorithms."""

import numpy as np


def chunked_biweight_location(
    data: np.ndarray,
    *,
    axis: int = 0,
    tuning_constant: float = 6.0,
    chunk_pixels: int = 65_536,
) -> np.ndarray:
    """Return the fixed-median biweight location without full-stack temporaries.

    This implements the same one-pass estimator as
    ``astropy.stats.biweight_location(..., axis=0, ignore_nan=True)``.  Master
    frames are chunked only over pixels; every input frame remains represented
    in each estimate.
    """
    values = np.asanyarray(data)
    if values.ndim < 2 or axis != 0:
        raise ValueError("chunked_biweight_location currently requires axis=0")
    if chunk_pixels <= 0:
        raise ValueError("chunk_pixels must be positive")

    output_shape = values.shape[1:]
    flattened = values.reshape(values.shape[0], -1)
    output = np.empty(flattened.shape[1], dtype=np.float64)

    for start in range(0, flattened.shape[1], chunk_pixels):
        stop = min(flattened.shape[1], start + chunk_pixels)
        block = np.asarray(flattened[:, start:stop], dtype=np.float64)
        center = np.nanmedian(block, axis=0)
        delta = block - center
        absolute_delta = np.abs(delta)
        mad = np.nanmedian(absolute_delta, axis=0)
        del absolute_delta

        with np.errstate(divide="ignore", invalid="ignore"):
            weights = delta / (tuning_constant * mad)
        rejected = np.abs(weights) >= 1.0
        np.square(weights, out=weights)
        np.subtract(1.0, weights, out=weights)
        np.square(weights, out=weights)
        weights[rejected] = 0.0
        np.multiply(delta, weights, out=delta)
        with np.errstate(divide="ignore", invalid="ignore"):
            location = center + (
                np.nansum(delta, axis=0) / np.nansum(weights, axis=0)
            )
        output[start:stop] = np.where(mad == 0.0, center, location)

    return output.reshape(output_shape)
