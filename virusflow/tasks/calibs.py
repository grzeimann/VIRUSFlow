from __future__ import annotations

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
from ..algorithms.fiber_response import fit_within_amplifier_response
from ..algorithms.master_spectrum import extract_master_spectrum
from ..algorithms.master_sci_mask import build_master_sci_spectral_mask
from ..algorithms.wave import fit_wavelength_solution
from ..artifacts import ArtifactService, Scope
from ..artifacts.models import ConfigurationReference
from ..artifacts.requests import ArtifactRequest, LogicalComponent
from ..config.defaults import (
    MASTER_SCI_EXTRACTION_CONFIGURATION,
    MASTER_SCI_SPECTRAL_MASK_CONFIGURATION,
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
    AmplifierFiberResponseResultContract,
    ExtractedMasterSpectrumResultContract,
    ExtractedMasterSciSpectrumResultContract,
    FiberWavelengthSpectralMaskResultContract,
)
from ..core.algo_result import AlgoResult, ensure_algo_result
from ..core.scientific_metadata import (
    SCIENTIFIC_METADATA_FIELDS,
    aggregate_scientific_metadata,
    normalize_scientific_metadata,
)
from ..ontology.artifact_kinds import kind_spec
from ..persistence.policy import DefaultPersistencePolicy
from ..publication.context import PublicationContext
from ..publication.service import DefaultPublicationService


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
        configuration_refs=None,
        parameters=None,
    ):
        parent_ids = [int(value) for value in parent_ids]
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
            validity=self.target_validity(),
            configuration_refs=list(configuration_refs or self.configuration_references()),
            labels=["calibration", self.artifact_name],
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
        return artifact


class _RawCalibrationTask(_CanonicalTask):
    combine_method = "unspecified"

    def validate_scientific_result(self, result: AlgoResult) -> None:
        return None

    def run(self, inputs):
        from ..performance import current_task_timing, phase

        self._require_target()
        raw_inputs, parent_ids = self.query_inputs()
        arrays = self.load_reduced_inputs(raw_inputs)
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
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError(f"{self.__class__.__name__} result contract: {'; '.join(report.errors)}")
        self.validate_scientific_result(result)
        from ..registry import database as db

        result.meta.update(aggregate_scientific_metadata(
            db.list_raw_scientific_metadata(parent_ids, db_path=self.ctx.resolved_raw_db_path())
        ))
        artifact = self._publish(result, parent_ids)
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
    version = "v2"
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
    version = "v1"
    master_kind = ""
    result_contract = ExtractedMasterSpectrumResultContract
    algorithm_name = "virusflow.algorithms.master_spectrum.extract_master_spectrum"
    component_map = {
        "spectrum": "spectrum",
        "valid_pixel_fraction": "valid_pixel_fraction",
        "effective_aperture_width": "effective_aperture_width",
        "extraction_valid": "extraction_valid",
    }
    component_units = {
        "spectrum": "electron",
        "valid_pixel_fraction": "1",
        "effective_aperture_width": "pixel",
        "extraction_valid": "1",
    }

    def run(self, inputs):
        self._require_target()
        service = ArtifactService(self.ctx.db_path)
        master_row = self._dependency(inputs, self.master_kind) or self._resolve_artifact(
            self.master_kind, required=True
        )
        trace_row = self._dependency(inputs, "trace_map") or self._resolve_artifact(
            "trace_map", required=True
        )
        master_id = int(master_row.id) if hasattr(master_row, "id") else int(master_row["id"])
        trace_id = int(trace_row.id) if hasattr(trace_row, "id") else int(trace_row["id"])
        params = dict(MASTER_SCI_EXTRACTION_CONFIGURATION.value)
        params.update(self._params())
        result = extract_master_spectrum(
            service.load_component(master_id, self.master_kind)["data"],
            service.load_component(trace_id, "fiber_trace_map")["data"],
            result_kind=self.artifact_name,
            aperture_width=float(params["aperture_width"]),
        )
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
            result, [master_id, trace_id], configuration_refs=refs, parameters=params
        )
        return {self.artifact_name: artifact}


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


