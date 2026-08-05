from __future__ import annotations

from pathlib import Path
import numpy as np

from virusflow.artifacts.service import ArtifactService
from virusflow.artifacts.models import Scope
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.ontology.scopes import PhysicalScope
from virusflow.performance import PerformanceRun
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


def test_planned_group_lookup_filters_in_one_database_connection(tmp_path: Path):
    db_path = str(tmp_path / "planned-groups.sqlite3")
    svc = ArtifactService(db_path)
    publisher = DefaultPublicationService(
        svc=svc,
        policy=DefaultPersistencePolicy(),
        base_dir=str(tmp_path / "products"),
    )
    context = PublicationContext("fixture", "1", "fixture", "1", {}, [], {})

    def publish(group_id: str, value: float, revision: str):
        data = np.full((2, 2), value, dtype=float)
        return publisher.publish([ArtifactRequest(
            kind="master_ldls",
            components={
                "master_ldls": LogicalComponent("master_ldls", "array2d", data),
                "flat_response_mask": LogicalComponent(
                    "flat_response_mask", "array2d", np.zeros_like(data, dtype=np.uint8)
                ),
            },
            metadata={"calibration_group_id": group_id},
            scope=Scope(zipcode=None),
            revision=revision,
        )], context)[0]

    selected = publish("wanted", 1.0, "selected")
    inactive = publish("wanted", 2.0, "inactive")
    publish("other", 3.0, "other")
    svc.adapter.set_state(int(inactive.id), "obsolete")

    performance = PerformanceRun(workers=1)
    performance.mark_queued("planned-parent-lookup")
    timing, token = performance.begin_task(
        "planned-parent-lookup", "registry", "group=wanted", "worker", 1
    )
    rows = svc.adapter.find_by_calibration_groups(
        kind="master_ldls", calibration_group_ids={"wanted"}, state="active"
    )
    performance.end_task(timing, token, "succeeded")

    assert [int(row["id"]) for row in rows] == [int(selected.id)]
    assert rows[0]["metadata"]["calibration_group_id"] == "wanted"
    assert rows[0]["state"] == "active"
    assert timing.counters["database_connections"] == 1
    selects = [
        query for query in timing.database_queries
        if query["operation"] == "SELECT"
    ]
    assert len(selects) == 1
    assert "json_extract" in selects[0]["sql"]

    performance.mark_queued("list-all")
    list_timing, list_token = performance.begin_task(
        "list-all", "registry", "kind=master_ldls", "worker", 1
    )
    all_rows = svc.adapter.list_all(kind="master_ldls")
    performance.end_task(list_timing, list_token, "succeeded")

    assert len(all_rows) == 3
    assert all("metadata" in row for row in all_rows)
    assert list_timing.counters["database_connections"] == 2
    assert len([
        query for query in list_timing.database_queries
        if query["operation"] == "SELECT"
    ]) == 2
