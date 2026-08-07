#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone scientific diagnostic for a completed VIRUSFlow observation.

Run this after a normal ``virusflow run observation`` reduction:

    python scripts/diagnose_observation.py 20260609-OBSID6

It reads only retained Artifacts through the existing ArtifactService/Scope
API -- it never reruns a reduction stage, never writes to the Artifact
database, and never invents a new Artifact kind. Every optional diagnostic is
generated only when the required evidence is retained; otherwise it is
skipped and the reason is recorded in ``index.md``.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import EllipseCollection
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from virusflow.artifacts import ArtifactService, Scope
from virusflow.algorithms.spatial_psf import ChromaticPSFModel
from virusflow.algorithms.source_extraction import select_source_fibers
from virusflow.config.defaults import SOURCE_EXTRACTION_CONFIGURATION

FIGSIZE = (7.0, 6.0)


# ---------------------------------------------------------------------------
# Artifact discovery helpers
# ---------------------------------------------------------------------------

def get_product(service, kind, *, exposure_id=None, observation_id=None):
    """Current (latest active) Artifact of ``kind`` for the given scope, or None."""

    scope = Scope(zipcode=None, exposure_id=exposure_id, observation_id=observation_id)
    return service.select_best(kind=kind, scope=scope, policy="latest")


def get_products(service, kind, *, exposure_id=None, observation_id=None):
    """All current (active) Artifacts of ``kind`` for the given scope.

    Used for kinds with more than one Artifact per scope, e.g. one
    ``spatial_psf_measurement`` per fitted wavelength interval.
    """

    rows = []
    for row in service.adapter.list_all(kind=kind):
        if str(row.get("state") or "active") != "active":
            continue
        if exposure_id is not None and row.get("exposure_id") != exposure_id:
            continue
        if observation_id is not None and row.get("observation_id") != observation_id:
            continue
        rows.append(row)
    rows.sort(key=lambda row: int(row.get("id") or 0))
    return rows


def load(service, row, name):
    return service.load_component(row, name)["data"]


def summary_of(service, row):
    return service.describe(row)["summary"]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class DiagnosticReport:
    """Accumulates what was generated, skipped, or failed for ``index.md``."""

    def __init__(self):
        self.entries = []

    def generated(self, category, path, detail=""):
        self.entries.append(("generated", category, str(path), detail))

    def skipped(self, category, reason):
        self.entries.append(("skipped", category, "", reason))

    def failed(self, category, reason):
        self.entries.append(("failed", category, "", reason))