class AmplifierFiberResponseTask(_CanonicalTask):
    """Publish the reusable LDLS-fine, twilight-anchored amplifier response."""

    name = "amplifier_fiber_response"
    version = "v1"
    artifact_name = "within_amp_fiber_normalization"
    algorithm_name = "virusflow.algorithms.fiber_response.fit_within_amplifier_response"
    result_kind = "within_amplifier_normalization"
    result_contract = AmplifierFiberResponseResultContract
    component_map = {
        "raw_ratio": "raw_ratio",
        "normalization": "normalization",
        "valid_mask": "valid_mask",
        "common_twilight": "common_twilight",
        "ftf_ldls": "ftf_ldls",
        "twilight_broad_correction": "twilight_broad_correction",
        "twilight_residual_correction": "twilight_residual_correction",
        "wavelength": "wavelength",
        "amplifier_twilight_level": "amplifier_twilight_level",
        "science_residual_per_fiber": "science_residual_per_fiber",
    }
    component_units = {
        "raw_ratio": "1",
        "normalization": "1",
        "valid_mask": "1",
        "common_twilight": "electron",
        "ftf_ldls": "1",
        "twilight_broad_correction": "1",
        "twilight_residual_correction": "1",
        "wavelength": "Angstrom",
        "amplifier_twilight_level": "electron",
        "science_residual_per_fiber": "1",
    }

    def run(self, inputs):
        self._require_target()
        service = ArtifactService(self.ctx.db_path)

        def required(kind: str):
            row = self._dependency(inputs, kind) or self._resolve_artifact(kind, required=True)
            return row, int(row.id) if hasattr(row, "id") else int(row["id"])

        ldls_row, ldls_id = required("extracted_master_ldls_spectrum")
        twilight_row, twilight_id = required("extracted_master_twilight_spectrum")
        wavelength_row, wavelength_id = required("wavelength_map")
        science_row = self._dependency(inputs, "extracted_master_sci_spectrum")
        science_id = None
        science_spectrum = None
        if science_row is not None:
            science_id = int(science_row.id) if hasattr(science_row, "id") else int(science_row["id"])
            science_spectrum = service.load_component(science_id, "spectrum")["data"]

        params = self._params()
        result = fit_within_amplifier_response(
            service.load_component(ldls_id, "spectrum")["data"],
            service.load_component(twilight_id, "spectrum")["data"],
            service.load_component(wavelength_id, "wavelength_map")["data"],
            science_spectrum=science_spectrum,
            common_model_bins=int(params.get("common_model_bins", 3000)),
            broad_twilight_bins=int(params.get("broad_twilight_bins", 5)),
            twilight_residual_bins=int(params.get("twilight_residual_bins", 25)),
            minimum_wavelength_finite_fraction=float(
                params.get("minimum_wavelength_finite_fraction", 0.8)
            ),
        )
        result.meta.update(service.get_scientific_metadata(twilight_id))
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError(
                "AmplifierFiberResponseTask result contract: " + "; ".join(report.errors)
            )
        parent_ids = [ldls_id, twilight_id, wavelength_id]
        if science_id is not None:
            parent_ids.append(science_id)
        artifact = self._publish(result, parent_ids, parameters=params)
        return {self.artifact_name: artifact}


