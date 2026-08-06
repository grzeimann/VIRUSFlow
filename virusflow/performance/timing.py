from __future__ import annotations

"""First-class, context-local performance instrumentation.

Durations are monotonic seconds.  Phase summaries expose inclusive and
exclusive time separately; counters that do not apply are zero.  Semantic
events are retained rather than display-refresh samples.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import statistics
from threading import Lock
import time
import uuid
from typing import Any, Mapping


PHASES = (
    "raw_lookup", "raw_archive_open", "raw_member_lookup", "raw_byte_read",
    "raw_cache_wait", "fits_header_parse", "pixel_array_load", "artifact_lookup", "artifact_load",
    "compute", "serialization", "content_hash", "artifact_publish",
    "database_query", "database_transaction", "database_lock_wait", "scratch_cleanup",
    "payload_retention",
    "process_serialize", "process_deserialize", "calibration_singleflight_wait",
    "trace_chunk_sampling", "trace_fiber_modeling", "trace_residuals",
    "trace_feature_expansion", "trace_huber_fit", "trace_huber_predict",
    "trace_modeling_wait",
    "load_raw_frames", "base_reduction", "combine_frames",
)

LEGACY_BASELINE_ENV = "VIRUSFLOW_PERFORMANCE_LEGACY_BASELINE"


def legacy_baseline_enabled() -> bool:
    """Return whether diagnostic runs should emulate the pre-fix hot paths.

    This switch exists only to produce controlled before/after performance
    evidence from the same source tree.  It is deliberately not a public CLI
    option and must never change task identity or scientific algorithms.
    """

    return os.environ.get(LEGACY_BASELINE_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _source_revision() -> str | None:
    """Read the checkout revision without invoking git during a reduction."""

    git_dir = Path(__file__).resolve().parents[2] / ".git"
    try:
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            return (git_dir / head[5:]).read_text().strip() or None
        return head or None
    except OSError:
        return os.environ.get("VIRUSFLOW_GIT_COMMIT") or None


@dataclass
class PhaseValue:
    count: int = 0
    inclusive_seconds: float = 0.0
    exclusive_seconds: float = 0.0


@dataclass
class TaskTiming:
    run_id: str
    task_id: str
    kind: str
    target: str
    worker_id: str
    attempt: int
    queued_monotonic: float
    queued_at: str
    started_monotonic: float = 0.0
    started_at: str | None = None
    completed_monotonic: float = 0.0
    completed_at: str | None = None
    queue_wait_seconds: float = 0.0
    wall_seconds: float = 0.0
    thread_cpu_seconds: float = 0.0
    status: str = "queued"
    error: str | None = None
    process_id: int = field(default_factory=os.getpid)
    phases: dict[str, PhaseValue] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    identities: dict[str, set[str]] = field(default_factory=dict)
    raw_reads: list[dict[str, Any]] = field(default_factory=list)
    database_queries: list[dict[str, Any]] = field(default_factory=list)
    artifact_events: list[dict[str, Any]] = field(default_factory=list)
    raw_accesses: list[dict[str, Any]] = field(default_factory=list)
    phase_events: list[dict[str, Any]] = field(default_factory=list)
    target_identity: dict[str, str] = field(default_factory=dict)
    _stack: list[Any] = field(default_factory=list, repr=False)
    _cpu_started: float = field(default=0.0, repr=False)

    def start(self) -> None:
        self.started_monotonic = time.perf_counter()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.queue_wait_seconds = max(0.0, self.started_monotonic - self.queued_monotonic)
        self._cpu_started = getattr(time, "thread_time", time.process_time)()
        self.status = "running"

    def finish(self, status: str, error: BaseException | None = None) -> None:
        self.completed_monotonic = time.perf_counter()
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.wall_seconds = max(0.0, self.completed_monotonic - self.started_monotonic)
        self.thread_cpu_seconds = max(
            0.0, getattr(time, "thread_time", time.process_time)() - self._cpu_started
        )
        self.status = str(status)
        self.error = None if error is None else f"{type(error).__name__}: {error}"
        classified = sum(value.exclusive_seconds for value in self.phases.values())
        unclassified = max(0.0, self.wall_seconds - classified)
        if unclassified:
            value = self.phases.setdefault("compute", PhaseValue())
            value.count += 1
            value.inclusive_seconds += unclassified
            value.exclusive_seconds += unclassified

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] = int(self.counters.get(name, 0)) + int(value)

    def identity(self, category: str, value: str) -> None:
        self.identities.setdefault(category, set()).add(str(value))

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "task_id": self.task_id, "task_kind": self.kind,
            "target": self.target, "worker_id": self.worker_id, "process_id": self.process_id,
            "attempt": self.attempt, "queued_at": self.queued_at, "started_at": self.started_at,
            "completed_at": self.completed_at, "queue_wait_seconds": self.queue_wait_seconds,
            "wall_seconds": self.wall_seconds, "thread_cpu_seconds": self.thread_cpu_seconds,
            "status": self.status, "error": self.error,
            "phases": {
                name: {
                    "count": value.count,
                    "inclusive_seconds": value.inclusive_seconds,
                    "exclusive_seconds": value.exclusive_seconds,
                }
                for name, value in sorted(self.phases.items())
            },
            "counters": dict(sorted(self.counters.items())),
            "identities": {name: sorted(values) for name, values in sorted(self.identities.items())},
            "raw_reads": list(self.raw_reads), "database_queries": list(self.database_queries),
            "artifact_events": list(self.artifact_events), "raw_accesses": list(self.raw_accesses),
            "phase_events": list(self.phase_events), "target_identity": dict(self.target_identity),
        }


_CURRENT_TASK: ContextVar[TaskTiming | None] = ContextVar("virusflow_task_timing", default=None)


def current_task_timing() -> TaskTiming | None:
    return _CURRENT_TASK.get()


class _Phase:
    def __init__(self, name: str, attributes: Mapping[str, Any] | None = None) -> None:
        self.name = str(name)
        self.attributes = dict(attributes or {})
        self.task: TaskTiming | None = None
        self.started = 0.0
        self.child_seconds = 0.0

    def __enter__(self):
        self.task = current_task_timing()
        if self.task is not None:
            self.started = time.perf_counter()
            self.task._stack.append(self)
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self.task is None:
            return False
        elapsed = max(0.0, time.perf_counter() - self.started)
        if self.task._stack and self.task._stack[-1] is self:
            self.task._stack.pop()
        if self.task._stack:
            self.task._stack[-1].child_seconds += elapsed
        value = self.task.phases.setdefault(self.name, PhaseValue())
        value.count += 1
        value.inclusive_seconds += elapsed
        value.exclusive_seconds += max(0.0, elapsed - self.child_seconds)
        if self.name in {
            "raw_lookup", "raw_archive_open", "raw_member_lookup", "raw_byte_read",
            "raw_cache_wait", "fits_header_parse", "pixel_array_load", "artifact_load",
            "serialization", "content_hash", "artifact_publish", "database_transaction",
            "database_lock_wait", "scratch_cleanup",
            "payload_retention",
            "calibration_singleflight_wait",
        }:
            self.task.phase_events.append({
                "phase": self.name,
                "seconds": elapsed,
                "exclusive_seconds": max(0.0, elapsed - self.child_seconds),
                **self.attributes,
            })
        for key, amount in self.attributes.items():
            if isinstance(amount, (int, float)):
                self.task.increment(key, int(amount))
        return False


def phase(name: str, **attributes: Any) -> _Phase:
    return _Phase(name, attributes)


def measure_instrumentation_overhead(iterations: int = 100_000) -> dict[str, Any]:
    """Microbenchmark the context-local phase recorder without filesystem work."""

    count = max(1, int(iterations))
    for _ in range(min(1_000, count)):
        with phase("compute"):
            pass
    started = time.perf_counter()
    for _ in range(count):
        with phase("compute"):
            pass
    inactive = time.perf_counter() - started

    run = PerformanceRun(workers=1, configuration={"overhead_microbenchmark": True})
    run.mark_queued("overhead")
    timing, token = run.begin_task("overhead", "overhead", "microbenchmark", "main", 1)
    started = time.perf_counter()
    for _ in range(count):
        with phase("compute"):
            pass
    active = time.perf_counter() - started
    run.end_task(timing, token, "succeeded")
    run.finish("succeeded")
    incremental = max(0.0, active - inactive)
    return {
        "schema": "virusflow.performance-overhead.v1",
        "iterations": count,
        "inactive_total_seconds": inactive,
        "active_total_seconds": active,
        "inactive_nanoseconds_per_phase": inactive * 1.0e9 / count,
        "active_nanoseconds_per_phase": active * 1.0e9 / count,
        "incremental_nanoseconds_per_phase": incremental * 1.0e9 / count,
    }


class PerformanceRun:
    def __init__(
        self,
        *,
        workers: int,
        run_id: str | None = None,
        configuration: Mapping[str, Any] | None = None,
        database_paths: list[str] | None = None,
    ) -> None:
        self.run_id = run_id or uuid.uuid4().hex
        self.workers = max(1, int(workers))
        self.configuration = dict(configuration or {})
        self.configuration.setdefault("legacy_performance_baseline", legacy_baseline_enabled())
        self.database_paths = [str(Path(value).resolve()) for value in (database_paths or [])]
        self.started_monotonic = time.perf_counter()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: str | None = None
        self.completed_monotonic: float | None = None
        self.status = "running"
        self.tasks: list[TaskTiming] = []
        self.dependencies: dict[str, list[str]] = {}
        self._queued: dict[str, tuple[float, str]] = {}
        self._active_events: list[tuple[float, int]] = [(self.started_monotonic, 0)]
        self._active = 0
        self._lock = Lock()
        self.reporting: dict[str, float] = {}

    def mark_queued(self, task_id: str) -> None:
        with self._lock:
            self._queued[str(task_id)] = (
                time.perf_counter(), datetime.now(timezone.utc).isoformat()
            )

    def begin_task(
        self,
        task_id: str,
        kind: str,
        target: str,
        worker_id: str,
        attempt: int,
        target_identity: Mapping[str, Any] | None = None,
    ) -> tuple[TaskTiming, Token]:
        with self._lock:
            queued, queued_at = self._queued.get(
                str(task_id), (self.started_monotonic, self.started_at)
            )
            task = TaskTiming(
                self.run_id, str(task_id), str(kind), str(target), str(worker_id),
                int(attempt), queued, queued_at,
            )
            task.target_identity = {
                str(key): str(value) for key, value in (target_identity or {}).items()
                if value is not None
            }
            task.start()
            self.tasks.append(task)
            self._active += 1
            self._active_events.append((time.perf_counter(), self._active))
        return task, _CURRENT_TASK.set(task)

    def record_terminal(self, task_id: str, kind: str, target: str, status: str, message: str | None = None) -> TaskTiming:
        now = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            queued, queued_at = self._queued.get(str(task_id), (now, timestamp))
            task = TaskTiming(
                self.run_id, str(task_id), str(kind), str(target), "", 0,
                queued, queued_at, started_monotonic=now, started_at=timestamp,
                completed_monotonic=now, completed_at=timestamp,
                queue_wait_seconds=max(0.0, now - queued), status=str(status), error=message,
            )
            self.tasks.append(task)
        return task

    def end_task(self, task: TaskTiming, token: Token, status: str, error: BaseException | None = None) -> None:
        task.finish(status, error)
        _CURRENT_TASK.reset(token)
        with self._lock:
            self._active = max(0, self._active - 1)
            self._active_events.append((time.perf_counter(), self._active))

    def finish(self, status: str) -> None:
        self.completed_monotonic = time.perf_counter()
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.status = str(status)

    @property
    def wall_seconds(self) -> float:
        end = self.completed_monotonic if self.completed_monotonic is not None else time.perf_counter()
        return max(0.0, end - self.started_monotonic)

    @staticmethod
    def _distribution(values: list[float]) -> dict[str, float | int]:
        return {
            "count": len(values),
            "total": sum(values),
            "mean": statistics.fmean(values) if values else 0.0,
            "median": statistics.median(values) if values else 0.0,
            "p90": _percentile(values, 0.90),
            "p95": _percentile(values, 0.95),
            "max": max(values, default=0.0),
        }

    def _kind_summary(self) -> dict[str, Any]:
        grouped: dict[str, list[TaskTiming]] = {}
        for task in self.tasks:
            grouped.setdefault(task.kind, []).append(task)
        output = {}
        for kind, tasks in grouped.items():
            durations = [task.wall_seconds for task in tasks]
            phases = {
                name: sum(task.phases.get(name, PhaseValue()).exclusive_seconds for task in tasks)
                for name in PHASES
            }
            output[kind] = {
                "count": len(tasks), "total_seconds": sum(durations),
                "mean_seconds": statistics.fmean(durations),
                "median_seconds": statistics.median(durations),
                "p90_seconds": _percentile(durations, 0.90),
                "p95_seconds": _percentile(durations, 0.95), "max_seconds": max(durations),
                "queue_seconds": sum(task.queue_wait_seconds for task in tasks),
                "phases": phases,
            }
        return dict(sorted(output.items(), key=lambda item: item[1]["total_seconds"], reverse=True))

    def _raw_summary(self) -> dict[str, Any]:
        reads = [event for task in self.tasks for event in task.raw_reads]
        accesses = [event for task in self.tasks for event in task.raw_accesses]
        durations = [float(event.get("seconds", 0.0)) for event in accesses]
        physical_durations = [float(event.get("seconds", 0.0)) for event in reads]
        identities = [str(event.get("identity")) for event in reads]
        unique = set(identities)
        requested = {
            identity
            for task in self.tasks
            for identity in task.identities.get("raw_frames_requested", set())
        }
        seen: set[str] = set()
        repeated_by_kind: dict[str, int] = {}
        repeated_by_worker: dict[str, int] = {}
        for event in reads:
            identity = str(event.get("identity"))
            if identity in seen:
                kind = str(event.get("task_kind") or "unknown")
                worker = str(event.get("worker_id") or "unknown")
                repeated_by_kind[kind] = repeated_by_kind.get(kind, 0) + 1
                repeated_by_worker[worker] = repeated_by_worker.get(worker, 0) + 1
            seen.add(identity)
        raw_phases = (
            "raw_lookup", "raw_archive_open", "raw_member_lookup", "raw_byte_read",
            "raw_cache_wait", "fits_header_parse", "pixel_array_load",
        )
        operations = {
            name: self._distribution([
                float(event.get("seconds", 0.0))
                for task in self.tasks for event in task.phase_events
                if event.get("phase") == name
            ])
            for name in raw_phases
        }
        archive_groups: dict[str, list[float]] = {}
        for event in accesses:
            archive_groups.setdefault(str(event.get("path") or "unknown"), []).append(
                float(event.get("seconds", 0.0))
            )
        raw_paths = [str(event.get("path")) for event in accesses if event.get("path")]
        return {
            "requests": sum(task.counters.get("raw_frames_requested", 0) for task in self.tasks),
            "unique_frames_requested": len(requested),
            "physical_reads": len(reads), "unique_physical_frames": len(unique),
            "repeated_physical_reads": max(0, len(reads) - len(unique)),
            "repeated_reads_by_task_kind": dict(sorted(repeated_by_kind.items())),
            "repeated_reads_by_worker": dict(sorted(repeated_by_worker.items())),
            "cache_hits": sum(task.counters.get("raw_cache_hits", 0) for task in self.tasks),
            "cache_misses": sum(task.counters.get("raw_cache_misses", 0) for task in self.tasks),
            "cache_evictions": sum(task.counters.get("raw_cache_evictions", 0) for task in self.tasks),
            "bytes_read": sum(int(event.get("bytes", 0)) for event in reads),
            "archive_opens": sum(task.counters.get("archive_opens", 0) for task in self.tasks),
            "archive_handle_reuses": sum(task.counters.get("archive_handle_reuses", 0) for task in self.tasks),
            "archive_index_loads": sum(task.counters.get("archive_index_loads", 0) for task in self.tasks),
            "archive_index_builds": sum(task.counters.get("archive_index_builds", 0) for task in self.tasks),
            "archive_index_reuses": sum(task.counters.get("archive_index_reuses", 0) for task in self.tasks),
            "access_seconds": self._distribution(durations),
            "physical_access_seconds": self._distribution(physical_durations),
            "cold_access_seconds": self._distribution([
                float(event.get("seconds", 0.0)) for event in accesses if event.get("cold")
            ]),
            "warm_access_seconds": self._distribution([
                float(event.get("seconds", 0.0)) for event in accesses if not event.get("cold")
            ]),
            "cold_reads": sum(bool(event.get("cold")) for event in accesses),
            "warm_reads": sum(not bool(event.get("cold")) for event in accesses),
            "operations": operations,
            "by_archive": {
                path: self._distribution(values)
                for path, values in sorted(archive_groups.items())
            },
            "raw_data_root": (
                os.path.commonpath([str(Path(path).parent) for path in raw_paths])
                if raw_paths else None
            ),
        }

    def _database_summary(self) -> dict[str, Any]:
        events = [event for task in self.tasks for event in task.database_queries]
        per_kind: dict[str, dict[str, float | int]] = {}
        query_counts: list[float] = []
        for task in self.tasks:
            query_counts.append(float(len(task.database_queries)))
            bucket = per_kind.setdefault(task.kind, {
                "query_count": 0, "query_seconds": 0.0, "raw_catalog_queries": 0,
                "write_queries": 0,
            })
            bucket["query_count"] += len(task.database_queries)
            bucket["query_seconds"] += sum(
                float(event.get("seconds", 0.0)) for event in task.database_queries
            )
            bucket["raw_catalog_queries"] += sum(
                bool(event.get("raw_catalog")) for event in task.database_queries
            )
            bucket["write_queries"] += sum(
                bool(event.get("write")) for event in task.database_queries
            )
        transaction_events = [
            {**event, "task_id": task.task_id, "task_kind": task.kind}
            for task in self.tasks for event in task.phase_events
            if event.get("phase") == "database_transaction"
        ]
        paths = set(self.database_paths)
        return {
            "database_paths": self.database_paths,
            "shared_raw_and_artifact_registry": True if len(paths) == 1 else (False if paths else None),
            "connections_created": sum(task.counters.get("database_connections", 0) for task in self.tasks),
            "query_count": len(events),
            "query_seconds": sum(float(event.get("seconds", 0.0)) for event in events),
            "query_seconds_distribution": self._distribution([
                float(event.get("seconds", 0.0)) for event in events
            ]),
            "queries_per_task_distribution": self._distribution(query_counts),
            "transaction_seconds": sum(task.phases.get("database_transaction", PhaseValue()).exclusive_seconds for task in self.tasks),
            "lock_wait_seconds": sum(task.phases.get("database_lock_wait", PhaseValue()).exclusive_seconds for task in self.tasks),
            "raw_catalog_queries": sum(bool(event.get("raw_catalog")) for event in events),
            "write_queries": sum(bool(event.get("write")) for event in events),
            "tables_touched": sorted({
                table for event in events for table in event.get("tables", [])
            }),
            "by_task_kind": dict(sorted(per_kind.items())),
            "slowest_queries": sorted(events, key=lambda event: float(event.get("seconds", 0.0)), reverse=True)[:20],
            "slowest_transactions": sorted(
                transaction_events,
                key=lambda event: float(event.get("seconds", 0.0)), reverse=True,
            )[:20],
        }

    def _utilization(self, end: float) -> dict[str, float]:
        events = list(self._active_events) + [(end, self._active)]
        worker_seconds = 0.0
        for (start, active), (stop, _) in zip(events, events[1:]):
            worker_seconds += max(0.0, stop - start) * active
        wall = max(1.0e-12, end - self.started_monotonic)
        return {
            "active_worker_seconds": worker_seconds,
            "mean_active_workers": worker_seconds / wall,
            "worker_utilization_fraction": worker_seconds / (wall * self.workers),
            "parallel_efficiency": worker_seconds / (wall * self.workers),
        }

    def _artifact_summary(self) -> dict[str, Any]:
        events = [event for task in self.tasks for event in task.artifact_events]
        loads = [event for event in events if event.get("operation") == "load"]
        writes = [event for event in events if event.get("operation") == "write"]
        load_keys = [
            f"{event.get('artifact_id')}:{event.get('component')}" for event in loads
        ]
        model_keys = [
            key for key, event in zip(load_keys, loads)
            if str(event.get("lifecycle") or "") == "model"
        ]
        slowest = lambda phase_name: sorted(
            (
                {
                    "task_id": task.task_id, "task_kind": task.kind,
                    "seconds": float(event.get("seconds", 0.0)),
                }
                for task in self.tasks for event in task.phase_events
                if event.get("phase") == phase_name
            ),
            key=lambda event: event["seconds"], reverse=True,
        )[:20]
        return {
            "loaded": len(loads),
            "unique_components_loaded": len(set(load_keys)),
            "repeated_component_loads": max(0, len(load_keys) - len(set(load_keys))),
            "model_component_loads": len(model_keys),
            "repeated_model_component_loads": max(0, len(model_keys) - len(set(model_keys))),
            "bytes_loaded": sum(int(event.get("bytes", 0)) for event in loads),
            "written": len(writes),
            "bytes_written": sum(int(event.get("bytes", 0)) for event in writes),
            "serialization_seconds": sum(
                task.phases.get("serialization", PhaseValue()).exclusive_seconds
                for task in self.tasks
            ),
            "hash_seconds": sum(
                task.phases.get("content_hash", PhaseValue()).exclusive_seconds
                for task in self.tasks
            ),
            "publication_seconds": sum(
                task.phases.get("artifact_publish", PhaseValue()).exclusive_seconds
                for task in self.tasks
            ),
            "slowest_serializations": slowest("serialization"),
            "slowest_hashes": slowest("content_hash"),
            "slowest_publications": slowest("artifact_publish"),
            "events": events,
        }

    def _critical_path(self) -> dict[str, Any]:
        durations: dict[str, float] = {}
        for task in self.tasks:
            durations[task.task_id] = max(durations.get(task.task_id, 0.0), task.wall_seconds)
        best: dict[str, tuple[float, list[str]]] = {}
        visiting: set[str] = set()

        def visit(node: str) -> tuple[float, list[str]]:
            if node in best:
                return best[node]
            if node in visiting:
                return 0.0, []
            visiting.add(node)
            candidates = [visit(dep) for dep in self.dependencies.get(node, [])]
            prefix = max(candidates, default=(0.0, []), key=lambda value: value[0])
            result = (prefix[0] + durations.get(node, 0.0), [*prefix[1], node])
            visiting.remove(node)
            best[node] = result
            return result

        result = max((visit(node) for node in durations), default=(0.0, []), key=lambda value: value[0])
        return {"duration_seconds": result[0], "task_ids": result[1]}

    def report(self) -> dict[str, Any]:
        end = self.completed_monotonic if self.completed_monotonic is not None else time.perf_counter()
        wall = max(0.0, end - self.started_monotonic)
        tasks = [task.as_dict() for task in self.tasks]
        phase_totals = {
            name: sum(task.phases.get(name, PhaseValue()).exclusive_seconds for task in self.tasks)
            for name in PHASES
        }
        return {
            "schema": "virusflow.performance.v1", "run_id": self.run_id,
            "status": self.status, "started_at": self.started_at, "completed_at": self.completed_at,
            "wall_seconds": wall, "workers_configured": self.workers,
            "host": {"hostname": platform.node(), "platform": platform.platform(), "python": platform.python_version()},
            "software_revision": _source_revision(),
            "configuration": self.configuration,
            "counts": {
                state: sum(task.status == state for task in self.tasks)
                for state in ("succeeded", "failed", "blocked", "cached", "skipped")
            },
            "task_cpu_seconds": sum(task.thread_cpu_seconds for task in self.tasks),
            "task_kind_summary": self._kind_summary(), "phase_totals": phase_totals,
            "raw_io": self._raw_summary(), "database": self._database_summary(),
            "artifacts": self._artifact_summary(),
            "worker_utilization": self._utilization(end), "critical_path": self._critical_path(),
            "slowest_tasks": sorted(tasks, key=lambda task: task["wall_seconds"], reverse=True)[:20],
            "reporting": dict(self.reporting),
            "tasks": tasks,
        }

    def write(self, path: str | Path) -> tuple[Path, Path]:
        json_path = Path(path)
        if json_path.suffix.lower() != ".json":
            json_path = json_path / "performance.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        report = self.report()
        self.reporting["report_generation_seconds"] = time.perf_counter() - started
        report["reporting"] = dict(self.reporting)
        started = time.perf_counter()
        json_text = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
        self.reporting["json_serialization_seconds"] = time.perf_counter() - started
        report["reporting"] = dict(self.reporting)
        json_text = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
        started = time.perf_counter()
        json_path.write_text(json_text)
        self.reporting["json_publication_seconds"] = time.perf_counter() - started
        md_path = json_path.with_suffix(".md")
        lines = [
            "# VIRUSFlow performance report", "", f"Run `{self.run_id}`: **{self.status}**",
            "", f"Wall: {report['wall_seconds']:.3f} s; workers: {self.workers}; "
            f"utilization: {100 * report['worker_utilization']['worker_utilization_fraction']:.1f}%.",
            "", "| Task kind | Count | Total | Mean | Median | p90 | p95 | Max | Queue | Raw I/O | Artifact load | DB | Compute | Serialize | Hash | Publish | Cleanup |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for kind, value in report["task_kind_summary"].items():
            phases = value["phases"]
            raw = phases["raw_archive_open"] + phases["raw_member_lookup"] + phases["raw_byte_read"] + phases["fits_header_parse"] + phases["pixel_array_load"]
            lines.append(
                f"| {kind} | {value['count']} | {value['total_seconds']:.3f} | {value['mean_seconds']:.3f} | "
                f"{value['median_seconds']:.3f} | {value['p90_seconds']:.3f} | {value['p95_seconds']:.3f} | "
                f"{value['max_seconds']:.3f} | {value['queue_seconds']:.3f} | {raw:.3f} | "
                f"{phases['artifact_load']:.3f} | {phases['database_query']:.3f} | {phases['compute']:.3f} | "
                f"{phases['serialization']:.3f} | {phases['content_hash']:.3f} | "
                f"{phases['artifact_publish']:.3f} | {phases['scratch_cleanup']:.3f} |"
            )
        lines.extend([
            "", "## Raw I/O", "", f"```json\n{json.dumps(report['raw_io'], indent=2, sort_keys=True)}\n```",
            "", "## Database", "", f"```json\n{json.dumps(report['database'], indent=2, sort_keys=True)}\n```",
            "", "## Critical path", "", f"```json\n{json.dumps(report['critical_path'], indent=2, sort_keys=True)}\n```",
            "", "## Artifact serialization and publication", "", f"```json\n{json.dumps(report['artifacts'], indent=2, sort_keys=True)}\n```",
            "", "## Reporting overhead", "", f"```json\n{json.dumps(self.reporting, indent=2, sort_keys=True)}\n```",
        ])
        started = time.perf_counter()
        md_path.write_text("\n".join(lines) + "\n")
        self.reporting["markdown_publication_seconds"] = time.perf_counter() - started
        report["reporting"] = dict(self.reporting)
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
        return md_path, json_path

    def persist(self, db_path: str) -> None:
        """Persist one compact run summary and one semantic record per attempt."""

        from ..registry import database as db

        report = self.report()
        compact = dict(report)
        compact.pop("tasks", None)
        with db.connect(db_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO performance_runs(run_id,schema_version,status,started_at,completed_at,workers,wall_seconds,summary_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    self.run_id, report["schema"], self.status, self.started_at,
                    self.completed_at, self.workers, report["wall_seconds"],
                    json.dumps(compact, sort_keys=True, default=str),
                ),
            )
            connection.executemany(
                "INSERT OR REPLACE INTO performance_tasks(run_id,task_id,attempt,task_kind,target,worker_id,status,wall_seconds,timing_json) VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        self.run_id, task.task_id, task.attempt, task.kind, task.target,
                        task.worker_id, task.status, task.wall_seconds,
                        json.dumps(task.as_dict(), sort_keys=True, default=str),
                    )
                    for task in self.tasks
                ],
            )
