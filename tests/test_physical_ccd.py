from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from virusflow.algorithms.physical_ccd import (
    assemble_physical_ccd,
    fit_gap_scattered_light,
    inverse_upper_y,
    upper_y,
)
from virusflow.artifacts import ArtifactService, Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.core.identity import ZipCode
from virusflow.ontology.scopes import PhysicalScope
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.planning.targets import PhysicalCCDTarget
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.tasks.base import TaskContext
from virusflow.tasks.science import PhysicalCCDTask
from virusflow.tasks.science import ReducedAmplifierState


def _pair(side="left", nx=24):
    lower = np.broadcast_to(np.arange(1032, dtype=float)[:, None], (1032, nx)).copy()
    upper = 10_000.0 + lower
    lower_amp, upper_amp = (("LL", "LU") if side == "left" else ("RU", "RL"))
    return assemble_physical_ccd(
        lower, upper, side=side, lower_amp=lower_amp, upper_amp=upper_amp,
        lower_variance=np.ones_like(lower), upper_variance=np.full_like(upper, 2.0),
    )


def test_upper_transform_endpoints_roundtrip_and_complete_row_coverage():
    rows = np.arange(2064)
    np.testing.assert_array_equal(upper_y(np.array([0, 2063])), [2063, 0])
    np.testing.assert_array_equal(inverse_upper_y(upper_y(rows)), rows)
    mapped = np.concatenate((np.arange(1032), upper_y(np.arange(1032))))
    np.testing.assert_array_equal(np.sort(mapped), rows)


@pytest.mark.parametrize("side,amps", [("left", ("LL", "LU")), ("right", ("RU", "RL"))])
def test_physical_ccd_assembly_has_explicit_seam_gap_and_source_coordinates(side, amps):
    result = _pair(side)
    image = result.get_array("image")
    assert image.shape == (2064, 24)
    assert image[1031, 0] == 1031
    assert image[1032, 0] == 11031
    assert image[2063, 0] == 10000
    assert result.get_array("seam_mask").sum() == 48
    assert result.get_array("inter_amplifier_gap_mask").sum() == 0
    assert result.meta["upper_transform"] == "upper_y = 2063 - y"
    source_y = result.get_array("source_y_coordinate")[:, 0]
    np.testing.assert_array_equal(source_y[:1032], np.arange(1032))
    np.testing.assert_array_equal(source_y[1032:], np.arange(1031, -1, -1))


def test_missing_or_mismatched_pairs_fail_explicitly():
    image = np.zeros((1032, 8))
    with pytest.raises(ValueError, match="requires"):
        assemble_physical_ccd(image, image, side="left", lower_amp="RU", upper_amp="RL")
    with pytest.raises(ValueError, match="shape-matched"):
        assemble_physical_ccd(image, image[:, :-1], side="left", lower_amp="LL", upper_amp="LU")


def test_gap_scatter_fit_retains_masks_holdout_and_residual_evidence():
    nx = 40
    yy, xx = np.indices((2064, nx), dtype=float)
    truth = 15.0 + 0.004 * yy + 0.03 * xx + 0.000001 * yy * yy
    lower, upper_physical = truth[:1032].copy(), truth[1032:].copy()
    upper = upper_physical[::-1]
    traces = np.array([[100.0] * nx, [108.0] * nx, [420.0] * nx, [428.0] * nx, [800.0] * nx, [808.0] * nx])
    physical_traces = np.vstack((traces, 2063.0 - traces))
    image = truth.copy()
    for trace in physical_traces:
        for x in range(nx):
            y = int(round(trace[x]))
            image[max(0, y - 2):min(2064, y + 3), x] += 500.0
    assembly = assemble_physical_ccd(
        image[:1032], image[1032:][::-1], side="left", lower_amp="LL", upper_amp="LU",
        lower_variance=np.ones((1032, nx)), upper_variance=np.ones((1032, nx)),
    )
    result = fit_gap_scattered_light(assembly, traces, traces)
    assert result.scalars["fit_sample_count"] > 100
    assert result.scalars["holdout_sample_count"] > 0
    assert result.scalars["holdout_residual_robust_sigma"] < 1e-3
    assert set((result.arrays or {})) >= {
        "model", "gap_sample_mask", "fit_sample_mask", "holdout_sample_mask",
        "fit_residual", "model_parameters", "scatter_subtracted_image",
    }
    np.testing.assert_allclose(result.get_array("model"), truth, atol=1e-3)


