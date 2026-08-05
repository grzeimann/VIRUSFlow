from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from virusflow.artifacts import (
    ArtifactPayloadEvictedError,
    ArtifactPayloadMissingError,
    ArtifactService,
    Scope,
    Validity,
)
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.core.identity import ZipCode
from virusflow.core.algo_result import AlgoResult
from virusflow.ontology.artifact_kinds import kind_spec
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService, _get_contract
from virusflow.storage.cleanup import cleanup_cache
from virusflow.performance import PerformanceRun
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.calibs import BiasTask


ZIPCODE = ZipCode("013", "043", "412", "LL", "S_N 0021")


def _fixture(tmp_path: Path):
    service = ArtifactService(str(tmp_path / "registry.sqlite3"))
    publisher = DefaultPublicationService(
        svc=service,
        policy=DefaultPersistencePolicy(),
        base_dir=str(tmp_path / "products"),
    )
    return service, publisher


def _component(name: str, model_type: str = "array2d") -> LogicalComponent:
    shape = (4,) if model_type == "array1d" else (3, 4)
    value = np.zeros(shape, dtype=np.uint8) if model_type == "mask" else np.ones(shape)
    return LogicalComponent(name, model_type, value, "1", "none")


def _publish(publisher, kind: str, *, parents=(), zipcode=ZIPCODE):
    contract = _get_contract(kind).spec()
    names = kind_spec(kind).required_components
    contract_types = {
        component.name: component.model_type
        for component in contract.components
    }
    metadata = {
        name: {} if name == "algorithm_metadata" else "test"
        for name in contract.required_metadata
    }
    request = ArtifactRequest(
        kind=kind,
        components={
            name: _component(name, contract_types.get(name) or "array2d")
            for name in names
        },
        scope=Scope(zipcode),
        validity=Validity(datetime(2026, 6, 9), datetime(2026, 6, 10)),
        parents=list(parents),
        metadata=metadata,
    )
    context = PublicationContext(
        "retention-test", "1", "retention-test", "1", {}, list(parents), {}
    )
    return publisher.publish([request], context)[0]


def _pass_qa(service: ArtifactService, *artifacts) -> None:
    for artifact in artifacts:
        service.adapter.set_qa_bundle(
            int(artifact.id),
            facts={},
            status="pass",
            usability="usable",
            policy_version="test",
        )


