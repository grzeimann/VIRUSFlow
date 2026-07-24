from __future__ import annotations

"""Canonical science amplifier and physical-CCD orchestration Tasks."""

from datetime import datetime
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from .base import Task
from ..algorithms.ccd import reduce_amplifier_array
from ..algorithms.physical_ccd import (
    ALGORITHM_VERSION,
    TRANSFORM_VERSION,
    assemble_physical_ccd,
    compact_scattered_light_payload,
    fit_gap_scattered_light,
)
from ..artifacts import ArtifactService, Scope, Validity
from ..artifacts.models import ConfigurationReference
from ..artifacts.requests import ArtifactRequest, LogicalComponent
from ..ontology.artifact_kinds import kind_spec
from ..ontology.scopes import PhysicalScope
from ..persistence.policy import DefaultPersistencePolicy
from ..publication.context import PublicationContext
from ..publication.service import DefaultPublicationService


def _instant(exposure_id: str) -> datetime:
    try:
        return datetime.strptime(str(exposure_id), "%Y%m%dT%H%M%S.%f")
    except ValueError:
        return datetime.strptime(str(exposure_id)[:8], "%Y%m%d")


@dataclass(frozen=True)
class ReducedAmplifierState:
    zipcode: object
    exposure_id: str
    image: np.ndarray
    variance: np.ndarray
    pixel_mask: np.ndarray
    header: dict
    parent_ids: tuple[int, ...]
    summaries: dict


@dataclass(frozen=True)
class PhysicalCCDState:
    assembly: object
    scatter: object
    model_artifact: object


class _SciencePublisher(Task):
    def _publish(self, request: ArtifactRequest, *, algorithm_name: str, algorithm_version: str):
        service = ArtifactService(self.ctx.db_path)
        publisher = DefaultPublicationService(
            svc=service, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir
        )
        context = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name=algorithm_name,
            algorithm_version=algorithm_version,
            parameters=dict(self.params or {}),
            parent_ids=[],
            timings={},
        )
        return publisher.publish([request], context)[0]

    def _qa(self, artifact, facts: dict, *, status: str = "pass", usability: str = "usable") -> None:
        ArtifactService(self.ctx.db_path).adapter.set_qa_bundle(
            int(artifact.id),
            facts={name: {"value": value, "units": None, "component": None} for name, value in facts.items()},
            status=status,
            usability=usability,
            policy_version="science-baseline-1",
            rules=[],
        )


