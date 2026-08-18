from __future__ import annotations

import logging
from typing import Dict, Iterable, Type

import numpy as np

from .base import CalibrationTask
from ..algorithms.bias import step_bias
from ..algorithms.cmp import step_cmp
from ..algorithms.arc import compose_master_arc
from ..algorithms.dark import step_dark
from ..algorithms.flat import step_flt
from ..algorithms.trace import fit_fiber_traces
from ..algorithms.twi import step_twi
from ..algorithms.master_sci import build_master_sci
from ..algorithms.fiber_response import (
    fit_exposure_fiber_response,
)
from ..algorithms.calibration_detector import (
    DARK_BIAS_CONVENTION,
    correct_response_calibration_frames,
)
from ..algorithms.master_spectrum import extract_master_spectrum
from ..algorithms.physical_ccd import (
    ALGORITHM_VERSION as SCATTER_ALGORITHM_VERSION,
    TRANSFORM_VERSION,
    amplifier_from_physical,
    assemble_physical_ccd,
    compact_scattered_light_payload,
    fit_gap_scattered_light,
)
from ..algorithms.wave import fit_wavelength_solution
from ..artifacts import ArtifactService, Scope
from ..artifacts.models import ConfigurationReference
from ..artifacts.requests import (ArtifactRequest, LogicalComponent,
                                  MeasurementGroupInputRequest,
                                  MeasurementGroupMembershipRequest)
from ..config.defaults import (
    MASTER_SCI_EXTRACTION_CONFIGURATION,
    WAVELENGTH_INPUT_MASK_CONFIGURATION,
)
from ..contracts.result import (
    BiasResultContract,
    CmpResultContract,
    DarkResultContract,
    FlatResultContract,
    TraceResultContract,
    TwiResultContract,
    WaveResultContract,
    MasterSciResultContract,
    ExposureFiberResponseResultContract,
    ExtractedMasterSpectrumResultContract,
    ExtractedMasterSciSpectrumResultContract,
)
from ..core.algo_result import AlgoResult, ensure_algo_result
from ..core.identity import parse_zipcode_key
from ..core.scientific_metadata import (
    SCIENTIFIC_METADATA_FIELDS,
    aggregate_scientific_metadata,
    normalize_scientific_metadata,
)
from ..ontology.artifact_kinds import kind_spec
from ..persistence.policy import DefaultPersistencePolicy
from ..publication.context import PublicationContext
from ..publication.service import DefaultPublicationService


logger = logging.getLogger(__name__)


AMP_CODE = {"LL": 0, "LU": 1, "RU": 2, "RL": 3}


def _artifact_id(row) -> int:
    return int(row.id) if hasattr(row, "id") else int(row["id"])


def _dependency_artifacts(inputs, kind: str) -> list:
    """Return all direct scheduled dependency outputs of one Artifact kind."""

    found = []
    for value in (inputs or {}).values():
        if not isinstance(value, dict):
            continue
        artifact = value.get(kind)
        if artifact is not None:
            found.append(artifact)
    return found


def _planned_parent_rows(service, target, kind: str) -> list[dict]:
    """Resolve registry-cached parents by the planner's exact group identity."""

    group_ids = {
        str(group_id)
        for parent_kind, group_id in (
            (getattr(target, "group_metadata", None) or {}).get("parent_groups", ())
        )
        if parent_kind == kind
    }
    if not group_ids:
        return []
    return service.adapter.find_by_calibration_groups(
        kind=kind, calibration_group_ids=group_ids, state="active"
    )


def _model_type(value) -> str:
    return "array1d" if np.asarray(value).ndim == 1 else "array2d"


