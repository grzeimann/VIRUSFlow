from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import numpy as np
import pytest

from virusflow.algorithms.sky import (
    LatentSkyModel,
    derive_sky_oversampling_factor,
    sky_sampling_convergence,
    wavelength_bin_edges,
)
from virusflow.algorithms.physical_ccd import (
    ScatteredLightModel,
    compact_scattered_light_payload,
    fit_gap_scattered_light,
)
from virusflow.artifacts import ArtifactService, Scope
from virusflow.artifacts.models import Artifact, Provenance, StorageRef
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.artifacts.sparse_mask import decode_mask, encode_mask
from virusflow.analytics.materialization import AnalysisStudyService, RetentionPolicy
from virusflow.executors.planning_executor import PlanningExecutor
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.storage.scratch import ScratchSpace


def _publisher(tmp_path: Path):
    service = ArtifactService(str(tmp_path / "registry.sqlite3"))
    publisher = DefaultPublicationService(
        svc=service, policy=DefaultPersistencePolicy(), base_dir=str(tmp_path / "artifacts")
    )
    context = PublicationContext("test", "1", "test", "1", {}, [], {})
    return service, publisher, context


def _register_legacy_dense_scatter(service: ArtifactService, root: Path) -> tuple[int, Path]:
    payload = root / "legacy-dense-scatter.bin"
    payload.write_bytes(b"legacy dense scatter payload")
    artifact = Artifact(
        id=None,
        kind="ccd_scattered_light_model",
        role="calibration",
        payload_type="array",
        storage_format="fits",
        storage=StorageRef(str(payload), "fits"),
        scope=Scope(zipcode=None),
        metadata={"payload_bytes": payload.stat().st_size},
        provenance=Provenance("legacy", {}, []),
        payload_bytes=payload.stat().st_size,
    )
    return service.adapter.register(artifact, components=[]), payload


def test_sparse_mask_all_encodings_roundtrip_bit_flags_and_service_is_transparent(tmp_path: Path):
    mask = np.zeros((17, 19), dtype=np.uint16)
    mask[1, 2] = 1
    mask[7, 3:9] = 4
    mask[15, 18] = 256
    for encoding in ("sparse", "rle", "dense"):
        encoded = encode_mask(mask, allowed=(encoding,))
        np.testing.assert_array_equal(decode_mask(encoded), mask)

    binary = (mask != 0).astype(np.uint8)
    np.testing.assert_array_equal(
        decode_mask(encode_mask(binary, allowed=("packed",))), binary
    )
    service, publisher, context = _publisher(tmp_path)
    artifact = publisher.publish([ArtifactRequest(
        kind="sky_fiber_mask",
        scope=Scope(zipcode=None),
        components={
            "mask": LogicalComponent("mask", "mask", mask),
            "broadband_flux": LogicalComponent("broadband_flux", "array1d", np.ones(17)),
            "fiber_identity": LogicalComponent("fiber_identity", "array2d", np.arange(34).reshape(17, 2)),
        },
    )], context)[0]
    np.testing.assert_array_equal(service.load_component(artifact.id, "mask")["data"], mask)
    component = next(item for item in service.describe(artifact.id)["components"] if item["name"] == "mask")
    assert component["metadata"]["mask_encoding"] in {"sparse", "rle", "packed", "dense"}


def test_scratch_only_publication_is_rejected_and_worker_scratch_is_cleaned(tmp_path: Path):
    _, publisher, context = _publisher(tmp_path)
    from virusflow.artifacts.migration import SUPERSEDED_STAGE_8_10_KINDS

    for kind in SUPERSEDED_STAGE_8_10_KINDS:
        with pytest.raises(ValueError, match="scratch-only"):
            publisher.publish([ArtifactRequest(
                kind=kind,
                components={"data": LogicalComponent("data", "array2d", np.ones((3, 3)))},
            )], context)
    with pytest.raises(ValueError, match="missing required components"):
        publisher.publish([ArtifactRequest(
            kind="ccd_scattered_light_model",
            components={"model": LogicalComponent("model", "array2d", np.ones((3, 3)))},
        )], context)
    with ScratchSpace(tmp_path, run_id="run", worker_id="worker-1") as scratch:
        path = scratch.child("detector")
        (path / "marker").write_text("temporary")
        root = scratch.path
    assert not root.exists()


