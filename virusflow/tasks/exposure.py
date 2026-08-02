"""Canonical full-width baseline reduction for one atomic VIRUS Exposure."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
from typing import Iterable

import numpy as np

from .science import PhysicalCCDTask, ReducedScienceAmplifierTask, _SciencePublisher, _instant
from ..algorithms.exposure import (
    ASTROMETRY_VERSION,
    CalibratedFiberState,
    LatentSkyModel,
    NORMALIZATION_VERSION,
    RESPONSE_VERSION,
    SKY_VERSION,
    amplifier_normalization,
    compact_fiber_response,
    classify_mode_and_effective_time,
    detect_fiber_sources,
    derive_sky_oversampling_factor,
    extract_fractional_aperture,
    fit_catalog_astrometry,
    oversampled_incident_sky,
    parse_header_pointing,
    select_sky_fibers,
    tan_fiber_coordinates,
    within_amplifier_normalization,
    wavelength_bin_edges,
)
from ..algorithms.physical_ccd import assemble_physical_ccd, fit_gap_scattered_light
from ..artifacts import ArtifactService, Scope, Validity
from ..artifacts.requests import ArtifactRequest, LogicalComponent
from ..artifacts.storage_conventions import FLUX_SCALE, VARIANCE_SCALE
from ..config import ConfigurationService
from ..config.defaults import BASELINE_RESPONSE_CONFIGURATION, EFFECTIVE_EXPOSURE_POLICY
from ..core.scientific_metadata import scientific_metadata_from_header
from ..io import PanSTARRSCSVProvider, RawFrameLoader
from ..ontology.scopes import PhysicalScope
from ..planning.targets import PhysicalCCDTarget


AMP_CODE = {"LL": 0, "LU": 1, "RU": 2, "RL": 3}
CALIBRATION_KINDS = (
    "master_bias", "master_dark", "master_ldls", "master_arc",
    "master_twilight", "trace_map", "wavelength_map",
)


def _component(name, value, units, coordinates, **metadata):
    array = np.asarray(value)
    if array.ndim > 2:
        raise ValueError(f"Component {name} must be flattened to at most two dimensions")
    return LogicalComponent(
        name, "array1d" if array.ndim == 1 else "array2d", array,
        units, coordinates, metadata,
    )


def _mask_component(name, value, units="1", coordinates="none", **metadata):
    return LogicalComponent(name, "mask", np.asarray(value), units, coordinates, metadata)


class ExposureTask(_SciencePublisher):
    name = "full_exposure"
    version = "v1"

    @staticmethod
    def _no_extractable_message(exposure_id: str, failures: dict, *, reason: str | None = None) -> str:
        calibration_hint = "; run 'virusflow run calibrations' first"
        include_calibration_hint = False
        by_failure = defaultdict(set)
        for zipcode_key, messages in failures.items():
            for message in messages:
                message = str(message)
                if message.endswith(calibration_hint):
                    message = message.removesuffix(calibration_hint)
                    include_calibration_hint = True
                by_failure[message].add(str(zipcode_key))
        details = []
        for message, zipcode_keys in sorted(
            by_failure.items(), key=lambda item: (-len(item[1]), item[0])
        )[:8]:
            count = len(zipcode_keys)
            details.append(f"{message} ({count} amplifier{'s' if count != 1 else ''})")
        omitted = max(0, len(by_failure) - len(details))
        if omitted:
            details.append(f"{omitted} additional failure reason{'s' if omitted != 1 else ''}")
        context = f": {reason}" if reason else ""
        summary = f"; failures: {'; '.join(details)}" if details else ""
        next_step = calibration_hint if include_calibration_hint else ""
        return f"Exposure {exposure_id} produced no extractable amplifier{context}{summary}{next_step}"

    def _request(
        self,
        *,
        kind: str,
        components: dict,
        scope: Scope,
        parents: Iterable[int] = (),
        summaries: dict | None = None,
        metadata: dict | None = None,
        refs=(),
        assumptions=(),
        algorithm: str,
        version: str,
        status: str = "pass",
        usability: str = "usable",
    ):
        at = self.target.at_time or _instant(self.target.exposure_id)
        request = ArtifactRequest(
            kind=kind, role="reduction", components=components,
            summaries=dict(summaries or {}), metadata=dict(metadata or {}), scope=scope,
            scientific_metadata=(
                dict(getattr(self, "_exposure_scientific_metadata", {}) or {})
                if scope.exposure_id == getattr(self.target, "exposure_id", None)
                else {}
            ),
            parents=[int(value) for value in parents], validity=Validity(at, at, "exposure_identity"),
            configuration_refs=list(refs), assumptions=list(assumptions),
        )
        artifact = self._publish(request, algorithm_name=algorithm, algorithm_version=version)
        self._qa(artifact, request.summaries, status=status, usability=usability)
        return artifact

    def _ensure_calibrations(self, zipcodes, at: datetime):
        from ..performance import phase

        service = ArtifactService(self.ctx.db_path)
        available = {}
        failures = {}
        for zipcode in zipcodes:
            kinds = {}
            for kind in CALIBRATION_KINDS:
                with phase("calibration_selection"):
                    existing = service.select_best(
                        kind=kind, scope=Scope(zipcode=zipcode), at_time=at,
                        policy="latest_valid",
                    )
                    if existing is None:
                        existing = service.select_best(
                            kind=kind, scope=Scope(zipcode=zipcode), at_time=at,
                            policy="nearest",
                        )
                if existing is None:
                    failures.setdefault(zipcode.key(), []).append(
                        f"{kind}: missing published calibration; run 'virusflow run calibrations' first"
                    )
                    continue
                kinds[kind] = existing
            available[zipcode.key()] = kinds
        return available, failures

    @staticmethod
    def _amp_from_physical(array, amp: str):
        data = np.asarray(array)
        half = data.shape[0] // 2
        return data[:half] if amp in {"LL", "RU"} else data[half:][::-1]

    def run(self, inputs):
        from ..registry import database as db

        exposure_id = self.target.exposure_id
        at = self.target.at_time or _instant(exposure_id)
        raw_rows = [
            raw for raw in db.list_raw_files(exposure_id=exposure_id, db_path=self.ctx.resolved_raw_db_path())
            if raw.frame_type == "sci" and raw.zipcode is not None
        ]
        if not raw_rows:
            raise RuntimeError(f"No science inputs for Exposure {exposure_id}")
        raw_rows.sort(key=lambda row: row.zipcode.key())
        zipcodes = [row.zipcode for row in raw_rows]
        if len({zipcode.key() for zipcode in zipcodes}) != len(zipcodes):
            raise RuntimeError(f"Repeated amplifier identity in Exposure {exposure_id}")

        loader = self.ctx.config.get("raw_frame_loader") if isinstance(self.ctx.config, dict) else None
        representative = (loader or RawFrameLoader()).load_ref(raw_rows[0])
        header = representative.header
        self._exposure_scientific_metadata = scientific_metadata_from_header(header)
        service = ArtifactService(self.ctx.db_path)
        config_root = self.ctx.config.get("configuration_root") if isinstance(self.ctx.config, dict) else None
        config = ConfigurationService(root=config_root)
        fplane_path = self.ctx.config.get("fplane_path") if isinstance(self.ctx.config, dict) else None
        fplane, fplane_ref = config.resolve_fplane(fplane_path)
        fiber_offsets_by_ifuid = {}
        fiber_refs = []
        for ifuid in sorted({zipcode.ifuid for zipcode in zipcodes}):
            fiber_offsets_by_ifuid[ifuid], fiber_ref = config.fiber_offsets(ifuid)
            fiber_refs.append(fiber_ref)
        exposure_refs = config.exposure_references() + [fplane_ref, *fiber_refs]

        calibration, failures = self._ensure_calibrations(zipcodes, at)
        complete_calibration_keys = {
            key for key, kinds in calibration.items()
            if all(kind in kinds for kind in CALIBRATION_KINDS)
        }
        if not complete_calibration_keys:
            raise RuntimeError(self._no_extractable_message(
                exposure_id, failures, reason="no amplifier has complete calibration coverage",
            ))
        reduced = {}
        for zipcode in zipcodes:
            try:
                state = ReducedScienceAmplifierTask(
                    self.ctx, target=SimpleNamespace(zipcode=zipcode, exposure_id=exposure_id),
                    params={"apply_calibrations": True},
                ).run(calibration.get(zipcode.key(), {}))["reduced_science_state"]
                reduced[zipcode.key()] = state
            except Exception as exc:
                failures.setdefault(zipcode.key(), []).append(f"reduced_science_state: {type(exc).__name__}: {exc}")

        groups = {}
        for zipcode in zipcodes:
            key = (zipcode.ifuslot, zipcode.ifuid, zipcode.specid, zipcode.controller)
            groups.setdefault(key, {})[zipcode.amp] = zipcode

        physical = {}
        for identity, amps in sorted(groups.items()):
            for side, pair in (("left", ("LL", "LU")), ("right", ("RU", "RL"))):
                if not all(amp in amps for amp in pair):
                    for amp in pair:
                        if amp in amps:
                            failures.setdefault(amps[amp].key(), []).append(f"missing physical-CCD partner for {side}")
                    continue
                lower, upper = amps[pair[0]], amps[pair[1]]
                if any(
                    "trace_map" not in calibration.get(zipcode.key(), {})
                    for zipcode in (lower, upper)
                ) or lower.key() not in reduced or upper.key() not in reduced:
                    failures.setdefault(lower.key(), []).append(f"{side} physical CCD unavailable from calibration coverage")
                    failures.setdefault(upper.key(), []).append(f"{side} physical CCD unavailable from calibration coverage")
                    continue
                try:
                    target = PhysicalCCDTarget(exposure_id, lower.specid, side, lower, upper, at)
                    result = PhysicalCCDTask(self.ctx, target=target).run({
                        "lower_state": reduced[lower.key()],
                        "upper_state": reduced[upper.key()],
                        "lower_trace": calibration[lower.key()]["trace_map"],
                        "upper_trace": calibration[upper.key()]["trace_map"],
                    })
                    physical[(identity, side)] = {
                        "model": result["ccd_scattered_light_model"],
                        "state": result["physical_ccd_state"],
                    }
                except Exception as exc:
                    failures.setdefault(lower.key(), []).append(f"{side} CCD: {type(exc).__name__}: {exc}")
                    failures.setdefault(upper.key(), []).append(f"{side} CCD: {type(exc).__name__}: {exc}")

        amp_results = {}
        wavelength_fiber_exclusions = {}
        reduction_parent_ids = []
        for identity, amps in sorted(groups.items()):
            for side, pair in (("left", ("LL", "LU")), ("right", ("RU", "RL"))):
                product = physical.get((identity, side))
                if product is None:
                    continue
                physical_state = product["state"]
                physical_image = np.asarray(physical_state.scatter.get_array("scatter_subtracted_image"), dtype=np.float32)
                physical_variance = np.asarray(physical_state.assembly.get_array("variance"), dtype=np.float32)
                physical_mask = np.asarray(physical_state.assembly.get_array("pixel_mask"), dtype=np.uint8)
                scatter_model_id = int(product["model"].id)
                reduction_parent_ids.append(scatter_model_id)
                lower, upper = amps[pair[0]], amps[pair[1]]
                lower_trace_row = calibration[lower.key()]["trace_map"]
                upper_trace_row = calibration[upper.key()]["trace_map"]
                lower_trace = service.load_component(lower_trace_row, "fiber_trace_map")["data"]
                upper_trace = service.load_component(upper_trace_row, "fiber_trace_map")["data"]

                # Scatter-correct the paired twilight reference in memory before normalization.
                lower_twi_row = calibration[lower.key()]["master_twilight"]
                upper_twi_row = calibration[upper.key()]["master_twilight"]
                lower_twi = service.load_component(lower_twi_row, "master_twilight")["data"]
                upper_twi = service.load_component(upper_twi_row, "master_twilight")["data"]
                twi_assembly = assemble_physical_ccd(
                    lower_twi, upper_twi, side=side, lower_amp=pair[0], upper_amp=pair[1],
                    lower_variance=np.ones_like(lower_twi), upper_variance=np.ones_like(upper_twi),
                )
                twi_scatter = fit_gap_scattered_light(twi_assembly, lower_trace, upper_trace)
                twi_subtracted = twi_scatter.get_array("scatter_subtracted_image")

                for zipcode, trace, trace_row, twi_row in (
                    (lower, lower_trace, lower_trace_row, lower_twi_row),
                    (upper, upper_trace, upper_trace_row, upper_twi_row),
                ):
                    if "wavelength_map" not in calibration.get(zipcode.key(), {}):
                        failures.setdefault(zipcode.key(), []).append(
                            "extraction unavailable from wavelength calibration coverage"
                        )
                        continue
                    image = self._amp_from_physical(physical_image, zipcode.amp)
                    variance = self._amp_from_physical(physical_variance, zipcode.amp)
                    mask = self._amp_from_physical(physical_mask, zipcode.amp)
                    extraction = extract_fractional_aperture(image, variance, trace, pixel_mask=mask, width=5.0)
                    twilight_image = self._amp_from_physical(twi_subtracted, zipcode.amp)
                    twilight_extraction = extract_fractional_aperture(
                        twilight_image, np.ones_like(twilight_image), trace, width=5.0
                    )
                    raw_ratio, within, normalization_valid, common_twi = within_amplifier_normalization(
                        twilight_extraction.spectrum
                    )
                    wave_row = calibration[zipcode.key()]["wavelength_map"]
                    wavelength = np.asarray(service.load_component(wave_row, "wavelength_map")["data"], dtype=np.float32)
                    if wavelength.shape != extraction.spectrum.shape:
                        failures.setdefault(zipcode.key(), []).append(
                            "wavelength map shape does not match extracted spectrum"
                        )
                        continue
                    finite_rows = np.all(np.isfinite(wavelength), axis=1)
                    increasing_rows = np.all(np.diff(wavelength, axis=1) > 0.0, axis=1)
                    valid_wavelength_rows = finite_rows & increasing_rows
                    excluded_rows = np.flatnonzero(~valid_wavelength_rows)
                    if excluded_rows.size:
                        wavelength_fiber_exclusions[zipcode.key()] = {
                            "excluded_count": int(excluded_rows.size),
                            "fiber_indices": excluded_rows.tolist(),
                            "non_finite_fiber_indices": np.flatnonzero(~finite_rows).tolist(),
                            "non_increasing_fiber_indices": np.flatnonzero(~increasing_rows).tolist(),
                            "wavelength_map_artifact_id": int(wave_row["id"]),
                        }
                    if not valid_wavelength_rows.any():
                        failures.setdefault(zipcode.key(), []).append(
                            "wavelength calibration has no finite, strictly increasing fiber rows"
                        )
                        continue
                    amp_results[zipcode.key()] = {
                        "zipcode": zipcode,
                        "spectrum": extraction.spectrum,
                        "variance": extraction.variance,
                        "valid_fraction": extraction.valid_pixel_fraction,
                        "within": within,
                        "wavelength": wavelength,
                        "valid_wavelength_rows": valid_wavelength_rows,
                        "twilight_level": float(np.nanmedian(common_twi)),
                        "parent_ids": [scatter_model_id, int(trace_row["id"]), int(wave_row["id"]), int(twi_row["id"])],
                        "normalization_valid": normalization_valid,
                        "twilight_scatter_sigma": float(twi_scatter.scalars["holdout_residual_robust_sigma"]),
                    }

        ordered_keys = sorted(amp_results)
        if not ordered_keys:
            raise RuntimeError(self._no_extractable_message(exposure_id, failures))
        levels = np.asarray([amp_results[key]["twilight_level"] for key in ordered_keys], dtype=float)
        amp_factors, reference_level = amplifier_normalization(levels)
        amp_identity = np.asarray([
            [int(amp_results[key]["zipcode"].ifuslot), int(amp_results[key]["zipcode"].specid), AMP_CODE[amp_results[key]["zipcode"].amp]]
            for key in ordered_keys
        ], dtype=np.int32)
        exposure_scope = Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.EXPOSURE)
        amp_artifact = self._request(
            kind="amp_to_amp_normalization", scope=exposure_scope,
            components={
                "amplifier_factors": _component("amplifier_factors", amp_factors, "1", "none"),
                "amplifier_twilight_levels": _component("amplifier_twilight_levels", levels, "electron", "none"),
                "reference_level": _component("reference_level", np.asarray([reference_level]), "electron", "none"),
                "amplifier_identity": _component("amplifier_identity", amp_identity, "1", "none"),
            },
            parents=sorted({parent for item in amp_results.values() for parent in item["parent_ids"]}),
            summaries={
                "amplifier_count": len(ordered_keys),
                "factor_median": float(np.nanmedian(amp_factors)),
                "factor_robust_sigma": float(1.4826 * np.nanmedian(np.abs(amp_factors - np.nanmedian(amp_factors)))),
            },
            refs=exposure_refs, assumptions=["Center-track twilight is uniform across the exposure."],
            algorithm="virusflow.algorithms.exposure.amplifier_normalization", version=NORMALIZATION_VERSION,
        )

        global_spectrum = []
        global_variance = []
        global_valid = []
        global_wavelength = []
        global_identity = []
        global_focal = []
        global_within = []
        normalization_parent_ids = [int(amp_artifact.id)]
        amp_index_by_key = {key: index for index, key in enumerate(ordered_keys)}
        for key, amp_factor in zip(ordered_keys, amp_factors):
            item = amp_results[key]
            final = item["within"] * amp_factor
            normalized = item["spectrum"] / final
            normalized_variance = item["variance"] / np.square(final)
            zipcode = item["zipcode"]
            normalization_parent_ids.extend(item["parent_ids"])
            if zipcode.ifuslot not in fplane:
                failures.setdefault(key, []).append("IFUSLOT absent from fplane configuration")
                continue
            valid_rows = item["valid_wavelength_rows"]
            original_fiber_indices = np.flatnonzero(valid_rows)
            n_fiber = original_fiber_indices.size
            local = fiber_offsets_by_ifuid[zipcode.ifuid][zipcode.amp]
            fp_x, fp_y = fplane[zipcode.ifuslot]
            focal = local[valid_rows] + np.asarray([fp_x, fp_y])
            identities = np.column_stack((
                np.full(n_fiber, amp_index_by_key[key]),
                original_fiber_indices,
                np.full(n_fiber, int(zipcode.ifuslot)),
                np.full(n_fiber, int(zipcode.specid)),
                np.full(n_fiber, AMP_CODE[zipcode.amp]),
            )).astype(np.int32)
            global_spectrum.append(normalized[valid_rows].astype(np.float32))
            global_variance.append(normalized_variance[valid_rows].astype(np.float32))
            global_valid.append(item["valid_fraction"][valid_rows].astype(np.float32))
            global_wavelength.append(item["wavelength"][valid_rows].astype(np.float32))
            global_identity.append(identities)
            global_focal.append(focal.astype(np.float32))
            global_within.append(item["within"][valid_rows].astype(np.float32))

        if not global_spectrum:
            raise RuntimeError(self._no_extractable_message(exposure_id, failures))
        spectrum = np.concatenate(global_spectrum)
        spectrum_variance = np.concatenate(global_variance)
        valid_fraction = np.concatenate(global_valid)
        wavelength = np.concatenate(global_wavelength)
        fiber_identity = np.concatenate(global_identity)
        focal = np.concatenate(global_focal)
        within_response = np.concatenate(global_within)

        ra0, dec0, pa, header_evidence = parse_header_pointing(header)
        initial_ra, initial_dec, initial_rotation = tan_fiber_coordinates(ra0, dec0, pa, focal[:, 0], focal[:, 1])
        initial_artifact = self._request(
            kind="initial_astrometry", scope=exposure_scope,
            components={
                "parameters": _component("parameters", np.asarray([ra0, dec0, pa, initial_rotation]), "deg", "icrs"),
                "header_evidence": _component("header_evidence", np.asarray([ra0, dec0, pa]), "deg", "icrs"),
            },
            parents=sorted(set(reduction_parent_ids)),
            summaries={"fiber_count": int(spectrum.shape[0]), "initial_ra": ra0, "initial_dec": dec0, "initial_pa": pa},
            metadata={"header_evidence": header_evidence}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.tan_fiber_coordinates", version=ASTROMETRY_VERSION,
        )
        broadband = np.nanmedian(spectrum, axis=1)
        detections = detect_fiber_sources(
            broadband, fiber_identity[:, 2], focal[:, 0], focal[:, 1],
            threshold_sigma=float(self.params.get("detection_sigma", 5.0)),
        )
        if detections.size:
            indices = detections[:, 0].astype(int)
            detections = np.column_stack((detections, initial_ra[indices], initial_dec[indices]))
        else:
            detections = np.empty((0, 8), dtype=float)
        detection_artifact = self._request(
            kind="source_detection_catalog", scope=exposure_scope,
            components={"detections": _component("detections", detections, "electron", "icrs")},
            parents=[int(initial_artifact.id), *sorted(set(reduction_parent_ids))],
            summaries={"detection_count": int(detections.shape[0])}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.detect_fiber_sources", version=ASTROMETRY_VERSION,
        )

        provider = self.ctx.config.get("catalog_provider") if isinstance(self.ctx.config, dict) else None
        provider = provider or PanSTARRSCSVProvider()
        catalog_error = None
        try:
            table = provider.cone_search(ra0, dec0, 9.0 / 60.0)
            catalog = np.column_stack((
                np.asarray(table["raMean"], dtype=float), np.asarray(table["decMean"], dtype=float),
                np.asarray(table["gMeanPSFMag"], dtype=float),
            )) if len(table) else np.empty((0, 3), dtype=float)
        except Exception as exc:
            catalog = np.empty((0, 3), dtype=float)
            catalog_error = f"{type(exc).__name__}: {exc}"
        match_table, fit_parameters, astrometry_success = fit_catalog_astrometry(
            detections, ra0, dec0, catalog
        )
        match_status = "pass" if astrometry_success else "warn"
        match_usability = "usable" if astrometry_success else "degraded"
        match_artifact = self._request(
            kind="catalog_match_table", scope=exposure_scope,
            components={
                "matches": _component("matches", match_table, "arcsec", "icrs"),
                "catalog_rows": _component("catalog_rows", catalog, "1", "icrs"),
            },
            parents=[int(initial_artifact.id), int(detection_artifact.id)],
            summaries={
                "catalog_row_count": int(catalog.shape[0]), "candidate_match_count": int(np.sum(match_table[:, 5])) if match_table.size else 0,
                "accepted_match_count": int(np.sum(match_table[:, 6])) if match_table.size else 0,
                "astrometry_refined": int(astrometry_success),
            },
            metadata={
                "provider": getattr(provider, "name", type(provider).__name__),
                "provider_version": getattr(provider, "version", "unknown"),
                "environmental_error": catalog_error,
                "columns": ["detection_index", "catalog_index", "separation_arcsec", "dra_arcsec", "ddec_arcsec", "candidate", "accepted", "residual_arcsec", "g_mag"],
            }, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.fit_catalog_astrometry", version=ASTROMETRY_VERSION,
            status=match_status, usability=match_usability,
        )
        if astrometry_success:
            final_ra0 = ra0 + fit_parameters[0] / (np.cos(np.deg2rad(dec0)) * 3600.0)
            final_dec0 = dec0 + fit_parameters[1] / 3600.0
            final_pa = pa + np.rad2deg(fit_parameters[2])
        else:
            final_ra0, final_dec0, final_pa = ra0, dec0, pa
        final_ra, final_dec, final_rotation = tan_fiber_coordinates(
            final_ra0, final_dec0, final_pa, focal[:, 0], focal[:, 1]
        )
        accepted_residual = match_table[:, 7][match_table[:, 6].astype(bool)] if match_table.size else np.array([])
        final_artifact = self._request(
            kind="final_astrometry", scope=exposure_scope,
            components={
                "parameters": _component("parameters", np.asarray([final_ra0, final_dec0, final_pa, final_rotation]), "deg", "icrs"),
                "fit_evidence": _component("fit_evidence", fit_parameters, "arcsec", "icrs"),
            },
            parents=[int(initial_artifact.id), int(match_artifact.id)],
            summaries={
                "accepted_match_count": int(accepted_residual.size),
                "residual_rms_arcsec": float(np.sqrt(np.nanmean(np.square(accepted_residual)))) if accepted_residual.size else float("nan"),
                "refined": int(astrometry_success),
            },
            metadata={"fallback": None if astrometry_success else "initial_header_tan", "catalog_error": catalog_error},
            refs=exposure_refs, algorithm="virusflow.algorithms.exposure.fit_catalog_astrometry", version=ASTROMETRY_VERSION,
            status=match_status, usability=match_usability,
        )
        coordinates_artifact = self._request(
            kind="fiber_sky_coordinates", scope=Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.FIBER),
            components={
                "coordinates": _component("coordinates", np.column_stack((final_ra, final_dec)), "deg", "icrs"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
                "focal_plane_coordinates": _component("focal_plane_coordinates", focal, "arcsec", "none"),
            },
            parents=[int(final_artifact.id)], summaries={"fiber_count": int(fiber_identity.shape[0])}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.tan_fiber_coordinates", version=ASTROMETRY_VERSION,
            status=match_status, usability=match_usability,
        )

        source_mask = np.zeros(spectrum.shape[0], dtype=bool)
        if detections.size:
            source_mask[detections[:, 0].astype(int)] = True
        sky_mask, broadband_flux, sky_center, sky_sigma = select_sky_fibers(
            spectrum, valid_fraction, source_mask=source_mask
        )
        sky_mask_artifact = self._request(
            kind="sky_fiber_mask", scope=Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.FIBER),
            components={
                "mask": _mask_component("mask", sky_mask),
                "broadband_flux": _component("broadband_flux", broadband_flux, "electron", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(coordinates_artifact.id), int(detection_artifact.id), *sorted(set(normalization_parent_ids))],
            summaries={
                "sky_fiber_count": int(sky_mask.sum()), "sky_ifuslot_count": int(np.unique(fiber_identity[sky_mask.astype(bool), 2]).size),
                "sky_broadband_center": sky_center, "sky_broadband_robust_sigma": sky_sigma,
            }, refs=exposure_refs, algorithm="virusflow.algorithms.exposure.select_sky_fibers", version=SKY_VERSION,
        )
        representative_width = float(np.nanmedian(np.diff(wavelength_bin_edges(wavelength), axis=1)))
        minimum_lsf_fwhm = float(self.params.get("minimum_lsf_fwhm", 2.0 * representative_width))
        sampling_target = float(self.params.get("sky_samples_per_fwhm", 6.0))
        configured_oversample = self.params.get("sky_oversample")
        if configured_oversample is None:
            oversampling_factor, native_samples_per_fwhm = derive_sky_oversampling_factor(
                minimum_lsf_fwhm, representative_width,
                target_samples_per_fwhm=sampling_target,
            )
        else:
            oversampling_factor = max(1, int(configured_oversample))
            native_samples_per_fwhm = minimum_lsf_fwhm / representative_width
        sky_wave, incident_sky, sky_variance, sky_counts = oversampled_incident_sky(
            wavelength, spectrum, sky_mask, oversample=oversampling_factor,
            minimum_lsf_fwhm=minimum_lsf_fwhm,
            target_samples_per_fwhm=sampling_target,
        )

        # Exposure illumination remains separate from baseline response and sky.
        amp_indices = fiber_identity[:, 0].astype(int)
        amp_sky_level = np.full(len(ordered_keys), np.nan)
        for index in range(len(ordered_keys)):
            selected = (amp_indices == index) & sky_mask.astype(bool)
            if selected.any():
                amp_sky_level[index] = np.nanmedian(broadband_flux[selected])
        global_level = float(np.nanmedian(amp_sky_level[np.isfinite(amp_sky_level)]))
        amp_illumination = amp_sky_level / global_level
        fiber_illumination = amp_illumination[amp_indices]
        illumination_artifact = self._request(
            kind="exposure_illumination_correction", scope=exposure_scope,
            components={
                "fiber_factor": _component("fiber_factor", fiber_illumination.astype(np.float32), "1", "none"),
                "amplifier_factor": _component("amplifier_factor", amp_illumination.astype(np.float32), "1", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(sky_mask_artifact.id)],
            summaries={
                "factor_median": float(np.nanmedian(amp_illumination)),
                "factor_robust_sigma": float(1.4826 * np.nanmedian(np.abs(amp_illumination - np.nanmedian(amp_illumination)))),
            },
            refs=exposure_refs, algorithm="virusflow.algorithms.exposure.exposure_illumination", version=RESPONSE_VERSION,
        )
        sky_model = LatentSkyModel(
            sky_wave, incident_sky, sky_variance,
            sampling_target=sampling_target,
            oversampling_factor=oversampling_factor,
            reference_resolution="intrinsic_without_accepted_lsf",
        )
        sky_model_artifact = self._request(
            kind="sky_model", scope=exposure_scope,
            components={
                "latent_wavelength": _component("latent_wavelength", sky_wave, "Angstrom", "wavelength_angstrom"),
                "latent_flux_density": _component("latent_flux_density", incident_sky, "electron / Angstrom", "wavelength_angstrom"),
                "latent_variance_density": _component("latent_variance_density", sky_variance, "electron2 / Angstrom2", "wavelength_angstrom"),
                "sample_count": _component("sample_count", sky_counts, "1", "wavelength_angstrom"),
                "fiber_coefficients": _component("fiber_coefficients", fiber_illumination, "1", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(sky_mask_artifact.id), int(illumination_artifact.id), *sorted(set(normalization_parent_ids))],
            summaries={
                "grid_samples": int(sky_wave.size),
                "minimum_sample_count": int(np.min(sky_counts)),
                "sampling_target_per_fwhm": sampling_target,
                "native_samples_per_fwhm": float(native_samples_per_fwhm),
                "oversampling_factor": int(oversampling_factor),
            },
            metadata={
                "representation": "supersampled_regular_flux_density_grid",
                "reference_resolution": sky_model.reference_resolution,
                "lsf_model_reference": None,
                "pixel_integration": sky_model.integration_method,
                "sampling_rule": "ceil(target_samples_per_fwhm / native_samples_per_fwhm)",
                "minimum_lsf_fwhm_angstrom": minimum_lsf_fwhm,
                "representative_native_pixel_width_angstrom": representative_width,
            },
            refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.oversampled_incident_sky",
            version=SKY_VERSION,
        )
        sky_prediction = sky_model.evaluate(
            wavelength_bin_edges(wavelength), coefficients=fiber_illumination
        )
        sky_subtracted = spectrum - sky_prediction
        residual = sky_subtracted[sky_mask.astype(bool)]
        residual_sigma = float(1.4826 * np.nanmedian(np.abs(residual - np.nanmedian(residual))))

        baseline_response = np.ones(sky_wave.shape, dtype=np.float32)
        baseline_artifact = self._request(
            kind="baseline_relative_response",
            scope=Scope(zipcode=None, physical_scope=PhysicalScope.INSTRUMENT_EPOCH),
            components={
                "wavelength": _component("wavelength", sky_wave, "Angstrom", "wavelength_angstrom"),
                "response": _component("response", baseline_response, "1", "wavelength_angstrom"),
            },
            summaries={"response_median": 1.0}, metadata={"evidence_state": BASELINE_RESPONSE_CONFIGURATION.evidence_state},
            refs=exposure_refs, assumptions=["No historical relative-response curve was supplied; identity is explicit and provisional."],
            algorithm="virusflow.algorithms.exposure.baseline_relative_response", version=BASELINE_RESPONSE_CONFIGURATION.version,
            status="warn", usability="degraded",
        )
        response_model = compact_fiber_response(
            wavelength, within_response, amp_factors, fiber_illumination,
            fiber_identity, knot_stride=int(self.params.get("response_knot_stride", 16)),
        )
        response_artifact = self._request(
            kind="fiber_response_model", scope=exposure_scope,
            components={
                "wavelength_knots": _component("wavelength_knots", response_model.wavelength_knots, "Angstrom", "wavelength_angstrom"),
                "within_amp_knots": _component("within_amp_knots", response_model.within_amp_knots, "1", "wavelength_angstrom"),
                "amplifier_factors": _component("amplifier_factors", amp_factors, "1", "none"),
                "illumination_factors": _component("illumination_factors", fiber_illumination, "1", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(baseline_artifact.id), int(illumination_artifact.id), int(amp_artifact.id), *sorted(set(normalization_parent_ids))],
            summaries={
                "response_median": float(np.nanmedian(fiber_illumination)),
                "response_outlier_fraction": float(np.mean(np.abs(fiber_illumination - np.nanmedian(fiber_illumination)) > 5 * np.nanstd(fiber_illumination))),
                "knot_count": int(response_model.wavelength_knots.shape[1]),
            },
            metadata={
                "composition": ["baseline_relative_response", "within_amplifier_fiber_normalization", "amplifier_normalization", "exposure_illumination_correction"],
                "interpolation": "linear_in_wavelength",
            },
            refs=exposure_refs, algorithm="virusflow.algorithms.exposure.final_response", version=RESPONSE_VERSION,
            status="warn", usability="degraded",
        )
        final_response = fiber_illumination[:, None]
        calibrated_flux = sky_subtracted / final_response
        calibrated_variance = spectrum_variance / np.square(final_response)
        final_mask = np.zeros(calibrated_flux.shape, dtype=np.uint16)
        final_mask[~np.isfinite(calibrated_flux) | ~np.isfinite(calibrated_variance) | ~np.isfinite(wavelength)] |= 1
        final_mask[valid_fraction < 0.8] |= 2
        calibrated_state = CalibratedFiberState(
            exposure_id=exposure_id,
            flux=(calibrated_flux * FLUX_SCALE).astype(np.float32),
            variance=(calibrated_variance * VARIANCE_SCALE).astype(np.float32),
            mask=final_mask,
            wavelength=wavelength.astype(np.float32),
            fiber_identity=fiber_identity.astype(np.int32),
            sky_coordinates=np.column_stack((final_ra, final_dec)).astype(np.float64),
            focal_plane_coordinates=focal.astype(np.float32),
            model_artifact_ids=(
                int(sky_model_artifact.id), int(response_artifact.id),
                int(final_artifact.id), *tuple(sorted(set(reduction_parent_ids))),
            ),
            metadata={
                "sky_residual_robust_sigma": residual_sigma,
                "sky_evaluator_version": SKY_VERSION,
                "pixel_integration": sky_model.integration_method,
                "response_evidence_state": BASELINE_RESPONSE_CONFIGURATION.evidence_state,
                "variance_terms": "extracted_statistical_only; sky-model and response-model covariance not yet added",
                "wavelength_fiber_exclusions": wavelength_fiber_exclusions,
            },
        )

        mode, effective_seconds, time_evidence = classify_mode_and_effective_time(
            header, parallel_offset_seconds=EFFECTIVE_EXPOSURE_POLICY.parallel_offset_seconds
        )
        mode_artifact = self._request(
            kind="exposure_mode_classification", scope=exposure_scope,
            components={
                "classification": _component("classification", np.asarray([1 if mode == "parallel" else 0]), "1", "none"),
                "source_fields": _component("source_fields", np.asarray([time_evidence["EXPTIME"] or np.nan, time_evidence["PEXPTIME"] or np.nan]), "s", "none"),
            },
            parents=[int(initial_artifact.id)],
            summaries={"parallel": int(mode == "parallel")}, metadata={"mode": mode, **time_evidence}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.classify_mode", version=EFFECTIVE_EXPOSURE_POLICY.version,
        )
        effective_artifact = self._request(
            kind="effective_exposure_time", scope=exposure_scope,
            components={
                "effective_seconds": _component("effective_seconds", np.asarray([effective_seconds]), "s", "none"),
                "source_fields": _component("source_fields", np.asarray([time_evidence["EXPTIME"] or np.nan, time_evidence["PEXPTIME"] or np.nan, EFFECTIVE_EXPOSURE_POLICY.parallel_offset_seconds]), "s", "none"),
            },
            parents=[int(mode_artifact.id)], summaries={"effective_seconds": float(effective_seconds)},
            metadata={"mode": mode, "policy_version": EFFECTIVE_EXPOSURE_POLICY.version, **time_evidence}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.effective_exposure_time", version=EFFECTIVE_EXPOSURE_POLICY.version,
        )

        coverage = []
        identities = []
        for zipcode in zipcodes:
            kinds = calibration.get(zipcode.key(), {})
            coverage.append([
                int(zipcode.key() in reduced), int("trace_map" in kinds), int("wavelength_map" in kinds),
                int(zipcode.key() in amp_results), int(zipcode.key() not in failures),
            ])
            identities.append([int(zipcode.ifuslot), int(zipcode.specid), AMP_CODE[zipcode.amp]])
        coverage_array = np.asarray(coverage, dtype=np.uint8)
        completion_status = "pass" if not failures and not wavelength_fiber_exclusions else "warn"
        completion_usability = "usable" if not failures and not wavelength_fiber_exclusions else "degraded"
        exposure_product_status = {"pass": 0, "warn": 0, "fail": 0, "unknown": 0}
        for row in service.adapter.list_all():
            if row.get("exposure_id") != exposure_id:
                continue
            diagnostic = service.adapter.get_diagnostics(int(row["id"])) or {}
            status_name = str(diagnostic.get("status") or "unknown")
            exposure_product_status[status_name if status_name in exposure_product_status else "unknown"] += 1
        completion_artifact = self._request(
            kind="exposure_completion_manifest", scope=exposure_scope,
            components={
                "coverage": _component("coverage", coverage_array, "1", "none"),
                "amplifier_identity": _component("amplifier_identity", np.asarray(identities, dtype=np.int32), "1", "none"),
            },
            parents=[int(sky_model_artifact.id), int(response_artifact.id), int(effective_artifact.id), int(final_artifact.id)],
            summaries={
                "raw_amplifier_count": len(zipcodes), "reduced_amplifier_count": int(coverage_array[:, 0].sum()),
                "extracted_amplifier_count": int(coverage_array[:, 3].sum()),
                "ifuslot_count": len({zipcode.ifuslot for zipcode in zipcodes}),
                "extracted_ifuslot_count": len({amp_results[key]["zipcode"].ifuslot for key in ordered_keys}),
                "failed_or_missing_amplifier_count": len(failures),
                "excluded_wavelength_fiber_count": sum(
                    item["excluded_count"] for item in wavelength_fiber_exclusions.values()
                ),
                "amplifier_count_with_wavelength_fiber_exclusions": len(wavelength_fiber_exclusions),
                "usable_product_count": exposure_product_status["pass"],
                "suspect_product_count": exposure_product_status["warn"] + exposure_product_status["unknown"],
                "failed_product_count": exposure_product_status["fail"],
            },
            metadata={
                "failures": failures,
                "wavelength_fiber_exclusions": wavelength_fiber_exclusions,
                "zipcode_order": [zipcode.key() for zipcode in zipcodes],
                "coverage_columns": ["reduced", "trace", "wavelength", "extracted", "no_recorded_failure"],
                "persistent_science_intermediates": [],
                "scratch_cleanup": "in_memory_released_after_observation_assembly",
            },
            refs=exposure_refs, algorithm="virusflow.tasks.exposure.ExposureTask", version=self.version,
            status=completion_status, usability=completion_usability,
        )
        return {
            "exposure_completion_manifest": completion_artifact,
            "initial_astrometry": initial_artifact,
            "source_detection_catalog": detection_artifact,
            "catalog_match_table": match_artifact,
            "final_astrometry": final_artifact,
            "fiber_sky_coordinates": coordinates_artifact,
            "sky_fiber_mask": sky_mask_artifact,
            "sky_model": sky_model_artifact,
            "baseline_relative_response": baseline_artifact,
            "exposure_illumination_correction": illumination_artifact,
            "fiber_response_model": response_artifact,
            "exposure_mode_classification": mode_artifact,
            "effective_exposure_time": effective_artifact,
            "amp_to_amp_normalization": amp_artifact,
            "calibrated_fiber_state": calibrated_state,
        }
