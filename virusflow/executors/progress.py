from __future__ import annotations

"""Graph-aware progress state and terminal/batch renderers."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time
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
    ) -> None:
        if state not in VALID_STATES:
            raise ValueError(f"invalid progress state: {state}")
        with self._lock:
            if node_id in self._nodes:
                raise ValueError(f"progress node already registered: {node_id}")
            self._nodes[node_id] = ProgressNode(
                str(node_id), str(kind), str(target or node_id), state=state, message=message
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
            eta = None if rate is None or rate <= 0 else remaining / rate
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
            )


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

    def emit(self, event: Mapping[str, Any], *, force: bool = False) -> None:
        if not self.enabled:
            return
        snapshot = self.progress.snapshot()
        payload = self._payload(event, snapshot)
        with self._lock:
            self._write_structured(payload)
            now = time.monotonic()
            if self.mode == "tty" or force or now - self._last_emit >= self.interval_seconds:
                if self.mode == "json":
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
                        f"rate={rate} eta={self._duration(snapshot.eta_seconds)} active={active}"
                    )
                if self.mode == "tty":
                    self.stream.write("\r\x1b[2K" + text)
                else:
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
        if self.mode == "tty":
            self.stream.write("\n")
            self.stream.flush()


__all__ = [
    "GraphProgress",
    "ProgressNode",
    "ProgressReporter",
    "ProgressSnapshot",
    "TERMINAL_STATES",
]
