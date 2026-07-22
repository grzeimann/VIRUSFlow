from __future__ import annotations

import time
from typing import Dict, Iterable, Type

import numpy as np

from .base import CalibrationTask
from ..algorithms.bias import step_bias
from ..algorithms.cmp import step_cmp
from ..algorithms.dark import step_dark
from ..algorithms.flat import step_flt
from ..algorithms.sci import build_master_science
from ..algorithms.trace import fit_fiber_traces
from ..algorithms.twi import step_twi
from ..algorithms.wave import fit_wavelength_solution
from ..artifacts import ArtifactService, Scope
from ..artifacts.requests import ArtifactRequest, LogicalComponent
from ..contracts.result import (
    BiasResultContract,
    CmpResultContract,
    DarkResultContract,
    FlatResultContract,
    SciResultContract,
    TraceResultContract,
    TwiResultContract,
    WaveResultContract,
)
from ..core.algo_result import AlgoResult, ensure_algo_result
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
    algorithm_name: str = ""

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
            unit = "1" if "mask" in component_name else spec.units
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

    def _publish(self, result: AlgoResult, parent_ids: Iterable[int], *, configuration_refs=None):
        parent_ids = [int(value) for value in parent_ids]
        components = self._components(result)
        summaries = dict(result.scalars or {})
        request = ArtifactRequest(
            kind=self.artifact_name,
            components=components,
            summaries=summaries,
            metadata={"n_inputs": summaries.get("n_inputs", 0)},
            scope=Scope(zipcode=self.target.zipcode),
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
            parameters=dict(self.params or {}),
            # ArtifactRequest.parents is the sole authoritative parent interface.
            parent_ids=[],
            timings={},
        )
        artifact = publisher.publish([request], context)[0]
        self.evaluate_qa(service, artifact, result)
        return artifact


class _RawCalibrationTask(_CanonicalTask):
    def run(self, inputs):
        self._require_target()
        raw_inputs, parent_ids = self.query_inputs()
        arrays = self.load_reduced_inputs(raw_inputs)
        result = ensure_algo_result(
            self.algorithm(raw_inputs=arrays, params=self._params()), kind=self.result_kind
        )
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError(f"{self.__class__.__name__} result contract: {'; '.join(report.errors)}")
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


class FlatTask(_RawCalibrationTask):
    """Legacy public class name producing the canonical LDLS Product."""

    name = "flat"
    version = "v2"
    frame_type = "flt"
    artifact_name = "master_ldls"
    algorithm = staticmethod(step_flt)
    algorithm_name = "virusflow.algorithms.flat.step_flt"
    result_kind = "flat"
    result_contract = FlatResultContract
    component_map = {"master_flat": "master_ldls", "flat_response_mask": "flat_response_mask"}

    def run(self, inputs):
        result = super().run(inputs)
        result["master_flat"] = result[self.artifact_name]
        return result


class CmpTask(_RawCalibrationTask):
    """Legacy public class name producing aggregate Master Arc from raw cmp."""

    name = "cmp"
    version = "v2"
    frame_type = "cmp"
    artifact_name = "master_arc"
    algorithm = staticmethod(step_cmp)
    algorithm_name = "virusflow.algorithms.cmp.step_cmp"
    result_kind = "cmp"
    result_contract = CmpResultContract
    component_map = {"master_comparison_lamp": "master_arc"}

    def run(self, inputs):
        result = super().run(inputs)
        result["master_cmp"] = result[self.artifact_name]
        return result


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

    def run(self, inputs):
        result = super().run(inputs)
        result["master_twi"] = result[self.artifact_name]
        return result


class SciTask(_RawCalibrationTask):
    name = "sci"
    version = "v2"
    frame_type = "sci"
    artifact_name = "master_sci"
    algorithm = staticmethod(build_master_science)
    algorithm_name = "virusflow.algorithms.sci.build_master_science"
    result_kind = "sci"
    result_contract = SciResultContract
    # master_sci is outside the Step 1 canonical Product registry; retain legacy behavior.
    component_map = {"master_science": "master_science"}

    def _components(self, result):
        value = result.get_array("master_science")
        return {"master_science": LogicalComponent("master_science", "array2d", value, "electron", "oriented_amplifier_blue_to_red")}


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
        parent = self._resolve_artifact("master_ldls", required=True)
        master = service.load_component(parent, "master_ldls")["data"]
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
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError("TraceTask result contract: " + "; ".join(report.errors))
        refs = self.configuration_references() + [trace_ref]
        artifact = self._publish(result, [int(parent["id"])], configuration_refs=refs)
        return {self.artifact_name: artifact, "trace": artifact}


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

    def run(self, inputs):
        self._require_target()
        service = ArtifactService(self.ctx.db_path)
        arc_row = self._resolve_artifact("master_arc", required=True)
        trace_row = self._resolve_artifact("trace_map", required=True)
        arc = service.load_component(arc_row, "master_arc")["data"]
        trace = service.load_component(trace_row, "fiber_trace_map")["data"]

        mask = None
        masks = []
        for kind, component in (("master_ldls", "flat_response_mask"), ("master_dark", "dark_pixel_mask")):
            row = self._resolve_artifact(kind, required=False)
            if row is not None:
                try:
                    masks.append(np.asarray(service.load_component(row, component)["data"], dtype=bool))
                except KeyError:
                    pass
        if masks:
            mask = np.logical_or.reduce(masks)
            if mask.shape == arc.shape:
                from ..algorithms.utils.masks import interpolate_masked_detector_pixels

                arc = interpolate_masked_detector_pixels(np.asarray(arc, dtype=float), mask)

        result = fit_wavelength_solution(
            master_comparison_lamp=arc,
            fiber_trace_map=trace,
            params={**self._params(), "mask_applied": bool(mask is not None)},
        )
        result = ensure_algo_result(result, kind="wave")
        report = self.result_contract().validate(result)
        if not report.ok:
            raise ValueError("WaveTask result contract: " + "; ".join(report.errors))
        artifact = self._publish(result, [int(arc_row["id"]), int(trace_row["id"])])
        return {self.artifact_name: artifact, "wave": artifact}
