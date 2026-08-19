#!/usr/bin/env python3
"""Read-only response-model development workbench for one VIRUS physical CCD.

This is deliberately an analysis script, not a VIRUSFlow Task.  It reads
registered calibration Products and writes only an inspectable work directory.
In particular it never publishes Products or modifies the registry.

Example
-------
python scripts/develop_response_model.py --db run/virusflow.sqlite3 \\
    --zipcode 026+078+318+RU+S --output-dir response_model_RU

The requested amplifier selects the physical CCD automatically (LL/LU or
RU/RL).  The first halo model is intentionally an experiment:
``H(r) = A / (1 + (abs(r) / r0)**alpha)``.  Its constants and the projection
of a five-pixel measurement back to pixels are command-line settings, not
production calibration policy.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from scipy.ndimage import median_filter
from scipy.interpolate import PchipInterpolator, UnivariateSpline
from scipy.optimize import least_squares
from scipy.special import betainc

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose_arc_spectrum import common_arc_spectrum, plot_common_arc_spectrum
from diagnose_observation import load
from virusflow.algorithms.extraction import extract_fractional_aperture, fractional_aperture_geometry
from virusflow.algorithms.fiber import find_peaks
from virusflow.algorithms.physical_ccd import (
    assemble_physical_ccd, fit_gap_scattered_light, physical_trace_map,
)
from virusflow.algorithms.wave import REFERENCE_ARC_WAVELENGTHS
from virusflow.artifacts import ArtifactService
from virusflow.core.identity import ZipCode, parse_zipcode_key


PAIR = {
    "LL": ("left", "LL", "LU"), "LU": ("left", "LL", "LU"),
    "RU": ("right", "RU", "RL"), "RL": ("right", "RU", "RL"),
}
HALO_LINES = (4358.335, 5085.822, 5460.750)


@dataclass(frozen=True)
class AmpProducts:
    zipcode: ZipCode
    master_ldls: dict[str, Any]
    master_arc: dict[str, Any]
    master_sci: dict[str, Any]
    trace_map: dict[str, Any]
    wavelength_map: dict[str, Any]


@dataclass(frozen=True)
class PairedProducts:
    """One coherence-first LDLS/Arc/Science choice for a physical CCD."""

    lower: AmpProducts
    upper: AmpProducts
    selection_evidence: dict[str, Any]


def active_rows(service: ArtifactService, kind: str, zipcode: ZipCode) -> list[dict[str, Any]]:
    """Return newest active rows for one exact amplifier scope."""
    return sorted(
        (row for row in service.adapter.list_all(kind=kind)
         if str(row.get("state") or "active") == "active"
         and str(row.get("amp_key") or "") == zipcode.key()),
        key=lambda row: int(row["id"]), reverse=True,
    )


def parent_ids(service: ArtifactService, row: dict[str, Any]) -> set[int]:
    return {int(value) for value in service.describe(row)["provenance"]["parents"]}


def rows_by_id(service: ArtifactService, ids: set[int], kind: str) -> list[dict[str, Any]]:
    result = []
    for artifact_id in ids:
        row = service.adapter.get_row(artifact_id)
        if row is not None and str(row.get("canonical_kind") or row.get("kind")) == kind:
            result.append(row)
    return result


def metadata(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("metadata") or {})


def calibration_group_id(row: dict[str, Any]) -> str:
    value = metadata(row).get("calibration_group_id")
    if not value:
        raise ValueError(f"artifact {row['id']} has no persisted calibration_group_id")
    return str(value)


def parent_groups(row: dict[str, Any]) -> set[tuple[str, str]]:
    """Return a derived Product's persisted defining CalibrationGroups."""
    values = (metadata(row).get("calibration_group") or {}).get("parent_groups") or ()
    return {(str(kind), str(group_id)) for kind, group_id in values}


def midpoint(row: dict[str, Any]) -> datetime:
    start = datetime.fromisoformat(str(row["validity_start"]))
    end = datetime.fromisoformat(str(row["validity_end"]))
    return start + (end - start) / 2


def contains_time(row: dict[str, Any], when: datetime) -> bool:
    return datetime.fromisoformat(str(row["validity_start"])) <= when <= datetime.fromisoformat(str(row["validity_end"]))


def frame_signature(row: dict[str, Any]) -> tuple[str, ...]:
    """Shared exposure identity, deliberately excluding amplifier raw IDs."""
    frames = (metadata(row).get("calibration_group") or {}).get("frame_membership") or ()
    return tuple(str(item["exposure_id"]) for item in frames)


def state_signature(service: ArtifactService, row: dict[str, Any]) -> tuple[Any, ...]:
    """Scientific input signature comparable across amplifier scopes.

    Root masters retain accepted exposure IDs in ``frame_membership``.  A
    composed Master Arc retains its Hg/Cd parents, so use their exposure sets
    instead.  This is the durable evidence present in currently published
    calibration Products; the per-amplifier ``calibration_group_id`` itself
    intentionally differs because its raw IDs differ by amplifier.
    """
    frames = frame_signature(row)
    if frames:
        return ("frames", frames)
    parents = []
    for artifact_id in parent_ids(service, row):
        parent = service.adapter.get_row(artifact_id)
        if parent is None:
            continue
        kind = str(parent.get("canonical_kind") or parent.get("kind"))
        if kind in {"master_hg", "master_cd"}:
            parents.append((kind, frame_signature(parent)))
    if parents:
        return ("composed", tuple(sorted(parents)))
    raise ValueError(
        f"artifact {row['id']} ({row.get('canonical_kind') or row.get('kind')}) "
        "has no comparable persisted exposure-state evidence"
    )


