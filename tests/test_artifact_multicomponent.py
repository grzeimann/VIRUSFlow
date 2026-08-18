from datetime import datetime
from pathlib import Path

import numpy as np

from virusflow.artifacts import ConfigurationReference, Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.artifacts.service import ArtifactService
from virusflow.config import CCD_TRANSFORM_CONFIGURATION, ConfigurationService, EFFECTIVE_EXPOSURE_POLICY
from virusflow.core.identity import ZipCode
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.registry import database as db


def _context(parents=()):
    return PublicationContext(
        task_name="bias",
        task_version="v2",
        algorithm_name="bias.combine",
        algorithm_version="2",
        parent_ids=list(parents),
    )


def _request(zc, parents=(), validity=None, configuration_refs=()):
    return ArtifactRequest(
        kind="master_bias",
        components={
            "master": LogicalComponent("master", "array2d", np.ones((3, 4)), "electron", "oriented_amplifier_blue_to_red"),
            "per_pixel_bias_scatter": LogicalComponent(
                "per_pixel_bias_scatter", "array2d", np.full((3, 4), 2.0), "electron", "oriented_amplifier_blue_to_red"
            ),
        },
        summaries={"read_noise": 2.0, "n_inputs": 4},
        metadata={"n_inputs": 4},
        scope=Scope(zc),
        validity=validity or Validity(datetime(2026, 6, 9), datetime(2026, 6, 10)),
        parents=list(parents),
        configuration_refs=list(configuration_refs),
    )


def test_multicomponent_roundtrip_validity_revision_checksum_and_lineage(tmp_path: Path):
    database = tmp_path / "registry.sqlite"
    db.init_db(str(database))
    svc = ArtifactService(str(database))
    pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=str(tmp_path / "products"))
    zc = ZipCode("013", "043", "412", "RU", "S_N 0021")

    first = pub.publish([_request(zc)], _context())[0]
    second = pub.publish([_request(zc, [first.id])], _context([first.id]))[0]

    scope_folder = Path(first.storage.uri).relative_to(tmp_path / "products").parts[1]
    assert scope_folder == "013_043_412_RU_S_N_0021"
    assert first.scope.zipcode.key() == "013+043+412+RU+S_N 0021"
    assert first.storage.uri != second.storage.uri
    assert first.revision != second.revision
    assert first.checksum and second.checksum
    np.testing.assert_allclose(svc.load_component(second.id, "master")["data"], 1.0)
    np.testing.assert_allclose(svc.load_component(second.id, "per_pixel_bias_scatter")["data"], 2.0)

    desc = svc.describe(second.id)
    assert {item["name"] for item in desc["components"]} == {"master", "per_pixel_bias_scatter"}
    assert desc["validity"]["start"] == datetime(2026, 6, 9)
    assert desc["revision"] == second.revision
    assert desc["checksum"] == second.checksum
    assert desc["relations"] == [{"parent_id": first.id, "child_id": second.id, "relation": "derived_from"}]

    with db.connect(str(database)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM artifact_relations").fetchone()[0] == 1


def test_revision_uses_effective_inputs_and_configuration_not_nominal_validity(tmp_path: Path):
    database = tmp_path / "registry.sqlite"
    db.init_db(str(database))
    svc = ArtifactService(str(database))
    pub = DefaultPublicationService(
        svc=svc, policy=DefaultPersistencePolicy(), base_dir=str(tmp_path / "products")
    )
    zc = ZipCode("013", "043", "412", "LL", "S_N_0021")
    config = ConfigurationReference("gain", "1", zc.key(), "measured")

    first = pub.publish([
        _request(
            zc,
            validity=Validity(datetime(2026, 6, 9), datetime(2026, 6, 10)),
            configuration_refs=[config],
        )
    ], _context())[0]
    same_computation = pub.publish([
        _request(
            zc,
            validity=Validity(datetime(2026, 6, 8), datetime(2026, 6, 11)),
            configuration_refs=[config],
        )
    ], _context())[0]
    changed_config = pub.publish([
        _request(
            zc,
            validity=Validity(datetime(2026, 6, 8), datetime(2026, 6, 11)),
            configuration_refs=[ConfigurationReference("gain", "2", zc.key(), "measured")],
        )
    ], _context())[0]

    assert same_computation.id == first.id
    assert same_computation.revision == first.revision
    assert changed_config.id != first.id
    assert changed_config.revision != first.revision


def test_nearest_selection_prefers_closest_applicability_interval(tmp_path: Path):
    svc = ArtifactService(str(tmp_path / "registry.sqlite"))
    pub = DefaultPublicationService(
        svc=svc, policy=DefaultPersistencePolicy(), base_dir=str(tmp_path / "products")
    )
    zc = ZipCode("013", "043", "412", "LL", "S_N_0021")
    first = pub.publish([_request(
        zc, validity=Validity(datetime(2026, 5, 1), datetime(2026, 6, 1)),
        configuration_refs=[ConfigurationReference("gain", "1")],
    )], _context())[0]
    second = pub.publish([_request(
        zc, validity=Validity(datetime(2026, 7, 1), datetime(2026, 8, 1)),
        configuration_refs=[ConfigurationReference("gain", "2")],
    )], _context())[0]
    selected = svc.select_best(
        kind="master_bias", scope=Scope(zc), at_time=datetime(2026, 6, 25),
        policy="nearest",
    )
    assert int(selected["id"]) == int(second.id)
    assert int(selected["id"]) != int(first.id)


def test_configuration_baselines_preserve_unknown_and_provisional_evidence():
    zc = ZipCode("013", "043", "412", "LL", "S_N_0021")
    refs = ConfigurationService().amplifier_references(zc)
    assert any(ref.kind == "gain_fallback" and ref.evidence_state == "unknown" for ref in refs)
    assert CCD_TRANSFORM_CONFIGURATION.value == {"upper_y_offset": 1032}
    assert EFFECTIVE_EXPOSURE_POLICY.effective_seconds(exptime=None, pexptime=100.0, parallel=True) == 92.0