class _CanonicalTask(CalibrationTask):
    result_kind: str = ""
    result_contract: Type = object
    component_map: Dict[str, str] = {}
    component_units: Dict[str, str] = {}
    algorithm_name: str = ""

    @staticmethod
    def _dependency(inputs, kind: str):
        """Return the exact artifact produced by a scheduled prerequisite."""

        for value in (inputs or {}).values():
            if not isinstance(value, dict):
                continue
            artifact = value.get(kind)
            if artifact is not None:
                return artifact
        return None

    def _params(self) -> dict:
        params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for key, value in self.ctx.config.items():
                if key != "raw_frame_loader":
                    params.setdefault(key, value)
        return params

    def _components(self, result: AlgoResult) -> Dict[str, LogicalComponent]:
        spec = kind_spec(self.artifact_name)
        components: Dict[str, LogicalComponent] = {}
        for output_name, component_name in self.component_map.items():
            value = result.get_array(output_name)
            if value is None:
                continue
            unit = self.component_units.get(
                component_name, "1" if "mask" in component_name else spec.units
            )
            components[component_name] = LogicalComponent(
                name=component_name,
                model_type=_model_type(value),
                value=value,
                units=unit,
                coordinates=spec.coordinates.value,
            )
        missing = set(spec.required_components) - set(components)
        if missing:
            raise RuntimeError(f"{self.__class__.__name__}: missing required components: {sorted(missing)}")
        return components

    def _publish(
        self,
        result: AlgoResult,
        parent_ids: Iterable[int],
        *,
        raw_parent_ids: Iterable[int] = (),
        configuration_refs=None,
        parameters=None,
    ):
        parent_ids = [int(value) for value in parent_ids]
        raw_parent_ids = [int(value) for value in raw_parent_ids]
        spec = kind_spec(self.artifact_name)
        components = self._components(result)
        summaries = dict(result.scalars or {})
        scientific_metadata = normalize_scientific_metadata(result.meta)
        algorithm_metadata = {
            key: value for key, value in dict(result.meta or {}).items()
            if key not in SCIENTIFIC_METADATA_FIELDS
        }
        request = ArtifactRequest(
            kind=self.artifact_name,
            components=components,
            summaries=summaries,
            metadata={
                "n_inputs": summaries.get("n_inputs", 0),
                "calibration_group_id": getattr(self.target, "group_id", None),
                "calibration_group": getattr(self.target, "group_metadata", None),
                "algorithm_metadata": algorithm_metadata,
            },
            scientific_metadata=scientific_metadata,
            scope=Scope(zipcode=self.target.zipcode, physical_scope=spec.scope),
            parents=parent_ids,
            raw_parents=raw_parent_ids,
            raw_catalog=(
                str(self.ctx.resolved_raw_db_path()) if raw_parent_ids else None
            ),
            validity=self.target_validity(),
            configuration_refs=list(
                self.configuration_references()
                if configuration_refs is None else configuration_refs
            ),
            labels=["calibration", self.artifact_name],
            measurement_group_membership=(
                MeasurementGroupMembershipRequest(
                    group=self.target.output_measurement_group,
                    member_scope_key=self.target.zipcode.key(),
                    member_computation_id=getattr(self.target, "group_id", ""),
                ) if getattr(self.target, "output_measurement_group", None) is not None
                and self.target.zipcode is not None else None
            ),
            measurement_group_inputs=[
                MeasurementGroupInputRequest(
                    input_name=selection.input_name, group=selection.group,
                    selection_policy=selection.policy, match_quality=selection.match_quality,
                    selection_reason=selection.reason,
                ) for selection in getattr(self.target, "selected_measurement_groups", ())
            ],
        )
        service = ArtifactService(self.ctx.db_path)
        publisher = DefaultPublicationService(
            svc=service, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir
        )
        context = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name=self.algorithm_name,
            algorithm_version=result.version,
            parameters=dict(parameters if parameters is not None else (self.params or {})),
            # ArtifactRequest.parents is the sole authoritative parent interface.
            parent_ids=[],
            timings={},
        )
        artifact = publisher.publish([request], context)[0]
        self.evaluate_qa(service, artifact, result)
        # A validated terminal evidence Product is the earliest safe point to
        # release dense rebuildable ancestors.  This follows only this
        # Product's provenance chain; it is not a registry-wide cleanup pass.
        try:
            from ..performance import current_task_timing, phase

            with phase("payload_retention"):
                eviction = service.evict_payloads_triggered_by(int(artifact.id))
            timing = current_task_timing()
            if timing is not None:
                timing.increment(
                    "retention_candidates", int(eviction.get("candidate_count") or 0)
                )
                timing.increment(
                    "retention_artifacts_evicted",
                    len(eviction.get("evicted_artifact_ids") or []),
                )
                timing.increment(
                    "retention_bytes_removed", int(eviction.get("removed_bytes") or 0)
                )
        except Exception:
            logger.exception(
                "Payload retention hook failed after publishing %s (artifact_id=%s); "
                "the scientific Product remains valid",
                artifact.kind,
                artifact.id,
            )
        else:
            for refusal in eviction["refused"]:
                logger.warning(
                    "Payload eviction deferred for %s (artifact_id=%s) after %s: %s",
                    refusal["kind"],
                    refusal["artifact_id"],
                    artifact.kind,
                    refusal["reason"],
                )
        return artifact


class _RawCalibrationTask(_CanonicalTask):
    combine_method = "unspecified"
    apply_response_detector_corrections = False

    def validate_scientific_result(self, result: AlgoResult) -> None:
        return None

    def run(self, inputs):
        from ..performance import current_task_timing, phase

        self._require_target()
        raw_inputs, parent_ids = self.query_inputs()
        arrays = self.load_reduced_inputs(raw_inputs)
        calibration_parent_ids = []
        correction_result = None
        if self.apply_response_detector_corrections:
            service = ArtifactService(self.ctx.db_path)
            bias_row = self._dependency(inputs, "master_bias") or self._resolve_artifact(
                "master_bias", required=True
            )
            dark_row = self._dependency(inputs, "master_dark") or self._resolve_artifact(
                "master_dark", required=True
            )
            bias_id, dark_id = _artifact_id(bias_row), _artifact_id(dark_row)
            dark_summary = service.describe(dark_id)["summary"]
            reference_seconds = float(
                dark_summary.get("reference_exposure_time_seconds") or 0.0
            )
            bias_convention = dark_summary.get("bias_convention")
            correction_result = correct_response_calibration_frames(
                np.stack([np.asarray(item["data"]) for item in arrays]),
                np.stack([np.asarray(item["variance"]) for item in arrays]),
                np.asarray([
                    float(item["header"].get("EXPTIME") or 0.0) for item in arrays
                ]),
                master_bias=service.load_component(bias_id, "master")["data"],
                master_bias_scatter=service.load_component(
                    bias_id, "per_pixel_bias_scatter"
                )["data"],
                master_dark=service.load_component(dark_id, "master_dark")["data"],
                dark_pixel_mask=service.load_component(dark_id, "dark_pixel_mask")["data"],
                dark_reference_exposure_time=reference_seconds,
                dark_bias_convention=bias_convention,
            )
            for index, item in enumerate(arrays):
                item["data"] = correction_result.get_array("corrected_images")[index]
                item["variance"] = correction_result.get_array("corrected_variances")[index]
                item["error"] = np.sqrt(item["variance"], dtype=np.float32)
                item["pixel_mask"] = correction_result.get_array("pixel_masks")[index]
            calibration_parent_ids = [bias_id, dark_id]
        timing = current_task_timing()
        if timing is not None:
            timing.increment("frame_count", len(arrays))
            if arrays:
                sample = np.asarray(arrays[0]["data"])
                timing.identity("array_shape", "x".join(str(value) for value in sample.shape))
                timing.identity("array_dtype", str(sample.dtype))
            timing.identity("combine_method", self.combine_method)
        with phase("combine_frames"):
            if timing is not None:
                timing.increment("combine_calls")
            result = ensure_algo_result(
                self.algorithm(raw_inputs=arrays, params=self._params()), kind=self.result_kind
            )
        if self.artifact_name == "master_dark":
            exposure_times = np.asarray([
                float(item["header"].get("EXPTIME") or 0.0) for item in arrays
            ])
            positive = exposure_times[np.isfinite(exposure_times) & (exposure_times > 0.0)]
            if positive.size != exposure_times.size:
                raise RuntimeError("master_dark inputs require a positive EXPTIME")
            reference_seconds = float(np.median(positive))
            if not np.allclose(positive, reference_seconds, rtol=0.0, atol=1e-6):
                raise RuntimeError(
                    "electron-valued master_dark inputs require one common EXPTIME"
                )
            result.scalars["reference_exposure_time_seconds"] = reference_seconds
            result.scalars["bias_convention"] = DARK_BIAS_CONVENTION
        if correction_result is not None:
            result.meta.update(correction_result.meta)
            result.meta.update({
                "detector_correction_bias_artifact_id": calibration_parent_ids[0],
                "detector_correction_dark_artifact_id": calibration_parent_ids[1],
                "scattered_light_treatment": "physical_ccd_before_master_spectrum_extraction",
            })
            result.scalars.update({
                key: value for key, value in correction_result.scalars.items()
                if key != "frame_count"
            })
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError(f"{self.__class__.__name__} result contract: {'; '.join(report.errors)}")
        self.validate_scientific_result(result)
        from ..registry import database as db

        result.meta.update(aggregate_scientific_metadata(
            db.list_raw_scientific_metadata(parent_ids, db_path=self.ctx.resolved_raw_db_path())
        ))
        artifact = self._publish(
            result, calibration_parent_ids, raw_parent_ids=parent_ids
        )
        return {self.artifact_name: artifact}