def defining_child(
    service: ArtifactService,
    *,
    kind: str,
    zipcode: ZipCode,
    parent: dict[str, Any],
    required_parent_groups: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Find a derived active product with both forms of defining evidence."""
    result = []
    for child in active_rows(service, kind, zipcode):
        if int(parent["id"]) not in parent_ids(service, child):
            continue
        if not required_parent_groups.issubset(parent_groups(child)):
            continue
        result.append(child)
    return result


def amp_ldls_states(service: ArtifactService, zipcode: ZipCode) -> list[dict[str, dict[str, Any]]]:
    states = []
    for ldls in active_rows(service, "master_ldls", zipcode):
        defining = {("master_ldls", calibration_group_id(ldls))}
        for trace in defining_child(
            service, kind="trace_map", zipcode=zipcode, parent=ldls,
            required_parent_groups=defining,
        ):
            states.append({"master_ldls": ldls, "trace_map": trace})
    return states


def amp_arc_states(
    service: ArtifactService, zipcode: ZipCode, trace: dict[str, Any],
) -> list[dict[str, dict[str, Any]]]:
    """Find Arc/wavelength states using Arc and trace as separate inputs."""
    states = []
    trace_group = calibration_group_id(trace)
    for arc in active_rows(service, "master_arc", zipcode):
        defining = {
            ("master_arc", calibration_group_id(arc)),
            ("trace_map", trace_group),
        }
        for wave in defining_child(
            service, kind="wavelength_map", zipcode=zipcode, parent=arc,
            required_parent_groups=defining,
        ):
            # The direct trace parent is a consistency check for the map used
            # below.  It does not make trace part of the Arc measurement state.
            if int(trace["id"]) in parent_ids(service, wave):
                states.append({"master_arc": arc, "wavelength_map": wave})
    return states


def matched_pairs(
    service: ArtifactService, lower_states: list[dict[str, dict[str, Any]]],
    upper_states: list[dict[str, dict[str, Any]]], root_kind: str,
) -> list[tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], tuple[Any, ...]]]:
    pairs = []
    for lower_state in lower_states:
        lower_signature = state_signature(service, lower_state[root_kind])
        for upper_state in upper_states:
            upper_signature = state_signature(service, upper_state[root_kind])
            if lower_signature == upper_signature:
                pairs.append((lower_state, upper_state, lower_signature))
    return pairs


def choose_paired_products(
    service: ArtifactService, lower_zipcode: ZipCode, upper_zipcode: ZipCode,
    *, max_arc_separation_hours: float,
) -> PairedProducts:
    """Resolve paired states: equal measurement evidence first, time second."""
    lower_ldls = amp_ldls_states(service, lower_zipcode)
    upper_ldls = amp_ldls_states(service, upper_zipcode)
    ldls_pairs = matched_pairs(service, lower_ldls, upper_ldls, "master_ldls")
    if not ldls_pairs:
        raise RuntimeError("no physical-CCD LDLS/trace pair with matching accepted exposure state")

    science_pairs = matched_pairs(
        service,
        [{"master_sci": row} for row in active_rows(service, "master_sci", lower_zipcode)],
        [{"master_sci": row} for row in active_rows(service, "master_sci", upper_zipcode)],
        "master_sci",
    )
    if not science_pairs:
        raise RuntimeError("no physical-CCD Master Science pair with matching accepted exposure state")

    options = []
    for lower_ldls_state, upper_ldls_state, ldls_signature in ldls_pairs:
        lower_arc = amp_arc_states(service, lower_zipcode, lower_ldls_state["trace_map"])
        upper_arc = amp_arc_states(service, upper_zipcode, upper_ldls_state["trace_map"])
        for lower_arc_state, upper_arc_state, arc_signature in matched_pairs(
            service, lower_arc, upper_arc, "master_arc"
        ):
            ldls_time = midpoint(lower_ldls_state["master_ldls"])
            arc_time = midpoint(lower_arc_state["master_arc"])
            separation_hours = abs((arc_time - ldls_time).total_seconds()) / 3600.0
            if separation_hours > float(max_arc_separation_hours):
                continue
            for lower_sci_state, upper_sci_state, science_signature in science_pairs:
                science = lower_sci_state["master_sci"]
                if not contains_time(science, ldls_time) or not contains_time(science, arc_time):
                    continue
                options.append((
                    separation_hours, -ldls_time.timestamp(), -arc_time.timestamp(),
                    lower_ldls_state, upper_ldls_state, lower_arc_state, upper_arc_state,
                    lower_sci_state, upper_sci_state, ldls_signature, arc_signature,
                    science_signature,
                ))
    if not options:
        raise RuntimeError(
            "no coherent physical-CCD state has an Arc within the configured LDLS/Arc "
            "time tolerance and a Master Science validity interval containing both"
        )
    chosen = min(options, key=lambda value: value[:3])
    (_, _, _, lower_ldls_state, upper_ldls_state, lower_arc_state, upper_arc_state,
     lower_sci_state, upper_sci_state, ldls_signature, arc_signature,
     science_signature) = chosen
    lower = AmpProducts(
        lower_zipcode, lower_ldls_state["master_ldls"], lower_arc_state["master_arc"],
        lower_sci_state["master_sci"], lower_ldls_state["trace_map"],
        lower_arc_state["wavelength_map"],
    )
    upper = AmpProducts(
        upper_zipcode, upper_ldls_state["master_ldls"], upper_arc_state["master_arc"],
        upper_sci_state["master_sci"], upper_ldls_state["trace_map"],
        upper_arc_state["wavelength_map"],
    )
    return PairedProducts(lower, upper, {
        "coherence_rule": "matching accepted exposure-state signatures across physical-CCD amplifiers",
        "ldls_signature": ldls_signature,
        "arc_signature": arc_signature,
        "science_signature": science_signature,
        "ldls_arc_separation_hours": float(chosen[0]),
        "science_validity_contains_ldls_and_arc": True,
        "selection_order": "coherence first; minimum LDLS/Arc temporal separation; newest tie-break",
    })


def detector_mask(service: ArtifactService, product: dict[str, Any], image: np.ndarray, *, ldls: bool) -> np.ndarray:
    """Use the same finite/LDLS/dark input-mask ideas as master extraction."""
    mask = ~np.isfinite(image)
    if ldls:
        mask |= np.asarray(load(service, product, "flat_response_mask"), dtype=bool)
    for artifact_id in parent_ids(service, product):
        parent = service.adapter.get_row(artifact_id)
        if parent is not None and str(parent.get("canonical_kind") or parent.get("kind")) == "master_dark":
            try:
                mask |= np.asarray(load(service, parent, "dark_pixel_mask"), dtype=bool)
            except KeyError:
                pass
    return mask.astype(np.uint8)


def assemble_pair(service: ArtifactService, lower: AmpProducts, upper: AmpProducts, component: str, *, subtract_gap_baseline: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load, assemble, and optionally baseline-subtract a pair without mutation."""
    lower_image = np.asarray(load(service, getattr(lower, component), component), dtype=np.float32)
    upper_image = np.asarray(load(service, getattr(upper, component), component), dtype=np.float32)
    lower_mask = detector_mask(service, getattr(lower, component), lower_image, ldls=component == "master_ldls")
    upper_mask = detector_mask(service, getattr(upper, component), upper_image, ldls=component == "master_ldls")
    side, lower_amp, upper_amp = PAIR[lower.zipcode.amp]
    assembly = assemble_physical_ccd(
        lower_image, upper_image, side=side, lower_amp=lower_amp, upper_amp=upper_amp,
        lower_variance=np.zeros_like(lower_image), upper_variance=np.zeros_like(upper_image),
        lower_mask=lower_mask, upper_mask=upper_mask,
    )
    image = np.asarray(assembly.get_array("image"), dtype=np.float32)
    mask = np.asarray(assembly.get_array("pixel_mask"), dtype=bool)
    if subtract_gap_baseline:
        scatter = fit_gap_scattered_light(
            assembly,
            load(service, lower.trace_map, "fiber_trace_map"),
            load(service, upper.trace_map, "fiber_trace_map"),
        )
        image = np.asarray(scatter.get_array("scatter_subtracted_image"), dtype=np.float32)
    return image, np.zeros_like(image, dtype=np.float32), mask


def smooth_spectra(spectrum: np.ndarray, window: int) -> np.ndarray:
    """Per-fiber robust preliminary LDLS spectra for profile normalization."""
    width = max(3, int(window) // 2 * 2 + 1)
    result = np.full_like(spectrum, np.nan, dtype=float)
    for fiber, values in enumerate(np.asarray(spectrum, dtype=float)):
        finite = np.isfinite(values)
        if finite.sum() < width:
            continue
        filled = np.interp(np.arange(values.size), np.flatnonzero(finite), values[finite])
        result[fiber] = median_filter(filled, size=width, mode="nearest")
    return result


def trapezoidal_integral(values: np.ndarray, coordinates: np.ndarray) -> float:
    """Integrate sampled values with the trapezoidal rule without NumPy API dependencies."""
    values = np.asarray(values, dtype=float)
    coordinates = np.asarray(coordinates, dtype=float)
    if values.size < 2 or coordinates.size < 2:
        return 0.0
    return float(np.sum(0.5 * (values[1:] + values[:-1]) * np.diff(coordinates)))


def gauss_hermite_convolved_circular_fiber_profile(
    coordinate: np.ndarray,
    *,
    height: float,
    radius: float,
    sigma: float,
    h3: float,
    h4: float,
    center: float,
    left_support_u: float,
    right_support_u: float,
    step: float,
) -> np.ndarray | None:
    """Return a circular-fiber profile blurred by a normalized GH kernel.

    The kernel uses normalized probabilists' Hermite polynomials, so ``h3``
    is odd (skewness-like) and ``h4`` is even (kurtosis-like).  A non-negative
    sampled kernel is required for this compact optical-blur experiment.
    """
    from scipy.ndimage import convolve1d

    if radius <= 0.0 or sigma <= 0.0 or step <= 0.0:
        raise ValueError("radius, sigma, and step must be positive")
    grid = np.arange(left_support_u, right_support_u + step / 2.0, step)
    # Fixed q=0 circular-aperture source model.
    core = np.sqrt(np.clip(radius ** 2 - (grid - center) ** 2, 0.0, None))
    kernel_half_width = int(np.ceil(5.0 * sigma / step))
    kernel_coordinate = np.arange(-kernel_half_width, kernel_half_width + 1) * step
    standardized = kernel_coordinate / sigma
    hermite3 = (standardized ** 3 - 3.0 * standardized) / np.sqrt(6.0)
    hermite4 = (standardized ** 4 - 6.0 * standardized ** 2 + 3.0) / np.sqrt(24.0)
    kernel = np.exp(-0.5 * standardized ** 2) * (1.0 + h3 * hermite3 + h4 * hermite4)
    if not np.all(np.isfinite(kernel)) or np.any(kernel < 0.0):
        return None
    kernel_sum = float(np.sum(kernel))
    if not np.isfinite(kernel_sum) or kernel_sum <= 0.0:
        return None
    kernel /= kernel_sum
    blurred = convolve1d(core, kernel, mode="constant", cval=0.0)
    blurred /= max(float(np.max(blurred)), np.finfo(float).tiny)
    coordinate = np.asarray(coordinate, dtype=float)
    return np.where(
        (coordinate >= left_support_u) & (coordinate <= right_support_u),
        height * np.interp(coordinate, grid, blurred),
        0.0,
    )


@dataclass(frozen=True)
class LocalProfileMap:
    """Empirical LDLS profiles indexed by dispersion chunk and fiber group."""

    u_grid: np.ndarray
    density: np.ndarray
    chunk_columns: np.ndarray
    group_bounds: np.ndarray
    fiber_group: np.ndarray
    sample_count: np.ndarray
    valley_constraint_count: np.ndarray
    bin_count: np.ndarray
    bin_scatter: np.ndarray
    bin_used: np.ndarray
    peak_u: np.ndarray
    centroid_offset: np.ndarray
    height: np.ndarray
    radius: np.ndarray
    sigma: np.ndarray
    profile_integral: np.ndarray
    h3: np.ndarray
    h4: np.ndarray
    bin_model_rms: np.ndarray
    bin_model_weighted_rms: np.ndarray
    optimizer_status: np.ndarray
    optimizer_cost: np.ndarray
    parameter_at_bound: np.ndarray
    left_valley_u: np.ndarray
    right_valley_u: np.ndarray
    left_inflection_u: np.ndarray
    right_inflection_u: np.ndarray

    def indices(self, fiber: int, column: int) -> tuple[int, int]:
        group = int(self.fiber_group[int(fiber)])
        chunk = int(np.searchsorted(self.chunk_columns[:, 1], int(column), side="left"))
        return min(chunk, self.density.shape[0] - 1), group

    def profile(self, fiber: int, column: int) -> np.ndarray:
        chunk, group = self.indices(fiber, column)
        return self.density[chunk, group]


@dataclass(frozen=True)
class ProfileEvidence:
    """One immutable detector-sample collection for all local-profile passes."""

    u: np.ndarray
    signal: np.ndarray
    fiber: np.ndarray
    column: np.ndarray
    chunk: np.ndarray
    group: np.ndarray
    five_pixel_normalization: np.ndarray
    seed_core: np.ndarray
    neighbor_fiber: np.ndarray
    neighbor_u: np.ndarray
    neighbor_overlaps: np.ndarray
    valley_signal: np.ndarray
    valley_first_fiber: np.ndarray
    valley_second_fiber: np.ndarray
    valley_column: np.ndarray
    valley_chunk: np.ndarray
    valley_group: np.ndarray
    valley_first_u: np.ndarray
    valley_second_u: np.ndarray


def _profile_layout(trace: np.ndarray, *, support: float, chunk_width: int, group_size: int) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    """Build fixed local chunks and groups, splitting groups at physical gaps."""
    nx = trace.shape[1]
    chunks = [np.arange(start, min(nx, start + int(chunk_width))) for start in range(0, nx, int(chunk_width))]
    groups: list[np.ndarray] = []
    group_start = 0
    for fiber in range(1, trace.shape[0]):
        separation = np.abs(trace[fiber] - trace[fiber - 1])
        has_gap = bool(np.nanmedian(separation) > 2.0 * support)
        if fiber - group_start >= int(group_size) or has_gap:
            groups.append(np.arange(group_start, fiber))
            group_start = fiber
    groups.append(np.arange(group_start, trace.shape[0]))
    fiber_group = np.empty(trace.shape[0], dtype=np.int16)
    for group_index, fibers in enumerate(groups):
        fiber_group[fibers] = group_index
    return chunks, groups, fiber_group


def collect_profile_evidence(
    image: np.ndarray,
    trace: np.ndarray,
    five_pixel_normalization: np.ndarray,
    mask: np.ndarray,
    *,
    support: float,
    chunks: list[np.ndarray],
    groups: list[np.ndarray],
    fiber_group: np.ndarray,
) -> ProfileEvidence:
    """Collect every valid profile pixel and overlap/valley fact exactly once."""
    ny, nx = image.shape
    chunk_for_column = np.empty(nx, dtype=np.int16)
    for chunk_index, columns in enumerate(chunks):
        chunk_for_column[columns] = chunk_index
    pair_overlap = np.zeros((trace.shape[0] - 1, nx), dtype=bool)
    valley_row = np.full((trace.shape[0] - 1, nx), np.nan, dtype=float)
    for first in range(trace.shape[0] - 1):
        for column in range(nx):
            first_center, second_center = trace[first, column], trace[first + 1, column]
            if not (np.isfinite(first_center) and np.isfinite(second_center) and abs(second_center - first_center) <= 2.0 * support):
                continue
            pair_overlap[first, column] = True
            low = max(0, int(np.ceil(min(first_center, second_center))))
            high = min(ny, int(np.floor(max(first_center, second_center))) + 1)
            if high - low < 2:
                continue
            rows = np.arange(low, high)
            usable = ~mask[rows, column] & np.isfinite(image[rows, column])
            if np.any(usable):
                valley_row[first, column] = float(rows[usable][np.argmin(image[rows[usable], column])])

    values: dict[str, list[np.ndarray]] = {name: [] for name in (
        "u", "signal", "fiber", "column", "chunk", "group", "normalization", "seed_core",
        "neighbor_fiber", "neighbor_u", "neighbor_overlaps",
    )}
    for fiber in range(trace.shape[0]):
        for column in range(nx):
            center = trace[fiber, column]
            normalization = five_pixel_normalization[fiber, column]
            if not (np.isfinite(center) and np.isfinite(normalization) and normalization > 0.0):
                continue
            lo = max(0, int(np.floor(center - support)))
            hi = min(ny, int(np.ceil(center + support)) + 1)
            rows = np.arange(lo, hi)
            good = ~mask[rows, column] & np.isfinite(image[rows, column])
            if not np.any(good):
                continue
            rows = rows[good]
            lower_valley = valley_row[fiber - 1, column] if fiber > 0 and pair_overlap[fiber - 1, column] else np.nan
            upper_valley = valley_row[fiber, column] if fiber + 1 < trace.shape[0] and pair_overlap[fiber, column] else np.nan
            core = np.ones(rows.size, dtype=bool)
            if np.isfinite(lower_valley):
                core &= rows >= lower_valley
            if np.isfinite(upper_valley):
                core &= rows <= upper_valley
            neighbor_fiber = np.full((rows.size, 2), -1, dtype=np.int16)
            neighbor_u = np.full((rows.size, 2), np.nan, dtype=np.float32)
            neighbor_overlaps = np.zeros((rows.size, 2), dtype=bool)
            for slot, neighbor in enumerate((fiber - 1, fiber + 1)):
                if neighbor < 0 or neighbor >= trace.shape[0]:
                    continue
                pair = min(fiber, neighbor)
                if not pair_overlap[pair, column]:
                    continue
                neighbor_fiber[:, slot] = neighbor
                neighbor_u[:, slot] = rows - trace[neighbor, column]
                neighbor_overlaps[:, slot] = True
            values["u"].append((rows - center).astype(np.float32))
            values["signal"].append(np.asarray(image[rows, column], dtype=np.float32))
            values["fiber"].append(np.full(rows.size, fiber, dtype=np.int16))
            values["column"].append(np.full(rows.size, column, dtype=np.int16))
            values["chunk"].append(np.full(rows.size, chunk_for_column[column], dtype=np.int16))
            values["group"].append(np.full(rows.size, fiber_group[fiber], dtype=np.int16))
            values["normalization"].append(np.full(rows.size, normalization, dtype=np.float32))
            values["seed_core"].append(core)
            values["neighbor_fiber"].append(neighbor_fiber)
            values["neighbor_u"].append(neighbor_u)
            values["neighbor_overlaps"].append(neighbor_overlaps)

    valley_values: dict[str, list[float | int]] = {name: [] for name in (
        "signal", "first", "second", "column", "chunk", "group", "first_u", "second_u",
    )}
    for first in range(trace.shape[0] - 1):
        second = first + 1
        if fiber_group[first] != fiber_group[second]:
            continue
        for column in range(nx):
            row = valley_row[first, column]
            if not np.isfinite(row):
                continue
            first_norm = five_pixel_normalization[first, column]
            second_norm = five_pixel_normalization[second, column]
            if not (np.isfinite(first_norm) and np.isfinite(second_norm) and first_norm > 0.0 and second_norm > 0.0):
                continue
            valley_values["signal"].append(float(image[int(row), column]))
            valley_values["first"].append(first)
            valley_values["second"].append(second)
            valley_values["column"].append(column)
            valley_values["chunk"].append(int(chunk_for_column[column]))
            valley_values["group"].append(int(fiber_group[first]))
            valley_values["first_u"].append(float(row - trace[first, column]))
            valley_values["second_u"].append(float(row - trace[second, column]))

    return ProfileEvidence(
        u=np.concatenate(values["u"]), signal=np.concatenate(values["signal"]),
        fiber=np.concatenate(values["fiber"]), column=np.concatenate(values["column"]),
        chunk=np.concatenate(values["chunk"]), group=np.concatenate(values["group"]),
        five_pixel_normalization=np.concatenate(values["normalization"]),
        seed_core=np.concatenate(values["seed_core"]),
        neighbor_fiber=np.concatenate(values["neighbor_fiber"]),
        neighbor_u=np.concatenate(values["neighbor_u"]),
        neighbor_overlaps=np.concatenate(values["neighbor_overlaps"]),
        valley_signal=np.asarray(valley_values["signal"], dtype=np.float32),
        valley_first_fiber=np.asarray(valley_values["first"], dtype=np.int16),
        valley_second_fiber=np.asarray(valley_values["second"], dtype=np.int16),
        valley_column=np.asarray(valley_values["column"], dtype=np.int16),
        valley_chunk=np.asarray(valley_values["chunk"], dtype=np.int16),
        valley_group=np.asarray(valley_values["group"], dtype=np.int16),
        valley_first_u=np.asarray(valley_values["first_u"], dtype=np.float32),
        valley_second_u=np.asarray(valley_values["second_u"], dtype=np.float32),
    )


def robust_profile_bins(u: np.ndarray, values: np.ndarray, *, support: float, bin_width: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return robust bin medians, counts, and scatter without fitting a curve."""
    centers = np.arange(-support + bin_width / 2.0, support, bin_width)
    edges = np.r_[centers - bin_width / 2.0, centers[-1] + bin_width / 2.0]
    median = np.full(centers.shape, np.nan)
    scatter = np.full(centers.shape, np.nan)
    count = np.zeros(centers.shape, dtype=np.int32)
    indices = np.digitize(u, edges) - 1
    for index in range(centers.size):
        selected = values[(indices == index) & np.isfinite(values)]
        if selected.size:
            location = float(np.median(selected))
            scale = float(1.4826 * np.median(np.abs(selected - location)))
            if np.isfinite(scale) and scale > 0.0:
                selected = selected[np.abs(selected - location) <= 5.0 * scale]
            count[index] = selected.size
            median[index] = np.median(selected)
            scatter[index] = 1.4826 * np.median(np.abs(selected - median[index]))
    return centers, median, scatter, count


def fit_constrained_profile(
    u: np.ndarray,
    values: np.ndarray,
    *,
    support: float,
    bin_width: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, float | int | bool]]:
    """Regularize robust bins with a circular source and GH optical blur.

    A weighted smoothing spline absorbs bin-level noise and supplies the
    provisional peak and valley locations.  The compact fit retains the
    circular-aperture source while allowing only low-order, non-negative
    Gauss-Hermite perturbations of the optical blur.
    """
    centers, median, scatter, count = robust_profile_bins(u, values, support=support, bin_width=bin_width)
    finite = np.isfinite(median) & np.isfinite(scatter) & (count >= 8)
    if finite.sum() < 8:
        raise ValueError("insufficient robust local-profile bins")
    peak_scale = float(np.nanmax(np.abs(median[finite])))
    noise = max(float(np.nanmedian(scatter[finite])), peak_scale * 1e-4, 1e-8)
    excessive_scatter = scatter > max(5.0 * noise, 0.5 * peak_scale)
    used = finite & ~excessive_scatter
    if used.sum() < 8:
        used = finite
    weights = np.sqrt(count[used]) / np.maximum(scatter[used], noise)
    try:
        smooth = UnivariateSpline(centers[used], median[used], w=weights, k=3, s=1.5 * used.sum())
        provisional = smooth(centers)
    except Exception:
        provisional = PchipInterpolator(centers[used], median[used], extrapolate=False)(centers)
    provisional = np.nan_to_num(provisional, nan=0.0, posinf=0.0, neginf=0.0)
    provisional = np.clip(provisional, 0.0, None)
    peak_index = int(np.argmax(provisional))
    if peak_index == 0 or peak_index == centers.size - 1:
        raise ValueError("local profile peak is not bracketed by detector evidence")
    if (used[:peak_index].sum() < 4 or used[peak_index + 1:].sum() < 4):
        raise ValueError("local profile lacks two-sided detector support")
    peak = float(provisional[peak_index])
    level = max(3.0 * noise, 0.003 * peak)
    left_active = np.flatnonzero((np.arange(centers.size) < peak_index) & (provisional >= level))
    right_active = np.flatnonzero((np.arange(centers.size) > peak_index) & (provisional >= level))
    left_valley_index = max(0, int(left_active[0]) - 1) if left_active.size else 0
    right_valley_index = min(centers.size - 1, int(right_active[-1]) + 1) if right_active.size else centers.size - 1
    # These are inferred inter-fiber valley locations for diagnostics and
    # overlap constraints.  They are not individual-profile endpoints.
    left_valley_u = float(centers[left_valley_index])
    right_valley_u = float(centers[right_valley_index])
    left_support_u = float(centers[0])
    right_support_u = float(centers[-1])
    peak_u = float(centers[peak_index])
    if not (left_support_u < peak_u < right_support_u):
        raise ValueError("local profile support does not bracket the peak")
    fit_u = centers[used]
    fit_values = np.clip(median[used], 0.0, None)
    fit_weights = weights

    def branch_model(theta: np.ndarray, coordinate: np.ndarray) -> np.ndarray | None:
        height, radius, sigma = np.exp(theta[:3])
        center = peak_u + theta[3]
        h3, h4 = theta[4:6]
        step = max(bin_width / 4.0, 0.01)
        fine_grid = np.arange(left_support_u, right_support_u + step / 2.0, step)
        continuous = gauss_hermite_convolved_circular_fiber_profile(
            fine_grid,
            height=height,
            radius=radius,
            sigma=sigma,
            h3=h3,
            h4=h4,
            center=center,
            left_support_u=left_support_u,
            right_support_u=right_support_u,
            step=step,
        )
        if continuous is None:
            return None
        # Integrate the continuous optical profile over the one-pixel detector
        # response before sampling at trace-relative pixel centers.  Cell
        # overlaps give a unit-area top-hat even when ``step`` does not divide
        # a pixel width exactly.
        from scipy.ndimage import convolve1d
        half_width = int(np.ceil(0.5 / step))
        offsets = np.arange(-half_width, half_width + 1) * step
        pixel_kernel = np.clip(
            np.minimum(offsets + step / 2.0, 0.5)
            - np.maximum(offsets - step / 2.0, -0.5),
            0.0,
            None,
        )
        pixel_kernel /= np.sum(pixel_kernel)
        pixel_integrated = convolve1d(continuous, pixel_kernel, mode="constant", cval=0.0)
        return np.interp(coordinate, fine_grid, pixel_integrated, left=0.0, right=0.0)

    # Keep the existing height, R, sigma, and du parameters.  The circular
    # source is fixed at q=0; h3 and h4 perturb only the optical blur kernel.
    initial = np.r_[np.log([max(peak, noise), 2.0, 0.8]), 0.0, 0.0, 0.0]
    lower = np.r_[np.log([max(peak * 0.25, noise), 0.15, 0.03]), -0.5, -0.2, -0.2]
    upper = np.r_[np.log([max(peak * 4.0, noise * 4.0), support, support / 2.0]), 0.5, 0.2, 0.2]

    def residual(theta: np.ndarray) -> np.ndarray:
        model = branch_model(theta, fit_u)
        if model is None:
            # Invalid Gauss-Hermite kernels are deliberately outside the fit.
            return np.full(fit_values.shape, 1e12, dtype=float)
        return (model - fit_values) * fit_weights

    result = least_squares(
        residual,
        initial, bounds=(lower, upper), loss="soft_l1",
    )
    regularized = branch_model(result.x, centers)
    if regularized is None:
        raise ValueError("constrained local profile has a negative Gauss-Hermite blur kernel")
    integral = trapezoidal_integral(regularized, centers)
    if not np.isfinite(integral) or integral <= 0.0:
        raise ValueError("constrained local profile has non-positive integral")
    # Commented out normalization to avoid fit to integration inconsistencies
    #regularized /= integral
    du = float(result.x[3])
    h3, h4 = map(float, result.x[4:6])
    model_at_bins = np.interp(fit_u, centers, regularized)
    bin_residual = model_at_bins - fit_values
    bin_model_rms = float(np.sqrt(np.mean(bin_residual ** 2)))
    bin_model_weighted_rms = float(
        np.sqrt(np.sum((fit_weights * bin_residual) ** 2) / np.sum(fit_weights ** 2))
    )
    parameter_at_bound = bool(
        np.any(result.active_mask != 0)
        or np.any(np.isclose(result.x, lower, rtol=0.0, atol=1e-8))
        or np.any(np.isclose(result.x, upper, rtol=0.0, atol=1e-8))
    )
    peak_index = int(np.argmax(regularized))
    left_transition = left_valley_u
    right_transition = right_valley_u
    topology = {
        "peak_u": peak_u + du,
        "centroid_offset": du,
        "height": float(np.exp(result.x[0])),
        "radius": float(np.exp(result.x[1])),
        "sigma": float(np.exp(result.x[2])),
        "profile_integral": integral,
        "h3": h3,
        "h4": h4,
        "bin_model_rms": bin_model_rms,
        "bin_model_weighted_rms": bin_model_weighted_rms,
        "optimizer_status": int(result.status),
        "optimizer_cost": float(result.cost),
        "parameter_at_bound": parameter_at_bound,
        "left_valley_u": left_valley_u,
        "right_valley_u": right_valley_u,
        "left_support_u": left_support_u,
        "right_support_u": right_support_u,
        "left_inflection_u": float(left_transition),
        "right_inflection_u": float(right_transition),
    }
    return centers, regularized, count, scatter, used, topology


def _fill_missing_local_profiles(profiles: np.ndarray, *arrays: np.ndarray) -> tuple[np.ndarray, ...]:
    """Borrow only the nearest chunk/group profile when a local fit is sparse."""
    result = profiles.copy()
    results = [array.copy() for array in arrays]
    available = np.isfinite(result).all(axis=-1)
    if not np.any(available):
        raise ValueError("no usable local LDLS profile fits")
    for chunk, group in np.argwhere(~available):
        distances = np.abs(np.argwhere(available) - np.asarray([chunk, group])).sum(axis=1)
        nearest_chunk, nearest_group = np.argwhere(available)[int(np.argmin(distances))]
        result[chunk, group] = result[nearest_chunk, nearest_group]
        for array in results:
            array[chunk, group] = array[nearest_chunk, nearest_group]
    return (result, *results)


def _profile_sample_values(
    evidence: ProfileEvidence,
    selection: np.ndarray,
    compact_total: np.ndarray,
    profiles: LocalProfileMap | None,
) -> np.ndarray:
    """Interpret fixed detector evidence with the current overlap model."""
    indices = np.flatnonzero(selection)
    value = evidence.signal[indices].astype(float)
    if profiles is not None:
        for slot in range(2):
            neighbor = evidence.neighbor_fiber[indices, slot]
            overlaps = evidence.neighbor_overlaps[indices, slot] & (neighbor >= 0)
            if not np.any(overlaps):
                continue
            for neighbor_group in np.unique(profiles.fiber_group[neighbor[overlaps]]):
                use = overlaps & (profiles.fiber_group[np.maximum(neighbor, 0)] == neighbor_group)
                if not np.any(use):
                    continue
                chunk = int(evidence.chunk[indices[use][0]])
                shape = np.interp(
                    evidence.neighbor_u[indices[use], slot], profiles.u_grid,
                    profiles.density[chunk, int(neighbor_group)], left=0.0, right=0.0,
                )
                value[use] -= compact_total[neighbor[use], evidence.column[indices[use]]] * shape
    normalization = compact_total[evidence.fiber[indices], evidence.column[indices]]
    return np.divide(value, normalization, out=np.full(value.shape, np.nan), where=np.isfinite(normalization) & (normalization > 0.0))


def _valley_constraints(
    evidence: ProfileEvidence,
    chunk: int,
    group: int,
    compact_total: np.ndarray,
    profiles: LocalProfileMap | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Split the fixed observed valleys according to the current local maps."""
    if profiles is None:
        return np.empty(0, dtype=float), np.empty(0, dtype=float)
    use = (evidence.valley_chunk == chunk) & (evidence.valley_group == group)
    if not np.any(use):
        return np.empty(0, dtype=float), np.empty(0, dtype=float)
    first = evidence.valley_first_fiber[use]
    second = evidence.valley_second_fiber[use]
    column = evidence.valley_column[use]
    first_group = profiles.fiber_group[first]
    second_group = profiles.fiber_group[second]
    first_profile = np.empty(first.size, dtype=float)
    second_profile = np.empty(second.size, dtype=float)
    for profile_group in np.unique(first_group):
        selected = first_group == profile_group
        first_profile[selected] = np.interp(evidence.valley_first_u[use][selected], profiles.u_grid, profiles.density[chunk, profile_group], left=0.0, right=0.0)
    for profile_group in np.unique(second_group):
        selected = second_group == profile_group
        second_profile[selected] = np.interp(evidence.valley_second_u[use][selected], profiles.u_grid, profiles.density[chunk, profile_group], left=0.0, right=0.0)
    first_total = compact_total[first, column]
    second_total = compact_total[second, column]
    denominator = first_total * first_profile + second_total * second_profile
    valid = np.isfinite(denominator) & (denominator > 0.0)
    if not np.any(valid):
        return np.empty(0, dtype=float), np.empty(0, dtype=float)
    observed = evidence.valley_signal[use][valid]
    return (
        np.r_[evidence.valley_first_u[use][valid], evidence.valley_second_u[use][valid]],
        np.r_[observed * first_profile[valid] / denominator[valid], observed * second_profile[valid] / denominator[valid]],
    )


def _profile_fit_inputs(
    evidence: ProfileEvidence,
    selection: np.ndarray,
    compact_total: np.ndarray,
    neighbor_profiles: LocalProfileMap | None,
    *,
    chunk: int,
    group: int,
    valley_weight: int,
    seed_core_only: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build the exact detector and valley samples supplied to one profile fit."""
    detector_u = evidence.u[selection]
    detector_values = _profile_sample_values(
        evidence, selection, compact_total, neighbor_profiles,
    )
    if seed_core_only:
        core = evidence.seed_core[selection]
        detector_u = detector_u[core]
        detector_values = detector_values[core]
    valley_u, valley_values = _valley_constraints(
        evidence, chunk, group, compact_total, neighbor_profiles,
    )
    fit_u = np.r_[detector_u, np.repeat(valley_u, max(1, int(valley_weight)))]
    fit_values = np.r_[detector_values, np.repeat(valley_values, max(1, int(valley_weight)))]
    return detector_u, detector_values, valley_u, valley_values, fit_u, fit_values


def measure_local_profiles(
    evidence: ProfileEvidence,
    trace: np.ndarray,
    five_pixel_normalization: np.ndarray,
    *,
    detector_rows: int,
    aperture_width: float,
    support: float,
    bin_width: float,
    chunk_width: int,
    group_size: int,
    iterations: int,
    valley_weight: int,
) -> tuple[LocalProfileMap, LocalProfileMap, np.ndarray, int, bool, float, float, list[dict[str, float | int]]]:
    """Fit local maps from fixed evidence, updating only its interpretation."""
    chunks, groups, fiber_group = _profile_layout(trace, support=support, chunk_width=chunk_width, group_size=group_size)
    u_grid = np.arange(-support + bin_width / 2.0, support, bin_width)
    profiles = np.full((len(chunks), len(groups), u_grid.size), np.nan, dtype=float)
    sample_count = np.zeros((len(chunks), len(groups)), dtype=np.int32)
    valley_count = np.zeros((len(chunks), len(groups)), dtype=np.int32)
    bin_count = np.zeros_like(profiles, dtype=np.int32)
    bin_scatter = np.full_like(profiles, np.nan)
    bin_used = np.zeros_like(profiles, dtype=bool)
    peak_u = np.full((len(chunks), len(groups)), np.nan)
    centroid_offset = np.full_like(peak_u, np.nan)
    height = np.full_like(peak_u, np.nan)
    radius = np.full_like(peak_u, np.nan)
    sigma = np.full_like(peak_u, np.nan)
    profile_integral = np.full_like(peak_u, np.nan)
    h3 = np.full_like(peak_u, np.nan)
    h4 = np.full_like(peak_u, np.nan)
    bin_model_rms = np.full_like(peak_u, np.nan)
    bin_model_weighted_rms = np.full_like(peak_u, np.nan)
    optimizer_status = np.zeros(peak_u.shape, dtype=np.int16)
    optimizer_cost = np.full_like(peak_u, np.nan)
    parameter_at_bound = np.zeros(peak_u.shape, dtype=bool)
    left_valley_u = np.full_like(peak_u, np.nan)
    right_valley_u = np.full_like(peak_u, np.nan)
    left_inflection_u = np.full_like(peak_u, np.nan)
    right_inflection_u = np.full_like(peak_u, np.nan)
    compact_total = five_pixel_normalization.copy()
    previous: LocalProfileMap | None = None
    completed_iterations = 0
    converged = False
    capture_change = float("inf")
    shape_change = float("inf")
    iteration_integral_stats: list[dict[str, float | int]] = []
    for iteration in range(max(1, int(iterations) + 1)):
        completed_iterations = iteration + 1
        updated = np.full_like(profiles, np.nan)
        for chunk_index in range(len(chunks)):
            for group_index in range(len(groups)):
                selected = (evidence.chunk == chunk_index) & (evidence.group == group_index)
                sample_count[chunk_index, group_index] = int(selected.sum())
                _, _, valley_u, _, sample_u, values = _profile_fit_inputs(
                    evidence, selected, compact_total, previous,
                    chunk=chunk_index, group=group_index,
                    valley_weight=valley_weight, seed_core_only=previous is None,
                )
                valley_count[chunk_index, group_index] = valley_u.size
                try:
                    fitted_u, fitted, counts, scatter, used, topology = fit_constrained_profile(sample_u, values, support=support, bin_width=bin_width)
                    integral = topology["profile_integral"]
                    updated[chunk_index, group_index] = np.interp(
                        u_grid, fitted_u, fitted / integral, left=0.0, right=0.0,
                    )
                    bin_count[chunk_index, group_index] = counts
                    bin_scatter[chunk_index, group_index] = scatter
                    bin_used[chunk_index, group_index] = used
                    peak_u[chunk_index, group_index] = topology["peak_u"]
                    centroid_offset[chunk_index, group_index] = topology["centroid_offset"]
                    height[chunk_index, group_index] = topology["height"]
                    radius[chunk_index, group_index] = topology["radius"]
                    sigma[chunk_index, group_index] = topology["sigma"]
                    profile_integral[chunk_index, group_index] = integral
                    h3[chunk_index, group_index] = topology["h3"]
                    h4[chunk_index, group_index] = topology["h4"]
                    bin_model_rms[chunk_index, group_index] = topology["bin_model_rms"]
                    bin_model_weighted_rms[chunk_index, group_index] = topology["bin_model_weighted_rms"]
                    optimizer_status[chunk_index, group_index] = topology["optimizer_status"]
                    optimizer_cost[chunk_index, group_index] = topology["optimizer_cost"]
                    parameter_at_bound[chunk_index, group_index] = topology["parameter_at_bound"]
                    left_valley_u[chunk_index, group_index] = topology["left_valley_u"]
                    right_valley_u[chunk_index, group_index] = topology["right_valley_u"]
                    left_inflection_u[chunk_index, group_index] = topology["left_inflection_u"]
                    right_inflection_u[chunk_index, group_index] = topology["right_inflection_u"]
                except ValueError:
                    if previous is not None:
                        updated[chunk_index, group_index] = previous.density[chunk_index, group_index]
        profiles, bin_count, bin_scatter, bin_used, peak_u, centroid_offset, height, radius, sigma, profile_integral, h3, h4, bin_model_rms, bin_model_weighted_rms, optimizer_status, optimizer_cost, parameter_at_bound, left_valley_u, right_valley_u, left_inflection_u, right_inflection_u = _fill_missing_local_profiles(
            updated, bin_count, bin_scatter, bin_used, peak_u, centroid_offset, height, radius, sigma, profile_integral, h3, h4, bin_model_rms, bin_model_weighted_rms, optimizer_status, optimizer_cost, parameter_at_bound, left_valley_u, right_valley_u, left_inflection_u, right_inflection_u,
        )
        current = LocalProfileMap(
            u_grid=u_grid, density=profiles,
            chunk_columns=np.asarray([(int(chunk[0]), int(chunk[-1])) for chunk in chunks], dtype=np.int32),
            group_bounds=np.asarray([(int(group[0]), int(group[-1])) for group in groups], dtype=np.int32),
            fiber_group=fiber_group, sample_count=sample_count, valley_constraint_count=valley_count,
            bin_count=bin_count, bin_scatter=bin_scatter, bin_used=bin_used,
            peak_u=peak_u, centroid_offset=centroid_offset,
            height=height, radius=radius, sigma=sigma, profile_integral=profile_integral,
            h3=h3, h4=h4,
            bin_model_rms=bin_model_rms,
            bin_model_weighted_rms=bin_model_weighted_rms,
            optimizer_status=optimizer_status, optimizer_cost=optimizer_cost,
            parameter_at_bound=parameter_at_bound,
            left_valley_u=left_valley_u, right_valley_u=right_valley_u,
            left_inflection_u=left_inflection_u, right_inflection_u=right_inflection_u,
        )
        # ``current`` contains only P=M/I, so both the capture calculation and
        # next pass's neighbor subtraction use a physical unit-integral map.
        aperture_capture(trace, detector_rows, current, aperture_width)
        updated_total = compact_total.copy()
        for chunk_index, columns in enumerate(chunks):
            for group_index, fibers in enumerate(groups):
                integral = profile_integral[chunk_index, group_index]
                if np.isfinite(integral) and integral > 0.0:
                    updated_total[np.ix_(fibers, columns)] *= integral
        finite_integrals = profile_integral[np.isfinite(profile_integral) & (profile_integral > 0.0)]
        iteration_integral_stats.append({
            "iteration": iteration + 1,
            "count": int(finite_integrals.size),
            "minimum": float(np.min(finite_integrals)),
            "p05": float(np.percentile(finite_integrals, 5)),
            "median": float(np.median(finite_integrals)),
            "p95": float(np.percentile(finite_integrals, 95)),
            "maximum": float(np.max(finite_integrals)),
        })
        if previous is not None:
            capture_change = np.nanmedian(np.abs(updated_total - compact_total) / np.maximum(np.abs(compact_total), 1e-12))
            shape_change = np.nanmedian(np.abs(current.density - previous.density) / np.maximum(previous.density, 1e-8))
            compact_total = updated_total
            if np.isfinite(capture_change) and np.isfinite(shape_change) and capture_change < 2e-3 and shape_change < 5e-3:
                converged = True
                break
        else:
            compact_total = updated_total
        previous = current

    # The outer loop has just updated compact_total from ``current``.  Freeze
    # both before a final diagnostic/profile closure fit: each refit sees the
    # same neighbor profiles and compact totals, and no capture normalization
    # is updated from this pass.
    frozen_total = compact_total.copy()
    frozen_neighbors = current
    closure_density = current.density.copy()
    closure_bin_count = current.bin_count.copy()
    closure_bin_scatter = current.bin_scatter.copy()
    closure_bin_used = current.bin_used.copy()
    closure_peak_u = current.peak_u.copy()
    closure_centroid_offset = current.centroid_offset.copy()
    closure_height = current.height.copy()
    closure_radius = current.radius.copy()
    closure_sigma = current.sigma.copy()
    closure_profile_integral = current.profile_integral.copy()
    closure_h3 = current.h3.copy()
    closure_h4 = current.h4.copy()
    closure_bin_model_rms = current.bin_model_rms.copy()
    closure_bin_model_weighted_rms = current.bin_model_weighted_rms.copy()
    closure_optimizer_status = current.optimizer_status.copy()
    closure_optimizer_cost = current.optimizer_cost.copy()
    closure_parameter_at_bound = current.parameter_at_bound.copy()
    closure_left_valley_u = current.left_valley_u.copy()
    closure_right_valley_u = current.right_valley_u.copy()
    closure_left_inflection_u = current.left_inflection_u.copy()
    closure_right_inflection_u = current.right_inflection_u.copy()
    closure_valley_count = current.valley_constraint_count.copy()
    for chunk_index in range(len(chunks)):
        for group_index in range(len(groups)):
            selected = (evidence.chunk == chunk_index) & (evidence.group == group_index)
            _, _, valley_u, _, fit_u, fit_values = _profile_fit_inputs(
                evidence, selected, frozen_total, frozen_neighbors,
                chunk=chunk_index, group=group_index,
                valley_weight=valley_weight, seed_core_only=False,
            )
            closure_valley_count[chunk_index, group_index] = valley_u.size
            try:
                fitted_u, fitted, counts, scatter, used, topology = fit_constrained_profile(
                    fit_u, fit_values, support=support, bin_width=bin_width,
                )
            except ValueError:
                # Preserve a usable prior map for extraction, while marking
                # the corresponding diagnostic panel as an unavailable fit.
                closure_optimizer_status[chunk_index, group_index] = -99
                closure_optimizer_cost[chunk_index, group_index] = np.nan
                closure_bin_model_rms[chunk_index, group_index] = np.nan
                closure_bin_model_weighted_rms[chunk_index, group_index] = np.nan
                closure_parameter_at_bound[chunk_index, group_index] = False
                continue
            integral = topology["profile_integral"]
            closure_density[chunk_index, group_index] = np.interp(
                u_grid, fitted_u, fitted / integral, left=0.0, right=0.0,
            )
            closure_bin_count[chunk_index, group_index] = counts
            closure_bin_scatter[chunk_index, group_index] = scatter
            closure_bin_used[chunk_index, group_index] = used
            closure_peak_u[chunk_index, group_index] = topology["peak_u"]
            closure_centroid_offset[chunk_index, group_index] = topology["centroid_offset"]
            closure_height[chunk_index, group_index] = topology["height"]
            closure_radius[chunk_index, group_index] = topology["radius"]
            closure_sigma[chunk_index, group_index] = topology["sigma"]
            closure_profile_integral[chunk_index, group_index] = integral
            closure_h3[chunk_index, group_index] = topology["h3"]
            closure_h4[chunk_index, group_index] = topology["h4"]
            closure_bin_model_rms[chunk_index, group_index] = topology["bin_model_rms"]
            closure_bin_model_weighted_rms[chunk_index, group_index] = topology["bin_model_weighted_rms"]
            closure_optimizer_status[chunk_index, group_index] = topology["optimizer_status"]
            closure_optimizer_cost[chunk_index, group_index] = topology["optimizer_cost"]
            closure_parameter_at_bound[chunk_index, group_index] = topology["parameter_at_bound"]
            closure_left_valley_u[chunk_index, group_index] = topology["left_valley_u"]
            closure_right_valley_u[chunk_index, group_index] = topology["right_valley_u"]
            closure_left_inflection_u[chunk_index, group_index] = topology["left_inflection_u"]
            closure_right_inflection_u[chunk_index, group_index] = topology["right_inflection_u"]
    current = LocalProfileMap(
        u_grid=current.u_grid, density=closure_density,
        chunk_columns=current.chunk_columns, group_bounds=current.group_bounds,
        fiber_group=current.fiber_group, sample_count=current.sample_count,
        valley_constraint_count=closure_valley_count,
        bin_count=closure_bin_count, bin_scatter=closure_bin_scatter,
        bin_used=closure_bin_used, peak_u=closure_peak_u,
        centroid_offset=closure_centroid_offset, height=closure_height,
        radius=closure_radius, sigma=closure_sigma,
        profile_integral=closure_profile_integral, h3=closure_h3, h4=closure_h4,
        bin_model_rms=closure_bin_model_rms,
        bin_model_weighted_rms=closure_bin_model_weighted_rms,
        optimizer_status=closure_optimizer_status,
        optimizer_cost=closure_optimizer_cost,
        parameter_at_bound=closure_parameter_at_bound,
        left_valley_u=closure_left_valley_u,
        right_valley_u=closure_right_valley_u,
        left_inflection_u=closure_left_inflection_u,
        right_inflection_u=closure_right_inflection_u,
    )
    return (
        current, frozen_neighbors, frozen_total, completed_iterations, converged,
        float(capture_change), float(shape_change), iteration_integral_stats,
    )


def profile_integral(u_grid: np.ndarray, profile: np.ndarray, left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Integrate a linearly interpolated profile over arbitrary detector intervals."""
    cumulative = np.r_[0.0, np.cumsum((profile[1:] + profile[:-1]) * np.diff(u_grid) / 2.0)]
    return np.interp(right, u_grid, cumulative, left=0.0, right=cumulative[-1]) - np.interp(left, u_grid, cumulative, left=0.0, right=cumulative[-1])


def aperture_capture(trace: np.ndarray, detector_rows: int, profiles: LocalProfileMap, width: float) -> np.ndarray:
    """Evaluate compact-flux capture from the local empirical profile map."""
    rows, weights, valid = fractional_aperture_geometry(trace, detector_rows, width=width)
    # The geometry supplies the exact continuous aperture boundaries; integrate
    # the compact density through those same pixel intersections.
    left = np.maximum(rows.astype(float) - trace[..., None], -width / 2.0)
    right = np.minimum(rows.astype(float) + 1.0 - trace[..., None], width / 2.0)
    captured = np.full(trace.shape, np.nan, dtype=float)
    for fiber in range(trace.shape[0]):
        for chunk, (start, stop) in enumerate(profiles.chunk_columns):
            profile = profiles.density[chunk, profiles.fiber_group[fiber]]
            captured[fiber, start:stop + 1] = np.sum(
                profile_integral(profiles.u_grid, profile, left[fiber, start:stop + 1], right[fiber, start:stop + 1])
                * (weights[fiber, start:stop + 1] > 0.0), axis=-1,
            )
    return np.where(valid, captured, np.nan)


def common_peaks(wave: np.ndarray, flux: np.ndarray, *, smoothing_window: int) -> list[dict[str, float | bool]]:
    """Adapt diagnose_arc_spectrum peak finding with the requested 51-sample baseline."""
    finite = np.isfinite(wave) & np.isfinite(flux)
    base = median_filter(flux[finite], size=max(3, smoothing_window // 2 * 2 + 1))
    residual = flux[finite] - base
    mad = 1.4826 * np.median(np.abs(residual - np.median(residual)))
    peaks, heights = find_peaks(residual, thresh=max(5.0 * mad, 1e-12))
    finite_wave = wave[finite]
    observed = np.interp(peaks, np.arange(finite_wave.size), finite_wave)
    found = []
    for reference in REFERENCE_ARC_WAVELENGTHS:
        index = int(np.argmin(np.abs(observed - reference))) if observed.size else 0
        matched = bool(observed.size and abs(observed[index] - reference) <= 2.0)
        found.append({"reference_wavelength": float(reference), "observed_wavelength": float(observed[index]) if matched else float("nan"), "offset_angstrom": float(observed[index] - reference) if matched else float("nan"), "prominence": float(heights[index]) if matched else float("nan"), "matched": matched})
    return found


def line_flux_and_wings(spectrum: np.ndarray, wavelength: np.ndarray, lines: tuple[float, ...], *, core_half_width: float, wing_inner: float, wing_outer: float, smooth_window: int, captured_fraction: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Collect per-fiber, aperture-corrected Arc wing samples and compact fluxes."""
    baseline = np.array([median_filter(np.nan_to_num(row, nan=0.0), size=smooth_window) for row in spectrum])
    residual = spectrum - baseline
    radii: list[np.ndarray] = []
    normalized: list[np.ndarray] = []
    source_fiber: list[np.ndarray] = []
    source_line: list[np.ndarray] = []
    line_info: list[tuple[int, float, float]] = []
    for fiber in range(spectrum.shape[0]):
        for line_index, line in enumerate(lines):
            delta = wavelength[fiber] - line
            core = np.isfinite(delta) & np.isfinite(residual[fiber]) & (np.abs(delta) <= core_half_width)
            if core.sum() < 3:
                continue
            compact_aperture_flux = trapezoidal_integral(residual[fiber, core], wavelength[fiber, core])
            capture = float(np.nanmedian(captured_fraction[fiber, core]))
            compact_total_flux = compact_aperture_flux / capture if np.isfinite(capture) and capture > 0 else np.nan
            if not np.isfinite(compact_total_flux) or compact_total_flux <= 0:
                continue
            wings = np.isfinite(delta) & np.isfinite(spectrum[fiber]) & (np.abs(delta) >= wing_inner) & (np.abs(delta) <= wing_outer)
            if np.any(wings):
                radii.append(delta[wings])
                normalized.append(spectrum[fiber, wings] / compact_total_flux)
                source_fiber.append(np.full(wings.sum(), fiber, dtype=np.int16))
                source_line.append(np.full(wings.sum(), line_index, dtype=np.int8))
                line_info.append((fiber, line, compact_total_flux))
    if not radii:
        raise ValueError("no usable Arc halo wing samples")
    info = np.asarray(line_info, dtype=float).reshape(-1, 3)
    return (np.concatenate(radii), np.concatenate(normalized),
            np.concatenate(source_fiber), np.concatenate(source_line), info)


def halo_model(radius: np.ndarray, amplitude: float, r0: float, alpha: float) -> np.ndarray:
    return amplitude / (1.0 + (np.abs(np.asarray(radius)) / r0) ** alpha)


def fit_halo(radius: np.ndarray, normalized: np.ndarray) -> np.ndarray:
    good = np.isfinite(radius) & np.isfinite(normalized) & (normalized > 0.0)
    if good.sum() < 30:
        raise ValueError("too few positive halo samples for a common fit")
    # Fit logarithms so weak 50-Angstrom wings influence the experiment.
    def residual(theta: np.ndarray) -> np.ndarray:
        model = halo_model(radius[good], *np.exp(theta))
        return np.log(model) - np.log(normalized[good])
    result = least_squares(residual, np.log([1e-2, 15.0, 2.0]), bounds=(np.log([1e-10, 0.2, 0.2]), np.log([10.0, 300.0, 12.0])), loss="soft_l1")
    return np.exp(result.x)


def predict_halo_image(traces: np.ndarray, wavelength: np.ndarray, source_info: np.ndarray, detector_shape: tuple[int, int], parameters: np.ndarray, *, aperture_width: float) -> np.ndarray:
    """First-pass 2-D projection: five-pixel extracted halo / five pixels.

    This intentionally assumes the measured five-pixel halo is locally shared
    equally among the five extraction pixels.  Contributions are accumulated,
    never overwritten, and this approximation is recorded in the output.
    """
    image = np.zeros(detector_shape, dtype=np.float32)
    rows, weights, valid = fractional_aperture_geometry(traces, detector_shape[0], width=aperture_width)
    columns = np.arange(detector_shape[1])
    for fiber, line, compact_flux in source_info:
        fiber_index = int(fiber)
        values = compact_flux * halo_model(wavelength[fiber_index] - line, *parameters) / float(aperture_width)
        for sample in range(rows.shape[-1]):
            good = valid[fiber_index] & (weights[fiber_index, :, sample] > 0.0)
            y = rows[fiber_index, good, sample]
            image[y, columns[good]] += values[good]
    return image


def plot_profile_diagnostics(
    trace: np.ndarray,
    evidence: ProfileEvidence,
    profiles: LocalProfileMap,
    neighbor_profiles: LocalProfileMap,
    compact_total: np.ndarray,
    path: Path,
    support: float,
    bin_width: float,
    valley_weight: int,
) -> None:
    """Show fixed local-profile evidence in detector-spatial panel order."""
    nx = trace.shape[1]
    ny = int(np.ceil(np.nanmax(trace))) + 1
    x_locations = (nx // 8, nx // 2, 7 * nx // 8)
    y_locations = (ny // 8, ny // 2, 7 * ny // 8)
    targets = [(x0, y0) for y0 in y_locations for x0 in x_locations]
    fig, axes = plt.subplots(3, 3, figsize=(18, 14.4), sharex=True, sharey=True)
    for axis, (x0, y0) in zip(axes.flat, targets):
        fiber = int(np.nanargmin(np.abs(trace[:, x0] - y0)))
        chunk, group = profiles.indices(fiber, x0)
        start, stop = profiles.chunk_columns[chunk]
        first_fiber, last_fiber = profiles.group_bounds[group]
        selected = (evidence.chunk == chunk) & (evidence.group == group)
        uu, vv, valley_u, valley_v, fit_u, fit_v = _profile_fit_inputs(
            evidence, selected, compact_total, neighbor_profiles,
            chunk=chunk, group=group, valley_weight=valley_weight,
            seed_core_only=False,
        )
        bin_u, bin_median, bin_scatter, bin_count = robust_profile_bins(
            fit_u, fit_v, support=support, bin_width=bin_width,
        )
        if profiles.optimizer_status[chunk, group] == -99:
            raise RuntimeError("profile diagnostic closure refit is unavailable")
        if not np.array_equal(bin_count, profiles.bin_count[chunk, group]):
            raise RuntimeError("profile diagnostic bins do not match the frozen closure fit")
        bin_used = profiles.bin_used[chunk, group] & np.isfinite(bin_median)
        bin_rejected = (bin_count > 0) & np.isfinite(bin_median) & ~bin_used
        axis.plot(uu, vv, ".", ms=1.0, alpha=0.035, color="tab:blue", label="all detector samples")
        if valley_u.size:
            axis.plot(valley_u, valley_v, "x", ms=2.5, alpha=0.55, color="tab:orange", label="valley constraints")
        if np.any(bin_rejected):
            axis.plot(bin_u[bin_rejected], bin_median[bin_rejected], "o", ms=2.5, mfc="none", mec="0.45", alpha=0.8, label="rejected robust bins")
        if np.any(bin_used):
            bin_error = bin_scatter[bin_used] / np.sqrt(np.maximum(bin_count[bin_used], 1))
            axis.errorbar(bin_u[bin_used], bin_median[bin_used], yerr=bin_error, fmt="o", ms=2.5, color="tab:purple", ecolor="tab:purple", elinewidth=0.55, capsize=0, alpha=0.85, label="accepted robust bins")
        axis.plot(profiles.u_grid, profiles.density[chunk, group], color="black", lw=1.5, label="constrained fit")
        axis.axvline(profiles.peak_u[chunk, group], color="0.35", lw=0.7, ls="--")
        axis.axvline(profiles.left_valley_u[chunk, group], color="0.6", lw=0.6, ls=":")
        axis.axvline(profiles.right_valley_u[chunk, group], color="0.6", lw=0.6, ls=":")
        axis.set(
            title=(
                f"x={x0}, fiber={fiber}\nchunk {start}-{stop}, group {first_fiber}-{last_fiber}\n"
                f"du={profiles.centroid_offset[chunk, group]:+.3f} px; "
                f"H={profiles.height[chunk, group]:.3g}; R={profiles.radius[chunk, group]:.3f}; "
                f"sigma={profiles.sigma[chunk, group]:.3f}\n"
                f"h3={profiles.h3[chunk, group]:+.3f}; h4={profiles.h4[chunk, group]:+.3f}; "
                f"RMS={profiles.bin_model_rms[chunk, group]:.3g}; "
                f"wRMS={profiles.bin_model_weighted_rms[chunk, group]:.3g}\n"
                f"status={profiles.optimizer_status[chunk, group]}; "
                f"cost={profiles.optimizer_cost[chunk, group]:.3g}; "
                f"bound={'yes' if profiles.parameter_at_bound[chunk, group] else 'no'}; n={uu.size}"
            ),
            xlim=(-support, support),
        )
        axis.grid(alpha=0.2)
    for axis in axes[-1, :]:
        axis.set_xlabel("u (pixel)")
    for axis in axes[:, 0]:
        axis.set_ylabel("LDLS pixel / estimated compact total flux")
    axes[0, 0].legend(frameon=False, fontsize=7, loc="upper right")
    fig.suptitle("Local LDLS evidence and constrained profiles by detector position")
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def plot_halo(radius: np.ndarray, normalized: np.ndarray, source_line: np.ndarray, parameters: np.ndarray, path: Path) -> None:
    fig, (all_axis, lines_axis) = plt.subplots(1, 2, figsize=(14, 5))
    keep = np.isfinite(radius) & np.isfinite(normalized) & (normalized > 0)
    all_axis.plot(np.abs(radius[keep]), normalized[keep], ".", alpha=0.08, ms=1.5)
    grid = np.linspace(15, 50, 300)
    all_axis.plot(grid, halo_model(grid, *parameters), color="black", lw=2, label="common fit")
    all_axis.set(xscale="log", yscale="log", xlabel="|wavelength offset| (A)", ylabel="five-pixel signal / compact total flux", title="Normalized Arc halo samples")
    all_axis.legend(frameon=False)
    for line_index, line in enumerate(HALO_LINES):
        use = keep & (source_line == line_index)
        if np.any(use):
            # No per-line refit: each line is compared directly with the one
            # normalization convention used to determine the common response.
            lines_axis.plot(np.abs(radius[use]), normalized[use], ".", alpha=0.08, ms=1.2, label=f"{line:.1f} A")
    lines_axis.plot(grid, halo_model(grid, *parameters), color="black", lw=2, label="same common fit")
    lines_axis.set(xscale="log", yscale="log", xlabel="|wavelength offset| (A)", ylabel="normalized five-pixel halo", title="Same common halo form by selected Arc line")
    lines_axis.legend(frameon=False, fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=170); plt.close(fig)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Artifact SQLite database (read only)")
    parser.add_argument("--zipcode", required=True, help="IFUSLOT+IFUID+SPECID+AMP+CONTROLLER")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--aperture-width", type=float, default=5.0)
    parser.add_argument("--profile-support", type=float, default=8.0)
    parser.add_argument("--profile-bin-width", type=float, default=0.4)
    parser.add_argument("--profile-chunk-width", type=int, default=100)
    parser.add_argument("--profile-group-size", type=int, default=16)
    parser.add_argument("--profile-deblend-iterations", type=int, default=3, help="Maximum post-seed capture/deblend refinements; exits early once stable")
    parser.add_argument("--profile-valley-weight", type=int, default=1)
    parser.add_argument("--ldls-smoothing-window", type=int, default=101)
    parser.add_argument("--arc-smoothing-window", type=int, default=51)
    parser.add_argument("--halo-core-half-width", type=float, default=8.0)
    parser.add_argument("--halo-wing-inner", type=float, default=15.0)
    parser.add_argument("--halo-wing-outer", type=float, default=50.0)
    parser.add_argument(
        "--max-arc-separation-hours", type=float, default=24.0,
        help="Maximum allowed separation between coherent LDLS and Arc state centers",
    )
    args = parser.parse_args(argv)
    requested = parse_zipcode_key(args.zipcode)
    if requested.amp not in PAIR:
        parser.error("zipcode amplifier must be one of LL, LU, RU, or RL")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    service = ArtifactService(args.db)
    side, lower_amp, upper_amp = PAIR[requested.amp]
    lower_zipcode = ZipCode(requested.ifuslot, requested.ifuid, requested.specid, lower_amp, requested.controller)
    upper_zipcode = ZipCode(requested.ifuslot, requested.ifuid, requested.specid, upper_amp, requested.controller)
    paired = choose_paired_products(
        service, lower_zipcode, upper_zipcode,
        max_arc_separation_hours=args.max_arc_separation_hours,
    )
    lower, upper = paired.lower, paired.upper

    records = {
        "requested_zipcode": requested.key(),
        "physical_ccd": {"side": side, "lower": lower_zipcode.key(), "upper": upper_zipcode.key()},
        "read_only": True,
        "selection": paired.selection_evidence,
        "products": {},
    }
    for label, products in (("lower", lower), ("upper", upper)):
        records["products"][label] = {
            name: {
                "id": int(getattr(products, name)["id"]),
                "calibration_group_id": calibration_group_id(getattr(products, name)),
                "validity": {
                    "start": str(getattr(products, name)["validity_start"]),
                    "end": str(getattr(products, name)["validity_end"]),
                },
                "parents": sorted(parent_ids(service, getattr(products, name))),
                "parent_groups": sorted(parent_groups(getattr(products, name))),
                "accepted_exposure_ids": list(frame_signature(getattr(products, name))),
            }
            for name in ("master_ldls", "master_arc", "master_sci", "trace_map", "wavelength_map")
        }
    write_json(output / "00_selected_artifacts.json", records)

    ldls, _, ldls_mask = assemble_pair(service, lower, upper, "master_ldls", subtract_gap_baseline=True)
    sci, _, sci_mask = assemble_pair(service, lower, upper, "master_sci", subtract_gap_baseline=True)
    arc, _, arc_mask = assemble_pair(service, lower, upper, "master_arc", subtract_gap_baseline=False)
    traces = physical_trace_map(load(service, lower.trace_map, "fiber_trace_map"), load(service, upper.trace_map, "fiber_trace_map"))
    waves = np.vstack((load(service, lower.wavelength_map, "wavelength_map"), load(service, upper.wavelength_map, "wavelength_map"))).astype(float)
    fits.writeto(output / "01_assembled_ldls_baseline_subtracted.fits", ldls, overwrite=True)
    fits.writeto(output / "01_assembled_science_baseline_subtracted.fits", sci, overwrite=True)
    fits.writeto(output / "01_assembled_arc_extended_light_retained.fits", arc, overwrite=True)

    # The profile normalizer must use the same compact aperture geometry as the
    # later flux correction.  A 15-pixel aperture has gap-dependent neighbor
    # contamination, which makes edge fibers such as 111 incomparable with
    # normally spaced fibers in a shared local profile fit.
    preliminary = np.asarray(extract_fractional_aperture(
        ldls, np.zeros_like(ldls), traces, pixel_mask=ldls_mask,
        width=args.aperture_width,
    ).get_array("spectrum"), dtype=float)
    preliminary = smooth_spectra(preliminary, args.ldls_smoothing_window)
    profile_chunks, profile_groups, profile_fiber_group = _profile_layout(
        traces, support=args.profile_support,
        chunk_width=args.profile_chunk_width, group_size=args.profile_group_size,
    )
    evidence = collect_profile_evidence(
        ldls, traces, preliminary, ldls_mask, support=args.profile_support,
        chunks=profile_chunks, groups=profile_groups, fiber_group=profile_fiber_group,
    )
    profiles, diagnostic_neighbors, compact_total, profile_iterations, profile_converged, capture_change, profile_change, profile_integral_iterations = measure_local_profiles(
        evidence, traces, preliminary, detector_rows=ldls.shape[0], aperture_width=args.aperture_width,
        support=args.profile_support, bin_width=args.profile_bin_width,
        chunk_width=args.profile_chunk_width, group_size=args.profile_group_size,
        iterations=args.profile_deblend_iterations,
        valley_weight=args.profile_valley_weight,
    )
    captured = aperture_capture(traces, ldls.shape[0], profiles, args.aperture_width)
    np.savez_compressed(
        output / "02_ldls_profile_data.npz",
        profile_u=profiles.u_grid, profile_density=profiles.density,
        profile_chunk_columns=profiles.chunk_columns,
        profile_group_bounds=profiles.group_bounds,
        fiber_profile_group=profiles.fiber_group,
        profile_sample_count=profiles.sample_count,
        valley_constraint_count=profiles.valley_constraint_count,
        profile_bin_count=profiles.bin_count,
        profile_bin_scatter=profiles.bin_scatter,
        profile_bin_used=profiles.bin_used,
        profile_peak_u=profiles.peak_u,
        profile_centroid_offset=profiles.centroid_offset,
        profile_height=profiles.height,
        profile_radius=profiles.radius,
        profile_sigma=profiles.sigma,
        profile_integral=profiles.profile_integral,
        profile_h3=profiles.h3,
        profile_h4=profiles.h4,
        profile_bin_model_rms=profiles.bin_model_rms,
        profile_bin_model_weighted_rms=profiles.bin_model_weighted_rms,
        profile_optimizer_status=profiles.optimizer_status,
        profile_optimizer_cost=profiles.optimizer_cost,
        profile_parameter_at_bound=profiles.parameter_at_bound,
        profile_closure_neighbor_density=diagnostic_neighbors.density,
        profile_left_valley_u=profiles.left_valley_u,
        profile_right_valley_u=profiles.right_valley_u,
        profile_left_inflection_u=profiles.left_inflection_u,
        profile_right_inflection_u=profiles.right_inflection_u,
        aperture_capture=captured, preliminary_spectrum=preliminary,
        compact_total_spectrum=compact_total,
        evidence_u=evidence.u, evidence_signal=evidence.signal,
        evidence_fiber=evidence.fiber, evidence_column=evidence.column,
        evidence_chunk=evidence.chunk, evidence_group=evidence.group,
        evidence_five_pixel_normalization=evidence.five_pixel_normalization,
        evidence_seed_core=evidence.seed_core,
        evidence_neighbor_fiber=evidence.neighbor_fiber,
        evidence_neighbor_u=evidence.neighbor_u,
        evidence_neighbor_overlaps=evidence.neighbor_overlaps,
        valley_signal=evidence.valley_signal,
        valley_first_fiber=evidence.valley_first_fiber,
        valley_second_fiber=evidence.valley_second_fiber,
        valley_column=evidence.valley_column,
        valley_chunk=evidence.valley_chunk, valley_group=evidence.valley_group,
        valley_first_u=evidence.valley_first_u, valley_second_u=evidence.valley_second_u,
    )
    plot_profile_diagnostics(
        traces, evidence, profiles, diagnostic_neighbors, compact_total,
        output / "02_ldls_profile_samples.png",
        args.profile_support, args.profile_bin_width, args.profile_valley_weight,
    )
    write_json(output / "02_aperture_capture.json", {
        "aperture_width_pixels": args.aperture_width,
        "capture_median": float(np.nanmedian(captured)),
        "capture_p05": float(np.nanpercentile(captured, 5)),
        "capture_p95": float(np.nanpercentile(captured, 95)),
        "profile_map": {
            "chunk_width_columns": args.profile_chunk_width,
            "group_size_fibers": args.profile_group_size,
            "chunk_count": int(profiles.density.shape[0]),
            "group_count": int(profiles.density.shape[1]),
            "normalization_iterations": profile_iterations,
            "normalization_converged": profile_converged,
            "profile_integral_iterations": profile_integral_iterations,
            "final_capture_relative_change": capture_change,
            "final_profile_relative_change": profile_change,
            "detector_sample_count": int(evidence.u.size),
            "seed_core_sample_count": int(evidence.seed_core.sum()),
            "final_constraining_sample_count": int(profiles.sample_count.sum()),
            "robust_bin_count": int(profiles.bin_used.sum()),
            "valley_constraint_count": int(profiles.valley_constraint_count.sum()),
            "topology": "non-negative, one-peak, monotone asymmetric beta-CDF branches fitted from smoothing-spline peak/valley evidence",
        },
        "interpretation": "after each fit, compact_total is multiplied by I=integral(M), while P=M/I is used for deblending and aperture capture",
    })

    arc_extract = extract_fractional_aperture(arc, np.zeros_like(arc), traces, pixel_mask=arc_mask, width=args.aperture_width)
    arc_spectrum = np.asarray(arc_extract.get_array("spectrum"), dtype=float)
    common_wave, common_flux = common_arc_spectrum(arc_spectrum, waves)
    identified = common_peaks(common_wave, common_flux, smoothing_window=args.arc_smoothing_window)
    plot_common_arc_spectrum(common_wave, common_flux, identified, [], output / "03_common_arc_spectrum.png", title=f"Common Master Arc: {side} CCD (51-sample baseline peaks)")
    np.savez_compressed(output / "03_arc_extraction_and_common_spectrum.npz", spectrum=arc_spectrum, wavelength=waves, common_wavelength=common_wave, common_flux=common_flux)

    radius, normalized, source_fiber, source_line, source_info = line_flux_and_wings(arc_spectrum, waves, HALO_LINES, core_half_width=args.halo_core_half_width, wing_inner=args.halo_wing_inner, wing_outer=args.halo_wing_outer, smooth_window=args.arc_smoothing_window, captured_fraction=captured)
    parameters = fit_halo(radius, normalized)
    np.savez_compressed(output / "04_halo_fit_data.npz", wavelength_offset=radius, normalized_wing_signal=normalized, source_fiber=source_fiber, source_line_index=source_line, line_sources=source_info, parameters=parameters)
    write_json(output / "04_halo_fit.json", {"model": "H(r) = A / (1 + (abs(r) / r0)**alpha)", "parameters": {"A": float(parameters[0]), "r0_angstrom": float(parameters[1]), "alpha": float(parameters[2])}, "selected_lines_angstrom": list(HALO_LINES), "core_half_width_angstrom": args.halo_core_half_width, "wing_range_angstrom": [args.halo_wing_inner, args.halo_wing_outer], "experimental": True})
    plot_halo(radius, normalized, source_line, parameters, output / "04_halo_fit.png")

    halo = predict_halo_image(traces, waves, source_info, arc.shape, parameters, aperture_width=args.aperture_width)
    cleaned = arc - halo
    fits.writeto(output / "05_arc_original_assembled.fits", arc, overwrite=True)
    fits.writeto(output / "05_arc_predicted_halo_first_pass.fits", halo, overwrite=True)
    fits.writeto(output / "05_arc_halo_subtracted.fits", cleaned, overwrite=True)
    write_json(output / "05_projection_assumptions.json", {"five_pixel_to_per_pixel": "local per-pixel halo = extracted five-pixel halo / 5", "projection": "sum all fiber/line contributions into the physical CCD; no overwrite", "status": "first-pass experimental approximation, not production policy"})
    (output / "06_NEXT_empirical_core_delta_lambda_u.md").write_text("# Next: empirical compact Arc core in (delta_lambda, u)\n\nStop here for scientific inspection.  The next experiment should use the halo-subtracted Arc, the LDLS compact profile, and wavelength/trace maps to measure compact detector response in `(delta_lambda [Angstrom], u [trace-relative detector pixels])`.  No core model is fit by this workbench yet.\n")
    print(f"Read-only response-model workbench completed: {output}")
    print(f"Physical CCD: {lower_zipcode.key()} + {upper_zipcode.key()}")
    print(f"Halo fit: A={parameters[0]:.5g}, r0={parameters[1]:.5g} A, alpha={parameters[2]:.5g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
