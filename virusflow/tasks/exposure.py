from __future__ import annotations

"""Canonical full-width baseline reduction for one atomic VIRUS Exposure."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import numpy as np

from .calibs import BiasTask, CmpTask, DarkTask, FlatTask, TraceTask, TwiTask, WaveTask
from .science import PhysicalCCDTask, ReducedScienceAmplifierTask, _SciencePublisher, _instant
from ..algorithms.exposure import (
    ASTROMETRY_VERSION,
    EXTRACTION_VERSION,
    NORMALIZATION_VERSION,
    RESPONSE_VERSION,
    SKY_VERSION,
    amplifier_normalization,
    classify_mode_and_effective_time,
    detect_fiber_sources,
    extract_fractional_aperture,
    fit_catalog_astrometry,
    oversampled_incident_sky,
    parse_header_pointing,
    predict_sky,
    select_sky_fibers,
    tan_fiber_coordinates,
    within_amplifier_normalization,
)
from ..algorithms.physical_ccd import assemble_physical_ccd, fit_gap_scattered_light
from ..artifacts import ArtifactService, Scope, Validity
from ..artifacts.requests import ArtifactRequest, LogicalComponent
from ..config import ConfigurationService
from ..config.defaults import BASELINE_RESPONSE_CONFIGURATION, EFFECTIVE_EXPOSURE_POLICY
from ..io import PanSTARRSCSVProvider, RawFrameLoader
from ..ontology.artifact_kinds import kind_spec
from ..ontology.scopes import PhysicalScope
from ..planning.targets import PhysicalCCDTarget


AMP_CODE = {"LL": 0, "LU": 1, "RU": 2, "RL": 3}
CALIBRATION_TASKS = (
    (BiasTask, "master_bias"), (DarkTask, "master_dark"),
    (FlatTask, "master_ldls"), (CmpTask, "master_arc"),
    (TwiTask, "master_twilight"), (TraceTask, "trace_map"),
    (WaveTask, "wavelength_map"),
)


def _component(name, value, units, coordinates, **metadata):
    array = np.asarray(value)
    if array.ndim > 2:
        raise ValueError(f"Component {name} must be flattened to at most two dimensions")
    return LogicalComponent(
        name, "array1d" if array.ndim == 1 else "array2d", array,
        units, coordinates, metadata,
    )


class ExposureTask(_SciencePublisher):
    name = "full_exposure"
    version = "v1"

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
            parents=[int(value) for value in parents], validity=Validity(at, at, "exposure_identity"),
            configuration_refs=list(refs), assumptions=list(assumptions),
        )
        artifact = self._publish(request, algorithm_name=algorithm, algorithm_version=version)
        self._qa(artifact, request.summaries, status=status, usability=usability)
        return artifact

    def _ensure_calibrations(self, zipcodes, at: datetime):
        service = ArtifactService(self.ctx.db_path)
        date = at.strftime("%Y%m%d")
        end = (at + timedelta(days=1)).strftime("%Y%m%d")
        available = {}
        failures = {}
        reuse = bool(self.params.get("reuse_calibrations", True))
        for zipcode in zipcodes:
            target = SimpleNamespace(
                zipcode=zipcode, start_date=date, end_date=end,
                start_dt=datetime.strptime(date, "%Y%m%d"), end_dt=datetime.strptime(end, "%Y%m%d"),
            )
            kinds = {}
            for task_type, kind in CALIBRATION_TASKS:
                existing = service.select_best(kind=kind, scope=Scope(zipcode=zipcode), at_time=at) if reuse else None
                if existing is not None:
                    kinds[kind] = existing
                    continue
                try:
                    output = task_type(self.ctx, target=target).run({})
                    artifact = output[task_type.artifact_name]
                    kinds[kind] = service.adapter.get_row(int(artifact.id))
                except Exception as exc:
                    failures.setdefault(zipcode.key(), []).append(f"{kind}: {type(exc).__name__}: {exc}")
                    # Trace failure necessarily prevents Wavelength; retain earlier detector calibrations.
                    if kind == "trace_map":
                        break
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
            raw for raw in db.list_raw_files(exposure_id=exposure_id, db_path=self.ctx.db_path)
            if raw.frame_type == "sci" and raw.zipcode is not None
        ]
        if not raw_rows:
            raise RuntimeError(f"No science inputs for Exposure {exposure_id}")
        raw_rows.sort(key=lambda row: row.zipcode.key())
        zipcodes = [row.zipcode for row in raw_rows]
        if len({zipcode.key() for zipcode in zipcodes}) != len(zipcodes):
            raise RuntimeError(f"Repeated amplifier identity in Exposure {exposure_id}")

        loader = self.ctx.config.get("raw_frame_loader") if isinstance(self.ctx.config, dict) else None
        representative = (loader or RawFrameLoader()).load(raw_rows[0].path, raw_rows[0].tar_member)
        header = representative.header
        service = ArtifactService(self.ctx.db_path)
        config_root = self.ctx.config.get("configuration_root") if isinstance(self.ctx.config, dict) else None
        config = ConfigurationService(root=config_root)
        fplane_path = self.ctx.config.get("fplane_path") if isinstance(self.ctx.config, dict) else None
        fplane, fplane_ref = config.resolve_fplane(fplane_path)
        fiber_offsets, fiber_ref = config.fiber_offsets()
        exposure_refs = config.exposure_references() + [fplane_ref, fiber_ref]

        calibration, failures = self._ensure_calibrations(zipcodes, at)
        reduced = {}
        for zipcode in zipcodes:
            try:
                artifact = ReducedScienceAmplifierTask(
                    self.ctx, target=SimpleNamespace(zipcode=zipcode, exposure_id=exposure_id),
                    params={"apply_calibrations": True},
                ).run({})["reduced_science_image"]
                reduced[zipcode.key()] = service.adapter.get_row(int(artifact.id))
            except Exception as exc:
                failures.setdefault(zipcode.key(), []).append(f"reduced_science_image: {type(exc).__name__}: {exc}")

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
                    result = PhysicalCCDTask(self.ctx, target=target).run({})
                    physical[(identity, side)] = {
                        "model": result["ccd_scattered_light_model"],
                        "subtracted": result["scatter_subtracted_image"],
                    }
                except Exception as exc:
                    failures.setdefault(lower.key(), []).append(f"{side} CCD: {type(exc).__name__}: {exc}")
                    failures.setdefault(upper.key(), []).append(f"{side} CCD: {type(exc).__name__}: {exc}")

        amp_results = {}
        extracted_artifacts = []
        variance_artifacts = []
        within_artifacts = []
        for identity, amps in sorted(groups.items()):
            for side, pair in (("left", ("LL", "LU")), ("right", ("RU", "RL"))):
                product = physical.get((identity, side))
                if product is None:
                    continue
                sub_row = service.adapter.get_row(int(product["subtracted"].id))
                physical_image = service.load_component(sub_row, "image")["data"]
                physical_variance = service.load_component(sub_row, "variance")["data"]
                physical_mask = service.load_component(sub_row, "pixel_mask")["data"]
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
                    scope = Scope(zipcode=zipcode, exposure_id=exposure_id, physical_scope=PhysicalScope.FIBER)
                    extracted = self._request(
                        kind="aperture_extracted_spectrum", scope=scope,
                        components={
                            "spectrum": _component("spectrum", extraction.spectrum, "electron", "fiber_by_dispersion_pixel"),
                            "valid_pixel_fraction": _component("valid_pixel_fraction", extraction.valid_pixel_fraction, "1", "fiber_by_dispersion_pixel"),
                            "effective_aperture_width": _component("effective_aperture_width", extraction.effective_aperture_width, "pixel", "fiber_by_dispersion_pixel"),
                            "aperture_start_row": _component("aperture_start_row", extraction.aperture_start_row, "pixel", "fiber_by_dispersion_pixel"),
                            "fractional_weights": _component(
                                "fractional_weights", extraction.fractional_weights.reshape(extraction.spectrum.shape[0], -1),
                                "1", "fiber_by_dispersion_pixel", original_shape=list(extraction.fractional_weights.shape),
                            ),
                            "extraction_valid": _component("extraction_valid", extraction.extraction_valid, "1", "fiber_by_dispersion_pixel"),
                        },
                        parents=[int(sub_row["id"]), int(trace_row["id"]), int(wave_row["id"])],
                        summaries={
                            "aperture_width": 5.0,
                            "median_valid_pixel_fraction": float(np.nanmedian(extraction.valid_pixel_fraction)),
                            "invalid_sample_fraction": float(np.mean(extraction.valid_pixel_fraction < 1.0)),
                        },
                        metadata={"output_scale": "sum", "weights_shape": list(extraction.fractional_weights.shape)},
                        refs=exposure_refs, algorithm="virusflow.algorithms.exposure.extract_fractional_aperture",
                        version=EXTRACTION_VERSION,
                    )
                    extracted_variance = self._request(
                        kind="extracted_variance", scope=scope,
                        components={"variance": _component("variance", extraction.variance, "electron2", "fiber_by_dispersion_pixel")},
                        parents=[int(extracted.id), int(sub_row["id"])],
                        summaries={"median_variance": float(np.nanmedian(extraction.variance))},
                        refs=exposure_refs, algorithm="virusflow.algorithms.exposure.extract_fractional_aperture",
                        version=EXTRACTION_VERSION,
                    )
                    within_artifact = self._request(
                        kind="within_amp_fiber_normalization", scope=scope,
                        components={
                            "raw_ratio": _component("raw_ratio", raw_ratio, "1", "fiber_by_dispersion_pixel"),
                            "normalization": _component("normalization", within, "1", "fiber_by_dispersion_pixel"),
                            "valid_mask": _component("valid_mask", normalization_valid, "1", "fiber_by_dispersion_pixel"),
                            "common_twilight": _component("common_twilight", common_twi, "electron", "wavelength_pixel"),
                        },
                        parents=[int(twi_row["id"]), int(trace_row["id"])],
                        summaries={
                            "median_factor": float(np.nanmedian(within)),
                            "invalid_factor_fraction": float(np.mean(normalization_valid == 0)),
                            "twilight_scatter_holdout_sigma": float(twi_scatter.scalars["holdout_residual_robust_sigma"]),
                        },
                        metadata={"twilight_scatter_policy": "physical_ccd_gap_model_in_memory"},
                        refs=exposure_refs, assumptions=["Center-track twilight is uniform at exposure scope."],
                        algorithm="virusflow.algorithms.exposure.within_amplifier_normalization",
                        version=NORMALIZATION_VERSION,
                    )
                    amp_results[zipcode.key()] = {
                        "zipcode": zipcode,
                        "spectrum": extraction.spectrum,
                        "variance": extraction.variance,
                        "valid_fraction": extraction.valid_pixel_fraction,
                        "within": within,
                        "wavelength": wavelength,
                        "twilight_level": float(np.nanmedian(common_twi)),
                        "extracted_id": int(extracted.id),
                        "variance_id": int(extracted_variance.id),
                        "within_id": int(within_artifact.id),
                    }
                    extracted_artifacts.append(int(extracted.id))
                    variance_artifacts.append(int(extracted_variance.id))
                    within_artifacts.append(int(within_artifact.id))

        ordered_keys = sorted(amp_results)
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
            parents=within_artifacts,
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
        normalization_ids = []
        amp_index_by_key = {key: index for index, key in enumerate(ordered_keys)}
        for key, amp_factor in zip(ordered_keys, amp_factors):
            item = amp_results[key]
            final = item["within"] * amp_factor
            normalized = item["spectrum"] / final
            normalized_variance = item["variance"] / np.square(final)
            zipcode = item["zipcode"]
            scope = Scope(zipcode=zipcode, exposure_id=exposure_id, physical_scope=PhysicalScope.FIBER)
            artifact = self._request(
                kind="fiber_normalization", scope=scope,
                components={
                    "normalization": _component("normalization", final, "1", "fiber_by_dispersion_pixel"),
                    "within_amp_factor": _component("within_amp_factor", item["within"], "1", "fiber_by_dispersion_pixel"),
                    "amp_to_amp_factor": _component("amp_to_amp_factor", np.asarray([amp_factor]), "1", "none"),
                },
                parents=[item["within_id"], int(amp_artifact.id)],
                summaries={"median_final_factor": float(np.nanmedian(final))}, refs=exposure_refs,
                algorithm="virusflow.algorithms.exposure.final_fiber_normalization", version=NORMALIZATION_VERSION,
            )
            item["normalization_id"] = int(artifact.id)
            normalization_ids.append(int(artifact.id))
            n_fiber = normalized.shape[0]
            if zipcode.ifuslot not in fplane:
                failures.setdefault(key, []).append("IFUSLOT absent from fplane configuration")
                continue
            local = fiber_offsets[zipcode.amp]
            fp_x, fp_y = fplane[zipcode.ifuslot]
            focal = local + np.asarray([fp_x, fp_y])
            identities = np.column_stack((
                np.full(n_fiber, amp_index_by_key[key]),
                np.arange(n_fiber),
                np.full(n_fiber, int(zipcode.ifuslot)),
                np.full(n_fiber, int(zipcode.specid)),
                np.full(n_fiber, AMP_CODE[zipcode.amp]),
            )).astype(np.int32)
            global_spectrum.append(normalized.astype(np.float32))
            global_variance.append(normalized_variance.astype(np.float32))
            global_valid.append(item["valid_fraction"].astype(np.float32))
            global_wavelength.append(item["wavelength"].astype(np.float32))
            global_identity.append(identities)
            global_focal.append(focal.astype(np.float32))

        if not global_spectrum:
            raise RuntimeError(f"Exposure {exposure_id} produced no extractable amplifier")
        spectrum = np.concatenate(global_spectrum)
        spectrum_variance = np.concatenate(global_variance)
        valid_fraction = np.concatenate(global_valid)
        wavelength = np.concatenate(global_wavelength)
        fiber_identity = np.concatenate(global_identity)
        focal = np.concatenate(global_focal)

        ra0, dec0, pa, header_evidence = parse_header_pointing(header)
        initial_ra, initial_dec, initial_rotation = tan_fiber_coordinates(ra0, dec0, pa, focal[:, 0], focal[:, 1])
        initial_artifact = self._request(
            kind="initial_astrometry", scope=exposure_scope,
            components={
                "parameters": _component("parameters", np.asarray([ra0, dec0, pa, initial_rotation]), "deg", "icrs"),
                "header_evidence": _component("header_evidence", np.asarray([ra0, dec0, pa]), "deg", "icrs"),
            },
            parents=extracted_artifacts,
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
            parents=[int(initial_artifact.id), *extracted_artifacts],
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
                "mask": _component("mask", sky_mask, "1", "none"),
                "broadband_flux": _component("broadband_flux", broadband_flux, "electron", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(coordinates_artifact.id), int(detection_artifact.id), *normalization_ids],
            summaries={
                "sky_fiber_count": int(sky_mask.sum()), "sky_ifuslot_count": int(np.unique(fiber_identity[sky_mask.astype(bool), 2]).size),
                "sky_broadband_center": sky_center, "sky_broadband_robust_sigma": sky_sigma,
            }, refs=exposure_refs, algorithm="virusflow.algorithms.exposure.select_sky_fibers", version=SKY_VERSION,
        )
        sky_wave, incident_sky, sky_variance, sky_counts = oversampled_incident_sky(
            wavelength, spectrum, sky_mask, oversample=int(self.params.get("sky_oversample", 2))
        )
        incident_artifact = self._request(
            kind="incident_sky_spectrum", scope=exposure_scope,
            components={
                "wavelength": _component("wavelength", sky_wave, "Angstrom", "wavelength_angstrom"),
                "spectrum": _component("spectrum", incident_sky, "electron", "wavelength_angstrom"),
                "variance": _component("variance", sky_variance, "electron2", "wavelength_angstrom"),
                "sample_count": _component("sample_count", sky_counts, "1", "wavelength_angstrom"),
            },
            parents=[int(sky_mask_artifact.id), *extracted_artifacts, *normalization_ids],
            summaries={"grid_samples": int(sky_wave.size), "minimum_sample_count": int(np.min(sky_counts))}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.oversampled_incident_sky", version=SKY_VERSION,
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
        sky_prediction = predict_sky(wavelength, sky_wave, incident_sky) * fiber_illumination[:, None]
        sky_subtracted = spectrum - sky_prediction
        residual = sky_subtracted[sky_mask.astype(bool)]
        residual_sigma = float(1.4826 * np.nanmedian(np.abs(residual - np.nanmedian(residual))))
        prediction_artifact = self._request(
            kind="fiber_sky_prediction", scope=Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.FIBER),
            components={
                "prediction": _component("prediction", sky_prediction.astype(np.float32), "electron", "fiber_by_dispersion_pixel"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(incident_artifact.id), int(illumination_artifact.id)],
            summaries={"prediction_median": float(np.nanmedian(sky_prediction))}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.predict_sky", version=SKY_VERSION,
        )
        sky_subtracted_artifact = self._request(
            kind="sky_subtracted_spectrum", scope=Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.FIBER),
            components={
                "spectrum": _component("spectrum", sky_subtracted.astype(np.float32), "electron", "fiber_by_dispersion_pixel"),
                "variance": _component("variance", spectrum_variance.astype(np.float32), "electron2", "fiber_by_dispersion_pixel"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(prediction_artifact.id), *extracted_artifacts, *variance_artifacts, *normalization_ids],
            summaries={"sky_residual_robust_sigma": residual_sigma, "sky_fiber_count": int(sky_mask.sum())}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure.sky_subtract", version=SKY_VERSION,
        )

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
        baseline_native = predict_sky(wavelength, sky_wave, baseline_response)
        final_response = baseline_native * fiber_illumination[:, None]
        response_artifact = self._request(
            kind="final_exposure_response", scope=exposure_scope,
            components={
                "response": _component("response", final_response.astype(np.float32), "1", "fiber_by_dispersion_pixel"),
                "baseline_response": _component("baseline_response", baseline_response, "1", "wavelength_angstrom"),
                "illumination_factor": _component("illumination_factor", fiber_illumination.astype(np.float32), "1", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(baseline_artifact.id), int(illumination_artifact.id)],
            summaries={
                "response_median": float(np.nanmedian(final_response)),
                "response_outlier_fraction": float(np.mean(np.abs(final_response - np.nanmedian(final_response)) > 5 * np.nanstd(final_response))),
            }, refs=exposure_refs, algorithm="virusflow.algorithms.exposure.final_response", version=RESPONSE_VERSION,
            status="warn", usability="degraded",
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
        completion_status = "pass" if not failures else "warn"
        completion_usability = "usable" if not failures else "degraded"
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
            parents=[int(sky_subtracted_artifact.id), int(response_artifact.id), int(effective_artifact.id), int(final_artifact.id)],
            summaries={
                "raw_amplifier_count": len(zipcodes), "reduced_amplifier_count": int(coverage_array[:, 0].sum()),
                "extracted_amplifier_count": int(coverage_array[:, 3].sum()),
                "ifuslot_count": len({zipcode.ifuslot for zipcode in zipcodes}),
                "extracted_ifuslot_count": len({amp_results[key]["zipcode"].ifuslot for key in ordered_keys}),
                "failed_or_missing_amplifier_count": len(failures),
                "usable_product_count": exposure_product_status["pass"],
                "suspect_product_count": exposure_product_status["warn"] + exposure_product_status["unknown"],
                "failed_product_count": exposure_product_status["fail"],
            },
            metadata={"failures": failures, "zipcode_order": [zipcode.key() for zipcode in zipcodes], "coverage_columns": ["reduced", "trace", "wavelength", "extracted", "no_recorded_failure"]},
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
            "incident_sky_spectrum": incident_artifact,
            "fiber_sky_prediction": prediction_artifact,
            "sky_subtracted_spectrum": sky_subtracted_artifact,
            "baseline_relative_response": baseline_artifact,
            "exposure_illumination_correction": illumination_artifact,
            "final_exposure_response": response_artifact,
            "exposure_mode_classification": mode_artifact,
            "effective_exposure_time": effective_artifact,
            "amp_to_amp_normalization": amp_artifact,
        }
