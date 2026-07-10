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

    def execute(self, max_workers: int = 1, debug: bool = False) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # QA-aware execution: if a node's produced artifacts have QA status 'fail',
        # mark the node as failed and skip all of its transitive dependents.
        from ..registry import database as _db

        indeg, dependents = self._indeg_and_dependents()
        ready = [k for k, v in indeg.items() if v == 0]
        done: Set[str] = set()
        failed: Set[str] = set()

        def _has_failed_dep(nid: str) -> bool:
            return any(d in failed for d in self.nodes[nid].deps)

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
                for dep in dependents.get(nid, ()):  # decrease indegree for dependents
                    indeg[dep] -= 1
                    if indeg[dep] == 0:
                        ready.append(dep)
            if not runnable:
                continue
            if debug:
                print(f"[Executor] Running batch of {len(runnable)} tasks (max_workers={max_workers})")
            if max_workers <= 1 or len(runnable) == 1:
                for nid in runnable:
                    res = self.nodes[nid].task.run({})
                    # Evaluate QA; if failed, mark node
                    if _eval_node_qa(nid, res):
                        failed.add(nid)
                        if debug:
                            print(f"[Executor] Node {nid} marked FAILED due to QA status of its outputs")
                    done.add(nid)
                    for dep in dependents.get(nid, ()):  # decrease indegree for dependents
                        indeg[dep] -= 1
                        if indeg[dep] == 0:
                            ready.append(dep)
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futs = {ex.submit(self.nodes[nid].task.run, {}): nid for nid in runnable}
                    for fut in as_completed(futs):
                        nid = futs[fut]
                        # propagate exceptions and capture result
                        res = fut.result()
                        if _eval_node_qa(nid, res):
                            failed.add(nid)
                            if debug:
                                print(f"[Executor] Node {nid} marked FAILED due to QA status of its outputs")
                        done.add(nid)
                        for dep in dependents.get(nid, ()):  # decrease indegree for dependents
                            indeg[dep] -= 1
                            if indeg[dep] == 0:
                                ready.append(dep)
        if len(done) != len(self.nodes):
            raise ValueError("Not all tasks executed; possible cycle in graph")