def _validated_arc_chain(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    arc = _publish(publisher, "master_arc")
    wave = _publish(publisher, "wavelength_map", parents=[arc.id])
    _pass_qa(service, arc, wave)
    return service, arc, wave


def test_permanently_retained_kind_refuses_payload_eviction(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    bias = _publish(publisher, "master_bias")
    _pass_qa(service, bias)
    paths = [Path(component["path"]) for component in service.describe(bias.id)["components"]]

    with pytest.raises(ValueError, match="permanently retained"):
        service.evict_payload(bias.id)

    assert service.payload_status(bias.id) == "present"
    assert all(path.is_file() for path in paths)


def test_eviction_requires_validated_downstream_products(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    arc = _publish(publisher, "master_arc")
    _pass_qa(service, arc)

    with pytest.raises(ValueError, match="wavelength_map"):
        service.evict_payload(arc.id)

    wave = _publish(publisher, "wavelength_map", parents=[arc.id])
    service.adapter.set_qa_bundle(
        wave.id,
        facts={},
        status="fail",
        usability="unusable",
        policy_version="test",
    )
    with pytest.raises(ValueError, match="invalid IDs"):
        service.evict_payload(arc.id)
    assert service.payload_status(arc.id) == "present"


def test_eviction_requires_verified_component_complete_descendant_evidence(
    tmp_path: Path,
):
    service, arc, wave = _validated_arc_chain(tmp_path)
    line_evidence = next(
        component for component in service.describe(wave.id)["components"]
        if component["name"] == "arc_line_evidence"
    )
    Path(line_evidence["path"]).write_bytes(b"not the registered evidence")

    with pytest.raises(ValueError, match="unverifiable payload evidence"):
        service.evict_payload(arc.id)

    assert service.payload_status(arc.id) == "present"


def test_intentional_eviction_retains_registry_evidence_and_is_discoverable(
    tmp_path: Path,
):
    service, arc, _ = _validated_arc_chain(tmp_path)
    before = service.describe(arc.id)
    component_before = before["components"][0]

    removed = service.evict_payload(arc.id)

    after = service.describe(arc.id)
    component_after = after["components"][0]
    assert removed > 0
    assert after["state"] == "active"
    assert after["payload_state"] == "evicted_rebuildable"
    assert component_after["payload_state"] == "evicted_rebuildable"
    assert component_after["path"] == component_before["path"]
    assert component_after["checksum"] == component_before["checksum"]
    assert component_after["shape"] == component_before["shape"]
    assert component_after["eviction"]["removed_files"]
    assert not Path(component_after["path"]).exists()
    sidecar = Path(component_after["path"] + ".json")
    assert sidecar.is_file()
    assert component_after["eviction"]["retained_description_files"] == [
        {"path": str(sidecar), "bytes": sidecar.stat().st_size}
    ]
    assert any(row["id"] == arc.id for row in service.find_artifacts(kind="master_arc"))
    with pytest.raises(ArtifactPayloadEvictedError, match="intentionally evicted"):
        service.load_component(arc.id, "master_arc")
    with pytest.raises(ArtifactPayloadEvictedError, match="intentionally evicted"):
        service.load_payload(service.adapter.get_row(arc.id))
    assert service.evict_payload(arc.id) == 0


def test_ldls_eviction_preserves_evidentiary_mask(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    ldls = _publish(publisher, "master_ldls")
    trace = _publish(publisher, "trace_map", parents=[ldls.id])
    scatter = _publish(
        publisher, "ccd_scattered_light_model", parents=[ldls.id, trace.id]
    )
    spectrum = _publish(
        publisher, "extracted_master_ldls_spectrum",
        parents=[ldls.id, trace.id, scatter.id],
    )
    response = _publish(
        publisher, "within_amp_fiber_normalization", parents=[spectrum.id]
    )
    _pass_qa(service, ldls, trace, scatter, spectrum, response)

    service.evict_payload(ldls.id)

    components = {
        component["name"]: component for component in service.describe(ldls.id)["components"]
    }
    assert components["master_ldls"]["payload_state"] == "evicted_rebuildable"
    assert not Path(components["master_ldls"]["path"]).exists()
    assert components["flat_response_mask"]["payload_state"] == "present"
    assert Path(components["flat_response_mask"]["path"]).is_file()
    assert service.load_component(ldls.id, "flat_response_mask")["data"].shape == (3, 4)


def test_wavelength_map_stage_evicts_arc_and_lamp_ancestors(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    hg = _publish(publisher, "master_hg")
    cd = _publish(publisher, "master_cd")
    arc = _publish(publisher, "master_arc", parents=[hg.id, cd.id])
    wave = _publish(publisher, "wavelength_map", parents=[arc.id])
    _pass_qa(service, hg, cd, arc, wave)

    report = service.evict_payloads_triggered_by(wave.id)

    assert set(report["evicted_artifact_ids"]) == {hg.id, cd.id, arc.id}
    assert report["removed_bytes"] > 0
    assert report["refused"] == []
    assert service.payload_status(hg.id) == "evicted_rebuildable"
    assert service.payload_status(cd.id) == "evicted_rebuildable"
    assert service.payload_status(arc.id) == "evicted_rebuildable"
    assert service.payload_status(wave.id) == "present"


def test_fiber_response_stage_evicts_only_dense_flat_payloads(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    ldls = _publish(publisher, "master_ldls")
    trace = _publish(publisher, "trace_map", parents=[ldls.id])
    ldls_scatter = _publish(
        publisher, "ccd_scattered_light_model", parents=[ldls.id, trace.id]
    )
    ldls_spectrum = _publish(
        publisher, "extracted_master_ldls_spectrum",
        parents=[ldls.id, trace.id, ldls_scatter.id],
    )
    twilight = _publish(publisher, "master_twilight")
    twilight_scatter = _publish(
        publisher, "ccd_scattered_light_model", parents=[twilight.id, trace.id]
    )
    twilight_spectrum = _publish(
        publisher,
        "extracted_master_twilight_spectrum",
        parents=[twilight.id, trace.id, twilight_scatter.id],
    )
    response = _publish(
        publisher,
        "within_amp_fiber_normalization",
        parents=[ldls_spectrum.id, twilight_spectrum.id],
    )
    _pass_qa(
        service,
        ldls,
        trace,
        ldls_scatter,
        ldls_spectrum,
        twilight,
        twilight_scatter,
        twilight_spectrum,
        response,
    )

    report = service.evict_payloads_triggered_by(response.id)

    assert set(report["evicted_artifact_ids"]) == {ldls.id, twilight.id}
    assert report["refused"] == []
    ldls_components = {
        item["name"]: item for item in service.describe(ldls.id)["components"]
    }
    assert ldls_components["master_ldls"]["payload_state"] == "evicted_rebuildable"
    assert ldls_components["flat_response_mask"]["payload_state"] == "present"
    assert service.payload_status(twilight.id) == "evicted_rebuildable"


def test_spectral_mask_stage_evicts_master_sci(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    science = _publish(publisher, "master_sci")
    scatter = _publish(publisher, "ccd_scattered_light_model", parents=[science.id])
    spectrum = _publish(
        publisher, "extracted_master_sci_spectrum", parents=[science.id, scatter.id]
    )
    mask = _publish(
        publisher, "fiber_wavelength_spectral_mask", parents=[spectrum.id]
    )
    _pass_qa(service, science, scatter, spectrum, mask)

    report = service.evict_payloads_triggered_by(mask.id)

    assert report["evicted_artifact_ids"] == [science.id]
    assert report["refused"] == []
    assert service.payload_status(science.id) == "evicted_rebuildable"
    assert service.payload_status(spectrum.id) == "present"
    assert service.payload_status(mask.id) == "present"


def test_calibration_publication_runs_retention_hook_only_after_qa(
    tmp_path: Path, monkeypatch
):
    context = TaskContext(
        db_path=str(tmp_path / "registry.sqlite3"),
        workdir=str(tmp_path / "products"),
    )
    target = SimpleNamespace(
        zipcode=ZIPCODE,
        start_date="20260609",
        end_date="20260610",
        group_id=None,
        group_metadata=None,
    )
    task = BiasTask(context, target=target)
    events = []

    def evaluate(service, artifact, result):
        events.append(("qa", int(artifact.id)))
        return "pass"

    def evict_after(self, artifact_id):
        events.append(("retention", int(artifact_id)))
        return {
            "trigger_artifact_id": int(artifact_id),
            "trigger_kind": "master_bias",
            "evicted_artifact_ids": [],
            "removed_bytes": 0,
            "refused": [],
        }

    task.evaluate_qa = evaluate
    monkeypatch.setattr(ArtifactService, "evict_payloads_triggered_by", evict_after)
    artifact = task._publish(
        AlgoResult(
            kind="bias",
            version="test",
            arrays={
                "master": np.ones((3, 4)),
                "per_pixel_bias_scatter": np.ones((3, 4)),
            },
        ),
        [],
    )

    assert events == [("qa", artifact.id), ("retention", artifact.id)]


def test_explicit_cache_cleanup_backfills_completed_calibration_runs(tmp_path: Path):
    service, arc, _ = _validated_arc_chain(tmp_path)
    bias = _publish(
        DefaultPublicationService(
            svc=service,
            policy=DefaultPersistencePolicy(),
            base_dir=str(tmp_path / "products"),
        ),
        "master_bias",
    )
    _pass_qa(service, bias)

    preview = cleanup_cache(service.db_path)
    assert preview.dry_run
    assert preview.artifact_ids == (arc.id,)
    assert preview.candidate_bytes > 0
    assert service.payload_status(arc.id) == "present"

    result = cleanup_cache(service.db_path, execute=True)
    assert not result.dry_run
    assert result.affected == 1
    assert result.removed_bytes > 0
    assert result.refusals == ()
    assert service.payload_status(arc.id) == "evicted_rebuildable"
    assert service.payload_status(bias.id) == "present"


def test_unexpectedly_missing_payload_is_not_mislabeled_as_evicted(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    bias = _publish(publisher, "master_bias")
    component = next(
        item for item in service.describe(bias.id)["components"] if item["name"] == "master"
    )
    Path(component["path"]).unlink()

    with pytest.raises(ArtifactPayloadMissingError, match="declared present"):
        service.load_component(bias.id, "master")

    description = service.describe(bias.id)
    missing = next(
        item for item in description["components"] if item["name"] == "master"
    )
    assert description["payload_state"] == "missing_error"
    assert missing["payload_state"] == "missing_error"
    assert missing["eviction"]["reason"] == "payload file does not exist"


def test_database_failure_restores_staged_payload(tmp_path: Path, monkeypatch):
    service, arc, _ = _validated_arc_chain(tmp_path)
    path = Path(service.describe(arc.id)["components"][0]["path"])

    def fail_update(*args, **kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(service.adapter, "set_component_payload_states", fail_update)
    with pytest.raises(RuntimeError, match="database write failed"):
        service.evict_payload(arc.id)

    assert path.is_file()


def test_raw_parent_id_collision_is_kept_out_of_artifact_lineage(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    coincident_artifact = _publish(publisher, "master_dark")
    context = TaskContext(
        db_path=service.db_path,
        raw_db_path=str(tmp_path / "raw.sqlite3"),
        workdir=str(tmp_path / "products"),
    )
    target = SimpleNamespace(
        zipcode=ZIPCODE,
        start_date="20260609",
        end_date="20260610",
        group_id=None,
        group_metadata=None,
    )
    task = BiasTask(context, target=target)
    artifact = task._publish(
        AlgoResult(
            kind="bias",
            version="test",
            arrays={
                "master": np.ones((3, 4)),
                "per_pixel_bias_scatter": np.ones((3, 4)),
            },
            scalars={"read_noise": 1.0, "n_inputs": 1},
        ),
        [],
        raw_parent_ids=[coincident_artifact.id],
    )

    assert service.adapter.list_relations(artifact.id) == []
    assert service.adapter.list_raw_relations(artifact.id) == [{
        "raw_catalog": str(tmp_path / "raw.sqlite3"),
        "raw_id": coincident_artifact.id,
        "child_id": artifact.id,
        "relation": "derived_from",
    }]
    description = service.describe(artifact.id)
    assert description["provenance"]["parents"] == []
    assert description["provenance"]["raw_parents"] == [coincident_artifact.id]


def test_triggered_eviction_cannot_cross_amplifier_scope(tmp_path: Path):
    service, publisher = _fixture(tmp_path)
    other_zipcode = ZipCode("014", "044", "413", "LU", "S_N 0022")
    unrelated_hg = _publish(publisher, "master_hg", zipcode=other_zipcode)
    hg = _publish(publisher, "master_hg")
    cd = _publish(publisher, "master_cd")
    arc = _publish(publisher, "master_arc", parents=[hg.id, cd.id])
    wave = _publish(publisher, "wavelength_map", parents=[arc.id])
    _pass_qa(service, unrelated_hg, hg, cd, arc, wave)
    # Reproduce a legacy coincident-ID edge that points into this lineage.
    with db.connect(service.db_path) as connection:
        connection.execute(
            "INSERT INTO artifact_relations(parent_id,child_id,relation) VALUES(?,?,?)",
            (unrelated_hg.id, arc.id, "derived_from"),
        )

    report = service.evict_payloads_triggered_by(wave.id)

    assert report["candidate_count"] == 3
    assert set(report["evicted_artifact_ids"]) == {hg.id, cd.id, arc.id}
    assert service.payload_status(unrelated_hg.id) == "present"


def test_eviction_batches_descendant_payload_validation_and_is_cheap_to_repeat(
    tmp_path: Path, monkeypatch
):
    service, arc, _ = _validated_arc_chain(tmp_path)

    def legacy_n_plus_one(*args, **kwargs):
        raise AssertionError("descendant validation must not call payload_status")

    monkeypatch.setattr(service, "payload_status", legacy_n_plus_one)
    assert service.evict_payload(arc.id) > 0
    monkeypatch.setattr(
        service,
        "_require_eviction_descendants",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("already-evicted payload must return before graph validation")
        ),
    )
    assert service.evict_payload(arc.id) == 0


def test_calibration_retention_has_a_first_class_timing_phase(tmp_path: Path):
    context = TaskContext(
        db_path=str(tmp_path / "registry.sqlite3"),
        workdir=str(tmp_path / "products"),
    )
    target = SimpleNamespace(
        zipcode=ZIPCODE,
        start_date="20260609",
        end_date="20260610",
        group_id=None,
        group_metadata=None,
    )
    task = BiasTask(context, target=target)
    run = PerformanceRun(workers=1)
    run.mark_queued("bias")
    timing, token = run.begin_task("bias", "master_bias", "test", "worker", 1)
    try:
        task._publish(
            AlgoResult(
                kind="bias",
                version="test",
                arrays={
                    "master": np.ones((3, 4)),
                    "per_pixel_bias_scatter": np.ones((3, 4)),
                },
                scalars={"read_noise": 1.0, "n_inputs": 1},
            ),
            [],
        )
    finally:
        run.end_task(timing, token, "succeeded")

    assert timing.phases["payload_retention"].count == 1
    assert timing.counters["retention_candidates"] == 0


def test_artifact_registry_uses_wal_for_concurrent_cleanup(tmp_path: Path):
    service, _ = _fixture(tmp_path)
    with db.connect(service.db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_registry_upgrade_moves_legacy_raw_calibration_edges_to_typed_lineage(
    tmp_path: Path,
):
    service, publisher = _fixture(tmp_path)
    coincident_artifact = _publish(publisher, "master_dark")
    request = ArtifactRequest(
        kind="master_bias",
        components={
            "master": _component("master"),
            "per_pixel_bias_scatter": _component("per_pixel_bias_scatter"),
        },
        scope=Scope(ZIPCODE),
        parents=[coincident_artifact.id],
        metadata={"n_inputs": 1, "algorithm_metadata": {}},
    )
    context = PublicationContext(
        "bias", "v2", "virusflow.algorithms.bias.step_bias", "test",
        {}, [coincident_artifact.id], {},
    )
    legacy = publisher.publish([request], context)[0]
    assert service.adapter.list_relations(legacy.id)

    with db.connect(service.db_path) as connection:
        connection.execute(
            "DELETE FROM registry_migrations "
            "WHERE name='typed_raw_calibration_provenance_v1'"
        )
    db._INITIALIZED_DATABASES.clear()
    upgraded = ArtifactService(service.db_path)

    assert upgraded.adapter.list_relations(legacy.id) == []
    assert upgraded.adapter.list_raw_relations(legacy.id) == [{
        "raw_catalog": "",
        "raw_id": coincident_artifact.id,
        "child_id": legacy.id,
        "relation": "derived_from",
    }]