def test_compact_scatter_model_reconstructs_dense_surface_with_small_payload():
    from virusflow.algorithms.physical_ccd import assemble_physical_ccd

    nx = 24
    yy, xx = np.indices((2064, nx), dtype=float)
    truth = 7 + 0.02 * xx + 0.003 * yy + 1e-6 * yy**2
    trace = np.asarray([[100.0] * nx, [108.0] * nx, [500.0] * nx, [508.0] * nx])
    assembly = assemble_physical_ccd(
        truth[:1032], truth[1032:][::-1], side="left", lower_amp="LL", upper_amp="LU",
        lower_variance=np.ones((1032, nx)), upper_variance=np.ones((1032, nx)),
    )
    result = fit_gap_scattered_light(assembly, trace, trace)
    compact = compact_scattered_light_payload(result)
    reconstructed = ScatteredLightModel(
        compact["model_parameters"], tuple(compact["detector_shape"])
    ).evaluate()
    np.testing.assert_allclose(reconstructed, result.get_array("model"), atol=2e-5)
    assert sum(value.nbytes for value in compact.values()) < result.get_array("model").nbytes


def test_latent_sky_integrates_true_fiber_bins_and_sampling_is_lsf_derived():
    grid = np.linspace(4995, 5005, 4001, dtype=np.float32)
    density = 2.0 + 50.0 * np.exp(-0.5 * ((grid - 5000.0) / 0.08) ** 2)
    model = LatentSkyModel(grid, density, sampling_target=6, oversampling_factor=3)
    centers = np.asarray([
        np.linspace(4996, 5004, 17),
        np.linspace(4996.11, 5004.11, 17),
    ])
    edges = wavelength_bin_edges(centers)
    prediction = model.evaluate(edges)
    assert prediction.shape == centers.shape
    expected_total = np.trapezoid(density, grid)
    broad = model.evaluate(np.asarray([grid[0], grid[-1]]))
    assert broad == pytest.approx(expected_total, rel=2e-4)
    assert not np.array_equal(prediction[0], prediction[1])
    factor, native = derive_sky_oversampling_factor(2.0, 1.0, target_samples_per_fwhm=6)
    assert (factor, native) == (3, 2.0)


def test_sky_convergence_compares_native_predictions_without_direct_fiber_interpolation():
    wave = np.vstack((np.linspace(3500, 3510, 16), np.linspace(3500.2, 3510.2, 16)))
    spectrum = np.vstack((np.ones(16), np.ones(16) * 1.1))
    result = sky_sampling_convergence(
        wave, spectrum, [1, 1], minimum_lsf_fwhm=1.4,
        candidate_samples_per_fwhm=(4, 6, 8),
    )
    assert [row["target_samples_per_fwhm"] for row in result["candidates"]] == [4, 6, 8]
    assert all(prediction.shape == wave.shape for prediction in result["predictions"])


def test_analysis_materialization_is_budgeted_and_completion_does_not_touch_canonical(tmp_path: Path):
    service = AnalysisStudyService(str(tmp_path / "registry.sqlite3"), str(tmp_path / "analysis"))
    study = service.create(
        scientific_question="Does a selected residual support a candidate model?",
        selection={"exposure_id": "e1"}, selected_observations=("o1",),
        model_versions={"scatter": "1"}, calibration_versions={"bias": "1"},
        software_version="test", algorithm_versions={"reducer": "1"},
        intermediate_kinds=("scatter_residual",),
        retention_policy=RetentionPolicy.UNTIL_STUDY_COMPLETION,
        expected_bytes=1_000_000,
    )
    value, artifact = service.materialize(
        study.study_id, intermediate_kind="scatter_residual",
        producer=lambda: np.ones((8, 8), dtype=np.float32), parent_ids=(), selected=True,
    )
    assert artifact is not None and value.shape == (8, 8)
    path = Path(service.svc.describe(artifact.id)["path"])
    assert path.exists()
    service.complete(study.study_id, summary={"decision": "retain candidate"})
    assert not path.exists()
    assert service.get(study.study_id).state == "complete"


