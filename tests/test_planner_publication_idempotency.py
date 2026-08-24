from __future__ import annotations

from datetime import datetime

import numpy as np

from virusflow.artifacts import ArtifactService, Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.core.identity import ZipCode
from virusflow.planning import ReductionGraph, TaskSpec
from virusflow.planning.targets import PurposeCadence
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.registry import database as db


ZIPCODE = ZipCode("020", "001", "001", "LL", "A")


def _seed_bias_catalog(path: str) -> int:
    db.init_db(path)
    with db.connect(path) as connection:
        connection.execute(
            "INSERT INTO amplifiers(key,ifuslot,ifuid,specid,amp,controller) VALUES(?,?,?,?,?,?)",
            (ZIPCODE.key(), *ZIPCODE.as_tuple()),
        )
        connection.execute(
            "INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)",
            ("20260609T010000.0", "20260609", "zro"),
        )
        connection.execute(
            "INSERT INTO exposure_details(exposure_id,exptime,pexptime) VALUES(?,?,?)",
            ("20260609T010000.0", 0.0, 0.0),
        )
        cursor = connection.execute(
            "INSERT INTO raw_files(exposure_id,frame_type,path,storage_backend,amp_key) VALUES(?,?,?,?,?)",
            ("20260609T010000.0", "zro", "/raw/bias.fits", "filesystem", ZIPCODE.key()),
        )
    return int(cursor.lastrowid)


def _bias_request(group_id: str, raw_id: int) -> ArtifactRequest:
    return ArtifactRequest(
        kind="master_bias",
        components={
            "master": LogicalComponent("master", "array2d", np.ones((3, 4))),
            "per_pixel_bias_scatter": LogicalComponent(
                "per_pixel_bias_scatter", "array2d", np.ones((3, 4)),
            ),
        },
        summaries={"read_noise": 2.0, "n_inputs": 1},
        metadata={"calibration_group_id": group_id},
        scope=Scope(ZIPCODE),
        validity=Validity(datetime(2026, 6, 9), datetime(2026, 6, 10), "target_window"),
        raw_parents=[raw_id],
        raw_catalog="raw.sqlite3",
    )


def _publish_usable_bias(publisher, service, request, context):
    artifact = publisher.publish([request], context)[0]
    service.adapter.set_qa_bundle(
        int(artifact.id), facts={}, status="pass", usability="usable",
        policy_version=service.diagnostics.policy_version_for("master_bias"),
        rules=[],
    )
    return artifact


def _bias_graph():
    return ReductionGraph([
        TaskSpec(
            kind="master_bias", task_cls=object, inputs_raw=["zro"],
            scope_mode="per_zipcode", cadence=PurposeCadence("nightly", minimum_exposures=1),
        )
    ], [])


def test_changed_bias_group_publishes_distinct_artifact_and_then_plans_existing(tmp_path):
    database = str(tmp_path / "registry.sqlite3")
    raw_id = _seed_bias_catalog(database)
    service = ArtifactService(database)
    publisher = DefaultPublicationService(
        svc=service, policy=DefaultPersistencePolicy(), base_dir=str(tmp_path / "products"),
    )
    context = PublicationContext(
        "bias", "v2", "bias.combine", "bias-1.1", {}, [], {},
    )

    artifact_a = _publish_usable_bias(
        publisher, service, _bias_request("group-A", raw_id), context
    )
    planned, first_report = _bias_graph().plan(
        db_path=database, scopes=[Scope(ZIPCODE)]
    )
    assert len(planned) == 1
    assert len(first_report.planned) == 1
    group_b = planned[0].group.group_id
    assert group_b != "group-A"

    artifact_b = _publish_usable_bias(
        publisher, service, _bias_request(group_b, raw_id), context
    )
    assert artifact_b.id != artifact_a.id
    assert service.describe(artifact_b.id)["summary"]["calibration_group_id"] == group_b

    repeated = publisher.publish([_bias_request(group_b, raw_id)], context)[0]
    assert repeated.id == artifact_b.id

    _planned_again, second_report = _bias_graph().plan(
        db_path=database, scopes=[Scope(ZIPCODE)]
    )
    assert len(second_report.planned) == 0
    assert len(second_report.existing) == 1