class BiasTask(_RawCalibrationTask):
    name = "bias"
    version = "v2"
    frame_type = "zro"
    artifact_name = "master_bias"
    algorithm = staticmethod(step_bias)
    algorithm_name = "virusflow.algorithms.bias.step_bias"
    result_kind = "bias"
    result_contract = BiasResultContract
    component_map = {"master": "master", "per_pixel_bias_scatter": "per_pixel_bias_scatter"}
    combine_method = "chunked fixed-center biweight_location + MAD"


class DarkTask(_RawCalibrationTask):
    name = "dark"
    version = "v3"
    frame_type = "drk"
    artifact_name = "master_dark"
    algorithm = staticmethod(step_dark)
    algorithm_name = "virusflow.algorithms.dark.step_dark"
    result_kind = "dark"
    result_contract = DarkResultContract
    component_map = {"master_dark": "master_dark", "dark_pixel_mask": "dark_pixel_mask"}
    combine_method = "chunked fixed-center biweight_location"


class FlatTask(_RawCalibrationTask):
    name = "flat"
    version = "v2"
    frame_type = "flt"
    artifact_name = "master_ldls"
    algorithm = staticmethod(step_flt)
    algorithm_name = "virusflow.algorithms.flat.step_flt"
    result_kind = "flat"
    result_contract = FlatResultContract
    component_map = {"master_flat": "master_ldls", "flat_response_mask": "flat_response_mask"}
    combine_method = "chunked fixed-center biweight_location"
    apply_response_detector_corrections = True

class CmpTask(_RawCalibrationTask):
    name = "cmp"
    version = "v2"
    frame_type = "cmp"
    artifact_name = "master_arc"
    algorithm = staticmethod(step_cmp)
    algorithm_name = "virusflow.algorithms.cmp.step_cmp"
    result_kind = "cmp"
    result_contract = CmpResultContract
    component_map = {"master_comparison_lamp": "master_arc"}
    combine_method = "chunked fixed-center biweight_location"


class HgTask(CmpTask):
    name = "hg"
    artifact_name = "master_hg"
    component_map = {"master_comparison_lamp": "master_hg"}


class CdTask(CmpTask):
    name = "cd"
    artifact_name = "master_cd"
    component_map = {"master_comparison_lamp": "master_cd"}


class ArcTask(_CanonicalTask):
    name = "arc"
    version = "v2"
    artifact_name = "master_arc"
    algorithm_name = "virusflow.algorithms.arc.compose_master_arc"
    result_kind = "cmp"
    result_contract = CmpResultContract
    component_map = {"master_comparison_lamp": "master_arc"}

    def run(self, inputs):
        self._require_target()
        service = ArtifactService(self.ctx.db_path)
        hg = self._dependency(inputs, "master_hg") or self._resolve_artifact("master_hg", required=True)
        cd = self._dependency(inputs, "master_cd") or self._resolve_artifact("master_cd", required=True)
        hg_id = int(hg.id) if hasattr(hg, "id") else int(hg["id"])
        cd_id = int(cd.id) if hasattr(cd, "id") else int(cd["id"])
        result = compose_master_arc(
            service.load_component(hg_id, "master_hg")["data"],
            service.load_component(cd_id, "master_cd")["data"],
        )
        result.meta.update(aggregate_scientific_metadata([
            service.get_scientific_metadata(hg_id),
            service.get_scientific_metadata(cd_id),
        ]))
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError("ArcTask result contract: " + "; ".join(report.errors))
        artifact = self._publish(result, [hg_id, cd_id])
        return {self.artifact_name: artifact}