def _publish(service, root: Path, request: ArtifactRequest):
    publication = DefaultPublicationService(
        svc=service, policy=DefaultPersistencePolicy(), base_dir=str(root)
    )
    context = PublicationContext(
        task_name="fixture", task_version="1", algorithm_name="fixture",
        algorithm_version="1", parameters={}, parent_ids=[], timings={},
    )
    return publication.publish([request], context)[0]


def test_physical_ccd_task_publishes_compact_model_and_keeps_evaluation_in_memory(tmp_path: Path):
    db_path = tmp_path / "registry.sqlite3"
    service = ArtifactService(str(db_path))
    exposure_id = "20260609T031649.6"
    at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
    lower = ZipCode("060", "003", "206", "LL", "S/N 0039")
    upper = ZipCode("060", "003", "206", "LU", "S/N 0039")
    trace = np.array([[100.0] * 24, [108.0] * 24, [420.0] * 24, [428.0] * 24, [800.0] * 24, [808.0] * 24])
    source_ids = []
    states = []
    trace_ids = []
    for index, zipcode in enumerate((lower, upper)):
        image = np.full((1032, 24), 10.0 + index)
        scope = Scope(zipcode=zipcode, exposure_id=exposure_id, physical_scope=PhysicalScope.AMPLIFIER)
        source_ids.append(10_000 + index)
        states.append(ReducedAmplifierState(
            zipcode, exposure_id, image.astype(np.float32), np.ones_like(image, dtype=np.float32),
            np.zeros_like(image, dtype=np.uint8), {}, (source_ids[-1],), {},
        ))
        trace_ids.append(_publish(service, tmp_path, ArtifactRequest(
            kind="trace_map", scope=Scope(zipcode=zipcode),
            validity=Validity(at + timedelta(hours=7), at + timedelta(hours=8), "fixture"),
            components={
                "fiber_trace_map": LogicalComponent(
                    "fiber_trace_map", "array2d", trace, "pixel", "fiber_by_dispersion_pixel"
                ),
                "trace_sample_columns": LogicalComponent(
                    "trace_sample_columns", "array1d", np.asarray([0.0, 23.0])
                ),
                "sampled_trace_positions": LogicalComponent(
                    "sampled_trace_positions", "array2d", trace[:, [0, -1]]
                ),
                "per_fiber_trace_residual_rms": LogicalComponent(
                    "per_fiber_trace_residual_rms", "array1d", np.zeros(trace.shape[0])
                ),
            },
        )).id)

    target = PhysicalCCDTarget(exposure_id, "206", "left", lower, upper, at)
    result = PhysicalCCDTask(TaskContext(str(db_path), str(tmp_path / "artifacts")), target=target).run(
        {
            "lower_state": states[0], "upper_state": states[1],
            "lower_trace": trace_ids[0], "upper_trace": trace_ids[1],
        }
    )
    assert set(result) == {"ccd_scattered_light_model", "physical_ccd_state"}
    model = result["ccd_scattered_light_model"]
    model_description = service.describe(model.id)
    assert {c["name"] for c in model_description["components"]} == {
        "model_parameters", "detector_shape", "gap_sample_indices", "fit_sample_indices",
        "holdout_sample_indices", "residual_sample_indices", "residual_sample_values",
    }
    assert {r["parent_id"] for r in model_description["relations"]} == set(source_ids + trace_ids)
    assert "scatter_subtracted_image" in result["physical_ccd_state"].scatter.arrays
    for component in service.describe(model.id)["components"]:
        service.load_component(model.id, component["name"], verify_checksum=True)
