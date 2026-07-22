from __future__ import annotations

from datetime import datetime

import numpy as np

from virusflow.artifacts import ArtifactService, Scope
from virusflow.core.algo_result import AlgoResult
from virusflow.core.identity import ZipCode
from virusflow.executors.planning_executor import PlanningExecutor
from virusflow.io import RawFrameData
from virusflow.planning import ReductionGraph, adapt_target, default_calibration_graph, schedule
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.mapping import default_kind_to_task


def _seed(conn, zipcode, frame_type, count):
    for index in range(count):
        exposure_id = f"20260609T{index:06d}_{frame_type}"
        conn.execute(
            "INSERT OR IGNORE INTO exposures(id, when_utc, frame_type) VALUES(?,?,?)",
            (exposure_id, "20260609", frame_type),
        )
        conn.execute(
            "INSERT INTO raw_files(exposure_id, frame_type, path, tar_member, storage_backend, amp_key) VALUES(?,?,?,?,?,?)",
            (exposure_id, frame_type, f"/{exposure_id}.fits", None, "filesystem", zipcode.key()),
        )


class _Loader:
    def load(self, path, tar_member=None):
        yy, xx = np.indices((24, 32))
        data = (100.0 + xx + yy).astype(float)
        header = {"GAIN": 1.0, "RDNOISE": 3.0, "CCDPOS": "L", "CCDHALF": "L"}
        return RawFrameData(data, header, path, tar_member)


def test_canonical_graph_declares_raw_arc_and_trace_to_wavelength_dependency():
    nodes, edges = default_calibration_graph()
    by_kind = {node.kind: node for node in nodes}
    assert set(by_kind) == {
        "master_bias", "master_dark", "master_ldls", "master_arc",
        "master_twilight", "trace_map", "wavelength_map",
    }
    assert by_kind["master_arc"].inputs_raw == ["cmp"]
    assert not by_kind["master_arc"].inputs_artifacts
    assert {(edge.src.kind, edge.dst.kind) for edge in edges} >= {
        ("master_ldls", "trace_map"),
        ("master_arc", "wavelength_map"),
        ("trace_map", "wavelength_map"),
    }


def test_one_amplifier_graph_persists_all_products_components_and_lineage(tmp_path, monkeypatch):
    zipcode = ZipCode(ifuslot="020", ifuid="001", specid="001", amp="LL", controller="A")
    database = str(tmp_path / "canonical.sqlite3")
    db.init_db(database)
    with db.connect(database) as conn:
        conn.execute(
            "INSERT INTO amplifiers(key, ifuslot, ifuid, specid, amp, controller) VALUES(?,?,?,?,?,?)",
            (zipcode.key(), zipcode.ifuslot, zipcode.ifuid, zipcode.specid, zipcode.amp, zipcode.controller),
        )
        for frame_type, count in (("zro", 25), ("drk", 20), ("flt", 30), ("cmp", 1), ("twi", 1)):
            _seed(conn, zipcode, frame_type, count)

    reference_dir = tmp_path / "Fiber_Locations" / "20260609"
    reference_dir.mkdir(parents=True)
    np.savetxt(reference_dir / "fiber_loc_001_020_001_LL.txt", np.array([[5.0, 0.0], [12.0, 0.0]]))

    import virusflow.tasks.calibs as calibs

    def fake_trace(*, master_ldls_array, trace_reference, zipcode):
        nx = master_ldls_array.shape[1]
        return AlgoResult(
            kind="trace", version="test-trace",
            arrays={
                "fiber_trace_map": np.repeat(np.array([[6.0], [14.0]]), nx, axis=1),
                "trace_sample_columns": np.array([4.0, 16.0, 28.0]),
                "sampled_trace_positions": np.array([[6.0, 6.0, 6.0], [14.0, 14.0, 14.0]]),
                "per_fiber_trace_residual_rms": np.array([0.0, 0.0]),
            },
        )

    def fake_wave(**kwargs):
        trace = kwargs["fiber_trace_map"]
        return AlgoResult(
            kind="wave", version="test-wave",
            arrays={
                "wavelength_map": np.full(trace.shape, 4500.0),
                "per_fiber_wavelength_residual_rms": np.array([0.1, 0.2]),
                "arc_identification": np.array([[100.0, 4358.3, 4358.4, 0.1, 0.05, 0.0]]),
            },
            scalars={"best_nmatch": 1, "best_rms": 0.1},
        )

    monkeypatch.setattr(calibs, "fit_fiber_traces", fake_trace)
    monkeypatch.setattr(calibs, "fit_wavelength_solution", fake_wave)

    nodes, edges = default_calibration_graph()
    graph = ReductionGraph(nodes, edges)
    _, report = graph.plan(db_path=database, scopes=[Scope(zipcode=zipcode)])
    assert {target.kind for target in report.planned} == {node.kind for node in nodes}

    context = TaskContext(
        database,
        str(tmp_path / "products"),
        {"raw_frame_loader": _Loader(), "configuration_root": str(tmp_path)},
    )
    scheduled = schedule(
        targets=report.planned,
        nodes=nodes,
        edges=edges,
        kind_to_task=default_kind_to_task(),
        task_context_factory=lambda: context,
        target_adapter=adapt_target,
    )
    executor = PlanningExecutor(max_workers=1, debug=False)
    for item in scheduled:
        executor.add_task(item.id, item.task, kind=item.kind, depends_on=item.depends_on)
    executor.run()

    service = ArtifactService(database)
    rows = {
        kind: service.select_best(kind=kind, scope=Scope(zipcode=zipcode), policy="latest")
        for kind in {node.kind for node in nodes}
    }
    assert all(rows.values())
    assert {item["name"] for item in service.describe(rows["master_bias"])["components"]} == {
        "master", "per_pixel_bias_scatter"
    }
    assert {item["name"] for item in service.describe(rows["trace_map"])["components"]} == {
        "fiber_trace_map", "trace_sample_columns", "sampled_trace_positions", "per_fiber_trace_residual_rms"
    }
    assert {item["name"] for item in service.describe(rows["wavelength_map"])["components"]} == {
        "wavelength_map", "per_fiber_wavelength_residual_rms", "arc_identification"
    }
    trace_relations = service.describe(rows["trace_map"])["relations"]
    wave_relations = service.describe(rows["wavelength_map"])["relations"]
    assert {item["parent_id"] for item in trace_relations} == {int(rows["master_ldls"]["id"])}
    assert {item["parent_id"] for item in wave_relations} == {
        int(rows["master_arc"]["id"]), int(rows["trace_map"]["id"])
    }