def test_executor_defaults_to_four_passes_dependency_outputs_and_blocks_duplicates():
    events = []

    class Task:
        def __init__(self, value):
            self.value = value

        def run(self, inputs):
            events.append((self.value, sorted(inputs)))
            return self.value

    executor = PlanningExecutor(debug=False)
    assert executor.max_workers == 4
    executor.add_task("a", Task("A"))
    executor.add_task("b", Task("B"), depends_on=["a"])
    with pytest.raises(ValueError, match="already registered"):
        executor.add_task("a", Task("duplicate"))
    outputs = executor.run()
    assert outputs == {"a": "A", "b": "B"}
    assert events == [("A", []), ("B", ["a"])]


def test_dtype_units_scaled_flux_roundtrip_and_atomic_publication(tmp_path: Path):
    from virusflow.artifacts.storage_conventions import (
        FLUX_BUNIT,
        scaled_flux_component,
        scaled_variance_component,
    )

    service, publisher, context = _publisher(tmp_path)
    flux = np.full((2, 3), 2.0e-17, dtype=np.float64)
    variance = np.full((2, 3), 4.0e-34, dtype=np.float64)
    artifact = publisher.publish([ArtifactRequest(
        kind="calibrated_fiber_observation",
        scope=Scope(zipcode=None, observation_id="obs"),
        components={
            "flux": scaled_flux_component("flux", flux, "fiber_by_dispersion_pixel"),
            "variance": scaled_variance_component("variance", variance, "fiber_by_dispersion_pixel"),
            "mask": LogicalComponent("mask", "mask", np.zeros((2, 3), dtype=np.uint16)),
            "wavelength": LogicalComponent("wavelength", "array2d", np.ones((2, 3), dtype=np.float64)),
            "fiber_identity": LogicalComponent("fiber_identity", "array2d", np.arange(4).reshape(2, 2)),
            "sky_coordinates": LogicalComponent("sky_coordinates", "array2d", np.ones((2, 2)), "deg", "icrs"),
            "focal_plane_coordinates": LogicalComponent("focal_plane_coordinates", "array2d", np.ones((2, 2))),
            "exposure_index": LogicalComponent("exposure_index", "array1d", np.zeros(2, dtype=np.uint8)),
        },
    )], context)[0]
    loaded_flux = service.load_component(artifact.id, "flux")
    loaded_variance = service.load_component(artifact.id, "variance")
    np.testing.assert_allclose(loaded_flux["data"], flux, rtol=2e-7)
    np.testing.assert_allclose(loaded_variance["data"], variance, rtol=2e-7)
    np.testing.assert_allclose(loaded_flux["stored_data"], 2.0)
    np.testing.assert_allclose(loaded_variance["stored_data"], 4.0)
    assert loaded_flux["header"]["BUNIT"] == FLUX_BUNIT
    components = {row["name"]: row for row in service.describe(artifact.id)["components"]}
    assert components["sky_coordinates"]["dtype"] == "float64"
    assert components["wavelength"]["dtype"] == "float32"
    assert artifact.payload_bytes == service.storage_summary()["total_bytes"]
    assert not list((tmp_path / "artifacts").rglob("*.tmp"))


def test_concurrent_publication_is_idempotent_and_leaves_no_registry_shells(tmp_path: Path):
    service, publisher, context = _publisher(tmp_path)
    request = ArtifactRequest(
        kind="baseline_relative_response",
        components={
            "wavelength": LogicalComponent("wavelength", "array1d", np.linspace(3500, 5500, 32)),
            "response": LogicalComponent("response", "array1d", np.ones(32)),
            "uncertainty": LogicalComponent("uncertainty", "array1d", np.full(32, 0.01)),
            "mask": LogicalComponent("mask", "mask", np.zeros(32, dtype=np.uint8)),
        },
    )
    barrier = Barrier(2)

    def publish():
        barrier.wait(timeout=3)
        return publisher.publish([request], context)[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: publish(), range(2)))
    assert first.id == second.id
    assert first.revision == second.revision
    assert len(service.adapter.list_all()) == 1
    assert not list((tmp_path / "artifacts").rglob("*.tmp"))


