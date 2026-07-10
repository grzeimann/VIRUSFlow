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
        # Simple level-by-level parallel execution honoring dependencies
        indeg, dependents = self._indeg_and_dependents()
        ready = [k for k, v in indeg.items() if v == 0]
        done: Set[str] = set()
        while ready:
            batch = ready[:]  # run all currently-ready tasks in this wave
            ready.clear()
            if debug:
                print(f"[Executor] Running batch of {len(batch)} tasks (max_workers={max_workers})")
            if max_workers <= 1 or len(batch) == 1:
                for nid in batch:
                    self.nodes[nid].task.run({})
                    done.add(nid)
                    for dep in dependents.get(nid, ()):  # decrease indegree for dependents
                        indeg[dep] -= 1
                        if indeg[dep] == 0:
                            ready.append(dep)
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futs = {ex.submit(self.nodes[nid].task.run, {}): nid for nid in batch}
                    for fut in as_completed(futs):
                        nid = futs[fut]
                        # propagate exceptions
                        fut.result()
                        done.add(nid)
                        for dep in dependents.get(nid, ()):  # decrease indegree for dependents
                            indeg[dep] -= 1
                            if indeg[dep] == 0:
                                ready.append(dep)
        if len(done) != len(self.nodes):
            raise ValueError("Not all tasks executed; possible cycle in graph")
