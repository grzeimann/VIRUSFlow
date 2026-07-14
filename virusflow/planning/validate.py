from __future__ import annotations

"""
Validation utilities for planning graphs.

These helpers ensure that nodes and edges satisfy minimal requirements so that
callers (CLI/CI) can fail fast on misconfigurations.
"""
from typing import Sequence

from .graph import TaskSpec, Edge


class PlanningValidationError(ValueError):
    pass


def validate_edges(nodes: Sequence[TaskSpec], edges: Sequence[Edge]) -> None:
    """Validate edges have policies and tolerances and refer to known kinds.

    - policy must be a non-empty string
    - tolerance_days must be a non-negative integer
    - src/dst kinds must exist in nodes
    """
    kinds = {n.kind for n in nodes}
    for i, e in enumerate(edges):
        if not getattr(e, "policy", None) or not str(e.policy).strip():
            raise PlanningValidationError(f"edge[{i}] {e.src.kind}->{e.dst.kind} missing policy")
        tol = getattr(e, "tolerance_days", None)
        if tol is None or int(tol) < 0:
            raise PlanningValidationError(f"edge[{i}] {e.src.kind}->{e.dst.kind} has invalid tolerance_days={tol}")
        if e.src.kind not in kinds:
            raise PlanningValidationError(f"edge[{i}] src kind {e.src.kind!r} not found in nodes")
        if e.dst.kind not in kinds:
            raise PlanningValidationError(f"edge[{i}] dst kind {e.dst.kind!r} not found in nodes")


def validate_graph(nodes: Sequence[TaskSpec], edges: Sequence[Edge]) -> None:
    """Run all available validations for the planning graph."""
    validate_edges(nodes, edges)
