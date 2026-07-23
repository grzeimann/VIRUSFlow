from __future__ import annotations

import json
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from virusflow.executors.planning_executor import PlanningExecutor
from virusflow.io.raw import RawFrameLoader
from virusflow.performance.timing import (
    PerformanceRun, current_task_timing, measure_instrumentation_overhead, phase,
)
from virusflow.performance.comparison import _compare_values, compare_performance_reports
from virusflow.registry import database as db
from virusflow.core.identity import ZipCode
from virusflow.tasks.base import TaskContext
from virusflow.tasks.exposure import ExposureTask


class _Task:
    kind = "measured"

    def __init__(self, value=1, fail=False, callback=None):
        self.value, self.fail, self.callback = value, fail, callback

    def run(self, inputs):
        if self.callback:
            self.callback()
        if self.fail:
            raise RuntimeError("measured failure")
        return self.value + sum(inputs.values())


def test_nested_phases_have_inclusive_and_exclusive_time():
    run = PerformanceRun(workers=1)
    run.mark_queued("task")
    timing, token = run.begin_task("task", "kind", "target", "worker", 1)
    with phase("compute"):
        time.sleep(0.002)
        with phase("artifact_lookup"):
            time.sleep(0.002)
    run.end_task(timing, token, "succeeded")
    assert timing.phases["compute"].inclusive_seconds >= timing.phases["compute"].exclusive_seconds >= 0
    assert timing.phases["artifact_lookup"].exclusive_seconds > 0
    assert timing.wall_seconds >= timing.phases["compute"].inclusive_seconds


def test_executor_records_success_failure_kind_summary_and_critical_path(tmp_path: Path):
    executor = PlanningExecutor(
        max_workers=2, progress=False, raise_on_failure=False,
        performance_path=str(tmp_path / "performance.json"),
    )
    executor.add_task("a", _Task(2, callback=lambda: time.sleep(0.002)), kind="bias")
    executor.add_task("b", _Task(3), kind="dark")
    executor.add_task(
        "c", _Task(fail=True, callback=lambda: time.sleep(0.002)),
        kind="trace", depends_on=["a"],
    )
    executor.add_task("d", _Task(), kind="wave", depends_on=["c"])
    executor.run()
    report = executor.performance_report
    assert report["schema"] == "virusflow.performance.v1"
    assert report["counts"]["failed"] == 1
    assert report["critical_path"]["task_ids"][-1] == "c"
    assert report["task_kind_summary"]["bias"]["count"] == 1
    assert json.loads((tmp_path / "performance.json").read_text())["run_id"] == report["run_id"]
    assert (tmp_path / "performance.md").exists()


def test_raw_and_database_instrumentation_counts_cache_reuse(tmp_path: Path):
    raw = tmp_path / "raw.fits"
    fits.PrimaryHDU(np.arange(12, dtype=np.int16).reshape(3, 4)).writeto(raw)
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))

    def work():
        loader = RawFrameLoader()
        first = loader.load(str(raw))
        second = loader.load(str(raw))
        assert np.array_equal(first.data, second.data)
        db.list_exposures(str(database))

    executor = PlanningExecutor(max_workers=1, progress=False)
    task = _Task(callback=work)
    task.ctx = type("Context", (), {"db_path": str(database)})()
    executor.add_task("raw", task, kind="raw_test")
    executor.run()
    report = executor.performance_report
    assert report["raw_io"]["requests"] == 2
    assert report["raw_io"]["physical_reads"] == 1
    assert report["raw_io"]["unique_physical_frames"] == 1
    assert report["raw_io"]["repeated_physical_reads"] == 0
    assert report["raw_io"]["cache_hits"] == 1
    assert report["raw_io"]["cache_misses"] == 1
    assert report["raw_io"]["bytes_read"] >= raw.stat().st_size
    assert report["database"]["query_count"] >= 1


