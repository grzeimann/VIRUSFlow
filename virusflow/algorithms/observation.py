from __future__ import annotations

"""Array-only dither assignment, registration, and footprint coverage baselines."""

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .exposure import CalibratedFiberState


ALGORITHM_VERSION = "observation-dither-baseline-1"


@dataclass(frozen=True)
class DitherAssignmentResult:
    assignments: np.ndarray
    complete: bool
    ambiguous: bool
    duplicate_count: int
    extra_count: int


def combine_calibrated_fiber_states(
    states: Sequence[CalibratedFiberState],
) -> dict[str, np.ndarray]:
    """Concatenate three atomic exposure states without coadding their measurements."""

    if len(states) != 3:
        raise ValueError("a complete calibrated VIRUS observation requires exactly three exposures")
    identities = [str(state.exposure_id) for state in states]
    if len(set(identities)) != 3:
        raise ValueError("calibrated observation members must have unique exposure identities")
    sample_counts = {np.asarray(state.flux).shape[1] for state in states}
    if len(sample_counts) != 1:
        raise ValueError("observation members must share a compatible spectral sample count")
    arrays = {
        "flux": np.concatenate([np.asarray(state.flux, dtype=np.float32) for state in states]),
        "variance": np.concatenate([np.asarray(state.variance, dtype=np.float32) for state in states]),
        "mask": np.concatenate([np.asarray(state.mask, dtype=np.uint16) for state in states]),
        "wavelength": np.concatenate([np.asarray(state.wavelength, dtype=np.float32) for state in states]),
        "fiber_identity": np.concatenate([np.asarray(state.fiber_identity, dtype=np.int32) for state in states]),
        "sky_coordinates": np.concatenate([np.asarray(state.sky_coordinates, dtype=np.float64) for state in states]),
        "focal_plane_coordinates": np.concatenate([np.asarray(state.focal_plane_coordinates, dtype=np.float32) for state in states]),
        "exposure_index": np.concatenate([
            np.full(np.asarray(state.flux).shape[0], index, dtype=np.uint8)
            for index, state in enumerate(states)
        ]),
    }
    shape = arrays["flux"].shape
    if arrays["variance"].shape != shape or arrays["mask"].shape != shape or arrays["wavelength"].shape != shape:
        raise ValueError("final spectral planes are not shape matched")
    return arrays


def assign_nominal_dithers(
    exposure_ids: Sequence[str],
    sequence_values: Sequence[float],
    nominal_pattern: np.ndarray,
) -> DitherAssignmentResult:
    """Assign sequence-ordered members without inventing absent positions.

    Columns are input index, sequence rank, dither index, nominal dx/dy,
    duplicate flag, extra flag, and ambiguous-order flag.
    """

    ids = [str(value) for value in exposure_ids]
    sequence = np.asarray(sequence_values, dtype=float)
    pattern = np.asarray(nominal_pattern, dtype=float)
    if sequence.shape != (len(ids),):
        raise ValueError("sequence_values must have one value per exposure")
    if pattern.ndim != 2 or pattern.shape[1] != 2 or pattern.shape[0] < 1:
        raise ValueError("nominal_pattern must have shape (position, 2)")

    finite = np.isfinite(sequence)
    ambiguous = bool(not finite.all() or len(np.unique(sequence[finite])) != int(finite.sum()))
    order_key = np.where(finite, sequence, np.inf)
    order = np.lexsort((np.arange(len(ids)), order_key))
    rank = np.empty(len(ids), dtype=int)
    rank[order] = np.arange(len(ids))
    counts = {identity: ids.count(identity) for identity in set(ids)}
    duplicate = np.asarray([counts[identity] > 1 for identity in ids], dtype=bool)
    duplicate_count = int(sum(count - 1 for count in counts.values() if count > 1))
    rows = np.full((len(ids), 8), np.nan, dtype=float)
    for input_index in range(len(ids)):
        sequence_rank = int(rank[input_index])
        extra = sequence_rank >= pattern.shape[0]
        dither_index = -1 if extra else sequence_rank
        rows[input_index, :3] = (input_index, sequence_rank, dither_index)
        if not extra:
            rows[input_index, 3:5] = pattern[dither_index]
        rows[input_index, 5:] = (int(duplicate[input_index]), int(extra), int(ambiguous))
    extra_count = max(0, len(ids) - pattern.shape[0])
    complete = len(ids) == pattern.shape[0] and duplicate_count == 0 and not ambiguous
    return DitherAssignmentResult(rows, complete, ambiguous, duplicate_count, extra_count)