class MasterSciTask(_RawCalibrationTask):
    name = "master_sci"
    version = "v2"
    frame_type = "sci"
    artifact_name = "master_sci"
    algorithm = staticmethod(build_master_sci)
    algorithm_name = "virusflow.algorithms.master_sci.build_master_sci"
    result_kind = "master_sci"
    result_contract = MasterSciResultContract
    component_map = {"master_sci": "master_sci"}
    combine_method = "chunked fixed-center biweight_location"
    apply_response_detector_corrections = True

    def validate_scientific_result(self, result: AlgoResult) -> None:
        metadata = getattr(self.target, "group_metadata", None) or {}
        grouping = metadata.get("grouping_configuration") or {}
        sufficiency = metadata.setdefault("sufficiency", {})
        measured = float(result.scalars["robust_illumination"])
        minimum = grouping.get("minimum_robust_illumination")
        illumination_ok = minimum is None or measured >= float(minimum)
        sufficiency.update({
            "measured_robust_illumination": measured,
            "illumination_measurement_pending": False,
            "illumination_sufficient": illumination_ok,
        })
        sufficiency["sufficient"] = bool(sufficiency.get("sufficient", True) and illumination_ok)
        if not illumination_ok:
            raise RuntimeError(
                "master_sci insufficient robust illumination: "
                f"measured={measured}, minimum={float(minimum)}"
            )


