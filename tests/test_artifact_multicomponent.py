from datetime import datetime
from pathlib import Path

import numpy as np

from virusflow.artifacts import Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.artifacts.service import ArtifactLoadError, ArtifactService
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


def _request(zc, parents=()):
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
        validity=Validity(datetime(2026, 6, 9), datetime(2026, 6, 10)),
        parents=list(parents),
    )


def test_multicomponent_roundtrip_validity_revision_checksum_and_lineage(tmp_path: Path):
    database = tmp_path / "registry.sqlite"
    db.init_db(str(database))
    svc = ArtifactService(str(database))
    pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=str(tmp_path / "products"))
    zc = ZipCode("013", "043", "412", "LL", "S_N_0021")

    first = pub.publish([_request(zc)], _context())[0]
    second = pub.publish([_request(zc, [first.id])], _context([first.id]))[0]

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


def test_configuration_baselines_preserve_unknown_and_provisional_evidence():
    zc = ZipCode("013", "043", "412", "LL", "S_N_0021")
    refs = ConfigurationService().amplifier_references(zc)
    assert any(ref.kind == "gain_fallback" and ref.evidence_state == "unknown" for ref in refs)
    assert CCD_TRANSFORM_CONFIGURATION.value == {"upper_reflection_index": 2063}
    assert EFFECTIVE_EXPOSURE_POLICY.effective_seconds(exptime=None, pexptime=100.0, parallel=True) == 92.0