def refine_relative_offsets(
    nominal_offsets: np.ndarray,
    astrometry_parameters: np.ndarray,
    astrometry_valid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return measured offsets, nominal residuals, and per-member success flags.

    Astrometry parameters are exposure centers ``(ra_deg, dec_deg, pa_deg)``.
    The first valid member defines the relative origin. Invalid members retain
    their nominal offset as an explicit fallback and are marked unsuccessful.
    """

    nominal = np.asarray(nominal_offsets, dtype=float)
    params = np.asarray(astrometry_parameters, dtype=float)
    valid = np.asarray(astrometry_valid, dtype=bool)
    if nominal.ndim != 2 or nominal.shape[1] != 2:
        raise ValueError("nominal_offsets must have shape (member, 2)")
    if params.shape != (nominal.shape[0], 3) or valid.shape != (nominal.shape[0],):
        raise ValueError("astrometry arrays do not match membership")
    valid &= np.isfinite(params[:, :2]).all(axis=1)
    measured = nominal.copy()
    residual = np.full_like(nominal, np.nan)
    success = np.zeros(nominal.shape[0], dtype=np.uint8)
    indices = np.flatnonzero(valid)
    if indices.size:
        reference = int(indices[0])
        ra0, dec0 = params[reference, :2]
        measured[valid, 0] = (params[valid, 0] - ra0) * np.cos(np.deg2rad(dec0)) * 3600.0
        measured[valid, 1] = (params[valid, 1] - dec0) * 3600.0
        residual[valid] = measured[valid] - nominal[valid]
        success[valid] = 1
    return measured, residual, success


def dither_coverage_map(
    fiber_xy: np.ndarray,
    offsets: np.ndarray,
    *,
    fiber_radius_arcsec: float = 0.75,
    grid_step_arcsec: float = 0.25,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rasterize native fiber footprints without combining exposure state."""

    fibers = np.asarray(fiber_xy, dtype=float)
    shifts = np.asarray(offsets, dtype=float)
    if fibers.ndim != 2 or fibers.shape[1] != 2 or shifts.ndim != 2 or shifts.shape[1] != 2:
        raise ValueError("fiber_xy and offsets must both have shape (n, 2)")
    if fiber_radius_arcsec <= 0 or grid_step_arcsec <= 0:
        raise ValueError("coverage radius and grid step must be positive")
    shifts = shifts[np.isfinite(shifts).all(axis=1)]
    if not shifts.size:
        raise ValueError("coverage requires at least one finite offset")
    points = np.concatenate([fibers + shift for shift in shifts], axis=0)
    margin = float(fiber_radius_arcsec + grid_step_arcsec)
    x = np.arange(points[:, 0].min() - margin, points[:, 0].max() + margin, grid_step_arcsec)
    y = np.arange(points[:, 1].min() - margin, points[:, 1].max() + margin, grid_step_arcsec)
    coverage = np.zeros((y.size, x.size), dtype=np.uint16)
    radius_pixels = int(np.ceil(fiber_radius_arcsec / grid_step_arcsec))
    radius2 = float(fiber_radius_arcsec ** 2)
    for px, py in points:
        ix = int(np.rint((px - x[0]) / grid_step_arcsec))
        iy = int(np.rint((py - y[0]) / grid_step_arcsec))
        x0, x1 = max(0, ix - radius_pixels), min(x.size, ix + radius_pixels + 1)
        y0, y1 = max(0, iy - radius_pixels), min(y.size, iy + radius_pixels + 1)
        xx, yy = np.meshgrid(x[x0:x1], y[y0:y1])
        coverage[y0:y1, x0:x1] += ((xx - px) ** 2 + (yy - py) ** 2 <= radius2)
    return coverage, x, y