class _ExtractedMasterSpectrumTask(_CanonicalTask):
    version = "v2"
    master_kind = ""
    result_contract = ExtractedMasterSpectrumResultContract
    algorithm_name = "virusflow.algorithms.master_spectrum.extract_master_spectrum"
    component_map = {
        "spectrum": "spectrum",
        "valid_pixel_fraction": "valid_pixel_fraction",
        "effective_aperture_width": "effective_aperture_width",
        "extraction_valid": "extraction_valid",
        "aperture_start_row": "aperture_start_row",
        "aperture_first_weight": "aperture_first_weight",
        "aperture_last_weight": "aperture_last_weight",
        "aperture_sample_mask_bits": "aperture_sample_mask_bits",
    }
    component_units = {
        "spectrum": "electron",
        "valid_pixel_fraction": "1",
        "effective_aperture_width": "pixel",
        "extraction_valid": "1",
        "aperture_start_row": "pixel",
        "aperture_first_weight": "1",
        "aperture_last_weight": "1",
        "aperture_sample_mask_bits": "1",
    }

    def run(self, inputs):
        self._require_target()
        service = ArtifactService(self.ctx.db_path)
        zipcode = self.target.zipcode
        pair = {"LL": ("left", "LL", "LU"), "LU": ("left", "LL", "LU"),
                "RU": ("right", "RU", "RL"), "RL": ("right", "RU", "RL")}
        side, lower_amp, upper_amp = pair[zipcode.amp]
        lower_zipcode = type(zipcode)(
            zipcode.ifuslot, zipcode.ifuid, zipcode.specid, lower_amp, zipcode.controller
        )
        upper_zipcode = type(zipcode)(
            zipcode.ifuslot, zipcode.ifuid, zipcode.specid, upper_amp, zipcode.controller
        )

        planned_rows_by_kind = {}

        def planned_rows_for(kind: str) -> list[dict]:
            if kind not in planned_rows_by_kind:
                planned_rows_by_kind[kind] = _planned_parent_rows(
                    service, self.target, kind
                )
            return planned_rows_by_kind[kind]

        def rows_for(kind: str) -> dict[str, dict]:
            rows = {}
            for artifact in _dependency_artifacts(inputs, kind):
                row = service.adapter.get_row(_artifact_id(artifact))
                if row is not None and row.get("amp_key"):
                    rows[str(row["amp_key"])] = row
            for row in planned_rows_for(kind):
                if row.get("amp_key"):
                    rows.setdefault(str(row["amp_key"]), row)
            return rows

        master_rows = rows_for(self.master_kind)
        trace_rows = rows_for("trace_map")
        for required_zipcode in (lower_zipcode, upper_zipcode):
            key = required_zipcode.key()
            if key not in master_rows:
                row = None
                if not planned_rows_for(self.master_kind):
                    row = service.select_best(
                        kind=self.master_kind, scope=Scope(zipcode=required_zipcode),
                        at_time=self._target_mid_time(), policy="latest_valid",
                    ) or service.select_best(
                        kind=self.master_kind, scope=Scope(zipcode=required_zipcode),
                        at_time=self._target_mid_time(), policy="nearest",
                    )
                if row is not None:
                    master_rows[key] = row
            if key not in trace_rows:
                row = None
                if not planned_rows_for("trace_map"):
                    row = service.select_best(
                        kind="trace_map", scope=Scope(zipcode=required_zipcode),
                        at_time=self._target_mid_time(), policy="latest_valid",
                    ) or service.select_best(
                        kind="trace_map", scope=Scope(zipcode=required_zipcode),
                        at_time=self._target_mid_time(), policy="nearest",
                    )
                if row is not None:
                    trace_rows[key] = row
        missing = [
            f"{kind}:{key}" for kind, mapping in (
                (self.master_kind, master_rows), ("trace_map", trace_rows)
            ) for key in (lower_zipcode.key(), upper_zipcode.key()) if key not in mapping
        ]
        if missing:
            raise RuntimeError(
                f"{self.__class__.__name__} requires a complete physical-CCD pair: "
                + ", ".join(missing)
            )

        lower_master_row, upper_master_row = (
            master_rows[lower_zipcode.key()], master_rows[upper_zipcode.key()]
        )
        lower_trace_row, upper_trace_row = (
            trace_rows[lower_zipcode.key()], trace_rows[upper_zipcode.key()]
        )
        lower_master_id, upper_master_id = (
            _artifact_id(lower_master_row), _artifact_id(upper_master_row)
        )
        lower_trace_id, upper_trace_id = (
            _artifact_id(lower_trace_row), _artifact_id(upper_trace_row)
        )
        lower_image = np.asarray(
            service.load_component(lower_master_id, self.master_kind)["data"], dtype=np.float32
        )
        upper_image = np.asarray(
            service.load_component(upper_master_id, self.master_kind)["data"], dtype=np.float32
        )

        def detector_mask(master_row, image) -> np.ndarray:
            mask = (~np.isfinite(image)).astype(np.uint8)
            if self.master_kind == "master_ldls":
                mask |= np.asarray(
                    service.load_component(master_row, "flat_response_mask")["data"],
                    dtype=np.uint8,
                )
            for parent_id in service.describe(master_row)["provenance"]["parents"]:
                parent = service.adapter.get_row(int(parent_id))
                if parent is not None and parent.get("kind") == "master_dark":
                    mask |= np.asarray(
                        service.load_component(parent, "dark_pixel_mask")["data"],
                        dtype=np.uint8,
                    )
            return mask

        lower_mask = detector_mask(lower_master_row, lower_image)
        upper_mask = detector_mask(upper_master_row, upper_image)
        assembly = assemble_physical_ccd(
            lower_image, upper_image, side=side, lower_amp=lower_amp, upper_amp=upper_amp,
            lower_variance=np.zeros_like(lower_image, dtype=np.float32),
            upper_variance=np.zeros_like(upper_image, dtype=np.float32),
            lower_mask=lower_mask, upper_mask=upper_mask,
        )
        scatter = fit_gap_scattered_light(
            assembly,
            service.load_component(lower_trace_id, "fiber_trace_map")["data"],
            service.load_component(upper_trace_id, "fiber_trace_map")["data"],
            **{key: value for key, value in self._params().items() if key in {
                "core_exclusion_pixels", "minimum_group_gap_pixels",
                "holdout_chunk_period", "sigma_clip", "iterations",
            }},
        )
        compact = compact_scattered_light_payload(scatter)
        coordinate = kind_spec("ccd_scattered_light_model").coordinates.value
        scatter_request = ArtifactRequest(
            kind="ccd_scattered_light_model",
            components={
                name: LogicalComponent(
                    name=name,
                    model_type=_model_type(compact[name]),
                    value=compact[name],
                    units=(
                        "electron" if name in {"model_parameters", "residual_sample_values"}
                        else "1"
                    ),
                    coordinates=coordinate,
                )
                for name in kind_spec("ccd_scattered_light_model").required_components
            },
            summaries=dict(scatter.scalars),
            metadata={
                "calibration_group_id": getattr(self.target, "group_id", None),
                "calibration_group": getattr(self.target, "group_metadata", None),
                "calibration_input_kind": self.master_kind,
                "participating_amplifiers": [lower_zipcode.key(), upper_zipcode.key()],
                "algorithm_metadata": dict(scatter.meta),
            },
            scientific_metadata=aggregate_scientific_metadata([
                service.get_scientific_metadata(lower_master_id),
                service.get_scientific_metadata(upper_master_id),
            ]),
            # Retain one amplifier-addressable projection of the jointly fitted
            # CCD model so existing scope-local lineage/retention queries can
            # audit either participating dense master without duplicating the
            # numerical fit.
            scope=Scope(zipcode=zipcode, physical_scope=kind_spec(
                "ccd_scattered_light_model"
            ).scope),
            parents=sorted({
                lower_master_id, upper_master_id, lower_trace_id, upper_trace_id
            }),
            validity=self.target_validity(),
            configuration_refs=[ConfigurationReference(
                "ccd_transform", TRANSFORM_VERSION, f"{zipcode.specid}:{side}", "verified"
            )],
            labels=["calibration", "response", self.master_kind, "scattered_light"],
            assumptions=["The summed scattered-light field is smooth across the physical CCD."],
        )
        publisher = DefaultPublicationService(
            svc=service, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir
        )
        scatter_artifact = publisher.publish([scatter_request], PublicationContext(
            task_name=f"{self.name}_physical_ccd_scatter",
            task_version=self.version,
            algorithm_name="virusflow.algorithms.physical_ccd.fit_gap_scattered_light",
            algorithm_version=SCATTER_ALGORITHM_VERSION,
            parameters=dict(self.params or {}), parent_ids=[], timings={},
        ))[0]
        self.evaluate_qa(service, scatter_artifact, scatter)

        physical_image = np.asarray(
            scatter.get_array("scatter_subtracted_image"), dtype=np.float32
        )
        physical_mask = np.asarray(assembly.get_array("pixel_mask"), dtype=np.uint8)
        if zipcode.amp in {"LL", "RU"}:
            trace_row = lower_trace_row
        else:
            trace_row = upper_trace_row
        master_image = amplifier_from_physical(physical_image, zipcode.amp)
        pixel_mask = amplifier_from_physical(physical_mask, zipcode.amp)
        master_row = master_rows[zipcode.key()]
        master_id, trace_id = _artifact_id(master_row), _artifact_id(trace_row)
        params = dict(MASTER_SCI_EXTRACTION_CONFIGURATION.value)
        params.update(self._params())
        result = extract_master_spectrum(
            master_image,
            service.load_component(trace_id, "fiber_trace_map")["data"],
            result_kind=self.artifact_name,
            aperture_width=float(params["aperture_width"]),
            pixel_mask=pixel_mask,
        )
        result.meta.update({
            "scattered_light_treatment": "paired_physical_ccd_gap_model_subtracted",
            "scattered_light_artifact_id": int(scatter_artifact.id),
            "participating_amplifiers": [lower_zipcode.key(), upper_zipcode.key()],
        })
        result.meta.update(service.get_scientific_metadata(master_id))
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError(
                f"{self.__class__.__name__} result contract: "
                + "; ".join(report.errors)
            )
        refs = self.configuration_references() + [ConfigurationReference(
            MASTER_SCI_EXTRACTION_CONFIGURATION.kind,
            MASTER_SCI_EXTRACTION_CONFIGURATION.version,
            self.target.zipcode.key(),
            MASTER_SCI_EXTRACTION_CONFIGURATION.evidence_state,
        )]
        artifact = self._publish(
            result, [master_id, trace_id, int(scatter_artifact.id)],
            configuration_refs=refs, parameters=params
        )
        return {
            self.artifact_name: artifact,
            "ccd_scattered_light_model": scatter_artifact,
        }