class ReducedScienceAmplifierTask(_SciencePublisher):
    name = "reduced_science_amplifier"
    version = "v1"

    def run(self, inputs):
        from ..io import RawFrameLoader
        from ..registry import database as db

        zipcode = self.target.zipcode
        exposure_id = self.target.exposure_id
        rows = [
            (row_id, raw) for row_id, raw in db.list_raw_files_scoped(
                frame_type="sci", start_date=exposure_id[:8], end_date=exposure_id[:8],
                zipcode=zipcode, db_path=self.ctx.db_path,
            ) if raw.exposure_id == exposure_id
        ]
        if len(rows) != 1:
            raise RuntimeError(f"Expected one science amplifier input for {exposure_id}/{zipcode.key()}, found {len(rows)}")
        row_id, raw = rows[0]
        loader = self.ctx.config.get("raw_frame_loader") if isinstance(self.ctx.config, dict) else None
        frame = (loader or RawFrameLoader()).load_ref(raw)
        reduced = reduce_amplifier_array(frame.data, frame.header)
        image = np.asarray(reduced.get_array("oriented_detector_image"), dtype=np.float32)
        variance = np.asarray(reduced.get_array("detector_variance"), dtype=np.float32)
        mask = (~np.isfinite(image) | ~np.isfinite(variance)).astype(np.uint8)
        service = ArtifactService(self.ctx.db_path)
        at = _instant(exposure_id)
        calibration_parents = []
        calibration_policy = "overscan_orientation_gain_only"
        science_exptime = float(frame.header.get("EXPTIME") or 0.0)
        dark_exptime = None
        dark_scale = 0.0
        if bool(self.params.get("apply_calibrations", True)):
            cal_scope = Scope(zipcode=zipcode)

            def calibration(kind):
                row = inputs.get(kind) if isinstance(inputs, dict) else None
                if row is None:
                    row = service.select_best(
                        kind=kind, scope=cal_scope, at_time=at, policy="latest_valid"
                    )
                if row is None:
                    row = service.select_best(
                        kind=kind, scope=cal_scope, at_time=at, policy="nearest"
                    )
                return row

            def artifact_id(row):
                return int(row.id) if hasattr(row, "id") else int(row["id"])

            bias_row = calibration("master_bias")
            dark_row = calibration("master_dark")
            ldls_row = calibration("master_ldls")
            if bias_row is None or dark_row is None:
                raise RuntimeError(f"Missing Bias/Dark calibration for {exposure_id}/{zipcode.key()}")
            bias_id, dark_id = artifact_id(bias_row), artifact_id(dark_row)
            bias = np.asarray(service.load_component(bias_id, "master")["data"], dtype=np.float32)
            bias_scatter = np.asarray(
                service.load_component(bias_id, "per_pixel_bias_scatter")["data"], dtype=np.float32
            )
            dark = np.asarray(service.load_component(dark_id, "master_dark")["data"], dtype=np.float32)
            dark_rows = db.list_raw_files_scoped(
                frame_type="drk", start_date=exposure_id[:8], end_date=exposure_id[:8],
                zipcode=zipcode, db_path=self.ctx.db_path,
            )
            dark_exptime = None
            if dark_rows:
                dark_raw = dark_rows[0][1]
                dark_frame = (loader or RawFrameLoader()).load_ref(dark_raw)
                dark_exptime = float(dark_frame.header.get("EXPTIME") or 0.0)
            dark_scale = science_exptime / dark_exptime if science_exptime > 0 and dark_exptime and dark_exptime > 0 else 1.0
            dark_residual = dark - bias
            image = image - bias - np.float32(dark_scale) * dark_residual
            variance = variance + np.square(bias_scatter, dtype=np.float32)
            mask |= np.asarray(service.load_component(dark_id, "dark_pixel_mask")["data"], dtype=np.uint8)
            calibration_parents.extend([bias_id, dark_id])
            if ldls_row is not None:
                ldls_id = artifact_id(ldls_row)
                mask |= np.asarray(
                    service.load_component(ldls_id, "flat_response_mask")["data"], dtype=np.uint8
                )
                calibration_parents.append(ldls_id)
            mask |= (~np.isfinite(image) | ~np.isfinite(variance)).astype(np.uint8)
            calibration_policy = "bias_plus_exptime_scaled_dark_residual-1"
        summaries = {
            "invalid_pixel_fraction": float(mask.mean()),
            "gain": float(reduced.scalars["gain"]),
            "read_noise": float(reduced.scalars["read_noise"]),
            "science_exptime": science_exptime,
            "dark_exptime": float(dark_exptime or 0.0),
            "dark_scale": float(dark_scale),
            "calibration_policy": calibration_policy,
        }
        state = ReducedAmplifierState(
            zipcode=zipcode,
            exposure_id=exposure_id,
            image=np.asarray(image, dtype=np.float32),
            variance=np.asarray(variance, dtype=np.float32),
            pixel_mask=np.asarray(mask, dtype=np.uint8),
            header=dict(frame.header),
            parent_ids=tuple([int(row_id), *calibration_parents]),
            summaries=summaries,
        )
        return {"reduced_science_state": state}


