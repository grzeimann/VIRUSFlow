from __future__ import annotations

from pathlib import Path
import numpy as np

from virusflow.artifacts.service import ArtifactService
from virusflow.artifacts.models import Scope
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
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