class ExtractedMasterSciSpectrumTask(_ExtractedMasterSpectrumTask):
    name = "master_sci_extraction"
    artifact_name = "extracted_master_sci_spectrum"
    master_kind = "master_sci"
    result_kind = artifact_name
    result_contract = ExtractedMasterSciSpectrumResultContract


class ExtractedMasterLdlsSpectrumTask(_ExtractedMasterSpectrumTask):
    name = "master_ldls_extraction"
    artifact_name = "extracted_master_ldls_spectrum"
    master_kind = "master_ldls"
    result_kind = artifact_name


class ExtractedMasterTwilightSpectrumTask(_ExtractedMasterSpectrumTask):
    name = "master_twilight_extraction"
    artifact_name = "extracted_master_twilight_spectrum"
    master_kind = "master_twilight"
    result_kind = artifact_name


class ExposureFiberResponseTask(_CanonicalTask):
    """Publish one exposure-wide, three-component fiber response."""

    name = "exposure_fiber_response"
    version = "v2"
    artifact_name = "exposure_fiber_response"
    algorithm_name = "virusflow.algorithms.fiber_response.fit_exposure_fiber_response"
    result_kind = artifact_name
    result_contract = ExposureFiberResponseResultContract
    component_map = {
        "raw_ratio": "raw_ratio",
        "normalization": "normalization",
        "valid_mask": "valid_mask",
        "common_ldls": "common_ldls",
        "common_twilight": "common_twilight",
        "within_amplifier_response": "within_amplifier_response",
        "amplifier_response": "amplifier_response",
        "amplifier_scalar": "amplifier_scalar",
        "amplifier_common_response": "amplifier_common_response",
        "fiber_amplifier_index": "fiber_amplifier_index",
        "amplifier_identity": "amplifier_identity",
        "ftf_ldls": "ftf_ldls",
        "twilight_broad_correction": "twilight_broad_correction",
        "twilight_residual_correction": "twilight_residual_correction",
        "wavelength": "wavelength",
        "science_residual_scaled": "science_residual_scaled",
    }
    component_units = {
        "raw_ratio": "1",
        "normalization": "1",
        "valid_mask": "1",
        "common_ldls": "electron",
        "common_twilight": "electron",
        "within_amplifier_response": "1",
        "amplifier_response": "1",
        "amplifier_scalar": "1",
        "amplifier_common_response": "1",
        "fiber_amplifier_index": "1",
        "amplifier_identity": "1",
        "ftf_ldls": "1",
        "twilight_broad_correction": "1",
        "twilight_residual_correction": "1",
        "wavelength": "Angstrom",
        "science_residual_scaled": "1",
    }

    def run(self, inputs):
        self._require_target()
        service = ArtifactService(self.ctx.db_path)
        required_kinds = (
            "extracted_master_ldls_spectrum",
            "extracted_master_twilight_spectrum",
            "wavelength_map",
        )
        # Selections are frozen by the planner.  Build an ID index from only
        # terminal-successful scheduled dependencies; never query current slots.
        selections = {item.input_name: item for item in getattr(
            self.target, "selected_measurement_groups", ()
        )}
        resolved_rows = {}
        for kind in (*required_kinds, "extracted_master_sci_spectrum"):
            rows = {}
            selection = selections.get(kind)
            if selection is not None:
                for scope_key, artifact_id in selection.existing_artifact_ids.items():
                    row = service.adapter.get_row(int(artifact_id))
                    if row is not None:
                        rows[str(scope_key)] = row
                for scope_key, node_id in selection.scheduled_node_ids.items():
                    output = inputs.get(node_id)
                    artifact = output.get(kind) if isinstance(output, dict) else None
                    if artifact is not None:
                        row = service.adapter.get_row(_artifact_id(artifact))
                        if row is not None:
                            rows[str(scope_key)] = row
            else:  # compatibility for legacy targets only
                for artifact in _dependency_artifacts(inputs, kind):
                    row = service.adapter.get_row(_artifact_id(artifact))
                    if row is not None and row.get("amp_key"):
                        rows[str(row["amp_key"])] = row
                for row in _planned_parent_rows(service, self.target, kind):
                    if row.get("amp_key"):
                        rows.setdefault(str(row["amp_key"]), row)
            resolved_rows[kind] = rows
        ldls_rows = resolved_rows["extracted_master_ldls_spectrum"]
        requested_keys = list(
            selections.get("extracted_master_ldls_spectrum").requested_scope_keys
            if selections.get("extracted_master_ldls_spectrum") is not None else
            (getattr(self.target, "group_metadata", None) or {}).get("amplifier_keys")
            or sorted(ldls_rows)
        )
        if not requested_keys:
            raise RuntimeError("ExposureFiberResponseTask requires planned LDLS spectra")
        participants = []
        for key in sorted(requested_keys):
            rows = {}
            for kind in required_kinds:
                row = resolved_rows[kind].get(key)
                if row is not None:
                    rows[kind] = row
            if len(rows) == len(required_kinds):
                participants.append((key, rows))
        if not participants:
            raise RuntimeError("ExposureFiberResponseTask has no amplifiers with all required dependencies")
        params = self._params()
        ldls = [service.load_component(rows["extracted_master_ldls_spectrum"], "spectrum")["data"]
                for _, rows in participants]
        twilight = [service.load_component(rows["extracted_master_twilight_spectrum"], "spectrum")["data"]
                    for _, rows in participants]
        wavelength = [service.load_component(rows["wavelength_map"], "wavelength_map")["data"]
                      for _, rows in participants]
        science_parts = []
        science_rows = []
        for key, _ in participants:
            row = resolved_rows["extracted_master_sci_spectrum"].get(key)
            if row is None:
                science_parts = []
                science_rows = []
                break
            science_rows.append(row)
            science_parts.append(service.load_component(row, "spectrum")["data"])
        result = fit_exposure_fiber_response(
            ldls, twilight, wavelength,
            science_spectrum=(np.concatenate(science_parts) if science_parts else None),
            common_model_bins=int(params.get("common_model_bins", 3000)),
            broad_ldls_bins=int(params.get("broad_ldls_bins", 5)),
            twilight_residual_bins=int(params.get("twilight_residual_bins", 25)),
            minimum_wavelength_finite_fraction=float(
                params.get("minimum_wavelength_finite_fraction", 0.8)
            ),
        )
        identities = []
        parent_ids = []
        for key, rows in participants:
            zipcode = parse_zipcode_key(key)
            identities.append([int(zipcode.ifuslot), int(zipcode.ifuid), int(zipcode.specid), AMP_CODE[zipcode.amp]])
            parent_ids.extend(_artifact_id(row) for row in rows.values())
        parent_ids.extend(_artifact_id(row) for row in science_rows)
        result.arrays["amplifier_identity"] = np.asarray(identities, dtype=np.int32)
        result.meta.update(aggregate_scientific_metadata([
            service.get_scientific_metadata(_artifact_id(rows["extracted_master_twilight_spectrum"]))
            for _, rows in participants
        ]))
        result.meta.update({
            "amplifier_keys": [key for key, _ in participants],
            "excluded_amplifier_keys": sorted(set(requested_keys) - {key for key, _ in participants}),
            "participating_amplifiers": len(participants),
        })
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError(
                "ExposureFiberResponseTask result contract: " + "; ".join(report.errors)
            )
        artifact = self._publish(
            result, sorted(set(parent_ids)), configuration_refs=[], parameters=params
        )
        return {self.artifact_name: artifact}