class PhysicalCCDTask(_SciencePublisher):
    name = "physical_ccd"
    version = "v1"

    @staticmethod
    def _components(result, names: Iterable[str], *, units: str, coordinates: str):
        components = {}
        for name in names:
            value = np.asarray(result.get_array(name))
            # Detector-valued surfaces do not require celestial-coordinate
            # float64 precision; make their storage precision explicit rather
            # than relying on a serializer-wide coercion.
            if value.dtype.kind == "f" and value.dtype.itemsize > 4:
                value = value.astype(np.float32)
            unit = (
                "1" if name.endswith("indices") or name == "detector_shape"
                else ("electron" if name in {"model_parameters", "residual_sample_values"} else units)
            )
            components[name] = LogicalComponent(
                name, "array1d" if np.asarray(value).ndim == 1 else "array2d",
                value, unit, coordinates,
            )
        return components

    def run(self, inputs):
        service = ArtifactService(self.ctx.db_path)
        target = self.target
        at = target.at_time or _instant(target.exposure_id)

        def source(zipcode, label):
            state = inputs.get(label) if isinstance(inputs, dict) else None
            if isinstance(state, dict):
                state = state.get("reduced_science_state")
            if isinstance(state, ReducedAmplifierState):
                return state
            return ReducedScienceAmplifierTask(
                self.ctx, target=type("Target", (), {"zipcode": zipcode, "exposure_id": target.exposure_id})(),
                params={"apply_calibrations": True},
            ).run({})["reduced_science_state"]

        def trace(zipcode, label):
            row = inputs.get(label) if isinstance(inputs, dict) else None
            if row is None:
                row = service.select_best(
                    kind="trace_map", scope=Scope(zipcode=zipcode), at_time=at,
                    policy="latest_valid",
                )
            if row is None:
                row = service.select_best(
                    kind="trace_map", scope=Scope(zipcode=zipcode), at_time=at,
                    policy="nearest",
                )
            if row is None:
                raise RuntimeError(f"Missing trace_map Product for {zipcode.key()}")
            return row

        def artifact_id(row):
            return int(row.id) if hasattr(row, "id") else int(row["id"] if isinstance(row, dict) else row)

        lower_state = source(target.lower_zipcode, "lower_state")
        upper_state = source(target.upper_zipcode, "upper_state")
        lower_trace_row = trace(target.lower_zipcode, "lower_trace")
        upper_trace_row = trace(target.upper_zipcode, "upper_trace")
        lower_trace_id, upper_trace_id = artifact_id(lower_trace_row), artifact_id(upper_trace_row)
        assembly = assemble_physical_ccd(
            lower_state.image, upper_state.image,
            side=target.side,
            lower_amp=target.lower_zipcode.amp,
            upper_amp=target.upper_zipcode.amp,
            lower_variance=lower_state.variance,
            upper_variance=upper_state.variance,
            lower_mask=lower_state.pixel_mask,
            upper_mask=upper_state.pixel_mask,
        )
        scatter = fit_gap_scattered_light(
            assembly,
            service.load_component(lower_trace_id, "fiber_trace_map")["data"],
            service.load_component(upper_trace_id, "fiber_trace_map")["data"],
            **{key: value for key, value in self.params.items() if key in {
                "core_exclusion_pixels", "minimum_group_gap_pixels", "holdout_chunk_period", "sigma_clip", "iterations"
            }},
        )
        coordinate = kind_spec("ccd_scattered_light_model").coordinates.value
        parents = [
            *lower_state.parent_ids, *upper_state.parent_ids,
            lower_trace_id, upper_trace_id,
        ]
        metadata = {
            **dict(assembly.meta or {}), **dict(scatter.meta or {}),
            "exposure_id": target.exposure_id,
            "specid": target.specid,
            "participating_amplifiers": [target.lower_zipcode.key(), target.upper_zipcode.key()],
        }
        refs = [ConfigurationReference("ccd_transform", TRANSFORM_VERSION, f"{target.specid}:{target.side}", "verified")]
        scope = Scope(
            zipcode=target.lower_zipcode, exposure_id=target.exposure_id,
            physical_scope=PhysicalScope.PHYSICAL_CCD,
        )
        compact = compact_scattered_light_payload(scatter)
        model_names = kind_spec("ccd_scattered_light_model").required_components
        compact_result = type(scatter)(
            kind=scatter.kind, version=scatter.version, arrays=compact,
            meta=scatter.meta, scalars=scatter.scalars,
        )
        model_request = ArtifactRequest(
            kind="ccd_scattered_light_model", role="reduction",
            components=self._components(compact_result, model_names, units="electron", coordinates=coordinate),
            summaries=dict(scatter.scalars or {}), metadata=metadata, scope=scope, parents=parents,
            validity=Validity(at, at, "exposure_identity"), configuration_refs=refs,
            assumptions=["The summed scattered-light field is smooth across the physical CCD."],
        )
        model_artifact = self._publish(model_request, algorithm_name="virusflow.algorithms.physical_ccd.fit_gap_scattered_light", algorithm_version=ALGORITHM_VERSION)
        status = "pass" if np.isfinite(scatter.scalars.get("holdout_residual_robust_sigma", np.nan)) else "fail"
        usability = "usable" if status == "pass" else "unusable"
        self._qa(model_artifact, dict(scatter.scalars or {}), status=status, usability=usability)
        return {
            "ccd_scattered_light_model": model_artifact,
            "physical_ccd_state": PhysicalCCDState(assembly, scatter, model_artifact),
        }
