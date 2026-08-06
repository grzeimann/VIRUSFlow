from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from virusflow.registry import database as db
from virusflow.core.identity import ZipCode
from virusflow.artifacts.models import Scope
from virusflow.planning import CadencePolicy, TemporalWindow, adapt_target
from virusflow.planning.cadence import time_cadence_windows, exposure_count_windows
from virusflow.planning.graph import ReductionGraph, TaskSpec
from virusflow.tasks.base import CalibrationTask, TaskContext


def _seed_zipcode(conn, z: ZipCode) -> None:
    # Minimal amplifier row to enable zipcode scoping in list_raw_files_scoped
    conn.execute(
        """
        INSERT INTO amplifiers(key, ifuslot, ifuid, specid, amp, controller)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO NOTHING
        """,
        (z.key(), z.ifuslot, z.ifuid, z.specid, z.amp, z.controller),
    )


def _seed_exposure(conn, *, exposure_id: str, when_ymd: str, frame_type: str) -> None:
    # Insert exposure and raw_files rows (no actual filesystem I/O required)
    conn.execute(
        """
        INSERT INTO exposures(id, when_utc, frame_type) VALUES(?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET when_utc=excluded.when_utc, frame_type=excluded.frame_type
        """,
        (exposure_id, when_ymd, frame_type),
    )


def _seed_raw_file(conn, *, exposure_id: str, frame_type: str, amp_key: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_files(exposure_id, frame_type, path, tar_member, storage_backend, amp_key)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (exposure_id, frame_type, f"/tmp/{exposure_id}_{frame_type}.fits", None, "filesystem", amp_key),
    )


def test_time_cadence_windows_min_count(tmp_path: Path):
    db_path = str(tmp_path / "test.sqlite3")
    db.init_db(db_path=db_path)
    z = ZipCode(ifuslot="001", ifuid="001", specid="001", amp="LL", controller="A")
    # Seed rows
    with db.connect(db_path) as conn:
        _seed_zipcode(conn, z)
        # Create 25 zero frames across 5 consecutive days
        base = datetime(2026, 5, 1)
        for i in range(25):
            t = base + timedelta(days=i // 5)
            eid = t.strftime("%Y%m%dT%H%M%S")
            _seed_exposure(conn, exposure_id=eid, when_ymd=t.strftime("%Y%m%d"), frame_type="zro")
            _seed_raw_file(conn, exposure_id=eid, frame_type="zro", amp_key=z.key())
    scope = Scope(zipcode=z)
    wins = time_cadence_windows(db_path=db_path, scope=scope, frame_type="zro", every_days=30, min_n_inputs=25)
    assert isinstance(wins, list)
    assert len(wins) == 1  # minimal helper emits a single open window when threshold met


def test_exposure_count_windows_min_n_and_span(tmp_path: Path):
    db_path = str(tmp_path / "test.sqlite3")
    db.init_db(db_path=db_path)
    z = ZipCode(ifuslot="002", ifuid="001", specid="001", amp="LL", controller="A")
    with db.connect(db_path) as conn:
        _seed_zipcode(conn, z)
        # Seed 10 dark frames within 10 days -> expect one window when min_n=10, span<=45
        base = datetime(2026, 6, 1, 0, 0, 0)
        for i in range(10):
            t = base + timedelta(days=i)
            eid = t.strftime("%Y%m%dT%H%M%S")
            _seed_exposure(conn, exposure_id=eid, when_ymd=t.strftime("%Y%m%d"), frame_type="drk")
            _seed_raw_file(conn, exposure_id=eid, frame_type="drk", amp_key=z.key())
    scope = Scope(zipcode=z)
    wins = exposure_count_windows(db_path=db_path, scope=scope, frame_type="drk", min_n=10, max_span_days=45)
    assert len(wins) == 1
    w = wins[0]
    assert w.start is not None and w.end is not None
    assert (w.end - w.start).days >= 9  # ~10-day span

    # Now verify that if not enough exposures and span not exceeded, returns empty
    db_path2 = str(tmp_path / "test2.sqlite3")
    db.init_db(db_path=db_path2)
    z2 = ZipCode(ifuslot="003", ifuid="001", specid="001", amp="LL", controller="A")
    with db.connect(db_path2) as conn:
        _seed_zipcode(conn, z2)
        base = datetime(2026, 7, 1)
        for i in range(3):
            t = base + timedelta(days=i)
            eid = t.strftime("%Y%m%dT%H%M%S")
            _seed_exposure(conn, exposure_id=eid, when_ymd=t.strftime("%Y%m%d"), frame_type="flt")
            _seed_raw_file(conn, exposure_id=eid, frame_type="flt", amp_key=z2.key())
    scope2 = Scope(zipcode=z2)
    wins2 = exposure_count_windows(db_path=db_path2, scope=scope2, frame_type="flt", min_n=5, max_span_days=30)
    assert wins2 == []


def test_planner_deduplicates_equal_effective_inputs_and_preserves_distinct_sets(tmp_path: Path):
    db_path = str(tmp_path / "effective-inputs.sqlite3")
    db.init_db(db_path=db_path)
    z = ZipCode(ifuslot="004", ifuid="001", specid="001", amp="LL", controller="A")
    with db.connect(db_path) as conn:
        _seed_zipcode(conn, z)
        for exposure_id in ("20260609T000500", "20260609T001000"):
            _seed_exposure(
                conn, exposure_id=exposure_id, when_ymd="20260609", frame_type="flt"
            )
            _seed_raw_file(conn, exposure_id=exposure_id, frame_type="flt", amp_key=z.key())

    class OverlappingCadence(CadencePolicy):
        def windows(self, **kwargs):
            return [
                TemporalWindow(datetime(2026, 6, 9, 0, 0), datetime(2026, 6, 9, 0, 6)),
                TemporalWindow(datetime(2026, 6, 9, 0, 4), datetime(2026, 6, 9, 0, 6)),
                TemporalWindow(datetime(2026, 6, 9, 0, 9), datetime(2026, 6, 9, 0, 11)),
            ]

    node = TaskSpec(kind="master_ldls", task_cls=object, inputs_raw=["flt"], cadence=OverlappingCadence())
    planned, report = ReductionGraph([node], []).plan(
        db_path=db_path, scopes=[Scope(zipcode=z)]
    )

    assert len(planned) == 2
    assert len(report.skipped) == 1
    assert set(report.reasons.values()) == {"duplicate_effective_raw_inputs"}

    class FlatInputs(CalibrationTask):
        frame_type = "flt"

    resolved_ids = []
    for target in planned:
        task = FlatInputs(TaskContext(db_path, str(tmp_path), {}), target=adapt_target(target))
        _, parent_ids = task.query_inputs()
        resolved_ids.append(parent_ids)
    assert resolved_ids == [[1], [2]]