def run_diagnostic(report, category, func, *args, **kwargs):
    """Run one plot_* function; never let it take down the whole script."""

    try:
        func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: keep going
        report.failed(category, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Observation context
# ---------------------------------------------------------------------------

class ObservationContext:
    def __init__(self, service, observation_id, membership_row):
        self.service = service
        self.observation_id = observation_id
        self.membership_row = membership_row
        membership_summary = summary_of(service, membership_row)
        self.exposure_ids = list(membership_summary.get("member_exposure_ids") or [])
        self.registration_row = get_product(service, "dither_registration", observation_id=observation_id)
        self.coverage_row = get_product(service, "dither_coverage_map", observation_id=observation_id)
        self.summary_row = get_product(service, "observation_summary", observation_id=observation_id)
        self.calibrated_observation_row = get_product(
            service, "calibrated_fiber_observation", observation_id=observation_id
        )
        self.combined_spectrum_row = get_product(
            service, "observation_source_spectrum", observation_id=observation_id
        )


def load_observation_context(service, observation_id):
    membership_row = get_product(service, "observation_membership", observation_id=observation_id)
    if membership_row is None:
        return None
    return ObservationContext(service, observation_id, membership_row)


def _exposure_state_row(service, ctx, exposure_id):
    return get_product(service, "observation_exposure_state", exposure_id=exposure_id, observation_id=ctx.observation_id)


# ---------------------------------------------------------------------------
# 8. Observation/exposure QA overview (built first: cheap, always attempted)
# ---------------------------------------------------------------------------

def plot_observation_summary(ctx, out_dir, report):
    service = ctx.service
    rows = []
    columns = [
        "exposure", "dither", "seeing", "airmass", "transparency",
        "astrometry", "detections", "extraction", "captured_frac",
        "reduced/raw amp", "failed amp",
    ]
    for index, exposure_id in enumerate(ctx.exposure_ids):
        state_row = _exposure_state_row(service, ctx, exposure_id)
        completion_row = get_product(service, "exposure_completion_manifest", exposure_id=exposure_id)
        astrometry_row = get_product(service, "final_astrometry", exposure_id=exposure_id)
        detections_row = get_product(service, "source_detection_catalog", exposure_id=exposure_id)
        extraction_row = get_product(service, "point_source_extraction", exposure_id=exposure_id)

        state_summary = summary_of(service, state_row) if state_row is not None else {}
        completion_summary = summary_of(service, completion_row) if completion_row is not None else {}
        astrometry_summary = summary_of(service, astrometry_row) if astrometry_row is not None else {}
        detection_summary = summary_of(service, detections_row) if detections_row is not None else {}
        extraction_summary = summary_of(service, extraction_row) if extraction_row is not None else {}

        state_array = load(service, state_row, "state") if state_row is not None else None
        seeing = float(state_array[0]) if state_array is not None else float("nan")
        transparency = float(state_array[1]) if state_array is not None else float("nan")
        airmass = float(state_array[6]) if state_array is not None else float("nan")

        astrometry_status = "refined" if astrometry_summary.get("refined") else (
            "header_only" if astrometry_row is not None else "missing"
        )
        raw_amp = completion_summary.get("raw_amplifier_count", "-")
        reduced_amp = completion_summary.get("reduced_amplifier_count", "-")
        failed_amp = completion_summary.get("failed_or_missing_amplifier_count", "-")

        rows.append([
            exposure_id, str(index), f"{seeing:.2f}", f"{airmass:.3f}", f"{transparency:.2f}",
            astrometry_status, str(detection_summary.get("detection_count", "-")),
            extraction_summary.get("median_captured_fraction") is not None and "extracted" or (
                "no_source" if extraction_row is None else "extracted"
            ),
            f"{extraction_summary.get('median_captured_fraction', float('nan')):.2f}"
            if extraction_summary.get("median_captured_fraction") is not None else "-",
            f"{reduced_amp}/{raw_amp}", str(failed_amp),
        ])

    if not rows:
        report.skipped("observation_summary", "no member exposure state evidence available")
        return

    fig, ax = plt.subplots(figsize=(11.0, 0.6 * len(rows) + 1.5))
    ax.axis("off")
    table = ax.table(cellText=rows, colLabels=columns, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 1.4)
    ax.set_title(f"Observation {ctx.observation_id}: exposure QA overview")
    path = out_dir / "observation_summary.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    report.generated("observation_summary", path, f"{len(rows)} member exposures")


# ---------------------------------------------------------------------------
# 2. Dither pattern
# ---------------------------------------------------------------------------

def plot_dithers(ctx, out_dir, report):
    service = ctx.service
    if ctx.registration_row is None:
        report.skipped("dither_pattern", "no dither_registration Artifact for this observation")
        return

    nominal = load(service, ctx.registration_row, "nominal_offsets")
    refined = load(service, ctx.registration_row, "refined_offsets")
    success = load(service, ctx.registration_row, "registration_success").astype(bool)
    registration_summary = summary_of(service, ctx.registration_row)
    exposure_ids = ctx.exposure_ids

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 6.0))

    ax = axes[0]
    for index, exposure_id in enumerate(exposure_ids):
        color = f"C{index % 10}"
        ax.scatter(*nominal[index], marker="x", color=color, s=80)
        if success[index]:
            ax.scatter(*refined[index], marker="o", color=color, s=80)
            ax.plot([nominal[index, 0], refined[index, 0]], [nominal[index, 1], refined[index, 1]],
                    color=color, linestyle="--", linewidth=1)
        ax.annotate(exposure_id, refined[index] if success[index] else nominal[index],
                    fontsize=7, color=color, xytext=(4, 4), textcoords="offset points")
    ax.set_xlabel("dither offset x (arcsec)")
    ax.set_ylabel("dither offset y (arcsec)")
    ax.set_title("nominal (x) vs refined (o) offsets")
    ax.axhline(0, color="0.8", linewidth=0.5)
    ax.axvline(0, color="0.8", linewidth=0.5)
    ax.set_aspect("equal", adjustable="datalim")

    ax = axes[1]
    for index, exposure_id in enumerate(exposure_ids):
        row = get_product(service, "fiber_sky_coordinates", exposure_id=exposure_id)
        if row is None:
            continue
        focal = load(service, row, "focal_plane_coordinates")
        offset = refined[index] if success[index] else nominal[index]
        ax.scatter(focal[:, 0] + offset[0], focal[:, 1] + offset[1], s=2, alpha=0.4,
                   color=f"C{index % 10}", label=exposure_id)
    ax.set_xlabel("focal-plane x + dither offset (arcsec)")
    ax.set_ylabel("focal-plane y + dither offset (arcsec)")
    ax.set_title("fiber footprints shifted by dither offset")
    ax.legend(fontsize=6, markerscale=3, loc="upper right")
    ax.set_aspect("equal", adjustable="datalim")

    fig.suptitle(
        f"Observation {ctx.observation_id}: dither pattern "
        f"(registration RMS={registration_summary.get('registration_residual_rms_arcsec', float('nan')):.3f}\")"
    )
    path = out_dir / "dither_pattern.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    report.generated("dither_pattern", path, f"{success.sum()}/{len(success)} exposures catalog-registered")


