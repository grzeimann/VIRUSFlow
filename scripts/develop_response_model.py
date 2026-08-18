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
from scipy.optimize import least_squares

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


def profile_samples(image: np.ndarray, trace: np.ndarray, preliminary: np.ndarray, mask: np.ndarray, *, support: float, column_stride: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Return floating-u, spectrum-normalized, nearest-trace profile samples.

    Keeping only pixels nearest to the candidate trace is deliberately
    conservative: it rejects unmodelled neighbor-fiber overlap instead of
    inheriting the legacy overwrite behavior.
    """
    ny, nx = image.shape
    u_values: list[np.ndarray] = []
    normalized: list[np.ndarray] = []
    all_traces = np.asarray(trace, dtype=float)
    cols = np.arange(0, nx, max(1, int(column_stride)))
    for fiber, center in enumerate(all_traces):
        valid_center = np.isfinite(center[cols]) & np.isfinite(preliminary[fiber, cols]) & (preliminary[fiber, cols] > 0)
        for column in cols[valid_center]:
            lo = max(0, int(np.floor(center[column] - support)))
            hi = min(ny, int(np.ceil(center[column] + support)) + 1)
            rows = np.arange(lo, hi)
            # At each column accept only the trace closest to this detector row.
            nearest = np.nanargmin(np.abs(all_traces[:, column, None] - rows[None, :]), axis=0) == fiber
            good = nearest & ~mask[rows, column] & np.isfinite(image[rows, column])
            if np.any(good):
                u_values.append(rows[good] - center[column])
                normalized.append(image[rows[good], column] / preliminary[fiber, column])
    if not u_values:
        raise ValueError("no valid LDLS profile samples")
    return np.concatenate(u_values), np.concatenate(normalized)


def fit_empirical_profile(u: np.ndarray, values: np.ndarray, *, support: float, bin_width: float) -> tuple[np.ndarray, np.ndarray]:
    """Robustly combine phase-sampled normalized profiles into unit compact flux."""
    edges = np.arange(-support, support + bin_width, bin_width)
    centers = 0.5 * (edges[:-1] + edges[1:])
    profile = np.full(centers.shape, np.nan)
    indices = np.digitize(u, edges) - 1
    for index in range(centers.size):
        selected = values[(indices == index) & np.isfinite(values)]
        if selected.size >= 8:
            location = np.median(selected)
            scale = 1.4826 * np.median(np.abs(selected - location))
            if np.isfinite(scale) and scale > 0:
                selected = selected[np.abs(selected - location) <= 5.0 * scale]
            profile[index] = np.median(selected)
    good = np.isfinite(profile)
    if good.sum() < 8:
        raise ValueError("insufficient empirical profile bins")
    profile = np.interp(centers, centers[good], profile[good], left=0.0, right=0.0)
    profile = np.clip(profile, 0.0, None)
    integral = np.trapezoid(profile, centers)
    if not np.isfinite(integral) or integral <= 0:
        raise ValueError("empirical profile has non-positive integral")
    return centers, profile / integral


def profile_integral(u_grid: np.ndarray, profile: np.ndarray, left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Integrate a linearly interpolated profile over arbitrary detector intervals."""
    cumulative = np.r_[0.0, np.cumsum((profile[1:] + profile[:-1]) * np.diff(u_grid) / 2.0)]
    return np.interp(right, u_grid, cumulative, left=0.0, right=cumulative[-1]) - np.interp(left, u_grid, cumulative, left=0.0, right=cumulative[-1])


def aperture_capture(trace: np.ndarray, detector_rows: int, u_grid: np.ndarray, profile: np.ndarray, width: float) -> np.ndarray:
    """Evaluate compact-flux capture using the canonical fractional aperture geometry."""
    rows, weights, valid = fractional_aperture_geometry(trace, detector_rows, width=width)
    # The geometry supplies the exact continuous aperture boundaries; integrate
    # the compact density through those same pixel intersections.
    left = np.maximum(rows.astype(float) - trace[..., None], -width / 2.0)
    right = np.minimum(rows.astype(float) + 1.0 - trace[..., None], width / 2.0)
    captured = np.sum(profile_integral(u_grid, profile, left, right) * (weights > 0.0), axis=-1)
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
            compact_aperture_flux = float(np.trapezoid(residual[fiber, core], wavelength[fiber, core]))
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


def plot_profile_diagnostics(image: np.ndarray, trace: np.ndarray, preliminary: np.ndarray, mask: np.ndarray, u_grid: np.ndarray, profile: np.ndarray, path: Path, support: float) -> None:
    ny, nx = image.shape
    targets = [(nx // 2, ny // 2), (nx // 8, ny // 8), (7 * nx // 8, ny // 8), (nx // 8, 7 * ny // 8), (7 * nx // 8, 7 * ny // 8)]
    fig, axes = plt.subplots(1, 5, figsize=(19, 3.6), sharey=True)
    for axis, (x0, y0) in zip(axes, targets):
        fiber = int(np.nanargmin(np.abs(trace[:, x0] - y0)))
        cols = np.arange(max(0, x0 - 35), min(nx, x0 + 36))
        uu, vv = profile_samples(image[:, max(0, x0 - 35):min(nx, x0 + 36)], trace[fiber:fiber + 1, cols], preliminary[fiber:fiber + 1, cols], mask[:, cols], support=support)
        axis.plot(uu, vv, ".", ms=1.2, alpha=0.16, color="tab:blue")
        axis.plot(u_grid, profile, color="black", lw=1.5)
        axis.set(title=f"x={x0}, nearest fiber={fiber}", xlabel="u (pixel)", xlim=(-support, support))
        axis.grid(alpha=0.2)
    axes[0].set_ylabel("LDLS pixel / preliminary spectrum")
    fig.suptitle("Phase-sampled empirical LDLS compact profiles")
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
    parser.add_argument("--profile-bin-width", type=float, default=0.05)
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

    preliminary = np.asarray(extract_fractional_aperture(ldls, np.zeros_like(ldls), traces, pixel_mask=ldls_mask, width=15.0).get_array("spectrum"), dtype=float)
    preliminary = smooth_spectra(preliminary, args.ldls_smoothing_window)
    u, sample_values = profile_samples(ldls, traces, preliminary, ldls_mask, support=args.profile_support)
    u_grid, profile = fit_empirical_profile(u, sample_values, support=args.profile_support, bin_width=args.profile_bin_width)
    captured = aperture_capture(traces, ldls.shape[0], u_grid, profile, args.aperture_width)
    np.savez_compressed(output / "02_ldls_profile_data.npz", u=u, normalized_sample=sample_values, profile_u=u_grid, profile_density=profile, aperture_capture=captured, preliminary_spectrum=preliminary)
    plot_profile_diagnostics(ldls, traces, preliminary, ldls_mask, u_grid, profile, output / "02_ldls_profile_samples.png", args.profile_support)
    write_json(output / "02_aperture_capture.json", {"aperture_width_pixels": args.aperture_width, "capture_median": float(np.nanmedian(captured)), "capture_p05": float(np.nanpercentile(captured, 5)), "capture_p95": float(np.nanpercentile(captured, 95)), "interpretation": "total compact flux = fractional-aperture flux / this capture fraction"})

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
