from __future__ import annotations

from pathlib import Path
import numpy as np

from virusflow.artifacts.service import ArtifactService
from virusflow.artifacts.models import Scope
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.ontology.scopes import PhysicalScope
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.registry import database as db


def test_service_publication_describe_and_select_best(tmp_path: Path):
    # Initialize a temp DB
    db_path = str(tmp_path / "test.sqlite3")
    db.init_db(db_path=db_path)

    data = np.ones((8, 8), dtype=float)
    svc = ArtifactService(db_path)
    publisher = DefaultPublicationService(
        svc=svc,
        policy=DefaultPersistencePolicy(),
        base_dir=str(tmp_path / "products"),
    )
    artifact = publisher.publish([ArtifactRequest(
        kind="master_ldls",
        components={
            "master_ldls": LogicalComponent("master_ldls", "array2d", data),
            "flat_response_mask": LogicalComponent(
                "flat_response_mask", "array2d", np.zeros_like(data, dtype=np.uint8)
            ),
        },
        scope=Scope(zipcode=None),
    )], PublicationContext("flat", "v2", "flat", "1", {}, [], {}))[0]
    art_id = int(artifact.id)

    # Describe returns a dict with basic fields
    desc = svc.describe(art_id)
    assert desc["id"] == art_id
    assert desc["kind"] == "master_ldls"
    assert desc["payload_type"] == "array"
    assert desc["storage_format"] == "fits"
    assert Path(desc["path"]).exists()

    # select_best should find it for a None-zipcode scope
    row = svc.select_best(kind="master_ldls", scope=Scope(zipcode=None))
    assert row is not None
    assert int(row.get("id")) == art_id


def test_service_publishes_zero_length_table_components(tmp_path: Path):
    db_path = str(tmp_path / "test.sqlite3")
    svc = ArtifactService(db_path)
    publisher = DefaultPublicationService(
        svc=svc, policy=DefaultPersistencePolicy(), base_dir=str(tmp_path / "products"),
    )
    request = ArtifactRequest(
        kind="catalog_match_table",
        role="reduction",
        scope=Scope(
            zipcode=None, exposure_id="20260609T031649.6",
            physical_scope=PhysicalScope.EXPOSURE,
        ),
        components={
            "matches": LogicalComponent("matches", "array2d", np.empty((0, 9))),
            "catalog_rows": LogicalComponent("catalog_rows", "array2d", np.empty((0, 3))),
        },
    )

    artifact = publisher.publish(
        [request], PublicationContext("fixture", "1", "fixture", "1", {}, [], {})
    )[0]

    assert svc.load_component(artifact.id, "matches")["data"].shape == (0, 9)
    assert svc.load_component(artifact.id, "catalog_rows")["data"].shape == (0, 3)