# ---------------------------------------------------------------------------
# 1. Astrometry (per exposure)
# ---------------------------------------------------------------------------

def plot_astrometry(service, exposure_id, out_dir, report):
    initial_row = get_product(service, "initial_astrometry", exposure_id=exposure_id)
    final_row = get_product(service, "final_astrometry", exposure_id=exposure_id)
    coords_row = get_product(service, "fiber_sky_coordinates", exposure_id=exposure_id)
    detections_row = get_product(service, "source_detection_catalog", exposure_id=exposure_id)
    matches_row = get_product(service, "catalog_match_table", exposure_id=exposure_id)

    if initial_row is None or final_row is None or coords_row is None:
        report.skipped(f"{exposure_id}/astrometry", "missing initial/final astrometry or fiber_sky_coordinates")
        return

    focal = load(service, coords_row, "focal_plane_coordinates")
    detections = load(service, detections_row, "detections") if detections_row is not None else np.empty((0, 8))
    final_summary = summary_of(service, final_row)
    refined = bool(final_summary.get("refined"))

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 6.0))

    ax = axes[0]
    ax.scatter(focal[:, 0], focal[:, 1], s=2, color="0.7", label="fibers")
    if detections.size:
        ax.scatter(detections[:, 2], detections[:, 3], s=30, marker="*", color="crimson", label="detections")
    ax.set_xlabel("focal-plane x (arcsec)")
    ax.set_ylabel("focal-plane y (arcsec)")
    ax.set_title(f"astrometry: {'catalog-refined' if refined else 'header-only/degraded'}")
    ax.legend(fontsize=8)
    ax.set_aspect("equal", adjustable="datalim")

    ax = axes[1]
    if matches_row is not None:
        matches = load(service, matches_row, "matches")
        accepted = matches[matches[:, 6].astype(bool)] if matches.size else np.empty((0, 9))
        if accepted.size:
            ax.quiver(
                np.zeros(accepted.shape[0]), np.zeros(accepted.shape[0]),
                accepted[:, 3], accepted[:, 4], angles="xy", scale_units="xy", scale=1,
                color="steelblue", width=0.004,
            )
            ax.scatter(accepted[:, 3], accepted[:, 4], s=15, color="steelblue")
            rms = float(np.sqrt(np.nanmean(np.square(accepted[:, 7]))))
            ax.set_title(f"accepted catalog residuals (rms={rms:.3f}\", n={accepted.shape[0]})")
        else:
            ax.set_title("catalog match table present, no accepted matches")
        ax.set_xlabel("dRA (arcsec)")
        ax.set_ylabel("dDec (arcsec)")
        ax.axhline(0, color="0.8", linewidth=0.5)
        ax.axvline(0, color="0.8", linewidth=0.5)
        ax.set_aspect("equal", adjustable="datalim")
    else:
        ax.text(0.5, 0.5, "no catalog_match_table available", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()

    fig.suptitle(f"Exposure {exposure_id}: astrometry")
    path = out_dir / "astrometry.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    report.generated(f"{exposure_id}/astrometry", path, "catalog-refined" if refined else "header-only/degraded")


# ---------------------------------------------------------------------------
# 3 & 4. Collapsed calibrated-fiber focal plane, and illumination
# ---------------------------------------------------------------------------

def _robust_range(values):
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    low, high = np.nanpercentile(finite, [2.0, 98.0])
    if low == high:
        return float(low - 1.0), float(high + 1.0)
    return float(low), float(high)


def plot_collapsed_focal_plane(service, exposure_id, out_dir, report, ctx):
    if ctx.calibrated_observation_row is None:
        report.skipped(f"{exposure_id}/collapsed_focal_plane", "no calibrated_fiber_observation for this observation")
        return

    member_ids = summary_of(service, ctx.calibrated_observation_row).get("member_exposure_ids") or []
    if exposure_id not in member_ids:
        report.skipped(f"{exposure_id}/collapsed_focal_plane", "exposure is not a member of calibrated_fiber_observation")
        return
    exposure_index = int(member_ids.index(exposure_id))

    exposure_selection = load(service, ctx.calibrated_observation_row, "exposure_index") == exposure_index
    if not np.any(exposure_selection):
        report.skipped(f"{exposure_id}/collapsed_focal_plane", "no retained fiber rows for this exposure")
        return

    flux = load(service, ctx.calibrated_observation_row, "flux")[exposure_selection]
    mask = load(service, ctx.calibrated_observation_row, "mask")[exposure_selection]
    focal = load(service, ctx.calibrated_observation_row, "focal_plane_coordinates")[exposure_selection]

    usable = mask == 0
    with np.errstate(invalid="ignore"):
        collapsed = np.where(usable, flux, np.nan)
    collapsed = np.nanmedian(collapsed, axis=1)

    vmin, vmax = _robust_range(collapsed)
    fig, ax = plt.subplots(figsize=FIGSIZE)

    fibers = EllipseCollection(
        widths=np.full(len(focal), 1.5),
        heights=np.full(len(focal), 1.5),
        angles=np.zeros(len(focal)),
        units="xy",
        offsets=focal,
        offset_transform=ax.transData,
        array=collapsed,
        cmap="viridis",
        edgecolors="none",
        rasterized=True,
    )

    fibers.set_clim(vmin, vmax)
    ax.add_collection(fibers)

    ax.update_datalim(focal)
    ax.autoscale_view()
    ax.set_aspect("equal")
    cax = inset_axes(ax, width="25%", height="3%", loc="upper left", bbox_to_anchor=(0.02, -0.1, 1, 1), 
                     bbox_transform=ax.transAxes, borderpad=0)

    cbar = fig.colorbar(fibers, cax=cax, orientation="horizontal")

    cbar.ax.xaxis.set_ticks_position("top")
    cbar.ax.xaxis.set_label_position("top")
    cbar.set_label("median calibrated flux")

    detections_row = get_product(service, "source_detection_catalog", exposure_id=exposure_id)
    if detections_row is not None:
        detections = load(service, detections_row, "detections")
        if detections.size:
            ax.scatter(detections[:, 2], detections[:, 3], s=60, marker="*", color="crimson", label="detections")

    extraction_row = get_product(service, "point_source_extraction", exposure_id=exposure_id)
    if extraction_row is not None:
        extraction_summary = summary_of(service, extraction_row)
        source_x, source_y = extraction_summary.get("source_focal_x"), extraction_summary.get("source_focal_y")
        if source_x is not None and source_y is not None:
            ax.scatter([source_x], [source_y], s=120, marker="+", color="white", linewidths=2, label="extraction position")

    ax.set_xlabel("focal-plane x (arcsec)")
    ax.set_ylabel("focal-plane y (arcsec)")
    ax.set_title(f"Exposure {exposure_id}: collapsed calibrated-fiber focal plane (diagnostic only)")
    ax.legend(fontsize=8)
    ax.set_aspect("equal", adjustable="datalim")
    path = out_dir / "collapsed_focal_plane.png"

    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    report.generated(f"{exposure_id}/collapsed_focal_plane", path)


def plot_illumination(service, exposure_id, out_dir, report):
    illumination_row = get_product(service, "exposure_illumination_correction", exposure_id=exposure_id)
    coords_row = get_product(service, "fiber_sky_coordinates", exposure_id=exposure_id)
    if illumination_row is None or coords_row is None:
        report.skipped(f"{exposure_id}/illumination", "missing exposure_illumination_correction or fiber_sky_coordinates")
        return

    fiber_factor = load(service, illumination_row, "fiber_factor")
    illumination_identity = load(service, illumination_row, "fiber_identity")
    coords_identity = load(service, coords_row, "fiber_identity")
    focal = load(service, coords_row, "focal_plane_coordinates")

    if illumination_identity.shape != coords_identity.shape or not np.array_equal(illumination_identity, coords_identity):
        report.skipped(f"{exposure_id}/illumination", "fiber_identity mismatch between illumination and coordinate products")
        return

    vmin, vmax = _robust_range(fiber_factor)
    vmin, vmax = (0.95, 1.05)
    fig, ax = plt.subplots(figsize=FIGSIZE)
    fibers = EllipseCollection(
        widths=np.full(len(focal), 1.5),
        heights=np.full(len(focal), 1.5),
        angles=np.zeros(len(focal)),
        units="xy",
        offsets=focal,
        offset_transform=ax.transData,
        array=fiber_factor,
        cmap="magma",
        edgecolors="none",
        rasterized=True,
    )

    fibers.set_clim(vmin, vmax)
    ax.add_collection(fibers)

    ax.update_datalim(focal)
    ax.autoscale_view()
    ax.set_aspect("equal")
    cax = inset_axes(ax, width="25%", height="3%", loc="upper left", bbox_to_anchor=(0.02, -0.1, 1, 1),
                     bbox_transform=ax.transAxes, borderpad=0)

    cbar = fig.colorbar(fibers, cax=cax, orientation="horizontal")

    cbar.ax.xaxis.set_ticks_position("top")
    cbar.ax.xaxis.set_label_position("top")
    cbar.set_label("illumination factor")
    
    ax.set_xlabel("focal-plane x (arcsec)")
    ax.set_ylabel("focal-plane y (arcsec)")
    ax.set_title(f"Exposure {exposure_id}: focal-plane illumination correction")
    ax.set_aspect("equal", adjustable="datalim")
    path = out_dir / "illumination.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    report.generated(f"{exposure_id}/illumination", path)


# ---------------------------------------------------------------------------
# 5. Source detection and extraction geometry
# ---------------------------------------------------------------------------

def plot_source_geometry(service, exposure_id, out_dir, report):
    extraction_row = get_product(service, "point_source_extraction", exposure_id=exposure_id)
    coords_row = get_product(service, "fiber_sky_coordinates", exposure_id=exposure_id)
    detections_row = get_product(service, "source_detection_catalog", exposure_id=exposure_id)
    if extraction_row is None or coords_row is None:
        report.skipped(f"{exposure_id}/source_geometry", "no point_source_extraction (no source evidence) for this exposure")
        return

    extraction_summary = summary_of(service, extraction_row)
    source_x, source_y = extraction_summary.get("source_focal_x"), extraction_summary.get("source_focal_y")
    if source_x is None or source_y is None:
        report.skipped(f"{exposure_id}/source_geometry", "point_source_extraction has no recorded source position")
        return

    focal = load(service, coords_row, "focal_plane_coordinates")
    max_distance = float(SOURCE_EXTRACTION_CONFIGURATION.value["max_fiber_distance_arcsec"])
    excluded = select_source_fibers(focal[:, 0], focal[:, 1], float(source_x), float(source_y), max_distance_arcsec=max_distance)
    captured_fraction = load(service, extraction_row, "captured_fraction")

    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.scatter(focal[~excluded, 0], focal[~excluded, 1], s=10, color="steelblue", label="selected fibers")
    ax.scatter(focal[excluded, 0], focal[excluded, 1], s=4, color="0.8", label="excluded fibers")
    if detections_row is not None:
        detections = load(service, detections_row, "detections")
        if detections.size:
            ax.scatter(detections[:, 2], detections[:, 3], s=40, marker="*", color="crimson", label="detection candidates")
    ax.scatter([source_x], [source_y], s=150, marker="+", color="darkorange", linewidths=2, label="fitted centroid")
    ax.set_xlabel("focal-plane x (arcsec)")
    ax.set_ylabel("focal-plane y (arcsec)")
    ax.set_title(
        f"Exposure {exposure_id}: source geometry "
        f"(median captured fraction={np.nanmedian(captured_fraction):.2f})"
    )
    ax.legend(fontsize=8)
    ax.set_aspect("equal", adjustable="datalim")
    path = out_dir / "source_geometry.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    report.generated(f"{exposure_id}/source_geometry", path)


# ---------------------------------------------------------------------------
# 6. PSF and DAR
# ---------------------------------------------------------------------------

def plot_psf_dar(service, exposure_id, out_dir, report):
    dar_row = get_product(service, "dar_seed_model", exposure_id=exposure_id)
    psf_rows = get_products(service, "spatial_psf_measurement", exposure_id=exposure_id)
    chromatic_row = get_product(service, "chromatic_psf_model", exposure_id=exposure_id)
    if dar_row is None:
        report.skipped(f"{exposure_id}/psf_dar", "no dar_seed_model (no source evidence) for this exposure")
        return

    dar_wavelength = load(service, dar_row, "wavelength")
    dar_delta_x = load(service, dar_row, "delta_x")
    dar_delta_y = load(service, dar_row, "delta_y")
    dar_summary = summary_of(service, dar_row)
    source_x = dar_summary.get("source_focal_x")
    source_y = dar_summary.get("source_focal_y")

    fig, axes = plt.subplots(3, 1, figsize=(8.0, 10.0), sharex=True)

    ax = axes[0]
    ax.plot(dar_wavelength, dar_delta_x, color="steelblue", linestyle="--", label="DAR seed dx")
    ax.plot(dar_wavelength, dar_delta_y, color="darkorange", linestyle=":", label="DAR seed dy")
    ax.set_ylabel("DAR seed offset (arcsec)")
    ax.set_title(f"Exposure {exposure_id}: Remedy DAR seed vs wavelength")
    ax.legend(fontsize=7)

    interval_wave, interval_x, interval_y, interval_valid = [], [], [], []
    interval_fwhm, interval_chi2, interval_dof = [], [], []
    for row in psf_rows:
        interval_wave.append(float(load(service, row, "reference_wavelength")[0]))
        interval_x.append(float(load(service, row, "centroid_x")[0]))
        interval_y.append(float(load(service, row, "centroid_y")[0]))
        interval_valid.append(bool(load(service, row, "valid")[0]))
        interval_fwhm.append(float(load(service, row, "fwhm")[0]))
        interval_chi2.append(float(load(service, row, "chi2")[0]))
        interval_dof.append(float(load(service, row, "dof")[0]))

    ax = axes[1]
    final_fwhm = None
    chromatic_status = "missing"
    if interval_wave and source_x is not None and source_y is not None:
        interval_wave = np.asarray(interval_wave)
        order = np.argsort(interval_wave)
        interval_wave = interval_wave[order]
        interval_x = np.asarray(interval_x)[order]
        interval_y = np.asarray(interval_y)[order]
        interval_valid = np.asarray(interval_valid)[order]
        interval_fwhm = np.asarray(interval_fwhm)[order]
        interval_chi2 = np.asarray(interval_chi2)[order]
        interval_dof = np.asarray(interval_dof)[order]

        seed_x_at_interval = float(source_x) + np.interp(interval_wave, dar_wavelength, dar_delta_x)
        seed_y_at_interval = float(source_y) + np.interp(interval_wave, dar_wavelength, dar_delta_y)
        residual_x = interval_x - seed_x_at_interval
        residual_y = interval_y - seed_y_at_interval

        ax.scatter(interval_wave[interval_valid], residual_x[interval_valid], color="steelblue", label="measured residual x (valid)")
        ax.scatter(interval_wave[interval_valid], residual_y[interval_valid], color="darkorange", label="measured residual y (valid)")
        if (~interval_valid).any():
            ax.scatter(interval_wave[~interval_valid], residual_x[~interval_valid], color="steelblue", marker="x", label="residual x (degraded)")
            ax.scatter(interval_wave[~interval_valid], residual_y[~interval_valid], color="darkorange", marker="x", label="residual y (degraded)")

        if chromatic_row is not None:
            chromatic_summary = summary_of(service, chromatic_row)
            model = ChromaticPSFModel(
                residual_centroid_coefficients_x=load(service, chromatic_row, "residual_centroid_coefficients_x"),
                residual_centroid_coefficients_y=load(service, chromatic_row, "residual_centroid_coefficients_y"),
                fwhm_coefficients=load(service, chromatic_row, "fwhm_coefficients"),
                valid_wavelength_min=float(load(service, chromatic_row, "valid_wavelength_min")[0]),
                valid_wavelength_max=float(load(service, chromatic_row, "valid_wavelength_max")[0]),
                beta=float(load(service, chromatic_row, "beta")[0]),
            )
            zeros = np.zeros_like(dar_wavelength)
            model_absolute_x, model_absolute_y, final_fwhm, status = model.evaluate(dar_wavelength, zeros, zeros)
            seed_x_at_dar = float(source_x) + dar_delta_x
            seed_y_at_dar = float(source_y) + dar_delta_y
            model_residual_x = model_absolute_x - seed_x_at_dar
            model_residual_y = model_absolute_y - seed_y_at_dar
            in_range = ~status.astype(bool)
            # Outside the fitted wavelength range evaluate() returns residual=0 by
            # construction (no measurement constrains it there), so model_residual
            # there is just -seed and carries no information; only plot in-range.
            ax.plot(dar_wavelength[in_range], model_residual_x[in_range], color="steelblue", label="fitted residual x")
            ax.plot(dar_wavelength[in_range], model_residual_y[in_range], color="darkorange", label="fitted residual y")
            chromatic_status = chromatic_summary.get("status")
            display_values = np.concatenate([residual_x[interval_valid], residual_y[interval_valid], model_residual_x[in_range], model_residual_y[in_range]])
            low, high = _robust_range(display_values)
            ax.set_ylim(low, high)
    else:
        ax.text(0.5, 0.5, "no valid PSF interval measurements or source position", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()

    ax.set_ylabel("centroid residual (arcsec)")
    ax.set_title(f"measured vs fitted centroid residual (chromatic model: {chromatic_status})")
    ax.legend(fontsize=6, ncol=2)

    ax = axes[2]
    if len(interval_wave):
        ax.scatter(interval_wave, interval_fwhm, color="seagreen", label="measured FWHM (arcsec)")
        with np.errstate(divide="ignore", invalid="ignore"):
            reduced_chi2 = np.where(interval_dof > 0, interval_chi2 / interval_dof, np.nan)
        twin = ax.twinx()
        twin.scatter(interval_wave, reduced_chi2, color="firebrick", marker="^", label="reduced chi2")
        twin.set_ylabel("reduced chi2", color="firebrick")
    if final_fwhm is not None:
        ax.plot(dar_wavelength, final_fwhm, color="seagreen", linestyle="--", label="chromatic model FWHM")
    ax.set_xlabel("wavelength (Angstrom)")
    ax.set_ylabel("FWHM (arcsec)", color="seagreen")
    ax.legend(fontsize=7, loc="upper left")

    fig.tight_layout()
    path = out_dir / "psf_dar.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    report.generated(f"{exposure_id}/psf_dar", path, f"{len(psf_rows)} fitted wavelength intervals")


# ---------------------------------------------------------------------------
# 7. Extracted source spectra
# ---------------------------------------------------------------------------

def plot_source_spectra(service, exposure_id, out_dir, report):
    extraction_row = get_product(service, "point_source_extraction", exposure_id=exposure_id)
    if extraction_row is None:
        report.skipped(f"{exposure_id}/source_spectrum", "no point_source_extraction (no source evidence) for this exposure")
        return

    wavelength = load(service, extraction_row, "wavelength")
    amplitude = load(service, extraction_row, "amplitude")
    variance = load(service, extraction_row, "variance")
    mask = load(service, extraction_row, "mask")
    captured_fraction = load(service, extraction_row, "captured_fraction")
    uncertainty = np.sqrt(np.where(variance > 0, variance, np.nan))
    usable = mask == 0

    fig, axes = plt.subplots(2, 1, figsize=(9.0, 7.0), sharex=True)
    ax = axes[0]
    ax.plot(wavelength[usable], amplitude[usable], color="steelblue", linewidth=0.8, label="flux")
    ax.fill_between(
        wavelength[usable], (amplitude - uncertainty)[usable], (amplitude + uncertainty)[usable],
        color="steelblue", alpha=0.25, label="1-sigma uncertainty",
    )
    if (~usable).any():
        ax.scatter(wavelength[~usable], amplitude[~usable], s=6, color="crimson", label="masked samples")
    ax.set_ylabel("flux (1e-17 response-corrected electron)")
    ax.set_title(f"Exposure {exposure_id}: extracted point-source spectrum")
    ax.legend(fontsize=8)

    ax = axes[1]
    ax.plot(wavelength, captured_fraction, color="darkorange")
    ax.set_ylabel("captured fraction")
    ax.set_xlabel("wavelength (Angstrom)")

    fig.tight_layout()
    path = out_dir / "source_spectrum.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    report.generated(f"{exposure_id}/source_spectrum", path, f"median captured fraction={np.nanmedian(captured_fraction):.2f}")


def plot_observation_source_spectrum(ctx, out_dir, report):
    service = ctx.service
    if ctx.combined_spectrum_row is None:
        report.skipped("observation_source_spectrum", "no observation_source_spectrum for this observation")
        return

    combined_summary = summary_of(service, ctx.combined_spectrum_row)
    wavelength = load(service, ctx.combined_spectrum_row, "wavelength")
    amplitude = load(service, ctx.combined_spectrum_row, "amplitude")
    variance = load(service, ctx.combined_spectrum_row, "variance")
    mask = load(service, ctx.combined_spectrum_row, "mask")
    uncertainty = np.sqrt(np.where(variance > 0, variance, np.nan))
    usable = mask == 0

    fig, ax = plt.subplots(figsize=(9.0, 5.0))
    for index, exposure_id in enumerate(ctx.exposure_ids):
        extraction_row = get_product(service, "point_source_extraction", exposure_id=exposure_id)
        if extraction_row is None:
            continue
        exposure_wavelength = load(service, extraction_row, "wavelength")
        exposure_amplitude = load(service, extraction_row, "amplitude")
        ax.plot(exposure_wavelength, exposure_amplitude, linewidth=0.5, alpha=0.5,
                color=f"C{index % 10}", label=f"{exposure_id}")

    ax.plot(wavelength[usable], amplitude[usable], color="black", linewidth=1.0, label="observation combined")
    ax.fill_between(
        wavelength[usable], (amplitude - uncertainty)[usable], (amplitude + uncertainty)[usable],
        color="black", alpha=0.15,
    )
    ax.set_xlabel("wavelength (Angstrom)")
    ax.set_ylabel("flux (1e-17 response-corrected electron)")
    ax.set_title(
        f"Observation {ctx.observation_id}: combined source spectrum (status={combined_summary.get('status')})"
    )
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    path = out_dir / "observation_source_spectrum.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    report.generated("observation_source_spectrum", path, f"status={combined_summary.get('status')}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def write_summary(out_dir, ctx, report):
    lines = [
        f"# Diagnostics for observation {ctx.observation_id}",
        "",
        f"Member exposures: {', '.join(ctx.exposure_ids) if ctx.exposure_ids else '(none found)'}",
        "",
    ]
    if ctx.summary_row is not None:
        observation_summary = summary_of(ctx.service, ctx.summary_row)
        lines += [
            f"observation_summary: usable={observation_summary.get('observation_usable')}, "
            f"registration_consistent={observation_summary.get('registration_consistent')}",
            "",
        ]

    generated = [entry for entry in report.entries if entry[0] == "generated"]
    skipped = [entry for entry in report.entries if entry[0] == "skipped"]
    failed = [entry for entry in report.entries if entry[0] == "failed"]

    lines.append("## Generated")
    for _, category, path, detail in generated:
        relative = Path(path).relative_to(out_dir) if path else path
        lines.append(f"- **{category}** -> `{relative}`" + (f" ({detail})" if detail else ""))
    if not generated:
        lines.append("- (none)")

    lines.append("")
    lines.append("## Skipped (evidence unavailable)")
    for _, category, _, reason in skipped:
        lines.append(f"- **{category}**: {reason}")
    if not skipped:
        lines.append("- (none)")

    if failed:
        lines.append("")
        lines.append("## Failed while generating")
        for _, category, _, reason in failed:
            first_line = reason.splitlines()[0] if reason else reason
            lines.append(f"- **{category}**: {first_line}")

    (out_dir / "index.md").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("observation_id", help="Observation identity string, e.g. 20260609-OBSID6")
    parser.add_argument("--db", default=os.environ.get("VIRUSFLOW_DB", "./run/virusflow.sqlite3"))
    parser.add_argument("--raw-db", default=os.environ.get("VIRUSFLOW_RAW_DB", "./run/virusflow_raw.sqlite3"))
    parser.add_argument("--workdir", default=os.environ.get("VIRUSFLOW_WORKDIR", "./run/artifacts"))
    parser.add_argument("--configuration-root", default=os.environ.get("VIRUSFLOW_CONFIG_ROOT", "."))
    parser.add_argument("--output-dir", default=None, help="Defaults to ./run/diagnostics/<observation-id>")
    args = parser.parse_args(argv)

    out_dir = Path(args.output_dir) if args.output_dir else Path("run") / "diagnostics" / args.observation_id
    out_dir.mkdir(parents=True, exist_ok=True)

    service = ArtifactService(args.db)
    ctx = load_observation_context(service, args.observation_id)
    if ctx is None:
        print(f"No observation_membership Artifact found for observation {args.observation_id!r}", file=sys.stderr)
        return 1

    report = DiagnosticReport()
    run_diagnostic(report, "observation_summary", plot_observation_summary, ctx, out_dir, report)
    run_diagnostic(report, "dither_pattern", plot_dithers, ctx, out_dir, report)

    for exposure_id in ctx.exposure_ids:
        exposure_dir = out_dir / exposure_id
        exposure_dir.mkdir(parents=True, exist_ok=True)
        run_diagnostic(report, f"{exposure_id}/astrometry", plot_astrometry, service, exposure_id, exposure_dir, report)
        run_diagnostic(report, f"{exposure_id}/collapsed_focal_plane", plot_collapsed_focal_plane, service, exposure_id, exposure_dir, report, ctx)
        run_diagnostic(report, f"{exposure_id}/illumination", plot_illumination, service, exposure_id, exposure_dir, report)
        run_diagnostic(report, f"{exposure_id}/source_geometry", plot_source_geometry, service, exposure_id, exposure_dir, report)
        run_diagnostic(report, f"{exposure_id}/psf_dar", plot_psf_dar, service, exposure_id, exposure_dir, report)
        run_diagnostic(report, f"{exposure_id}/source_spectrum", plot_source_spectra, service, exposure_id, exposure_dir, report)

    run_diagnostic(report, "observation_source_spectrum", plot_observation_source_spectrum, ctx, out_dir, report)

    write_summary(out_dir, ctx, report)
    print(f"Diagnostics written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
