from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from virusflow.registry import database as db
from virusflow.core.identity import ZipCode
from virusflow.artifacts.models import Scope
from virusflow.planning import default_calibration_graph
from virusflow.planning.graph import ReductionGraph
from virusflow.planning import schedule, adapt_target
from virusflow.tasks.mapping import default_kind_to_task
from virusflow.tasks.base import TaskContext
from virusflow.executors.planning_executor import PlanningExecutor


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


def test_planning_scheduler_executor_smoke(tmp_path: Path, monkeypatch):
    # Initialize DB
    db_path = str(tmp_path / "smoke.sqlite3")
    db.init_db(db_path=db_path)
    # Seed one zipcode with enough zro and drk frames
    z = ZipCode(ifuslot="020", ifuid="001", specid="001", amp="LL", controller="A")
    with db.connect(db_path) as conn:
        _seed_zipcode(conn, z)
        # Bias: 25 zro within a few days
        base = datetime(2026, 6, 1, 0, 0, 0)
        for i in range(25):
            t = base + timedelta(hours=i)
            eid = t.strftime("%Y%m%dT%H%M%S")
            _seed_exposure(conn, exposure_id=eid, when_ymd=t.strftime("%Y%m%d"), frame_type="zro")
            _seed_raw_file(conn, exposure_id=eid, frame_type="zro", amp_key=z.key())
        # Dark: 20 drk within 10 days
        for i in range(20):
            t = base + timedelta(days=i)
            eid = (t + timedelta(minutes=1)).strftime("%Y%m%dT%H%M%S")
            _seed_exposure(conn, exposure_id=eid, when_ymd=t.strftime("%Y%m%d"), frame_type="drk")
            _seed_raw_file(conn, exposure_id=eid, frame_type="drk", amp_key=z.key())

    # Build default graph and plan
    nodes, edges = default_calibration_graph()
    G = ReductionGraph(nodes, edges)
    scopes = [Scope(zipcode=z)]
    planned, report = G.plan(db_path=db_path, scopes=scopes)

    # We expect at least one planned target (bias and/or dark)
    assert len(report.planned) >= 1

    # Inject the approved raw-I/O boundary to avoid real FITS I/O.
    import numpy as _np
    from virusflow.io import RawFrameData

    class _SyntheticRawLoader:
        def load(self, path, tar_member=None):
            img = _np.zeros((16, 16), dtype=float)
            return RawFrameData(
                img,
                {
                    "GAIN": 1.0, "RDNOISE": 3.0, "EXPTIME": 360.0,
                    "CCDPOS": "L", "CCDHALF": "L",
                },
                path,
                tar_member,
            )


    # Schedule only the planned targets and execute
    kind_map = default_kind_to_task()
    ctx = TaskContext(db_path=db_path, workdir=str(tmp_path / "work"), config={"raw_frame_loader": _SyntheticRawLoader()})

    def _ctx_factory():
        return ctx

    scheduled = schedule(targets=report.planned, nodes=nodes, edges=edges, kind_to_task=kind_map, task_context_factory=_ctx_factory, target_adapter=adapt_target)
    # Submit to PlanningExecutor and run serially
    ex = PlanningExecutor(max_workers=1, debug=False)
    for st in scheduled:
        ex.add_task(st.id, st.task, kind=st.kind, depends_on=st.depends_on)
    ex.run()

    # Verify that at least one artifact row exists for master_bias and for master_dark
    bias_rows = db.list_artifacts(kind="master_bias", zipcode=z, db_path=db_path, limit=10)
    dark_rows = db.list_artifacts(kind="master_dark", zipcode=z, db_path=db_path, limit=10)
    assert bias_rows or dark_rows
