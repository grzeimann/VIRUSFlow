from __future__ import annotations

"""Graph-aware progress state and terminal/batch renderers."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time
import statistics
from typing import IO, Any, Mapping


TERMINAL_STATES = frozenset({"succeeded", "failed", "blocked", "cached", "skipped"})
VALID_STATES = frozenset({"pending", "running", *TERMINAL_STATES})


@dataclass
class ProgressNode:
    node_id: str
    kind: str
    target: str
    state: str = "pending"
    attempts: int = 0
    worker_id: str | None = None
    message: str | None = None
    timing: dict[str, Any] | None = None
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProgressSnapshot:
    sequence: int
    total: int
    completed: int
    pending: int
    running: int
    succeeded: int
    failed: int
    blocked: int
    cached: int
    skipped: int
    retried: int
    active: tuple[str, ...]
    elapsed_seconds: float
    completion_rate_per_second: float | None
    eta_seconds: float | None
    workers_active: int
    workers_configured: int
    finalized: bool
    task_kind_timing: Mapping[str, Any]
    timing_summary: Mapping[str, Any]
    eta_confidence: str

    @property
    def fraction(self) -> float:
        return 1.0 if self.total == 0 else self.completed / self.total

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fraction"] = self.fraction
        return payload


class GraphProgress:
    """Thread-safe state for one execution graph.

    The unit of work is one unique graph node. Cached and skipped planner
    targets may be registered as already-terminal nodes so they are visible
    without being executed or double-counted.
    """

    def __init__(self, workers: int, *, clock=time.monotonic) -> None:
        self.workers = max(1, int(workers))
        self._clock = clock
        self._started = float(clock())
        self._nodes: dict[str, ProgressNode] = {}
        self._sequence = 0
        self._retried = 0
        self._completion_times: list[float] = []
        self._finalized = False
        self._lock = Lock()

    def add_node(
        self,
        node_id: str,
        *,
        kind: str,
        target: str | None = None,
        state: str = "pending",
        message: str | None = None,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        if state not in VALID_STATES:
            raise ValueError(f"invalid progress state: {state}")
        with self._lock:
            if node_id in self._nodes:
                raise ValueError(f"progress node already registered: {node_id}")
            self._nodes[node_id] = ProgressNode(
                str(node_id), str(kind), str(target or node_id), state=state, message=message,
                dependencies=tuple(str(value) for value in dependencies),
            )
            self._sequence += 1
            if state in TERMINAL_STATES:
                self._completion_times.append(float(self._clock()))

    def transition(
        self,
        node_id: str,
        state: str,
        *,
        worker_id: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        if state not in VALID_STATES:
            raise ValueError(f"invalid progress state: {state}")
        with self._lock:
            node = self._nodes[node_id]
            previous = node.state
            if previous in TERMINAL_STATES:
                raise RuntimeError(f"terminal progress node cannot transition: {node_id}/{previous}")
            if state == "running":
                node.attempts += 1
            if previous == "running" and state == "pending":
                self._retried += 1
            node.state = state
            node.worker_id = worker_id if state == "running" else None
            node.message = message
            self._sequence += 1
            if state in TERMINAL_STATES:
                self._completion_times.append(float(self._clock()))
            return {
                "event": "state_changed",
                "sequence": self._sequence,
                "node_id": node.node_id,
                "kind": node.kind,
                "target": node.target,
                "previous_state": previous,
                "state": state,
                "attempt": node.attempts,
                "worker_id": worker_id,
                "message": message,
            }

    def record_timing(self, node_id: str, timing: Mapping[str, Any]) -> None:
        with self._lock:
            self._nodes[node_id].timing = dict(timing)
            self._sequence += 1

    def finalize(self) -> dict[str, Any]:
        with self._lock:
            nonterminal = [node.node_id for node in self._nodes.values() if node.state not in TERMINAL_STATES]
            if nonterminal:
                raise RuntimeError(f"cannot finalize progress with nonterminal nodes: {nonterminal[:5]}")
            self._finalized = True
            self._sequence += 1
            return {"event": "finished", "sequence": self._sequence}

    def snapshot(self) -> ProgressSnapshot:
        with self._lock:
            now = float(self._clock())
            counts = {state: 0 for state in VALID_STATES}
            for node in self._nodes.values():
                counts[node.state] += 1
            total = len(self._nodes)
            completed = sum(counts[state] for state in TERMINAL_STATES)
            active = tuple(
                f"{node.kind}:{node.target}"
                for node in self._nodes.values()
                if node.state == "running"
            )
            recent = [stamp for stamp in self._completion_times if now - stamp <= 120.0]
            rate = None
            if len(recent) >= 2:
                span = max(recent[-1] - recent[0], 1.0e-9)
                rate = (len(recent) - 1) / span
            remaining = total - completed
            kind_timings: dict[str, Any] = {}
            estimated_work = 0.0
            observed_kinds = 0
            medians_by_kind: dict[str, float] = {}
            all_measured: list[float] = []
            for kind in sorted({node.kind for node in self._nodes.values()}):
                matching = [node for node in self._nodes.values() if node.kind == kind]
                measured = [float(node.timing.get("wall_seconds", 0.0)) for node in matching if node.timing]
                running_kind = sum(node.state == "running" for node in matching)
                completed_kind = sum(node.state in TERMINAL_STATES for node in matching)
                remaining_kind = len(matching) - completed_kind
                if measured:
                    observed_kinds += 1
                    median = statistics.median(measured)
                    medians_by_kind[kind] = median
                    all_measured.extend(measured)
                    estimated_work += remaining_kind * median
                else:
                    median = None
                kind_timings[kind] = {
                    "total": len(matching), "completed": completed_kind,
                    "running": running_kind, "waiting": max(0, remaining_kind - running_kind),
                    "mean_seconds": statistics.fmean(measured) if measured else None,
                    "median_seconds": median,
                    "p95_seconds": self._percentile(measured, 0.95) if measured else None,
                }
            fallback = statistics.median(all_measured) if all_measured else 0.0
            if fallback:
                for kind, item in kind_timings.items():
                    if kind not in medians_by_kind:
                        estimated_work += int(item["waiting"]) * fallback

            path_cache: dict[str, tuple[float, list[str]]] = {}
            visiting: set[str] = set()

            def remaining_path(node_id: str) -> tuple[float, list[str]]:
                if node_id in path_cache:
                    return path_cache[node_id]
                if node_id in visiting:
                    return 0.0, []
                visiting.add(node_id)
                node = self._nodes[node_id]
                prefixes = [remaining_path(dep) for dep in node.dependencies if dep in self._nodes]
                prefix = max(prefixes, default=(0.0, []), key=lambda item: item[0])
                duration = 0.0
                if node.state not in TERMINAL_STATES:
                    duration = medians_by_kind.get(node.kind, fallback)
                result = (prefix[0] + duration, [*prefix[1], node_id] if duration else prefix[1])
                visiting.remove(node_id)
                path_cache[node_id] = result
                return result

            critical_remaining = max(
                (remaining_path(node_id) for node_id in self._nodes),
                default=(0.0, []), key=lambda item: item[0],
            )
            work_eta = estimated_work / self.workers if observed_kinds and estimated_work else 0.0
            eta = max(work_eta, critical_remaining[0]) if observed_kinds and remaining else None
            confidence = "none" if not observed_kinds else ("low" if observed_kinds < len(kind_timings) else "medium")
            timing_records = [node.timing for node in self._nodes.values() if node.timing]
            raw_durations = [
                float(event.get("seconds", 0.0)) for timing in timing_records
                for event in timing.get("raw_reads", [])
            ]
            task_wall = sum(float(timing.get("wall_seconds", 0.0)) for timing in timing_records)
            phase_total = lambda name: sum(
                float((timing.get("phases", {}).get(name) or {}).get("exclusive_seconds", 0.0))
                for timing in timing_records
            )
            timing_summary = {
                "raw_access_mean_seconds": statistics.fmean(raw_durations) if raw_durations else None,
                "raw_access_median_seconds": statistics.median(raw_durations) if raw_durations else None,
                "raw_access_p95_seconds": self._percentile(raw_durations, 0.95) if raw_durations else None,
                "database_share": (phase_total("database_query") / task_wall) if task_wall else 0.0,
                "publication_share": (phase_total("artifact_publish") / task_wall) if task_wall else 0.0,
                "compute_share": (phase_total("compute") / task_wall) if task_wall else 0.0,
                "estimated_remaining_critical_path_seconds": critical_remaining[0],
                "estimated_remaining_critical_path": critical_remaining[1],
            }
            return ProgressSnapshot(
                sequence=self._sequence,
                total=total,
                completed=completed,
                pending=counts["pending"],
                running=counts["running"],
                succeeded=counts["succeeded"],
                failed=counts["failed"],
                blocked=counts["blocked"],
                cached=counts["cached"],
                skipped=counts["skipped"],
                retried=self._retried,
                active=active,
                elapsed_seconds=max(0.0, now - self._started),
                completion_rate_per_second=rate,
                eta_seconds=eta,
                workers_active=counts["running"],
                workers_configured=self.workers,
                finalized=self._finalized,
                task_kind_timing=kind_timings,
                timing_summary=timing_summary,
                eta_confidence=confidence,
            )

    @staticmethod
    def _percentile(values: list[float], q: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        position = (len(ordered) - 1) * q
        lower, upper = int(position), min(len(ordered) - 1, int(position) + 1)
        fraction = position - lower
        return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


class ProgressReporter:
    """Render graph snapshots without coupling execution to a UI library."""

    def __init__(
        self,
        progress: GraphProgress,
        *,
        enabled: bool = True,
        mode: str = "auto",
        interval_seconds: float = 30.0,
        stream: IO[str] | None = None,
        structured_path: str | Path | None = None,
    ) -> None:
        self.progress = progress
        self.enabled = bool(enabled)
        self.stream = stream or sys.stdout
        requested = str(mode or "auto").lower()
        if requested not in {"auto", "tty", "plain", "json"}:
            raise ValueError("progress mode must be auto, tty, plain, or json")
        is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.mode = ("tty" if is_tty else "plain") if requested == "auto" else requested
        self.interval_seconds = max(0.05, float(interval_seconds))
        self.structured_path = Path(structured_path) if structured_path else None
        self._last_emit = 0.0
        self._stop = Event()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._tty_line_count = 0

    def start(self) -> None:
        if not self.enabled:
            return
        self.emit({"event": "started", "sequence": 0}, force=True)
        if self.mode != "tty":
            self._thread = Thread(target=self._periodic, name="virusflow-progress", daemon=True)
            self._thread.start()

    def _periodic(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.emit({"event": "heartbeat"}, force=True)

    @staticmethod
    def _duration(value: float | None) -> str:
        if value is None:
            return "--"
        seconds = max(0, int(value))
        return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"

    def _payload(self, event: Mapping[str, Any], snapshot: ProgressSnapshot) -> dict[str, Any]:
        return {
            "schema": "virusflow.progress.v1",
            "timestamp": time.time(),
            **dict(event),
            "progress": snapshot.as_dict(),
        }

    def _write_structured(self, payload: Mapping[str, Any]) -> None:
        if self.structured_path is None:
            return
        self.structured_path.parent.mkdir(parents=True, exist_ok=True)
        with self.structured_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")

    def _terminal_size(self) -> os.terminal_size:
        try:
            return os.get_terminal_size(self.stream.fileno())
        except (AttributeError, OSError):
            return os.terminal_size((100, 24))

    @staticmethod
    def _fit_line(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        if width <= 1:
            return text[:width]
        return text[: width - 1] + "…"

    def _tty_lines(self, snapshot: ProgressSnapshot) -> list[str]:
        size = self._terminal_size()
        width = max(10, size.columns - 1)
        height = max(4, size.lines - 2)
        rate = (
            "--" if snapshot.completion_rate_per_second is None
            else f"{snapshot.completion_rate_per_second:.2f}/s"
        )
        percent = 100.0 * snapshot.fraction
        lines = [
            (
                f"VIRUSFlow progress  {snapshot.completed}/{snapshot.total} ({percent:5.1f}%)"
                f"  elapsed {self._duration(snapshot.elapsed_seconds)}"
                f"  eta {self._duration(snapshot.eta_seconds)} ({snapshot.eta_confidence})"
            ),
            (
                f"Tasks  pending {snapshot.pending}  running {snapshot.running}"
                f"  ok {snapshot.succeeded}  failed {snapshot.failed}"
                f"  blocked {snapshot.blocked}  cached {snapshot.cached}"
                f"  skipped {snapshot.skipped}"
            ),
            (
                f"Workers {snapshot.workers_active}/{snapshot.workers_configured}"
                f"  rate {rate}  retries {snapshot.retried}"
            ),
        ]

        if snapshot.active:
            lines.append("Active workers")
            active_budget = min(3, max(1, height - len(lines) - 3))
            lines.extend(f"  {item}" for item in snapshot.active[:active_budget])
            hidden = len(snapshot.active) - active_budget
            if hidden > 0:
                lines.append(f"  … and {hidden} more")

        if snapshot.task_kind_timing and len(lines) < height:
            lines.append("Task kinds")
            lines.append("  kind                 done      run  wait  median    p95")
            kind_budget = max(0, height - len(lines))
            kinds = sorted(
                snapshot.task_kind_timing.items(),
                key=lambda item: (
                    -int(item[1]["running"]),
                    -int(item[1]["waiting"]),
                    item[0],
                ),
            )
            for kind, item in kinds[:kind_budget]:
                median = item.get("median_seconds")
                p95 = item.get("p95_seconds")
                median_text = "--" if median is None else f"{median:.2f}s"
                p95_text = "--" if p95 is None else f"{p95:.2f}s"
                lines.append(
                    f"  {kind[:20]:20} {item['completed']:>4}/{item['total']:<4}"
                    f" {item['running']:>4} {item['waiting']:>5}"
                    f" {median_text:>7} {p95_text:>7}"
                )

        return [self._fit_line(line, width) for line in lines[:height]]

    def _write_tty(self, snapshot: ProgressSnapshot) -> None:
        lines = self._tty_lines(snapshot)
        if self._tty_line_count:
            self.stream.write(f"\r\x1b[{self._tty_line_count}A\x1b[J")
        else:
            self.stream.write("\r")
        self.stream.write("\n".join(lines) + "\n")
        self._tty_line_count = len(lines)

    def emit(self, event: Mapping[str, Any], *, force: bool = False) -> None:
        if not self.enabled:
            return
        snapshot = self.progress.snapshot()
        payload = self._payload(event, snapshot)
        with self._lock:
            self._write_structured(payload)
            now = time.monotonic()
            if self.mode == "tty" or force or now - self._last_emit >= self.interval_seconds:
                if self.mode == "tty":
                    self._write_tty(snapshot)
                elif self.mode == "json":
                    text = json.dumps(payload, sort_keys=True, default=str)
                else:
                    active = ",".join(snapshot.active[:3]) or "-"
                    rate = (
                        "--" if snapshot.completion_rate_per_second is None
                        else f"{snapshot.completion_rate_per_second:.2f}/s"
                    )
                    text = (
                        f"[progress] {snapshot.completed}/{snapshot.total} "
                        f"pending={snapshot.pending} running={snapshot.running} "
                        f"failed={snapshot.failed} blocked={snapshot.blocked} "
                        f"cached={snapshot.cached} skipped={snapshot.skipped} "
                        f"workers={snapshot.workers_active}/{snapshot.workers_configured} "
                        f"elapsed={self._duration(snapshot.elapsed_seconds)} "
                        f"rate={rate} eta={self._duration(snapshot.eta_seconds)}({snapshot.eta_confidence}) active={active}"
                    )
                    if snapshot.task_kind_timing:
                        kinds = []
                        for kind, item in list(snapshot.task_kind_timing.items())[:4]:
                            median = item.get("median_seconds")
                            p95 = item.get("p95_seconds")
                            kinds.append(
                                f"{kind}={item['completed']}/{item['total']} r{item['running']} "
                                f"med={'--' if median is None else f'{median:.2f}s'} "
                                f"p95={'--' if p95 is None else f'{p95:.2f}s'}"
                            )
                        text += " kinds=[" + "; ".join(kinds) + "]"
                if self.mode != "tty":
                    self.stream.write(text + "\n")
                self.stream.flush()
                self._last_emit = now

    def finish(self, event: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2.0))
        self.emit(event, force=True)


__all__ = [
    "GraphProgress",
    "ProgressNode",
    "ProgressReporter",
    "ProgressSnapshot",
    "TERMINAL_STATES",
]