def test_legacy_diagnostic_mode_exposes_repeated_physical_reads(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIRUSFLOW_PERFORMANCE_LEGACY_BASELINE", "1")
    raw = tmp_path / "raw.fits"
    fits.PrimaryHDU(np.arange(12, dtype=np.int16).reshape(3, 4)).writeto(raw)

    def work():
        loader = RawFrameLoader()
        loader.load(str(raw))
        loader.load(str(raw))

    executor = PlanningExecutor(max_workers=1, progress=False)
    executor.add_task("raw", _Task(callback=work), kind="raw_test")
    executor.run()
    report = executor.performance_report
    assert report["configuration"]["legacy_performance_baseline"] is True
    assert report["raw_io"]["requests"] == 2
    assert report["raw_io"]["physical_reads"] == 2
    assert report["raw_io"]["unique_physical_frames"] == 1
    assert report["raw_io"]["repeated_physical_reads"] == 1
    assert report["raw_io"]["cache_hits"] == 0


def test_timing_does_not_change_result_or_identity():
    plain = _Task(7).run({})
    executor = PlanningExecutor(max_workers=1, progress=False)
    executor.add_task("stable-id", _Task(7), kind="science")
    result = executor.run()["stable-id"]
    assert result == plain
    assert executor.performance_report["tasks"][0]["task_id"] == "stable-id"


def test_saved_report_and_scientific_value_comparison():
    comparison = compare_performance_reports(
        {
            "wall_seconds": 10.0, "workers_configured": 1,
            "worker_utilization": {"worker_utilization_fraction": 1.0},
            "phase_totals": {"compute": 8.0},
        },
        {
            "wall_seconds": 8.0, "workers_configured": 4,
            "worker_utilization": {"worker_utilization_fraction": 0.8},
            "phase_totals": {"compute": 6.0},
        },
    )
    assert comparison["wall_seconds"]["change_fraction"] == pytest.approx(-0.2)
    assert comparison["phase_seconds"]["compute"]["after"] == 6.0
    assert comparison["worker_utilization"]["before"]["worker_utilization_fraction"] == 1.0
    assert comparison["worker_utilization"]["after"]["worker_utilization_fraction"] == 0.8
    assert _compare_values(np.array([1.0, np.nan]), np.array([1.0, np.nan]))["equal"]
    mismatch = _compare_values(np.array([1.0]), np.array([1.25]))
    assert not mismatch["equal"]
    assert mismatch["maximum_absolute_difference"] == 0.25
    overhead = measure_instrumentation_overhead(100)
    assert overhead["iterations"] == 100
    assert overhead["active_nanoseconds_per_phase"] >= 0


def test_concurrent_exposures_singleflight_missing_calibrations(monkeypatch, tmp_path):
    import virusflow.tasks.exposure as exposure_module

    calls = 0
    published = {}
    state_lock = __import__("threading").Lock()

    class FakeService:
        def __init__(self, path):
            self.adapter = self

        def select_best(self, *, kind, scope, at_time):
            with state_lock:
                return published.get((kind, scope.zipcode.key()))

        def get_row(self, artifact_id):
            with state_lock:
                return next(row for row in published.values() if row["id"] == artifact_id)

    class FakeCalibration:
        artifact_name = "master_bias"

        def __init__(self, ctx, target):
            self.target = target

        def run(self, inputs):
            nonlocal calls
            time.sleep(0.02)
            with state_lock:
                calls += 1
                artifact = SimpleNamespace(id=calls)
                published[("master_bias", self.target.zipcode.key())] = {
                    "id": artifact.id, "kind": "master_bias",
                }
            return {self.artifact_name: artifact}

    monkeypatch.setattr(exposure_module, "ArtifactService", FakeService)
    monkeypatch.setattr(exposure_module, "CALIBRATION_TASKS", ((FakeCalibration, "master_bias"),))
    zipcode = ZipCode("013", "043", "412", "LL", "S/N 0021")
    context = TaskContext(str(tmp_path / "registry.sqlite3"), str(tmp_path), {})
    task = ExposureTask(context, target=SimpleNamespace())
    instants = [
        __import__("datetime").datetime(2026, 6, 9, 3, 16, second)
        for second in (49, 50, 51)
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda at: task._ensure_calibrations([zipcode], at), instants))
    assert calls == 1
    assert all("master_bias" in result[0][zipcode.key()] for result in results)


def test_interrupted_partial_report_is_writable(tmp_path: Path):
    run = PerformanceRun(workers=4)
    run.mark_queued("partial")
    timing, token = run.begin_task("partial", "bias", "zipcode=x", "worker", 1)
    assert current_task_timing() is timing
    run.end_task(timing, token, "failed", KeyboardInterrupt())
    run.finish("interrupted")
    _, path = run.write(tmp_path)
    report = json.loads(path.read_text())
    assert report["status"] == "interrupted"
    assert report["tasks"][0]["error"].startswith("KeyboardInterrupt")


def test_executor_worker_interruption_persists_partial_report(tmp_path: Path):
    class Interrupted:
        kind = "interrupted"

        def run(self, inputs):
            raise KeyboardInterrupt("stop")

    path = tmp_path / "interrupted.json"
    executor = PlanningExecutor(
        max_workers=1, progress=False, performance_path=str(path)
    )
    executor.add_task("interrupted", Interrupted(), kind="interrupted")
    with pytest.raises(Exception) as exc:
        executor.run()
    assert isinstance(exc.value.__cause__, KeyboardInterrupt)
    report = json.loads(path.read_text())
    assert report["status"] == "interrupted"
    assert report["counts"]["failed"] == 1
