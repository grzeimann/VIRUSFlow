#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare a published PSF ``point_source_extraction`` against a direct
aperture sum over the same selected fibers, for one exposure.

The direct-aperture comparison array (per-fiber flux/variance/wavelength) is
only persisted at OBSERVATION scope, as part of ``calibrated_fiber_observation``
(the per-exposure ``CalibratedFiberState`` used to build it is run-local and
never itself written to storage). This script therefore requires the
exposure to be a member of a completed observation.

Usage:
  python scripts/compare_aperture_and_psf_extraction.py --exposure-id EXPOSURE_ID [--db PATH]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from virusflow.artifacts import ArtifactService, Scope
from virusflow.algorithms.source_extraction import select_source_fibers, sum_aperture_flux
from virusflow.config.defaults import SOURCE_EXTRACTION_CONFIGURATION


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exposure-id", required=True, help="Exposure identity string")
    parser.add_argument(
        "--db", default=os.environ.get("VIRUSFLOW_DB", str(Path.cwd() / "virusflow.sqlite3")),
        help="Artifact/Product registry SQLite path",
    )
    args = parser.parse_args()

    service = ArtifactService(args.db)
    exposure_scope = Scope(exposure_id=args.exposure_id)
    extraction_row = service.select_best(kind="point_source_extraction", scope=exposure_scope, policy="latest")
    if extraction_row is None:
        print(f"No point_source_extraction published for exposure {args.exposure_id}", file=sys.stderr)
        return 1
    extraction = service.describe(extraction_row)
    psf_amplitude = service.load_component(extraction_row, "amplitude")["data"]
    psf_captured_fraction = service.load_component(extraction_row, "captured_fraction")["data"]
    source_x = extraction["summary"].get("source_focal_x")
    source_y = extraction["summary"].get("source_focal_y")

    observation_row = None
    for row in service.adapter.list_all(kind="calibrated_fiber_observation"):
        if str(row.get("state") or "active") != "active":
            continue
        member_ids = service.describe(row)["summary"].get("member_exposure_ids") or []
        if args.exposure_id in member_ids:
            observation_row = row
            break
    if observation_row is None:
        print(
            f"No calibrated_fiber_observation with exposure {args.exposure_id} as a member "
            "was found; aperture comparison requires a completed observation.",
            file=sys.stderr,
        )
        return 1

    member_ids = service.describe(observation_row)["summary"]["member_exposure_ids"]
    exposure_index = int(member_ids.index(args.exposure_id))

    all_flux = service.load_component(observation_row, "flux")["data"]
    all_variance = service.load_component(observation_row, "variance")["data"]
    all_wavelength = service.load_component(observation_row, "wavelength")["data"]
    all_focal = service.load_component(observation_row, "focal_plane_coordinates")["data"]
    all_exposure_index = service.load_component(observation_row, "exposure_index")["data"]

    selection = all_exposure_index == exposure_index
    flux = all_flux[selection]
    variance = all_variance[selection]
    wavelength = np.nanmedian(all_wavelength[selection], axis=0)
    focal = all_focal[selection]

    if source_x is None or source_y is None:
        print("point_source_extraction metadata has no recorded source position", file=sys.stderr)
        return 1

    max_distance = float(SOURCE_EXTRACTION_CONFIGURATION.value["max_fiber_distance_arcsec"])
    exclusion_mask = select_source_fibers(
        focal[:, 0], focal[:, 1], float(source_x), float(source_y), max_distance_arcsec=max_distance,
    )
    aperture_result = sum_aperture_flux(flux, variance, fiber_mask=exclusion_mask)
    aperture_amplitude = aperture_result.get_array("amplitude")

    print(f"Exposure {args.exposure_id}: point_source_extraction id={extraction_row['id']}")
    print(f"Source position (focal arcsec): x={source_x}, y={source_y}")
    print(f"{'wavelength':>12} {'psf_amplitude':>15} {'aperture_sum':>15} {'aperture/captured':>18} {'ratio':>10}")
    step = max(1, wavelength.shape[0] // 20)
    for i in range(0, wavelength.shape[0], step):
        captured = psf_captured_fraction[i] if np.isfinite(psf_captured_fraction[i]) and psf_captured_fraction[i] > 0 else np.nan
        corrected_aperture = aperture_amplitude[i] / captured if np.isfinite(captured) else np.nan
        ratio = corrected_aperture / psf_amplitude[i] if psf_amplitude[i] not in (0.0,) and np.isfinite(corrected_aperture) else np.nan
        print(f"{wavelength[i]:12.2f} {psf_amplitude[i]:15.4f} {aperture_amplitude[i]:15.4f} {corrected_aperture:18.4f} {ratio:10.4f}")

    print(f"\nMedian PSF captured fraction: {float(np.nanmedian(psf_captured_fraction)):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
