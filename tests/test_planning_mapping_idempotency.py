from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from virusflow.registry import database as db
from virusflow.core.identity import ZipCode
from virusflow.artifacts.models import Scope
from virusflow.artifacts.provenance import build_provenance
from virusflow.planning.defaults import default_calibration_graph
from virusflow.planning.graph import ReductionGraph


def _seed_zipcode(conn, z: ZipCode) -> None:
    conn.execute(
        """
        INSERT INTO amplifiers(key, ifuslot, ifuid, specid, amp, controller)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO NOTHING
        """,
        (z.key(), z.ifuslot, z.ifuid, z.specid, z.amp, z.controller),
    )


def _seed_exposure(conn, *, exposure_id: str, when_ymd: str, frame_type: str) -> None:
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


def _seed_artifact(conn, *, kind: str, z: ZipCode, created_at: datetime, vstart: datetime | None = None, vend: datetime | None = None, path_suffix: str = "") -> int:
    """Insert a minimal artifact + provenance row directly for deterministic created_at.

    Returns the inserted artifact id.
    """
    art = {
        "kind": kind,
        "name": kind,
        "path": f"/tmp/{kind}{path_suffix or ''}.fits",
        "zipcode": z,
        "validity_start": vstart,
        "validity_end": vend,
    }
    prov = build_provenance(algorithm=f"test:{kind}", params={})
    # Override created_at deterministically
    prov["created_at"] = created_at
    return db.save_artifact(art, prov, db_path=conn.execute("PRAGMA database_list").fetchone()[2])


def test_planner_does_not_hide_distinct_inputs_behind_nominally_valid_artifact(tmp_path: Path):
    db_path = str(tmp_path / "test2.sqlite3")
    db.init_db(db_path=db_path)
    # Prepare zipcode and enough raw zero frames to satisfy TimeCadence min_n_inputs=25
    z = ZipCode(ifuslot="011", ifuid="001", specid="001", amp="LL", controller="A")
    with db.connect(db_path) as conn:
        _seed_zipcode(conn, z)
        base = datetime(2026, 5, 1)
        for i in range(25):
            t = base + timedelta(days=i // 5)
            eid = t.strftime("%Y%m%dT%H%M%S")
            _seed_exposure(conn, exposure_id=eid, when_ymd=t.strftime("%Y%m%d"), frame_type="zro")
            _seed_raw_file(conn, exposure_id=eid, frame_type="zro", amp_key=z.key())
        # Seed an existing master_bias artifact (created_at near base)
        _seed_artifact(conn, kind="master_bias", z=z, created_at=base + timedelta(days=1), vstart=base, vend=base + timedelta(days=30))

    nodes, edges = default_calibration_graph()
    G = ReductionGraph(nodes, edges)
    scopes = [Scope(zipcode=z)]
    planned, report = G.plan(db_path=db_path, scopes=scopes)

    # A nominally valid legacy artifact without the same raw-parent identity
    # cannot suppress a scientifically distinct nightly group.
    assert any(t.kind == "master_bias" for t in report.planned)
    assert all(t.kind != "master_bias" for t in report.existing)