class FiberWavelengthSpectralMaskTask(_CanonicalTask):
    name = "master_sci_spectral_mask"
    version = "v1"
    artifact_name = "fiber_wavelength_spectral_mask"
    algorithm_name = (
        "virusflow.algorithms.master_sci_mask.build_master_sci_spectral_mask"
    )
    result_kind = "fiber_wavelength_spectral_mask"
    result_contract = FiberWavelengthSpectralMaskResultContract
    component_map = {
        "mask": "mask",
        "spectral_model": "spectral_model",
        "normalization": "normalization",
        "good_wavelength_solution": "good_wavelength_solution",
    }
    component_units = {
        "mask": "1",
        "spectral_model": "electron",
        "normalization": "1",
        "good_wavelength_solution": "1",
    }

    def run(self, inputs):
        self._require_target()
        service = ArtifactService(self.ctx.db_path)
        spectrum_row = (
            self._dependency(inputs, "extracted_master_sci_spectrum")
            or self._resolve_artifact("extracted_master_sci_spectrum", required=True)
        )
        wavelength_row = self._dependency(inputs, "wavelength_map") or self._resolve_artifact(
            "wavelength_map", required=True
        )
        spectrum_id = int(spectrum_row.id) if hasattr(spectrum_row, "id") else int(spectrum_row["id"])
        wavelength_id = int(wavelength_row.id) if hasattr(wavelength_row, "id") else int(wavelength_row["id"])

        normalization = None
        parent_ids = [spectrum_id, wavelength_id]
        # Normalization is an explicit optional dependency, never an ambient
        # registry lookup: otherwise the same planned target could change when
        # a twilight product happened to appear between planning and execution.
        normalization_row = self._dependency(inputs, "within_amp_fiber_normalization")
        if normalization_row is not None:
            normalization_id = (
                int(normalization_row.id)
                if hasattr(normalization_row, "id") else int(normalization_row["id"])
            )
            normalization = service.load_component(
                normalization_id, "normalization"
            )["data"]
            parent_ids.append(normalization_id)

        params = dict(MASTER_SCI_SPECTRAL_MASK_CONFIGURATION.value)
        params.update(self._params())
        result = build_master_sci_spectral_mask(
            service.load_component(spectrum_id, "spectrum")["data"],
            service.load_component(wavelength_id, "wavelength_map")["data"],
            fiber_normalization=normalization,
            coarse_bins=int(params["coarse_bins"]),
            model_bins=int(params["model_bins"]),
            minimum_wavelength_finite_fraction=float(
                params["minimum_wavelength_finite_fraction"]
            ),
            amplifier_fibers=int(params["amplifier_fibers"]),
            very_bad_threshold=float(params["very_bad_threshold"]),
        )
        result.meta.update(service.get_scientific_metadata(spectrum_id))
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError(
                "FiberWavelengthSpectralMaskTask result contract: "
                + "; ".join(report.errors)
            )
        refs = self.configuration_references() + [ConfigurationReference(
            MASTER_SCI_SPECTRAL_MASK_CONFIGURATION.kind,
            MASTER_SCI_SPECTRAL_MASK_CONFIGURATION.version,
            self.target.zipcode.key(),
            MASTER_SCI_SPECTRAL_MASK_CONFIGURATION.evidence_state,
        )]
        artifact = self._publish(
            result, parent_ids, configuration_refs=refs, parameters=params
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
        mask_facts = {"flat_mask_fraction": 0.0, "dark_mask_fraction": 0.0, "flat_mask_applied": 0}
        mask_policy = WAVELENGTH_INPUT_MASK_CONFIGURATION
        for kind, component in (("master_ldls", "flat_response_mask"), ("master_dark", "dark_pixel_mask")):
            row = self._resolve_artifact(kind, required=False)
            if row is not None:
                try:
                    candidate = np.asarray(service.load_component(row, component)["data"], dtype=bool)
                    fraction = float(candidate.mean())
                    if kind == "master_ldls":
                        mask_facts["flat_mask_fraction"] = fraction
                        if fraction <= float(mask_policy.value["maximum_flat_mask_fraction"]):
                            masks.append(candidate)
                            mask_facts["flat_mask_applied"] = 1
                    else:
                        mask_facts["dark_mask_fraction"] = fraction
                        masks.append(candidate)
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
            result, [arc_id, trace_id], configuration_refs=refs
        )
        return {self.artifact_name: artifact}
