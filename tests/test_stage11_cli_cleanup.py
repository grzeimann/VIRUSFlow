from __future__ import annotations

import json
from pathlib import Path

import pytest

from virusflow.cli.virusflow import build_parser, resolve_progress_config
from virusflow.planning.config import load_planning_config_from_dict
from virusflow.registry import database as db
from virusflow.storage.cleanup import cleanup_legacy, cleanup_scratch


def test_planning_progress_configuration_and_cli_precedence():
    configured = load_planning_config_from_dict({
        "execution": {
            "nworkers": 6, "progress": False, "progress_mode": "json",
            "progress_interval": 12.5, "progress_path": "events.jsonl", "max_retries": 2,
        }
    })
    assert configured.nworkers == 6
    assert resolve_progress_config(
        type("Args", (), {
            "progress": True, "progress_mode": "plain", "progress_interval": 1.0,
            "progress_file": "cli.jsonl", "max_retries": 3,
        })(), configured
    ) == {
        "progress": True, "progress_mode": "plain", "progress_interval": 1.0,
        "progress_path": "cli.jsonl", "max_retries": 3,
    }
    with pytest.raises(ValueError, match="canonical calibration kind"):
        load_planning_config_from_dict({"nodes": {"master_flat": {"enabled": True}}})


def test_clean_cli_exposes_real_commands_and_retires_plan_stubs(tmp_path: Path):
    parser = build_parser()
    args = parser.parse_args(["run", "observation", "--observation-id", "20260609-OBSID6"])
    assert args.run_cmd == "observation"
    assert args.progress is None
    assert args.nworkers is None
    with pytest.raises(SystemExit):
        parser.parse_args(["plan", "night", "--date", "20260609"])
    with pytest.raises(SystemExit):
        parser.parse_args(["tasks"])
    for command in (
        ["artifact", "show", "1"], ["model", "list"], ["storage", "report"],
        ["cleanup", "cache"], ["config", "show"],
    ):
        parser.parse_args(command)


def test_retired_modules_and_hidden_handlers_are_absent():
    import virusflow.cli.virusflow as cli

    root = Path(__file__).resolve().parents[1]
    for relative in (
        "virusflow/algorithms/sci.py",
        "virusflow/artifacts/materialize.py",
        "virusflow/cli/verify_steps_1_7.py",
        "virusflow/planning/mapping.py",
    ):
        assert not (root / relative).exists()
    for name in (
        "cmd_storage_migrate", "cmd_scratch_cleanup", "cmd_debug_raw",
        "cmd_plan_calibrations", "cmd_plan_night", "cmd_plan_exposure",
        "cmd_plan_observation_set", "cmd_qa_set", "cmd_qa_backfill",
    ):
        assert not hasattr(cli, name)


def test_legacy_artifact_names_are_read_only_not_publication_aliases(tmp_path: Path):
    import numpy as np

    from virusflow.artifacts import ArtifactService
    from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
    from virusflow.ontology.artifact_kinds import canonical_kind, kind_spec
    from virusflow.persistence.policy import DefaultPersistencePolicy
    from virusflow.publication.context import PublicationContext
    from virusflow.publication.service import DefaultPublicationService

    assert canonical_kind("master_flat") == "master_ldls"
    with pytest.raises(KeyError, match="Unregistered"):
        kind_spec("master_sci")

    service = ArtifactService(str(tmp_path / "registry.sqlite3"))
    publisher = DefaultPublicationService(
        svc=service,
        policy=DefaultPersistencePolicy(),
        base_dir=str(tmp_path / "artifacts"),
    )
    request = ArtifactRequest(
        kind="master_flat",
        components={
            "master_flat": LogicalComponent("master_flat", "array2d", np.ones((2, 2)))
        },
    )
    context = PublicationContext("test", "1", "test", "1", {}, [], {})
    with pytest.raises(ValueError, match="read-only.*master_ldls"):
        publisher.publish([request], context)


def test_registry_derived_observation_membership(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    with db.connect(str(database)) as connection:
        for exposure_id, expnum, frame_type in (
            ("20260609T010000.0", 6, "sci"), ("20260609T010200.0", 6, "sci"),
            ("20260609T020000.0", 7, "sci"), ("20260609T010300.0", 6, "zro"),
        ):
            connection.execute(
                "INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)",
                (exposure_id, exposure_id, frame_type),
            )
            connection.execute(
                "INSERT INTO exposure_details(exposure_id,expnum) VALUES(?,?)",
                (exposure_id, expnum),
            )
    assert db.observation_exposure_ids("20260609-OBSID6", db_path=str(database)) == [
        "20260609T010000.0", "20260609T010200.0",
    ]


def test_cleanup_is_dry_run_by_default_and_explicit_when_destructive(tmp_path: Path):
    scratch = tmp_path / ".scratch" / "run" / "worker"
    scratch.mkdir(parents=True)
    payload = scratch / "temporary.bin"
    payload.write_bytes(b"temporary")
    preview = cleanup_scratch(tmp_path)
    assert preview.dry_run and preview.candidates == 1 and payload.exists()
    result = cleanup_scratch(tmp_path, execute=True)
    assert not result.dry_run and result.removed_bytes == 9 and not payload.exists()

    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    with pytest.raises(ValueError, match="deactivate"):
        cleanup_legacy(str(database), delete_payloads=True, validation_succeeded=True)
    with pytest.raises(ValueError, match="validation-succeeded"):
        cleanup_legacy(str(database), deactivate=True, delete_payloads=True)


def test_validation_report_schema_is_json_serializable():
    payload = {
        "schema": "virusflow.validation.v1", "result": "PASS",
        "checks": [{"name": "example", "passed": True}],
    }
    assert json.loads(json.dumps(payload))["result"] == "PASS"


def test_validation_report_is_preserved_as_artifact(tmp_path: Path):
    from virusflow.artifacts import ArtifactService
    from virusflow.cli.verify_steps_8_10 import _publish_validation_report

    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    # A normalized parent is required by the validation report's lineage.  A
    # raw registry row is sufficient for this focused publication test.
    with db.connect(str(database)) as connection:
        connection.execute(
            "INSERT INTO artifacts(kind,name,path) VALUES('parent','parent',?)",
            (str(tmp_path / "parent"),),
        )
        parent_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    markdown = tmp_path / "validation.md"
    machine = tmp_path / "validation.json"
    markdown.write_text("# PASS\n")
    machine.write_text('{"result":"PASS"}\n')
    artifact = _publish_validation_report(database, tmp_path, markdown, machine, parent_id)
    description = ArtifactService(str(database)).describe(artifact.id)
    assert description["kind"] == "validation_report"
    assert {item["name"] for item in description["components"]} == {
        "report_json", "report_markdown",
    }
