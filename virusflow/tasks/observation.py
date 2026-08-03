from __future__ import annotations

"""First-class Observation and DitherSet orchestration without exposure merging."""

from datetime import datetime
from typing import Iterable

import numpy as np

from .exposure import _component, _mask_component
from .science import _SciencePublisher, _instant
from ..algorithms.astrometry import parse_header_pointing
from ..algorithms.exposure import CalibratedFiberState
from ..algorithms.observation import (
    ALGORITHM_VERSION,
    assign_nominal_dithers,
    combine_calibrated_fiber_states,
    dither_coverage_map,
    refine_relative_offsets,
)
from ..artifacts import ArtifactService, Scope, Validity
from ..artifacts.models import ConfigurationReference
from ..artifacts.requests import ArtifactRequest
from ..artifacts.storage_conventions import scaled_flux_component, scaled_variance_component
from ..config import ConfigurationService
from ..config.defaults import DITHER_POLICY
from ..core.exposure_metadata import interpret_virus_exposure_header
from ..io import RawFrameLoader
from ..ontology.scopes import PhysicalScope


def _header_float(header, *names):
    for name in names:
        value = header.get(name)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return float("nan")


class ObservationTask(_SciencePublisher):
    name = "observation_dither_set"
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
        status: str = "pass",
        usability: str = "usable",
    ):
        times = [_instant(value) for value in self.target.exposure_ids]
        request = ArtifactRequest(
            kind=kind,
            role="reduction",
            components=components,
            summaries=dict(summaries or {}),
            metadata=dict(metadata or {}),
            scope=scope,
            parents=[int(value) for value in parents],
            validity=Validity(min(times), max(times), "observation_membership"),
            configuration_refs=list(refs),
            assumptions=list(assumptions),
        )
        artifact = self._publish(
            request,
            algorithm_name="virusflow.algorithms.observation",
            algorithm_version=ALGORITHM_VERSION,
        )
        self._qa(artifact, request.summaries, status=status, usability=usability)
        return artifact

    def run(self, inputs):  # noqa: ARG002
        from ..registry import database as db

        service = ArtifactService(self.ctx.db_path)
        loader = self.ctx.config.get("raw_frame_loader") if isinstance(self.ctx.config, dict) else None
        config_root = self.ctx.config.get("configuration_root") if isinstance(self.ctx.config, dict) else None
        config = ConfigurationService(config_root)
        first_raw_rows = [
            row for row in db.list_raw_file_rows(str(self.target.exposure_ids[0]), self.ctx.resolved_raw_db_path())
            if row[1].frame_type == "sci"
        ]
        if not first_raw_rows:
            raise RuntimeError(f"Observation member has no real science input: {self.target.exposure_ids[0]}")
        first_raw = first_raw_rows[0][1]
        if first_raw.zipcode is not None:
            geometry_ifuid = first_raw.zipcode.ifuid
        else:
            first_header = (loader or RawFrameLoader()).load_ref(first_raw).header
            geometry_ifuid = first_header.get("IFUID", "")
        fiber_offsets, fiber_ref = config.fiber_offsets(geometry_ifuid)
        policy_ref = ConfigurationReference(
            "dither_pattern", DITHER_POLICY.version, self.target.dither_set_id,
            DITHER_POLICY.evidence_state,
        )
        refs = [policy_ref, fiber_ref]
        exposure_ids = tuple(str(value) for value in self.target.exposure_ids)

        calibrated_states: dict[str, CalibratedFiberState] = {}

        def collect(value) -> None:
            if isinstance(value, CalibratedFiberState):
                calibrated_states[str(value.exposure_id)] = value
            elif isinstance(value, dict):
                for child in value.values():
                    collect(child)
            elif isinstance(value, (tuple, list)):
                for child in value:
                    collect(child)

        collect(inputs)
        if isinstance(self.ctx.config, dict):
            collect(self.ctx.config.get("calibrated_fiber_states"))
        state_artifacts = []
        astrometry_rows = []
        astrometry_valid = []
        sequence = []
        sequence_evidence = []
        state_values = []
        coverage_values = []
        state_metadata = []
        exposure_contexts = []
        astrometry_parent_ids = []

        start = min(_instant(value) for value in exposure_ids)
        for index, exposure_id in enumerate(exposure_ids):
            raw_rows = [row for row in db.list_raw_file_rows(exposure_id, self.ctx.resolved_raw_db_path()) if row[1].frame_type == "sci"]
            if not raw_rows:
                raise RuntimeError(f"Observation member has no real science input: {exposure_id}")
            representative = (loader or RawFrameLoader()).load_ref(raw_rows[0][1])
            header = representative.header
            exposure_context = interpret_virus_exposure_header(header, frame_type="sci")
            exposure_contexts.append(exposure_context)
            when = _instant(exposure_id)
            sequence.append((when - start).total_seconds())
            obsid = _header_float(header, "OBSID")
            sequence_evidence.append([index, sequence[-1], obsid, _header_float(header, "DITHER", "DITHPOS")])

            exposure_scope = Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.EXPOSURE)
            completion = service.select_best(kind="exposure_completion_manifest", scope=exposure_scope, at_time=when)
            final = service.select_best(kind="final_astrometry", scope=exposure_scope, at_time=when)
            initial = service.select_best(kind="initial_astrometry", scope=exposure_scope, at_time=when)
            response = service.select_best(kind="fiber_response_model", scope=exposure_scope, at_time=when)
            effective = service.select_best(kind="effective_exposure_time", scope=exposure_scope, at_time=when)

            pointing = parse_header_pointing(header)
            ra0, dec0, pa = pointing.scalars["ra0"], pointing.scalars["dec0"], pointing.scalars["pa"]
            pointing_evidence = pointing.meta["evidence"]
            astrometry = np.asarray([ra0, dec0, pa], dtype=float)
            refined = False
            astrometry_source = "header_initial_tan"
            selected_astrometry = final or initial
            if selected_astrometry is not None:
                values = np.asarray(service.load_component(selected_astrometry, "parameters")["data"], dtype=float)
                astrometry = values[:3]
                refined = bool(final is not None and int((final.get("metadata") or {}).get("refined", 0)) == 1)
                astrometry_source = "catalog_refined" if refined else "retained_initial_tan"
                astrometry_parent_ids.append(int(selected_astrometry["id"]))
            astrometry_rows.append(astrometry)
            astrometry_valid.append(refined)

            raw_amp_count = len({raw.zipcode.key() for _, raw in raw_rows if raw.zipcode is not None})
            raw_ifu_count = len({raw.zipcode.ifuslot for _, raw in raw_rows if raw.zipcode is not None})
            completion_summary = (completion or {}).get("metadata") or {}
            reduced_count = int(completion_summary.get("reduced_amplifier_count", 0))
            extracted_count = int(completion_summary.get("extracted_amplifier_count", 0))
            failed_count = int(completion_summary.get("failed_or_missing_amplifier_count", raw_amp_count if completion is None else 0))
            response_median = float(((response or {}).get("metadata") or {}).get("response_median", np.nan))
            effective_seconds = float(((effective or {}).get("metadata") or {}).get("effective_seconds", header.get("EXPTIME", np.nan)))
            seeing = _header_float(header, "SEEING", "IQ", "FWHM")
            transparency = _header_float(header, "TRANSPAR", "TRANSP", "THROUGHP")
            state = np.asarray([
                seeing, transparency, response_median, effective_seconds,
                raw_amp_count, raw_ifu_count,
            ], dtype=float)
            coverage_summary = np.asarray([raw_amp_count, reduced_count, extracted_count, failed_count], dtype=float)
            parent_ids = [int(raw_rows[0][0])]
            for row in (completion, response, effective, selected_astrometry):
                if row is not None and int(row["id"]) not in parent_ids:
                    parent_ids.append(int(row["id"]))
            state_scope = Scope(
                zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.EXPOSURE,
                observation_id=self.target.observation_id, dither_set_id=self.target.dither_set_id,
            )
            state_artifact = self._request(
                kind="observation_exposure_state", scope=state_scope,
                components={
                    "state": _component("state", state, "1", "none"),
                    "astrometry_parameters": _component("astrometry_parameters", astrometry, "deg", "icrs"),
                    "coverage_summary": _component("coverage_summary", coverage_summary, "1", "none"),
                },
                parents=parent_ids,
                summaries={
                    "raw_amplifier_count": raw_amp_count,
                    "raw_ifuslot_count": raw_ifu_count,
                    "extracted_amplifier_count": extracted_count,
                    "missing_or_failed_amplifier_count": failed_count,
                    "astrometry_refined": int(refined),
                },
                metadata={
                    "exposure_id": exposure_id,
                    "observation_id": self.target.observation_id,
                    "dither_set_id": self.target.dither_set_id,
                    "astrometry_source": astrometry_source,
                    "pointing_evidence": pointing_evidence,
                    "header_state": {name: header.get(name) for name in (
                        "OBJECT", "QOBJECT", "QRA", "QDEC", "QPROG", "OBSID",
                        "EXPTIME", "PEXPTIME", "SEEING", "IQ", "TRANSPAR", "TRANSP",
                    )},
                    "exposure_context": exposure_context.as_dict(),
                    "state_columns": ["seeing", "transparency", "response_median", "effective_seconds", "raw_amplifiers", "raw_ifuslots"],
                    "coverage_columns": ["raw", "reduced", "extracted", "failed_or_missing"],
                },
                refs=refs,
                status="pass" if completion is not None and failed_count == 0 else "warn",
                usability="usable" if completion is not None and failed_count == 0 else "degraded",
            )
            state_artifacts.append(state_artifact)
            state_values.append(state)
            coverage_values.append(coverage_summary)
            state_metadata.append({
                "exposure_id": exposure_id,
                "astrometry_source": astrometry_source,
                "exposure_context": exposure_context.as_dict(),
            })

        member_modes = {context.observing_mode for context in exposure_contexts}
        observation_mode = next(iter(member_modes)) if len(member_modes) == 1 else "mixed"
        dither_mode = "standard" if observation_mode == "primary" else "none"
        assignment = assign_nominal_dithers(
            exposure_ids,
            sequence,
            np.asarray(DITHER_POLICY.nominal_pattern_arcsec, dtype=float),
            dither_mode=dither_mode,
        )
        assignment_valid = assignment.valid and observation_mode != "mixed"
        virus_primary = (
            True if observation_mode == "primary"
            else False if observation_mode == "parallel"
            else None
        )
        membership_scope = Scope(
            zipcode=None, physical_scope=PhysicalScope.OBSERVATION,
            observation_id=self.target.observation_id, dither_set_id=self.target.dither_set_id,
        )
        membership = np.column_stack((
            assignment.assignments[:, :3],
            np.asarray([int(value.id) for value in state_artifacts]),
        ))
        membership_artifact = self._request(
            kind="observation_membership", scope=membership_scope,
            components={
                "membership": _component("membership", membership, "1", "none"),
                "exposure_state": _component("exposure_state", np.asarray(state_values), "1", "none"),
            },
            parents=[int(value.id) for value in state_artifacts],
            summaries={
                "exposure_count": len(exposure_ids),
                "unique_exposure_count": len(set(exposure_ids)),
                "complete_standard_sequence": int(assignment.complete),
                "valid_operational_group": int(assignment_valid),
            },
            metadata={
                "member_exposure_ids": list(exposure_ids),
                "state_evidence": state_metadata,
                "observing_mode": observation_mode,
                "virus_primary": virus_primary,
                "dither_mode": dither_mode,
                "coverage_mode": "complete_dither" if dither_mode == "standard" else "sparse",
                "membership_columns": ["input_index", "sequence_rank", "dither_index", "exposure_state_product_id"],
            }, refs=refs,
            status="pass" if assignment_valid else "warn",
            usability="usable" if assignment_valid else "degraded",
        )

        dither_scope = Scope(
            zipcode=None, physical_scope=PhysicalScope.DITHER_SET,
            observation_id=self.target.observation_id, dither_set_id=self.target.dither_set_id,
        )
        assignment_artifact = self._request(
            kind="dither_assignment", scope=dither_scope,
            components={
                "assignments": _component("assignments", assignment.assignments, "arcsec", "none"),
                "sequence_evidence": _component("sequence_evidence", np.asarray(sequence_evidence), "1", "none"),
            },
            parents=[int(membership_artifact.id)],
            summaries={
                "member_count": len(exposure_ids), "duplicate_count": assignment.duplicate_count,
                "extra_count": assignment.extra_count, "ambiguous": int(assignment.ambiguous),
                "complete": int(assignment.complete),
                "valid": int(assignment_valid),
            },
            metadata={
                "member_exposure_ids": list(exposure_ids),
                "assignment_method": (
                    "timestamp_sequence_plus_nominal_three_exposure_rule"
                    if dither_mode == "standard"
                    else "no_dither_from_virus_operational_context"
                ),
                "observing_mode": observation_mode,
                "virus_primary": virus_primary,
                "dither_mode": dither_mode,
                "coverage_mode": "complete_dither" if dither_mode == "standard" else "sparse",
                "policy_version": DITHER_POLICY.version,
                "policy_source": DITHER_POLICY.source,
                "assignment_columns": ["input_index", "sequence_rank", "dither_index", "nominal_dx", "nominal_dy", "duplicate", "extra", "ambiguous_order"],
                "sequence_columns": ["input_index", "seconds_from_first", "OBSID", "header_dither"],
            }, refs=refs,
            assumptions=(
                ["The provisional nominal pattern remains configurable pending authoritative history."]
                if dither_mode == "standard" else []
            ),
            status="pass" if assignment_valid else "warn",
            usability="usable" if assignment_valid else "degraded",
        )

        nominal = assignment.assignments[:, 3:5]
        astrometry_array = np.asarray(astrometry_rows, dtype=float)
        refined, residual, registration_success = refine_relative_offsets(
            nominal, astrometry_array, np.asarray(astrometry_valid, dtype=bool)
        )
        finite_residual = residual[np.isfinite(residual)]
        registration_rms = float(np.sqrt(np.mean(np.square(finite_residual)))) if finite_residual.size else float("nan")
        registration_consistent = bool(
            registration_success.all()
            and np.isfinite(registration_rms)
            and registration_rms <= DITHER_POLICY.registration_warn_rms_arcsec
        )
        registration_acceptable = registration_consistent or dither_mode == "none"
        registration_artifact = self._request(
            kind="dither_registration", scope=dither_scope,
            components={
                "nominal_offsets": _component("nominal_offsets", nominal, "arcsec", "none"),
                "refined_offsets": _component("refined_offsets", refined, "arcsec", "none"),
                "registration_residuals": _component("registration_residuals", residual, "arcsec", "none"),
                "registration_success": _component("registration_success", registration_success, "1", "none"),
                "astrometry_parameters": _component("astrometry_parameters", astrometry_array, "deg", "icrs"),
            },
            parents=[int(assignment_artifact.id), *astrometry_parent_ids],
            summaries={
                "registered_exposure_count": int(registration_success.sum()),
                "registration_residual_rms_arcsec": registration_rms,
                "fallback_nominal_count": int((registration_success == 0).sum()),
                "nominal_consistent": int(registration_consistent),
            },
            metadata={
                "member_exposure_ids": list(exposure_ids),
                "nominal_and_refined_stored_separately": True,
                "fallback": "nominal_offset_when_catalog_refined_astrometry_unavailable",
                "registration_warn_rms_arcsec": DITHER_POLICY.registration_warn_rms_arcsec,
                "registration_required": dither_mode == "standard",
                "dither_mode": dither_mode,
            }, refs=refs,
            status="pass" if registration_acceptable and assignment_valid else "warn",
            usability="usable" if registration_acceptable and assignment_valid else "degraded",
        )

        coverage_offsets = np.where(registration_success[:, None].astype(bool), refined, nominal)
        local_fibers = np.concatenate([fiber_offsets[amp] for amp in ("LL", "LU", "RU", "RL")])
        coverage, x_coordinate, y_coordinate = dither_coverage_map(local_fibers, coverage_offsets)
        covered = coverage > 0
        duplicated = coverage > 1
        coverage_artifact = self._request(
            kind="dither_coverage_map", scope=dither_scope,
            components={
                "coverage": _component("coverage", coverage, "1", "none"),
                "x_coordinate": _component("x_coordinate", x_coordinate, "arcsec", "none"),
                "y_coordinate": _component("y_coordinate", y_coordinate, "arcsec", "none"),
            },
            parents=[int(registration_artifact.id)],
            summaries={
                "covered_pixel_fraction": float(covered.mean()),
                "hole_pixel_fraction": float((~covered).mean()),
                "duplicated_coverage_fraction": float(duplicated[covered].mean()) if covered.any() else 0.0,
                "maximum_coverage": int(coverage.max()),
            },
            metadata={
                "member_exposure_ids": list(exposure_ids),
                "coverage_grid_step_arcsec": 0.25,
                "fiber_radius_arcsec": 0.75,
                "coverage_offsets": "refined_where_successful_else_nominal",
                "coverage_is_footprint_only_not_cube_reconstruction": True,
            }, refs=refs,
            status="pass" if covered.any() else "fail",
            usability="usable" if covered.any() else "unusable",
        )

        qa_usability = np.column_stack((
            np.arange(len(exposure_ids)),
            np.asarray(astrometry_valid, dtype=np.uint8),
            np.asarray(coverage_values)[:, 2] > 0,
            np.asarray(coverage_values)[:, 3],
        )).astype(float)
        observation_artifact = self._request(
            kind="observation_summary", scope=membership_scope,
            components={
                "member_state": _component("member_state", np.asarray(state_values), "1", "none"),
                "qa_usability": _component("qa_usability", qa_usability, "1", "none"),
            },
            parents=[int(membership_artifact.id), int(registration_artifact.id), int(coverage_artifact.id)],
            summaries={
                "exposure_count": len(exposure_ids),
                "fully_extracted_exposure_count": int(np.sum(np.asarray(coverage_values)[:, 2] > 0)),
                "catalog_refined_exposure_count": int(np.sum(astrometry_valid)),
                "observation_usable": int(assignment_valid and covered.any()),
                "registration_consistent": int(registration_consistent),
            },
            metadata={
                "member_exposure_ids": list(exposure_ids),
                "per_exposure_state_preserved": True,
                "observing_mode": observation_mode,
                "virus_primary": virus_primary,
                "dither_mode": dither_mode,
                "coverage_mode": "complete_dither" if dither_mode == "standard" else "sparse",
                "qa_columns": ["input_index", "catalog_refined_astrometry", "has_extraction", "failed_or_missing_amplifiers"],
            }, refs=refs,
            status="pass" if assignment_valid and covered.any() and registration_acceptable else "warn",
            usability="usable" if assignment_valid and covered.any() and registration_acceptable else "degraded",
        )
        result = {
            "observation_membership": membership_artifact,
            "dither_assignment": assignment_artifact,
            "dither_registration": registration_artifact,
            "dither_coverage_map": coverage_artifact,
            "observation_summary": observation_artifact,
            "exposure_states": tuple(state_artifacts),
        }
        if assignment.complete and all(exposure_id in calibrated_states for exposure_id in exposure_ids):
            ordered_states = [calibrated_states[exposure_id] for exposure_id in exposure_ids]
            final_arrays = combine_calibrated_fiber_states(ordered_states)
            final_parents = {
                int(observation_artifact.id), int(membership_artifact.id), int(registration_artifact.id),
                *(parent for state in ordered_states for parent in state.model_artifact_ids),
            }
            calibrated_observation = self._request(
                kind="calibrated_fiber_observation",
                scope=membership_scope,
                components={
                    "flux": scaled_flux_component(
                        "flux", final_arrays["flux"], "fiber_by_dispersion_pixel"
                    ),
                    "variance": scaled_variance_component(
                        "variance", final_arrays["variance"], "fiber_by_dispersion_pixel"
                    ),
                    "mask": _mask_component(
                        "mask", final_arrays["mask"], "1", "fiber_by_dispersion_pixel"
                    ),
                    "wavelength": _component(
                        "wavelength", final_arrays["wavelength"], "Angstrom",
                        "fiber_by_dispersion_pixel",
                    ),
                    "fiber_identity": _component(
                        "fiber_identity", final_arrays["fiber_identity"], "1", "none"
                    ),
                    "sky_coordinates": _component(
                        "sky_coordinates", final_arrays["sky_coordinates"], "deg", "icrs"
                    ),
                    "focal_plane_coordinates": _component(
                        "focal_plane_coordinates", final_arrays["focal_plane_coordinates"],
                        "arcsec", "none",
                    ),
                    "exposure_index": _component(
                        "exposure_index", final_arrays["exposure_index"], "1", "none"
                    ),
                },
                parents=sorted(final_parents),
                summaries={
                    "exposure_count": 3,
                    "fiber_count": int(final_arrays["flux"].shape[0]),
                    "wavelength_sample_count": int(final_arrays["flux"].shape[1]),
                    "spectral_plane_count": 3,
                    "masked_sample_fraction": float(np.mean(final_arrays["mask"] != 0)),
                },
                metadata={
                    "member_exposure_ids": list(exposure_ids),
                    "spectral_planes": ["flux", "variance", "mask"],
                    "wavelength_sampling": "per_fiber_native_bin_centers",
                    "flux_scale": 1e-17,
                    "uncertainty_convention": "variance",
                    "model_artifact_ids_by_exposure": {
                        state.exposure_id: list(state.model_artifact_ids) for state in ordered_states
                    },
                    "exposure_metadata": {
                        state.exposure_id: dict(state.metadata) for state in ordered_states
                    },
                    "intermediates_retained": False,
                },
                refs=refs,
                assumptions=[
                    "Exposure measurements remain separate rows; the observation product concatenates rather than coadds dithers."
                ],
                status="pass" if registration_consistent else "warn",
                usability="usable" if registration_consistent else "degraded",
            )
            result["calibrated_fiber_observation"] = calibrated_observation
        return result
