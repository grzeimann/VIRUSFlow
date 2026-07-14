from __future__ import annotations

"""
Planning-native executor that runs ScheduledTask instances without relying on the
legacy core.graph.TaskGraph. Intended for use with virusflow.planning.scheduler.schedule().

Features:
- Executes tasks in batches when their depends_on are satisfied.
- Optional threading with a max_workers parameter (similar to LocalExecutor).
- Captures exceptions per task and continues advancing the graph where possible.
- Prints a compact per-kind progress line when debug is enabled.

This module intentionally avoids importing legacy core.graph to help retire TaskGraph.
"""
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple, Optional


@dataclass
class _Node:
    id: str
    kind: str
    task: object
    deps: List[str]


class PlanningExecutor:
    def __init__(self, max_workers: int = 1, debug: bool = False) -> None:
        self.max_workers = max(1, int(max_workers))
        self.debug = bool(debug)
        self._nodes: Dict[str, _Node] = {}

    def add_task(self, node_id: str, task: object, kind: Optional[str] = None, depends_on: List[str] | None = None) -> None:
        k = kind or getattr(task, "kind", "task")
        self._nodes[node_id] = _Node(id=node_id, kind=str(k), task=task, deps=list(depends_on or []))

    def _indeg_and_dependents(self) -> Tuple[Dict[str, int], Dict[str, List[str]]]:
        indeg: Dict[str, int] = {k: 0 for k in self._nodes}
        dependents: Dict[str, List[str]] = {k: [] for k in self._nodes}
        for nid, n in self._nodes.items():
            for d in n.deps:
                indeg[nid] = indeg.get(nid, 0) + 1
                dependents.setdefault(d, []).append(nid)
        return indeg, dependents

    def run(self) -> None:
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

        indeg, dependents = self._indeg_and_dependents()
        ready = [k for k, v in indeg.items() if v == 0]
        done: Dict[str, bool] = {}
        failed: Dict[str, str] = {}

        def _has_failed_dep(nid: str) -> bool:
            return any((d in failed) for d in self._nodes[nid].deps)

        def _run_one(nid: str):
            try:
                return nid, self._nodes[nid].task.run({}), None
            except Exception as e:  # pragma: no cover - behavior mirrored from LocalExecutor
                return nid, None, str(e)

        # Simple progress counters by kind
        totals: Dict[str, int] = {}
        for n in self._nodes.values():
            totals[n.kind] = totals.get(n.kind, 0) + 1
        ok: Dict[str, int] = {k: 0 for k in totals}
        fl: Dict[str, int] = {k: 0 for k in totals}

        def _bump(kind: str, success: bool):
            if success:
                ok[kind] = ok.get(kind, 0) + 1
            else:
                fl[kind] = fl.get(kind, 0) + 1
            if self.debug:
                parts = [f"{k}: {ok.get(k,0)}/{totals.get(k,0)} ok, {fl.get(k,0)} fail" for k in sorted(totals.keys())]
                print("\r[Progress] " + " | ".join(parts), end="", flush=True)

        while ready:
            batch = ready[:]
            ready.clear()
            # Filter out nodes whose deps have failed
            runnable = [nid for nid in batch if not _has_failed_dep(nid)]
            skipped = [nid for nid in batch if _has_failed_dep(nid)]
            for nid in skipped:
                failed[nid] = "blocked-dependency"
                done[nid] = True
                for dep in dependents.get(nid, []):
                    indeg[dep] -= 1
                    if indeg[dep] == 0:
                        ready.append(dep)

            if not runnable:
                continue

            if self.max_workers <= 1 or len(runnable) == 1:
                for nid in runnable:
                    _, _res, err = _run_one(nid)
                    if err is not None:
                        failed[nid] = err
                        _bump(self._nodes[nid].kind, False)
                    else:
                        _bump(self._nodes[nid].kind, True)
                    done[nid] = True
                    for dep in dependents.get(nid, []):
                        indeg[dep] -= 1
                        if indeg[dep] == 0:
                            ready.append(dep)
            else:
                with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                    in_flight = {ex.submit(_run_one, nid): nid for nid in runnable[: self.max_workers]}
                    q = runnable[self.max_workers :]
                    while in_flight:
                        done_futs, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
                        for fut in done_futs:
                            nid = in_flight.pop(fut)
                            _nid, _res, err = fut.result()
                            if err is not None:
                                failed[nid] = err
                                _bump(self._nodes[nid].kind, False)
                            else:
                                _bump(self._nodes[nid].kind, True)
                            done[nid] = True
                            for dep in dependents.get(nid, []):
                                indeg[dep] -= 1
                                if indeg[dep] == 0:
                                    ready.insert(0, dep)  # prioritize newly-unlocked
                        # Top up
                        while q and len(in_flight) < self.max_workers:
                            nid2 = q.pop(0)
                            in_flight[ex.submit(_run_one, nid2)] = nid2

        # Final newline for progress print
        if self.debug:
            print()

        # If any nodes remain not done, raise to indicate a likely cycle
        if len(done) != len(self._nodes):
            raise ValueError("Not all tasks executed; possible cycle or unresolved dependencies")

        # If there were failures, surface a concise error summary without stopping the whole run
        if failed and self.debug:
            kinds: Dict[str, int] = {}
            for nid, reason in failed.items():
                k = self._nodes[nid].kind
                kinds[k] = kinds.get(k, 0) + 1
                print(f"[Error] {k} {nid}: {reason}")
