from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

import numpy as np
from astropy.io import fits

from virusflow.cli.formatting import format_exposures_table
from virusflow.core.exposure_metadata import interpret_virus_exposure_header
from virusflow.registry import database as db


def test_header_interpretation_separates_requested_target_and_operational_context():
    primary = interpret_virus_exposure_header({
        "QOBJECT": "Target_Name_With_Underscores",
        "OBJECT": "Target_Name_With_Underscores_082_W",
        "QRA": "13:30:00",
        "QDEC": "-08:30:00",
        "QPROG": "UT26-1-001",
    }, frame_type="sci")
    assert primary.requested_target == "Target_Name_With_Underscores"
    assert primary.requested_target_source == "QOBJECT"
    assert primary.virus_object == "Target_Name_With_Underscores_082_W"
    assert primary.requested_ifuslot == "082"
    assert primary.het_track == "W"
    assert primary.observing_mode == "primary" and primary.virus_primary is True
    assert primary.q_metadata_expected and primary.q_metadata_complete
    assert primary.object_qobject_consistent is True

    field_center = interpret_virus_exposure_header(
        {"OBJECT": "Survey_Field_000_E", "QOBJECT": "Survey_Field"}, frame_type="sci"
    )
    assert field_center.requested_ifuslot == "000" and field_center.het_track == "E"
    fallback = interpret_virus_exposure_header(
        {"OBJECT": "Target_With_Underscores_082_W"}, frame_type="sci"
    )
    assert fallback.requested_target == "Target_With_Underscores"
    assert fallback.requested_target_source == "OBJECT_prefix"

    parallel = interpret_virus_exposure_header({
        "OBJECT": "parallel", "QOBJECT": "Requested_Target", "QRA": 1,
        "QDEC": 2, "QPROG": "P001",
    }, frame_type="sci")
    assert parallel.requested_target == "Requested_Target"
    assert parallel.requested_ifuslot is None and parallel.het_track is None
    assert parallel.observing_mode == "parallel" and parallel.virus_primary is False

    calibration = interpret_virus_exposure_header({"OBJECT": "Hg"}, frame_type="cmp")
    assert calibration.observing_mode == "calibration"
    assert calibration.requested_target is None and calibration.virus_primary is None
    assert not calibration.q_metadata_expected and calibration.q_metadata_complete is None


def _write_raw(path: Path, **header_values) -> None:
    header = fits.Header({
        "IFUID": "043", "SPECID": "412", "CONTID": "S/N 0021", **header_values,
    })
    fits.PrimaryHDU(np.ones((2, 2)), header=header).writeto(path)


def test_registry_ingests_queries_and_formats_distinct_object_semantics(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    primary_id = "20260724T010000.0"
    parallel_id = "20260724T020000.0"
    calibration_id = "20260724T030000.0"
    primary_path = tmp_path / f"{primary_id}_013LL_sci.fits"
    parallel_path = tmp_path / f"{parallel_id}_013LL_sci.fits"
    calibration_path = tmp_path / f"{calibration_id}_013LL_cmp.fits"
    _write_raw(
        primary_path,
        OBJECT="Target_Name_082_W",
        QOBJECT="Target_Name",
        QRA="13:30:00",
        QDEC="-08:30:00",
        QPROG="SCIENCE-1",
    )
    _write_raw(
        parallel_path,
        OBJECT="parallel",
        QOBJECT="Parallel_Target",
        QRA="14:00:00",
        QDEC="+10:00:00",
        QPROG="SCIENCE-2",
    )
    _write_raw(calibration_path, OBJECT="Hg")
    for path in (primary_path, parallel_path, calibration_path):
        db.register_raw_file(str(path), db_path=str(database))

    primary = db.get_exposure_metadata(primary_id, db_path=str(database))
    assert primary["virus_object"] == "Target_Name_082_W"
    assert primary["object_name"] == "Target_Name_082_W"
    assert primary["qobject"] == primary["requested_target"] == "Target_Name"
    assert primary["requested_ifuslot"] == "082" and primary["het_track"] == "W"
    assert primary["observing_mode"] == "primary" and primary["virus_primary"] == 1

    parallel = db.get_exposure_metadata(parallel_id, db_path=str(database))
    assert parallel["virus_object"] == "parallel"
    assert parallel["qobject"] == parallel["requested_target"] == "Parallel_Target"
    assert parallel["observing_mode"] == "parallel" and parallel["virus_primary"] == 0

    calibration = db.get_exposure_metadata(calibration_id, db_path=str(database))
    assert calibration["qobject"] is None and calibration["qprog"] is None
    assert calibration["observing_mode"] == "calibration"
    assert calibration["q_metadata_expected"] == 0
    assert calibration["q_metadata_complete"] is None

    target_rows = db.list_exposure_table(
        db_path=str(database), requested_target="Target_Name"
    )
    assert [row["exposure_id"] for row in target_rows] == [primary_id]
    parallel_rows = db.list_exposure_table(
        db_path=str(database), observing_mode="parallel", requested_program="SCIENCE-2"
    )
    assert [row["exposure_id"] for row in parallel_rows] == [parallel_id]
    assert parallel_rows[0]["object"] == "parallel"
    assert parallel_rows[0]["qobject"] == "Parallel_Target"

    rendered = list(csv.DictReader(io.StringIO(format_exposures_table(parallel_rows, csv=True))))
    assert rendered[0]["object"] == "parallel"
    assert rendered[0]["qobject"] == rendered[0]["requested_target"] == "Parallel_Target"


def test_additive_migration_does_not_guess_legacy_object_origin(tmp_path: Path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE exposures(id TEXT PRIMARY KEY, when_utc TEXT, frame_type TEXT)")
        connection.execute("""
            CREATE TABLE exposure_details(
                exposure_id TEXT PRIMARY KEY, tar_path TEXT, expnum INTEGER, qobject TEXT,
                qprog TEXT, pexptime REAL, date TEXT, qra TEXT, qdec TEXT, exptime REAL,
                ambient_temperature REAL, object_name TEXT, lamp TEXT, observing_block TEXT
            )
        """)
        connection.execute(
            "INSERT INTO exposures VALUES('legacy','20260724','sci')"
        )
        connection.execute(
            "INSERT INTO exposure_details(exposure_id,qobject,object_name) VALUES(?,?,?)",
            ("legacy", "Target_Name", "Target_Name"),
        )

    db.init_db(str(database))
    migrated = db.get_exposure_metadata("legacy", db_path=str(database))
    assert migrated["object_name"] == "Target_Name"
    assert migrated["qobject"] == "Target_Name"
    assert migrated["virus_object"] is None
    assert migrated["requested_target"] is None
