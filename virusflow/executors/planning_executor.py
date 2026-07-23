from __future__ import annotations

"""Dependency-aware execution for planned VIRUSFlow task graphs."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import traceback
from typing import Dict, List, Optional, Tuple

from .progress import GraphProgress, ProgressReporter


@dataclass
class _Node:
    id: str
    kind: str
    task: object
    deps: List[str]
    target: str


class WorkflowExecutionError(RuntimeError):
    """Raised after graph state and dependents have been finalized."""

    def __init__(self, failures: list[dict], blocked: list[dict]) -> None:
        self.failures = failures
        self.blocked = blocked
        sample = failures[0] if failures else {"id": "unknown", "reason": "unknown"}
        super().__init__(
            f"workflow failed: {len(failures)} failed, {len(blocked)} blocked; "
            f"first failure {sample['id']}: {sample['reason']}"
        )


class PlanningExecutor:
    """Execute each unique graph node once when all prerequisites are ready."""

    def __init__(
        self,
        max_workers: int | None = None,
        debug: bool = False,
        *,
        progress: bool = True,
        progress_mode: str = "auto",
        progress_interval: float = 30.0,
        progress_stream=None,
        progress_path: str | None = None,
        max_retries: int = 0,
        raise_on_failure: bool = True,
    ) -> None:
        from .execution_context import in_task_worker

        requested = 4 if max_workers is None else max(1, int(max_workers))
        self.max_workers = 1 if in_task_worker() else requested
        self.debug = bool(debug)
        self.max_retries = max(0, int(max_retries))
        self.raise_on_failure = bool(raise_on_failure)
        self._nodes: Dict[str, _Node] = {}
        self.execution_stats: Dict[str, object] = {}
        self.progress = GraphProgress(self.max_workers)
        self.reporter = ProgressReporter(
            self.progress,
            enabled=progress,
            mode=progress_mode,
            interval_seconds=progress_interval,
            stream=progress_stream,
            structured_path=progress_path,
        )

    @staticmethod
    def _target_label(node_id: str, task: object) -> str:
        target = getattr(task, "target", None)
        if target is None:
            return str(node_id)
        for name in ("observation_id", "exposure_id", "dither_set_id"):
            value = getattr(target, name, None)
            if value:
                return f"{name}={value}"
        zipcode = getattr(target, "zipcode", None)
        if zipcode is not None and hasattr(zipcode, "key"):
            return f"zipcode={zipcode.key()}"
        return str(node_id)

    def add_task(
        self,
        node_id: str,
        task: object,
        kind: Optional[str] = None,
        depends_on: List[str] | None = None,
        *,
        target: str | None = None,
    ) -> None:
        if node_id in self._nodes:
            raise ValueError(f"task node is already registered: {node_id}")
        task_kind = str(kind or getattr(task, "kind", "task"))
        label = str(target or self._target_label(node_id, task))
        self._nodes[node_id] = _Node(
            id=node_id,
            kind=task_kind,
            task=task,
            deps=list(depends_on or []),
            target=label,
        )
        self.progress.add_node(node_id, kind=task_kind, target=label)

    def add_observed(
        self,
        node_id: str,
        *,
        kind: str,
        state: str,
        target: str | None = None,
        message: str | None = None,
    ) -> None:
        """Include a planner-cached or deliberately skipped unit in progress."""

        if state not in {"cached", "skipped"}:
            raise ValueError("observed graph work must be cached or skipped")
        if node_id in self._nodes:
            raise ValueError(f"task node is already registered: {node_id}")
        self.progress.add_node(
            node_id, kind=str(kind), target=target or node_id, state=state, message=message
        )

    def _indeg_and_dependents(self) -> Tuple[Dict[str, int], Dict[str, List[str]]]:
        indeg: Dict[str, int] = {key: 0 for key in self._nodes}
        dependents: Dict[str, List[str]] = {key: [] for key in self._nodes}
        for node_id, node in self._nodes.items():
            for dependency in node.deps:
                if dependency not in self._nodes:
                    raise ValueError(f"task {node_id} has unknown dependency {dependency}")
                indeg[node_id] += 1
                dependents[dependency].append(node_id)
        # Validate acyclicity before progress starts so final state is never a
        # misleading partial completion caused by malformed graph structure.
        check = dict(indeg)
        ready = deque(key for key, value in check.items() if value == 0)
        visited = 0
        while ready:
            current = ready.popleft()
            visited += 1
            for dependent in dependents[current]:
                check[dependent] -= 1
                if check[dependent] == 0:
                    ready.append(dependent)
        if visited != len(self._nodes):
            raise ValueError("task graph contains a cycle")
        return indeg, dependents

    def run(self) -> Dict[str, object]:
        from .execution_context import enter_worker, leave_worker

        indeg, dependents = self._indeg_and_dependents()
        ready = deque(node_id for node_id, value in indeg.items() if value == 0)
        results: Dict[str, object] = {}
        attempts = {node_id: 0 for node_id in self._nodes}
        unsuccessful: set[str] = set()
        failures: dict[str, dict] = {}
        blocked: dict[str, dict] = {}
        per_kind: dict[str, dict[str, int]] = {}
        for node in self._nodes.values():
            bucket = per_kind.setdefault(
                node.kind,
                {"total": 0, "succeeded": 0, "failed": 0, "blocked": 0, "retried": 0},
            )
            bucket["total"] += 1

        def release(node_id: str) -> None:
            for dependent in dependents[node_id]:
                indeg[dependent] -= 1
                if indeg[dependent] == 0:
                    ready.append(dependent)

        def run_one(node_id: str):
            worker_id = f"worker-{node_id}"
            token = enter_worker(worker_id)
            try:
                inputs = {
                    dependency: results[dependency]
                    for dependency in self._nodes[node_id].deps
                    if dependency in results
                }
                return self._nodes[node_id].task.run(inputs), None, None
            except Exception as exc:  # task failure is finalized by the graph owner
                return None, exc, traceback.format_exc()
            finally:
                leave_worker(token)

        self.reporter.start()
        first_exception: Exception | None = None
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="virusflow") as pool:
            in_flight: dict[Future, str] = {}
            while ready or in_flight:
                while ready and len(in_flight) < self.max_workers:
                    node_id = ready.popleft()
                    failed_dependencies = [dep for dep in self._nodes[node_id].deps if dep in unsuccessful]
                    if failed_dependencies:
                        reason = f"blocked by prerequisite(s): {', '.join(failed_dependencies)}"
                        unsuccessful.add(node_id)
                        blocked[node_id] = {
                            "id": node_id,
                            "kind": self._nodes[node_id].kind,
                            "reason": reason,
                        }
                        per_kind[self._nodes[node_id].kind]["blocked"] += 1
                        event = self.progress.transition(node_id, "blocked", message=reason)
                        self.reporter.emit(event, force=True)
                        release(node_id)
                        continue
                    attempts[node_id] += 1
                    worker_id = f"worker-{node_id}"
                    event = self.progress.transition(node_id, "running", worker_id=worker_id)
                    self.reporter.emit(event)
                    in_flight[pool.submit(run_one, node_id)] = node_id

                if not in_flight:
                    continue
                completed, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
                for future in completed:
                    node_id = in_flight.pop(future)
                    result, error, trace = future.result()
                    node = self._nodes[node_id]
                    if error is not None and attempts[node_id] <= self.max_retries:
                        per_kind[node.kind]["retried"] += 1
                        event = self.progress.transition(
                            node_id,
                            "pending",
                            message=f"retry {attempts[node_id]}/{self.max_retries}: {error}",
                        )
                        self.reporter.emit(event, force=True)
                        ready.append(node_id)
                        continue
                    if error is not None:
                        first_exception = first_exception or error
                        reason = f"{type(error).__name__}: {error}"
                        failures[node_id] = {
                            "id": node_id,
                            "kind": node.kind,
                            "reason": reason,
                            "exception_type": type(error).__name__,
                            "traceback": trace,
                            "attempts": attempts[node_id],
                        }
                        unsuccessful.add(node_id)
                        per_kind[node.kind]["failed"] += 1
                        event = self.progress.transition(node_id, "failed", message=reason)
                        self.reporter.emit(event, force=True)
                    else:
                        results[node_id] = result
                        per_kind[node.kind]["succeeded"] += 1
                        event = self.progress.transition(node_id, "succeeded")
                        self.reporter.emit(event)
                    release(node_id)

        final_event = self.progress.finalize()
        snapshot = self.progress.snapshot()
        self.reporter.finish(final_event)
        self.execution_stats = {
            "total": snapshot.total,
            "executed": len(self._nodes),
            "succeeded": snapshot.succeeded,
            "failed": snapshot.failed,
            "blocked": snapshot.blocked,
            "cached": snapshot.cached,
            "skipped": snapshot.skipped,
            "retried": snapshot.retried,
            "elapsed_seconds": snapshot.elapsed_seconds,
            "workers": self.max_workers,
            "per_kind": per_kind,
            "failures": list(failures.values()),
            "blocked_tasks": list(blocked.values()),
            "progress": snapshot.as_dict(),
        }
        if failures and self.raise_on_failure:
            error = WorkflowExecutionError(list(failures.values()), list(blocked.values()))
            if first_exception is not None:
                raise error from first_exception
            raise error
        return results


__all__ = ["PlanningExecutor", "WorkflowExecutionError"]
