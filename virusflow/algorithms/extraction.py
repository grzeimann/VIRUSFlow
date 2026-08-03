from __future__ import annotations

"""Fractional-aperture spectral extraction, independent of what is extracted."""

import numpy as np

from ..core.algo_result import AlgoResult


EXTRACTION_VERSION = "fractional-sum-aperture-1.0"


def fractional_aperture_geometry(traces, detector_rows: int, *, width: float = 5.0):
    """Return exact detector-pixel overlaps for a continuous top-hat aperture."""

    trace = np.asarray(traces, dtype=float)
    if trace.ndim != 2 or detector_rows <= 0 or not np.isfinite(width) or width <= 0:
        raise ValueError("fractional aperture requires 2D traces, positive rows, and positive width")
    nsample = int(np.ceil(width)) + 1
    left = trace - width / 2.0
    right = trace + width / 2.0
    start = np.floor(left).astype(np.int32)
    offsets = np.arange(nsample, dtype=np.int32)
    rows = start[..., None] + offsets
    weights = np.maximum(
        0.0,
        np.minimum(rows + 1.0, right[..., None]) - np.maximum(rows, left[..., None]),
    )
    valid = np.isfinite(trace) & (left >= 0.0) & (right <= float(detector_rows))
    weights[~valid] = 0.0
    return rows, weights.astype(np.float32), valid


def extract_fractional_aperture(image, variance, traces, *, pixel_mask=None, width: float = 5.0) -> AlgoResult:
    """Sum flux and propagate diagonal variance with the exact same weights."""

    data = np.asarray(image, dtype=float)
    var = np.asarray(variance, dtype=float)
    trace = np.asarray(traces, dtype=float)
    if data.ndim != 2 or var.shape != data.shape or trace.ndim != 2 or trace.shape[1] != data.shape[1]:
        raise ValueError("image, variance, and trace shapes are incompatible")
    mask = np.zeros(data.shape, dtype=bool) if pixel_mask is None else np.asarray(pixel_mask, dtype=bool)
    if mask.shape != data.shape:
        raise ValueError("pixel_mask must match image")
    rows, weights, aperture_valid = fractional_aperture_geometry(trace, data.shape[0], width=width)
    clipped = np.clip(rows, 0, data.shape[0] - 1)
    columns = np.broadcast_to(np.arange(data.shape[1])[None, :, None], clipped.shape)
    samples = data[clipped, columns]
    sample_variance = var[clipped, columns]
    sample_valid = (
        aperture_valid[..., None]
        & ~mask[clipped, columns]
        & np.isfinite(samples)
        & np.isfinite(sample_variance)
        & (sample_variance >= 0.0)
    )
    actual_weights = np.where(sample_valid, weights, 0.0)
    spectrum = np.sum(actual_weights * np.where(sample_valid, samples, 0.0), axis=-1)
    extracted_variance = np.sum(np.square(actual_weights) * np.where(sample_valid, sample_variance, 0.0), axis=-1)
    effective_width = np.sum(actual_weights, axis=-1)
    valid_fraction = effective_width / float(width)
    extraction_valid = aperture_valid & (effective_width > 0.0)
    spectrum = np.where(extraction_valid, spectrum, np.nan).astype(np.float32)
    extracted_variance = np.where(extraction_valid, extracted_variance, np.nan).astype(np.float32)
    return AlgoResult(
        kind="fractional_aperture_extraction",
        version=EXTRACTION_VERSION,
        arrays={
            "spectrum": spectrum,
            "variance": extracted_variance,
            "valid_pixel_fraction": valid_fraction.astype(np.float32),
            "effective_aperture_width": effective_width.astype(np.float32),
            "aperture_start_row": rows[..., 0].astype(np.int16),
            "fractional_weights": actual_weights.astype(np.float32),
            "extraction_valid": extraction_valid.astype(np.uint8),
        },
        scalars={"aperture_width_pixels": float(width)},
    )


def validate_wavelength_rows(wavelength, expected_shape) -> AlgoResult:
    """Flag fibers whose wavelength row is not finite and strictly increasing."""

    wave = np.asarray(wavelength, dtype=float)
    shape_matches = tuple(wave.shape) == tuple(expected_shape)
    if not shape_matches:
        return AlgoResult(
            kind="wavelength_row_validation",
            version=EXTRACTION_VERSION,
            arrays={
                "valid_rows": np.zeros((0,), dtype=bool),
                "non_finite_fiber_indices": np.empty((0,), dtype=np.int64),
                "non_increasing_fiber_indices": np.empty((0,), dtype=np.int64),
            },
            scalars={"shape_matches": False, "excluded_count": 0, "any_valid": False},
        )
    finite_rows = np.all(np.isfinite(wave), axis=1)
    increasing_rows = np.all(np.diff(wave, axis=1) > 0.0, axis=1)
    valid_rows = finite_rows & increasing_rows
    excluded = np.flatnonzero(~valid_rows)
    return AlgoResult(
        kind="wavelength_row_validation",
        version=EXTRACTION_VERSION,
        arrays={
            "valid_rows": valid_rows,
            "non_finite_fiber_indices": np.flatnonzero(~finite_rows),
            "non_increasing_fiber_indices": np.flatnonzero(~increasing_rows),
        },
        scalars={
            "shape_matches": True,
            "excluded_count": int(excluded.size),
            "any_valid": bool(valid_rows.any()),
        },
    )
