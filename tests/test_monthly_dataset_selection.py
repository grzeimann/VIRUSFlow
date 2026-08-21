from __future__ import annotations

import json
from pathlib import Path

from virusflow.cli.virusflow import main
from virusflow.core.identity import ZipCode
from virusflow.registry import database as db


def _seed_raw(conn, zipcode: ZipCode, exposure_id: str, *, night: str | None = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO amplifiers(key,ifuslot,ifuid,specid,amp,controller) VALUES(?,?,?,?,?,?)",
        (zipcode.key(), *zipcode.as_tuple()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)",
        (exposure_id, exposure_id[:8], "zro"),
    )
    conn.execute(
        "INSERT INTO raw_files(exposure_id,frame_type,path,storage_backend,amp_key) VALUES(?,?,?,?,?)",
        (
            exposure_id, "zro",
            f"/work/03946/hetdex/maverick/{night}/virus/virus0001.tar"
            if night else f"/raw/{exposure_id}_{zipcode.amp}.fits",
            "tar" if night else "filesystem", zipcode.key(),
        ),
    )


def _plan(tmp_path: Path, raw_db: Path, artifact_db: Path, *options: str) -> dict:
    workdir = tmp_path / ("work_" + str(len(list(tmp_path.glob("work_*")))))
    main([
        "run", "calibrations",
        "--db", str(artifact_db),
        "--raw-db", str(raw_db),
        "--workdir", str(workdir),
        "--configuration-root", str(tmp_path),
        "--plan-only",
        *options,
    ])
    return json.loads((workdir / "planning_report.json").read_text())


def test_calibration_cli_month_and_zipcode_selection_are_optional_and_composable(tmp_path: Path):
    raw_db = tmp_path / "raw.sqlite3"
    artifact_db = tmp_path / "artifacts.sqlite3"
    db.init_raw_db(str(raw_db))
    db.init_db(str(artifact_db))
    lower = ZipCode("001", "002", "003", "LL", "A")
    upper = ZipCode("001", "002", "003", "LU", "A")
    outside = ZipCode("004", "005", "006", "RU", "B")
    with db.connect(str(raw_db)) as conn:
        _seed_raw(conn, lower, "20260610T010000.0")
        _seed_raw(conn, upper, "20260610T010000.0")
        _seed_raw(conn, outside, "20260710T010000.0")

    # With no new options, existing all-date/all-amplifier behavior remains.
    unfiltered = _plan(tmp_path, raw_db, artifact_db)
    assert unfiltered["summary"]["n_scopes"] == 3

    # Inclusive bounds retain the requested day and exclude a later raw exposure.
    dated = _plan(tmp_path, raw_db, artifact_db, "--start-date", "20260610", "--end-date", "20260610")
    assert dated["summary"]["n_scopes"] == 2

    # A physical CCD is represented by its existing paired LL/LU ZipCode keys.
    pair = f"{lower.key()},{upper.key()}"
    selected = _plan(tmp_path, raw_db, artifact_db, "--only-zipcodes", pair)
    assert selected["summary"]["n_scopes"] == 2
    selected_amps = {item["zipcode"]["amp"] for item in selected["planned"]}
    assert selected_amps == {"LL", "LU"}

    combined = _plan(
        tmp_path, raw_db, artifact_db,
        "--start-date", "20260610", "--end-date", "20260610",
        "--only-zipcodes", pair,
    )
    assert combined["summary"]["n_scopes"] == 2
    assert {item["zipcode"]["amp"] for item in combined["planned"]} == {"LL", "LU"}


def test_calibration_observing_night_scope_filters_candidates_not_timestamps(tmp_path: Path):
    raw_db = tmp_path / "raw.sqlite3"
    artifact_db = tmp_path / "artifacts.sqlite3"
    db.init_raw_db(str(raw_db))
    db.init_db(str(artifact_db))
    zipcode = ZipCode("001", "002", "003", "LL", "A")
    with db.connect(str(raw_db)) as conn:
        _seed_raw(conn, zipcode, "20260602T010000.0", night="20260601")
        _seed_raw(conn, zipcode, "20260603T010000.0", night="20260602")

    rows = db.list_calibration_grouping_rows_bulk(
        db_path=str(raw_db), first_night="20260601", last_night="20260601",
    )
    assert [row["exposure_id"] for row in rows] == ["20260602T010000.0"]
    assert rows[0]["when_utc"] == "20260602"

    report = _plan(
        tmp_path, raw_db, artifact_db,
        "--first-night", "20260601", "--last-night", "20260601",
        "--only-zipcodes", zipcode.key(),
    )
    assert report["summary"]["n_scopes"] == 1
