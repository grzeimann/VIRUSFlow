from __future__ import annotations

"""Canonical science amplifier and physical-CCD orchestration Tasks."""

from datetime import datetime
from typing import Iterable

import numpy as np

from .base import Task
from ..algorithms.ccd import reduce_amplifier_array
from ..algorithms.physical_ccd import ALGORITHM_VERSION, TRANSFORM_VERSION, assemble_physical_ccd, fit_gap_scattered_light
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
        frame = (loader or RawFrameLoader()).load(raw.path, raw.tar_member)
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
            bias_row = service.select_best(kind="master_bias", scope=cal_scope, at_time=at)
            dark_row = service.select_best(kind="master_dark", scope=cal_scope, at_time=at)
            ldls_row = service.select_best(kind="master_ldls", scope=cal_scope, at_time=at)
            if bias_row is None or dark_row is None:
                raise RuntimeError(f"Missing Bias/Dark calibration for {exposure_id}/{zipcode.key()}")
            bias = np.asarray(service.load_component(bias_row, "master")["data"], dtype=np.float32)
            bias_scatter = np.asarray(
                service.load_component(bias_row, "per_pixel_bias_scatter")["data"], dtype=np.float32
            )
            dark = np.asarray(service.load_component(dark_row, "master_dark")["data"], dtype=np.float32)
            dark_rows = db.list_raw_files_scoped(
                frame_type="drk", start_date=exposure_id[:8], end_date=exposure_id[:8],
                zipcode=zipcode, db_path=self.ctx.db_path,
            )
            dark_exptime = None
            if dark_rows:
                dark_raw = dark_rows[0][1]
                dark_frame = (loader or RawFrameLoader()).load(dark_raw.path, dark_raw.tar_member)
                dark_exptime = float(dark_frame.header.get("EXPTIME") or 0.0)
            dark_scale = science_exptime / dark_exptime if science_exptime > 0 and dark_exptime and dark_exptime > 0 else 1.0
            dark_residual = dark - bias
            image = image - bias - np.float32(dark_scale) * dark_residual
            variance = variance + np.square(bias_scatter, dtype=np.float32)
            mask |= np.asarray(service.load_component(dark_row, "dark_pixel_mask")["data"], dtype=np.uint8)
            calibration_parents.extend([int(bias_row["id"]), int(dark_row["id"])])
            if ldls_row is not None:
                mask |= np.asarray(service.load_component(ldls_row, "flat_response_mask")["data"], dtype=np.uint8)
                calibration_parents.append(int(ldls_row["id"]))
            mask |= (~np.isfinite(image) | ~np.isfinite(variance)).astype(np.uint8)
            calibration_policy = "bias_plus_exptime_scaled_dark_residual-1"
        spec = kind_spec("reduced_science_image")
        request = ArtifactRequest(
            kind="reduced_science_image", role="reduction",
            components={
                "image": LogicalComponent("image", "array2d", image, spec.units, spec.coordinates.value),
                "variance": LogicalComponent("variance", "array2d", variance, "electron2", spec.coordinates.value),
                "pixel_mask": LogicalComponent("pixel_mask", "array2d", mask, "1", spec.coordinates.value),
            },
            summaries={
                "invalid_pixel_fraction": float(mask.mean()),
                "gain": float(reduced.scalars["gain"]),
                "read_noise": float(reduced.scalars["read_noise"]),
                "science_exptime": science_exptime,
                "dark_exptime": float(dark_exptime or 0.0),
                "dark_scale": float(dark_scale),
            },
            metadata={
                "exposure_id": exposure_id,
                "zipcode": zipcode.key(),
                "reduction_stage": "overscan_orientation_gain_bias_dark_mask",
                "calibration_policy": calibration_policy,
                "source_header": {key: frame.header.get(key) for key in ("DATE", "OBJECT", "EXPTIME", "PEXPTIME", "OBSID", "DITHER", "QRA", "QDEC", "PARANGLE")},
            },
            scope=Scope(zipcode=zipcode, exposure_id=exposure_id, physical_scope=PhysicalScope.AMPLIFIER),
            parents=[int(row_id), *calibration_parents],
            validity=Validity(at, at, "exposure_identity"),
            configuration_refs=[ConfigurationReference("amplifier_orientation", "legacy-characterized-1", zipcode.key(), "verified")],
            assumptions=[
                "Master Dark contains the Master Bias level; only its residual above Master Bias is exposure-time scaled.",
                "Detector covariance is not propagated in the baseline slice.",
            ],
        )
        artifact = self._publish(
            request, algorithm_name="virusflow.algorithms.ccd.reduce_amplifier_array",
            algorithm_version=str(reduced.version),
        )
        self._qa(artifact, request.summaries)
        return {"reduced_science_image": artifact}


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
            unit = "1" if "mask" in name or name == "source_amplifier_map" else (
                "pixel" if name == "source_y_coordinate" else units
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

        def source(zipcode):
            scope = Scope(zipcode=zipcode, exposure_id=target.exposure_id, physical_scope=PhysicalScope.AMPLIFIER)
            row = service.select_best(kind="reduced_science_image", scope=scope, at_time=at)
            if row is None:
                raise RuntimeError(f"Missing reduced science amplifier Product for {target.exposure_id}/{zipcode.key()}")
            return row

        def trace(zipcode):
            row = service.select_best(kind="trace_map", scope=Scope(zipcode=zipcode), at_time=at)
            if row is None:
                raise RuntimeError(f"Missing trace_map Product for {zipcode.key()}")
            return row

        lower_row, upper_row = source(target.lower_zipcode), source(target.upper_zipcode)
        lower_trace_row, upper_trace_row = trace(target.lower_zipcode), trace(target.upper_zipcode)
        lower_image = service.load_component(lower_row, "image")["data"]
        upper_image = service.load_component(upper_row, "image")["data"]
        assembly = assemble_physical_ccd(
            lower_image, upper_image,
            side=target.side,
            lower_amp=target.lower_zipcode.amp,
            upper_amp=target.upper_zipcode.amp,
            lower_variance=service.load_component(lower_row, "variance")["data"],
            upper_variance=service.load_component(upper_row, "variance")["data"],
            lower_mask=service.load_component(lower_row, "pixel_mask")["data"],
            upper_mask=service.load_component(upper_row, "pixel_mask")["data"],
        )
        scatter = fit_gap_scattered_light(
            assembly,
            service.load_component(lower_trace_row, "fiber_trace_map")["data"],
            service.load_component(upper_trace_row, "fiber_trace_map")["data"],
            **{key: value for key, value in self.params.items() if key in {
                "core_exclusion_pixels", "minimum_group_gap_pixels", "holdout_chunk_period", "sigma_clip", "iterations"
            }},
        )
        coordinate = kind_spec("ccd_scattered_light_model").coordinates.value
        common = {
            name: assembly.get_array(name) for name in (
                "seam_mask", "inter_amplifier_gap_mask", "source_amplifier_map", "source_y_coordinate"
            )
        }
        for name, value in common.items():
            scatter.arrays[name] = value
        parents = [int(lower_row["id"]), int(upper_row["id"]), int(lower_trace_row["id"]), int(upper_trace_row["id"])]
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
        model_names = kind_spec("ccd_scattered_light_model").required_components
        model_request = ArtifactRequest(
            kind="ccd_scattered_light_model", role="reduction",
            components=self._components(scatter, model_names, units="electron", coordinates=coordinate),
            summaries=dict(scatter.scalars or {}), metadata=metadata, scope=scope, parents=parents,
            validity=Validity(at, at, "exposure_identity"), configuration_refs=refs,
            assumptions=["The summed scattered-light field is smooth across the physical CCD."],
        )
        model_artifact = self._publish(model_request, algorithm_name="virusflow.algorithms.physical_ccd.fit_gap_scattered_light", algorithm_version=ALGORITHM_VERSION)
        scatter_image = np.asarray(scatter.get_array("scatter_subtracted_image"), dtype=np.float32)
        subtraction_arrays = dict(common)
        subtraction_arrays.update({
            "image": scatter_image,
            "variance": assembly.get_array("variance"),
            "pixel_mask": assembly.get_array("pixel_mask"),
        })
        subtraction_result = type(assembly)(kind="scatter_subtracted_image", version=ALGORITHM_VERSION, arrays=subtraction_arrays)
        sub_names = kind_spec("scatter_subtracted_image").required_components
        sub_request = ArtifactRequest(
            kind="scatter_subtracted_image", role="reduction",
            components=self._components(subtraction_result, sub_names, units="electron", coordinates=coordinate),
            summaries={"invalid_pixel_fraction": float(np.asarray(assembly.get_array("pixel_mask"), dtype=bool).mean())},
            metadata=metadata, scope=scope,
            parents=[int(lower_row["id"]), int(upper_row["id"]), int(model_artifact.id)],
            validity=Validity(at, at, "exposure_identity"), configuration_refs=refs,
        )
        sub_artifact = self._publish(sub_request, algorithm_name="virusflow.algorithms.physical_ccd.subtract", algorithm_version=ALGORITHM_VERSION)
        status = "pass" if np.isfinite(scatter.scalars.get("holdout_residual_robust_sigma", np.nan)) else "fail"
        usability = "usable" if status == "pass" else "unusable"
        self._qa(model_artifact, dict(scatter.scalars or {}), status=status, usability=usability)
        self._qa(sub_artifact, sub_request.summaries, status=status, usability=usability)
        return {"ccd_scattered_light_model": model_artifact, "scatter_subtracted_image": sub_artifact}
