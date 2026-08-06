from __future__ import annotations

from io import StringIO
import json
from threading import Barrier

import pytest

from virusflow.executors.planning_executor import PlanningExecutor, WorkflowExecutionError
from virusflow.executors.progress import GraphProgress


class _TTY(StringIO):
    def isatty(self):
        return True


class _Task:
    def __init__(self, value, barrier=None):
        self.value = value
        self.barrier = barrier

    def run(self, inputs):
        if self.barrier is not None:
            self.barrier.wait(timeout=3)
        return (self.value, tuple(sorted(inputs)))


def test_progress_enabled_by_default_and_tty_updates_in_place():
    output = _TTY()
    executor = PlanningExecutor(max_workers=1, progress_stream=output, progress_interval=0.05)
    assert executor.reporter.enabled
    executor.add_task("a", _Task("A"), kind="calibration", target="zipcode=z1")
    result = executor.run()
    assert result == {"a": ("A", ())}
    rendered = output.getvalue()
    assert "\x1b[" in rendered
    assert "\x1b[J" in rendered
    assert "VIRUSFlow progress" in rendered
    assert "Task kinds" in rendered
    assert "1/1" in rendered
    assert rendered.endswith("\n")
    assert executor.execution_stats["progress"]["finalized"] is True


def test_tty_progress_lines_are_bounded_to_the_terminal_width(monkeypatch):
    class NarrowTTY(_TTY):
        def fileno(self):
            return 123

    monkeypatch.setattr(
        "virusflow.executors.progress.os.get_terminal_size",
        lambda _fd: __import__("os").terminal_size((48, 10)),
    )
    output = NarrowTTY()
    executor = PlanningExecutor(max_workers=1, progress_stream=output)
    executor.add_task(
        "a",
        _Task("A"),
        kind="calibration-kind-with-a-long-name",
        target="zipcode=an-extremely-long-target-that-would-wrap",
    )
    executor.run()

    printable_lines = [
        line
        for line in output.getvalue().replace("\r", "").splitlines()
        if not line.startswith("\x1b[")
    ]
    assert printable_lines
    assert max(map(len, printable_lines)) <= 47


def test_plain_and_structured_progress_have_complete_graph_counters(tmp_path):
    output = StringIO()
    structured = tmp_path / "progress.jsonl"
    barrier = Barrier(2)
    executor = PlanningExecutor(
        max_workers=2,
        progress_mode="plain",
        progress_interval=0.05,
        progress_stream=output,
        progress_path=str(structured),
    )
    executor.add_observed("cached", kind="master_bias", state="cached", target="z1")
    executor.add_observed("skipped", kind="master_dark", state="skipped", target="z2")
    executor.add_task("a", _Task("A", barrier), kind="science")
    executor.add_task("b", _Task("B", barrier), kind="science")
    executor.add_task("c", _Task("C"), kind="observation", depends_on=["a", "b"])
    results = executor.run()
    assert results["c"] == ("C", ("a", "b"))
    assert "\x1b" not in output.getvalue()
    assert "cached=1 skipped=1" in output.getvalue()
    stats = executor.execution_stats
    assert stats["total"] == 5
    assert stats["succeeded"] == 3
    assert stats["cached"] == stats["skipped"] == 1
    events = [json.loads(line) for line in structured.read_text().splitlines()]
    assert events and all(event["schema"] == "virusflow.progress.v1" for event in events)
    assert events[-1]["event"] == "finished"
    assert events[-1]["progress"]["completed"] == 5
    assert events[-1]["progress"]["finalized"] is True


def test_json_progress_retries_failures_and_blocked_dependencies():
    output = StringIO()

    class Flaky:
        attempts = 0

        def run(self, inputs):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("transient")
            return "recovered"

    recovered = PlanningExecutor(
        max_workers=1,
        max_retries=1,
        progress_mode="json",
        progress_interval=0.05,
        progress_stream=output,
    )
    recovered.add_task("flaky", Flaky(), kind="test")
    assert recovered.run()["flaky"] == "recovered"
    assert recovered.execution_stats["retried"] == 1
    for line in output.getvalue().splitlines():
        json.loads(line)

    events = []

    class Fails:
        def run(self, inputs):
            raise ValueError("root cause")

    class Blocked:
        def run(self, inputs):
            events.append("incorrectly-ran")

    failed = PlanningExecutor(max_workers=2, progress=False)
    failed.add_task("bad", Fails(), kind="test")
    failed.add_task("dependent", Blocked(), kind="test", depends_on=["bad"])
    with pytest.raises(WorkflowExecutionError, match="1 failed, 1 blocked") as exc:
        failed.run()
    assert isinstance(exc.value.__cause__, ValueError)
    assert not events
    assert failed.execution_stats["failed"] == 1
    assert failed.execution_stats["blocked"] == 1


def test_progress_does_not_change_task_results_or_identity():
    def execute(enabled):
        executor = PlanningExecutor(max_workers=1, progress=enabled)
        executor.add_task("stable-id", _Task(7), kind="stable")
        return executor.run(), tuple(executor._nodes)

    assert execute(False) == execute(True)


def test_kind_timing_and_eta_are_bounded_by_remaining_dependency_path():
    progress = GraphProgress(2)
    progress.add_node("a", kind="bias")
    progress.add_node("b", kind="bias", dependencies=("a",))
    progress.add_node("c", kind="wave", dependencies=("b",))
    progress.transition("a", "running", worker_id="worker")
    progress.record_timing("a", {
        "wall_seconds": 2.0, "raw_reads": [], "phases": {},
    })
    progress.transition("a", "succeeded")
    snapshot = progress.snapshot()
    assert snapshot.task_kind_timing["bias"]["mean_seconds"] == 2.0
    assert snapshot.task_kind_timing["bias"]["median_seconds"] == 2.0
    assert snapshot.task_kind_timing["bias"]["p95_seconds"] == 2.0
    assert snapshot.eta_confidence == "low"
    assert snapshot.eta_seconds == 4.0
    assert snapshot.timing_summary["estimated_remaining_critical_path"] == ["b", "c"]
