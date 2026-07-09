from __future__ import annotations

from typing import List

from ..core.graph import TaskGraph


class LocalExecutor:
    """Simple in-process executor that runs tasks following a TaskGraph."""

    def __init__(self) -> None:
        self.graph = TaskGraph()

    def add_task(self, node_id: str, task: object, depends_on: List[str] | None = None) -> None:
        self.graph.add(node_id, task, depends_on=depends_on)

    def run(self) -> None:
        self.graph.execute()