class TwiTask(_RawCalibrationTask):
    name = "twi"
    version = "v2"
    frame_type = "twi"
    artifact_name = "master_twilight"
    algorithm = staticmethod(step_twi)
    algorithm_name = "virusflow.algorithms.twi.step_twi"
    result_kind = "twi"
    result_contract = TwiResultContract
    component_map = {"master_twilight": "master_twilight"}
    combine_method = "chunked fixed-center biweight_location"
    apply_response_detector_corrections = True

class TraceTask(_CanonicalTask):
    name = "trace"
    version = "v2"
    requires = ["flat"]
    frame_type = None
    artifact_name = "trace_map"
    algorithm_name = "virusflow.algorithms.trace.fit_fiber_traces"
    result_kind = "trace"
    result_contract = TraceResultContract
    component_map = {
        "fiber_trace_map": "fiber_trace_map",
        "trace_sample_columns": "trace_sample_columns",
        "sampled_trace_positions": "sampled_trace_positions",
        "per_fiber_trace_residual_rms": "per_fiber_trace_residual_rms",
        "trace_sample_valid_mask": "trace_sample_valid_mask",
        "trace_fit_residuals": "trace_fit_residuals",
        "per_fiber_valid_sample_count": "per_fiber_valid_sample_count",
        "trace_interpolated_fiber_mask": "trace_interpolated_fiber_mask",
    }
    component_units = {
        "fiber_trace_map": "pixel",
        "trace_sample_columns": "pixel",
        "sampled_trace_positions": "pixel",
        "per_fiber_trace_residual_rms": "pixel",
        "trace_sample_valid_mask": "1",
        "trace_fit_residuals": "pixel",
        "per_fiber_valid_sample_count": "1",
        "trace_interpolated_fiber_mask": "1",
    }

    def run(self, inputs):
        from ..config import ConfigurationService

        self._require_target()
        service = ArtifactService(self.ctx.db_path)
        parent = self._dependency(inputs, "master_ldls") or self._resolve_artifact("master_ldls", required=True)
        parent_id = int(parent.id) if hasattr(parent, "id") else int(parent["id"])
        master = service.load_component(parent_id, "master_ldls")["data"]
        root = self.ctx.config.get("configuration_root") if isinstance(self.ctx.config, dict) else None
        config = ConfigurationService(root=root)
        reference, trace_ref = config.resolve_trace_reference(
            zipcode=self.target.zipcode, at=self.target.start_date
        )
        result = fit_fiber_traces(
            master_ldls_array=master,
            trace_reference=reference,
            zipcode=self.target.zipcode,
        )
        result = ensure_algo_result(result, kind="trace")
        result.meta.update(service.get_scientific_metadata(parent_id))
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError("TraceTask result contract: " + "; ".join(report.errors))
        refs = self.configuration_references() + [trace_ref]
        artifact = self._publish(result, [parent_id], configuration_refs=refs)
        return {self.artifact_name: artifact}


