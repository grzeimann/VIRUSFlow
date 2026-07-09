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

    def topological_sort(self) -> List[str]:
        indeg: Dict[str, int] = {k: 0 for k in self.nodes}
        for n in self.nodes.values():
            for d in n.deps:
                indeg[n.id] += 1
        ready = [k for k, v in indeg.items() if v == 0]
        order: List[str] = []
        deps_by: Dict[str, Set[str]] = {k: set() for k in self.nodes}
        for nid, n in self.nodes.items():
            for d in n.deps:
                deps_by[d].add(nid)
        while ready:
            x = ready.pop()
            order.append(x)
            for y in deps_by.get(x, ()): 
                indeg[y] -= 1
                if indeg[y] == 0:
                    ready.append(y)
        if len(order) != len(self.nodes):
            raise ValueError("Cycle detected in task graph")
        return order

    def execute(self) -> None:
        for nid in self.topological_sort():
            task = self.nodes[nid].task
            # Tasks follow the interface task.run(inputs_dict); we pass empty inputs for now
            task.run({})