def test_worker_precedence_parallel_failures_nested_budget_and_scratch(tmp_path: Path):
    from virusflow.cli.virusflow import resolve_nworkers
    from virusflow.executors.execution_context import enter_worker, leave_worker
    from virusflow.planning.config import load_planning_config_from_dict

    assert resolve_nworkers() == 4
    assert resolve_nworkers(configured_value=3) == 3
    assert resolve_nworkers(cli_value=2, configured_value=3) == 2
    assert resolve_nworkers(cli_value=8, configured_value=3, serial=True) == 1
    assert load_planning_config_from_dict({"execution": {"nworkers": 6}}).nworkers == 6

    rendezvous = Barrier(2)

    class ConcurrentTask:
        def run(self, inputs):
            rendezvous.wait(timeout=3)
            return len(inputs)

    parallel = PlanningExecutor(max_workers=2)
    parallel.add_task("one", ConcurrentTask())
    parallel.add_task("two", ConcurrentTask())
    assert parallel.run() == {"one": 0, "two": 0}

    events = []

    class Fails:
        def run(self, inputs):
            raise RuntimeError("intentional")

    class MustNotRun:
        def run(self, inputs):
            events.append("ran")

    failed = PlanningExecutor(max_workers=2, raise_on_failure=False)
    failed.add_task("prerequisite", Fails())
    failed.add_task("dependent", MustNotRun(), depends_on=["prerequisite"])
    assert failed.run() == {}
    assert not events
    assert failed.execution_stats["failed"] == 1
    assert failed.execution_stats["blocked"] == 1

    token = enter_worker("outer")
    try:
        assert PlanningExecutor(max_workers=9).max_workers == 1
    finally:
        leave_worker(token)

    first = ScratchSpace(tmp_path, run_id="same", worker_id="one")
    second = ScratchSpace(tmp_path, run_id="same", worker_id="two")
    assert first.path != second.path
    first.cleanup()
    second.cleanup()
    retained = None
    retained_space = None
    with pytest.raises(RuntimeError, match="failure"):
        with ScratchSpace(tmp_path, run_id="failed", worker_id="one", preserve_failed=True) as scratch:
            retained_space = scratch
            retained = scratch.path
            (scratch.child("state") / "diagnostic.txt").write_text("kept")
            raise RuntimeError("failure")
    assert retained is not None and retained.exists()
    assert retained_space is not None
    retained_space.cleanup()