class WaveTask(_CanonicalTask):
    name = "wave"
    version = "v2"
    requires = ["trace", "cmp"]
    frame_type = None
    artifact_name = "wavelength_map"
    algorithm_name = "virusflow.algorithms.wave.fit_wavelength_solution"
    result_kind = "wave"
    result_contract = WaveResultContract
    component_map = {
        "wavelength_map": "wavelength_map",
        "per_fiber_wavelength_residual_rms": "per_fiber_wavelength_residual_rms",
        "arc_identification": "arc_identification",
        "arc_candidate_evidence": "arc_candidate_evidence",
        "arc_line_evidence": "arc_line_evidence",
        "seed_region_attempted_mask": "seed_region_attempted_mask",
        "seed_region_success_mask": "seed_region_success_mask",
        "seed_region_failure_code": "seed_region_failure_code",
        "seed_fit_coefficients": "seed_fit_coefficients",
        "interpolated_fiber_mask": "interpolated_fiber_mask",
        "extrapolated_fiber_mask": "extrapolated_fiber_mask",
        "input_mask_indices": "input_mask_indices",
        "input_mask_shape": "input_mask_shape",
    }
    component_units = {
        "wavelength_map": "Angstrom",
        "per_fiber_wavelength_residual_rms": "Angstrom",
        "arc_identification": "1",
        "arc_candidate_evidence": "1",
        "arc_line_evidence": "1",
        "seed_region_attempted_mask": "1",
        "seed_region_success_mask": "1",
        "seed_region_failure_code": "1",
        "seed_fit_coefficients": "1",
        "interpolated_fiber_mask": "1",
        "extrapolated_fiber_mask": "1",
        "input_mask_indices": "1",
        "input_mask_shape": "1",
    }

    @staticmethod
    def _require_wavelength_map(result: AlgoResult) -> None:
        if result.get_array("wavelength_map") is not None:
            return
        reason = result.meta.get("failure_reason") or "wavelength modeling produced no solution"
        raise RuntimeError(f"WaveTask: {reason}")

    def run(self, inputs):
        self._require_target()
        service = ArtifactService(self.ctx.db_path)
        arc_row = self._dependency(inputs, "master_arc") or self._resolve_artifact("master_arc", required=True)
        trace_row = self._dependency(inputs, "trace_map") or self._resolve_artifact("trace_map", required=True)
        arc_id = int(arc_row.id) if hasattr(arc_row, "id") else int(arc_row["id"])
        trace_id = int(trace_row.id) if hasattr(trace_row, "id") else int(trace_row["id"])
        arc = service.load_component(arc_id, "master_arc")["data"]
        trace = service.load_component(trace_id, "fiber_trace_map")["data"]

        mask = None
        masks = []
        mask_parent_ids = []
        mask_facts = {"flat_mask_fraction": 0.0, "dark_mask_fraction": 0.0, "flat_mask_applied": 0}
        mask_policy = WAVELENGTH_INPUT_MASK_CONFIGURATION
        for kind, component in (("master_ldls", "flat_response_mask"), ("master_dark", "dark_pixel_mask")):
            row = self._resolve_artifact(kind, required=False)
            if row is not None:
                try:
                    candidate = np.asarray(service.load_component(row, component)["data"], dtype=bool)
                    if candidate.shape != arc.shape:
                        continue
                    candidate_id = int(row.id) if hasattr(row, "id") else int(row["id"])
                    fraction = float(candidate.mean())
                    if kind == "master_ldls":
                        mask_facts["flat_mask_fraction"] = fraction
                        if fraction <= float(mask_policy.value["maximum_flat_mask_fraction"]):
                            masks.append(candidate)
                            mask_parent_ids.append(candidate_id)
                            mask_facts["flat_mask_applied"] = 1
                    else:
                        mask_facts["dark_mask_fraction"] = fraction
                        masks.append(candidate)
                        mask_parent_ids.append(candidate_id)
                except KeyError:
                    pass
        if masks:
            mask = np.logical_or.reduce(masks)
            if mask.shape == arc.shape:
                from ..algorithms.utils.masks import interpolate_masked_detector_pixels

                arc = interpolate_masked_detector_pixels(np.asarray(arc, dtype=float), mask)

        algorithm_params = self._params()
        result = fit_wavelength_solution(
            master_comparison_lamp=arc,
            fiber_trace_map=trace,
            npix_extract=int(algorithm_params.get("npix_extract", 5)),
            res_lim=float(algorithm_params.get("res_lim", 1.0)),
            order=int(algorithm_params.get("order", 4)),
            input_pixel_mask=mask,
            params={**algorithm_params, "mask_applied": bool(mask is not None), **mask_facts},
        )
        result = ensure_algo_result(result, kind="wave")
        result.meta.update(service.get_scientific_metadata(arc_id))
        self._require_wavelength_map(result)
        result.scalars.update(mask_facts)
        result.meta.update({
            "input_mask_policy_version": mask_policy.version,
            "input_mask_policy_evidence": mask_policy.evidence_state,
        })
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError("WaveTask result contract: " + "; ".join(report.errors))
        refs = self.configuration_references() + [ConfigurationReference(
            mask_policy.kind, mask_policy.version, self.target.zipcode.key(), mask_policy.evidence_state
        )]
        artifact = self._publish(
            result, sorted({arc_id, trace_id, *mask_parent_ids}),
            configuration_refs=refs,
        )
        return {self.artifact_name: artifact}
