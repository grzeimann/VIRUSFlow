from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Callable, Optional


@dataclass
class Node:
    id: str
    task: object
    deps: Set[str] = field(default_factory=set)


class TaskGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Node] = {}

    def add(self, node_id: str, task: object, depends_on: List[str] | None = None) -> None:
        if node_id in self.nodes:
            raise ValueError(f"Node {node_id} already exists")
        self.nodes[node_id] = Node(id=node_id, task=task, deps=set(depends_on or []))

    def _indeg_and_dependents(self) -> tuple[Dict[str, int], Dict[str, Set[str]]]:
        indeg: Dict[str, int] = {k: 0 for k in self.nodes}
        dependents: Dict[str, Set[str]] = {k: set() for k in self.nodes}
        for nid, n in self.nodes.items():
            for d in n.deps:
                indeg[nid] += 1
                dependents[d].add(nid)
        return indeg, dependents

    def execute(self, max_workers: int = 1, debug: bool = False, show_progress: bool = True) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # QA-aware execution: if a node's produced artifacts have QA status 'fail',
        # mark the node as failed and skip all of its transitive dependents.
        from ..registry import database as _db
        import sys

        # Precompute totals per task type (by task.name if available)
        def _task_type(nid: str) -> str:
            try:
                return str(getattr(self.nodes[nid].task, 'name', 'task'))
            except Exception:
                return 'task'

        type_totals: Dict[str, int] = {}
        for nid in self.nodes:
            t = _task_type(nid)
            type_totals[t] = type_totals.get(t, 0) + 1
        type_succeeded: Dict[str, int] = {k: 0 for k in type_totals}
        type_failed: Dict[str, int] = {k: 0 for k in type_totals}
        # Failure diagnostics: count reasons per task type and print concise error lines
        fail_reason_counts: Dict[str, Dict[str, int]] = {k: {} for k in type_totals}

        def _bump_reason(task_type: str, category: str) -> None:
            d = fail_reason_counts.setdefault(task_type, {})
            d[category] = int(d.get(category, 0)) + 1

        def _print_error_line(task_type: str, node_id: str, category: str, detail: Optional[str] = None) -> None:
            try:
                msg = f"[Error] {task_type} {node_id}: {category}"
                if detail:
                    s = str(detail)
                    if len(s) > 200:
                        s = s[:197] + "..."
                    msg += f" - {s}"
                print(msg, file=sys.stderr, flush=True)
            except Exception:
                # Never fail rendering errors
                pass

        def _record_failure(nid: str, category: str, detail: Optional[str] = None) -> None:
            t = _task_type(nid)
            _bump_reason(t, category)
            _print_error_line(t, nid, category, detail)

        def _print_failure_summary() -> None:
            # At end of run, summarize failures by task type and category
            try:
                any_fail = any(type_failed.get(t, 0) > 0 for t in type_totals)
                if not any_fail:
                    return
                for t in sorted(type_totals.keys()):
                    nfail = int(type_failed.get(t, 0))
                    if nfail <= 0:
                        continue
                    cats = fail_reason_counts.get(t, {})
                    # sort categories by count desc
                    items = sorted(cats.items(), key=lambda kv: kv[1], reverse=True)
                    parts = ", ".join([f"{k}={v}" for k, v in items[:3]]) if items else ""
                    line = f"[Failures] {t}: {nfail} failed" + (f" ({parts})" if parts else "")
                    print(line, file=sys.stderr, flush=True)
            except Exception:
                pass

        def _render_progress_line(final: bool = False) -> None:
            if not show_progress:
                return
            parts = []
            for t in sorted(type_totals.keys()):
                ok = type_succeeded.get(t, 0)
                fl = type_failed.get(t, 0)
                tot = type_totals.get(t, 0)
                parts.append(f"{t}: {ok}/{tot} ok, {fl} fail")
            line = " | ".join(parts)
            # Carriage return update; write to stderr to reduce buffering issues in some shells
            if final:
                print(f"[Progress] {line}", file=sys.stderr, flush=True)
            else:
                print(f"\r[Progress] {line}", end="", file=sys.stderr, flush=True)

        indeg, dependents = self._indeg_and_dependents()
        ready = [k for k, v in indeg.items() if v == 0]
        done: Set[str] = set()
        failed: Set[str] = set()

        def _has_failed_dep(nid: str) -> bool:
            return any(d in failed for d in self.nodes[nid].deps)

        def _mark_completion(nid: str) -> None:
            t = _task_type(nid)
            if nid in failed:
                type_failed[t] = type_failed.get(t, 0) + 1
            else:
                type_succeeded[t] = type_succeeded.get(t, 0) + 1
            _render_progress_line(final=False)

        def _eval_node_qa(nid: str, result: object) -> bool:
            """Inspect returned artifacts (dict) and check QA; return True if node failed."""
            try:
                if not isinstance(result, dict):
                    return False
                # Determine DB path from task context if available
                db_path = None
                try:
                    db_path = getattr(self.nodes[nid].task.ctx, 'db_path', None)
                except Exception:
                    db_path = None
                # Each value is expected to be an Artifact-like object with optional 'id'
                for v in result.values():
                    art_id = getattr(v, "id", None)
                    if art_id is None:
                        continue
                    qa = _db.get_qa_results(int(art_id), db_path=(db_path or _db.DEFAULT_DB_PATH))
                    if qa and str(qa.get("status", "")).lower() == "fail":
                        return True
                return False
            except Exception:
                # On any error evaluating QA, do not mark as failed
                return False

        # Initial render (all zeros)
        _render_progress_line(final=False)

        while ready:
            batch = ready[:]
            ready.clear()
            # Split batch into runnable and to-skip based on failed deps
            runnable = [nid for nid in batch if not _has_failed_dep(nid)]
            skipped = [nid for nid in batch if _has_failed_dep(nid)]
            if skipped and debug:
                for nid in skipped:
                    print(f"[Executor] Skipping {nid} because a dependency failed QA")
            # Process skipped nodes: mark as failed and propagate to dependents
            for nid in skipped:
                failed.add(nid)
                done.add(nid)
                # Record that this node could not run due to a failed dependency
                _record_failure(nid, "blocked-dependency")
                _mark_completion(nid)
                for dep in dependents.get(nid, ()):  # decrease indegree for dependents
                    indeg[dep] -= 1
                    if indeg[dep] == 0:
                        # Prioritize newly-unlocked dependents so pipelines advance promptly
                        ready.insert(0, dep)
            if not runnable:
                continue
            if debug:
                print(f"[Executor] Running batch of {len(runnable)} tasks (max_workers={max_workers})")
            if max_workers <= 1 or len(runnable) == 1:
                for nid in runnable:
                    res = None
                    try:
                        res = self.nodes[nid].task.run({})
                    except Exception as e:
                        # Mark node as failed on exception but allow the graph to continue
                        failed.add(nid)
                        # Record diagnostic and optionally print debug
                        _record_failure(nid, "exception", str(e))
                        if debug:
                            print(f"[Executor] Node {nid} raised exception: {e}")
                    else:
                        # Evaluate QA; if failed, mark node
                        if _eval_node_qa(nid, res):
                            failed.add(nid)
                            _record_failure(nid, "qa-fail")
                            if debug:
                                print(f"[Executor] Node {nid} marked FAILED due to QA status of its outputs")
                    done.add(nid)
                    _mark_completion(nid)
                    for dep in dependents.get(nid, ()):  # decrease indegree for dependents
                        indeg[dep] -= 1
                        if indeg[dep] == 0:
                            ready.append(dep)
            else:
                # Threaded execution with bounded submission: keep at most max_workers running
                from concurrent.futures import wait, FIRST_COMPLETED
                from collections import deque
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    q = deque(runnable)
                    in_flight = {}

                    # Prime the pool
                    while q and len(in_flight) < max_workers:
                        nid = q.popleft()
                        fut = ex.submit(self.nodes[nid].task.run, {})
                        in_flight[fut] = nid

                    while in_flight:
                        done_futs, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
                        for fut in done_futs:
                            nid = in_flight.pop(fut)
                            # propagate exceptions and capture result
                            res = None
                            try:
                                res = fut.result()
                            except Exception as e:
                                failed.add(nid)
                                _record_failure(nid, "exception", str(e))
                                if debug:
                                    print(f"[Executor] Node {nid} raised exception: {e}")
                            else:
                                if _eval_node_qa(nid, res):
                                    failed.add(nid)
                                    _record_failure(nid, "qa-fail")
                                    if debug:
                                        print(f"[Executor] Node {nid} marked FAILED due to QA status of its outputs")
                            done.add(nid)
                            _mark_completion(nid)
                            for dep in dependents.get(nid, ()):  # decrease indegree for dependents
                                indeg[dep] -= 1
                                if indeg[dep] == 0:
                                    # Prioritize newly-unlocked dependents ahead of the backlog
                                    from collections import deque as _deque
                                    if isinstance(q, _deque):
                                        q.appendleft(dep)
                                    else:
                                        q.append(dep)
                        # Top up the pool to keep max_workers busy
                        while q and len(in_flight) < max_workers:
                            nid = q.popleft()
                            fut = ex.submit(self.nodes[nid].task.run, {})
                            in_flight[fut] = nid
        if len(done) != len(self.nodes):
            _render_progress_line(final=True)
            _print_failure_summary()
            raise ValueError("Not all tasks executed; possible cycle in graph")
        _render_progress_line(final=True)
        _print_failure_summary()