def test_legacy_cleanup_separates_deactivation_from_payload_deletion(tmp_path: Path):
    from virusflow.storage.cleanup import cleanup_legacy

    service, publisher, context = _publisher(tmp_path)
    dense_id, payload = _register_legacy_dense_scatter(service, tmp_path)
    compact_components = {
        "model_parameters": LogicalComponent("model_parameters", "array1d", np.arange(6)),
        "detector_shape": LogicalComponent("detector_shape", "array1d", np.asarray([8, 8])),
        "gap_sample_indices": LogicalComponent("gap_sample_indices", "array1d", np.asarray([1])),
        "fit_sample_indices": LogicalComponent("fit_sample_indices", "array1d", np.asarray([2])),
        "holdout_sample_indices": LogicalComponent("holdout_sample_indices", "array1d", np.asarray([3])),
        "residual_sample_indices": LogicalComponent("residual_sample_indices", "array1d", np.asarray([2])),
        "residual_sample_values": LogicalComponent("residual_sample_values", "array1d", np.asarray([0.1])),
    }
    compact = publisher.publish([ArtifactRequest(
        kind="ccd_scattered_light_model", components=compact_components,
    )], context)[0]
    preview = cleanup_legacy(service.db_path)
    assert preview.dry_run and preview.candidates == 1
    assert service.adapter.get_row(dense_id)["state"] == "active"
    assert payload.exists()

    result = cleanup_legacy(service.db_path, deactivate=True)
    assert result.affected == 1 and result.removed_bytes == 0
    assert service.adapter.get_row(dense_id)["state"] == "obsolete"
    assert payload.exists()
    assert service.adapter.get_row(compact.id)["state"] == "active"
    assert service.select_best(kind="ccd_scattered_light_model", scope=Scope(zipcode=None))['id'] == compact.id

    delete_root = tmp_path / "delete"
    delete_root.mkdir()
    service2, _, _ = _publisher(delete_root)
    dense2_id, payload2 = _register_legacy_dense_scatter(service2, delete_root)
    with pytest.raises(ValueError, match="deactivate"):
        cleanup_legacy(service2.db_path, delete_payloads=True, validation_succeeded=True)
    with pytest.raises(ValueError, match="validation-succeeded"):
        cleanup_legacy(service2.db_path, deactivate=True, delete_payloads=True)
    deleted = cleanup_legacy(
        service2.db_path,
        deactivate=True,
        delete_payloads=True,
        validation_succeeded=True,
    )
    assert deleted.affected == 1 and deleted.removed_bytes > 0
    assert service2.adapter.get_row(dense2_id)["state"] == "obsolete"
    assert not payload2.exists()


def test_analysis_policies_budget_candidates_and_validation_provenance(tmp_path: Path):
    service = AnalysisStudyService(str(tmp_path / "registry.sqlite3"), str(tmp_path / "analysis"))
    none_study = service.create(
        scientific_question="Do not retain this diagnostic", selection={}, selected_observations=(),
        model_versions={}, calibration_versions={}, software_version="test", algorithm_versions={},
        intermediate_kinds=("diagnostic",), retention_policy=RetentionPolicy.NONE,
        expected_bytes=0,
    )
    _, artifact = service.materialize(
        none_study.study_id, intermediate_kind="diagnostic",
        producer=lambda: np.ones(4), parent_ids=(), selected=True,
    )
    assert artifact is None

    tiny = service.create(
        scientific_question="Test the enforced budget", selection={}, selected_observations=(),
        model_versions={}, calibration_versions={}, software_version="test", algorithm_versions={},
        intermediate_kinds=("diagnostic",), retention_policy=RetentionPolicy.ALL,
        expected_bytes=1,
    )
    with pytest.raises(RuntimeError, match="budget exceeded"):
        service.materialize(
            tiny.study_id, intermediate_kind="diagnostic",
            producer=lambda: np.ones(4, dtype=np.float32), parent_ids=(),
        )

    candidate_study = service.create(
        scientific_question="Does a compact candidate improve residuals?", selection={"exposure": "e1"},
        selected_observations=("o1",), model_versions={"scatter": "accepted"},
        calibration_versions={"bias": "1"}, software_version="test",
        algorithm_versions={"scatter": "2"}, intermediate_kinds=(),
        retention_policy=RetentionPolicy.PERMANENT, expected_bytes=1_000_000,
    )
    candidate = service.publish_candidate(
        candidate_study.study_id,
        candidate_kind="candidate_scattered_light_model",
        accepted_model_id=42,
        parent_ids=(43,),
        components={
            "model_parameters": LogicalComponent("model_parameters", "array1d", np.arange(6)),
            "detector_shape": LogicalComponent("detector_shape", "array1d", np.asarray([2064, 1032])),
        },
        validation_metrics={"rms": 0.8}, comparison={"accepted_rms": 1.0},
    )
    description = service.svc.describe(candidate.id)
    assert description["summary"]["study_id"] == candidate_study.study_id
    assert {row["parent_id"] for row in description["relations"]} == {42, 43}
    service.record_validation(
        candidate_study.study_id, candidate_artifact_id=candidate.id,
        metrics={"rms": 0.8}, comparison={"accepted_rms": 1.0}, decision="validate",
    )
    assert service.get(candidate_study.study_id).summary["validations"][0]["decision"] == "validate"
