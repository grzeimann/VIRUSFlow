from __future__ import annotations

"""
Thin scheduler for planned Targets.

This module provides a minimal scheduler that:
- Consumes (targets, TaskSpec nodes, Edge dependencies)
- Orders execution by topological order of kinds (edges src→dst), then by scope
- Instantiates task classes using a provided mapping kind→task_cls
- Returns a list of (node_id, task_instance, depends_on_node_ids)

Notes:
- This keeps planning independent: no imports from algorithms/storage layers here.
- Integration with CLI/executors is left to the runner; this module only assembles.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple, Optional

from .graph import TaskSpec, Edge
from .targets import Target


@dataclass(frozen=True)
class ScheduledTask:
    id: str
    kind: str
    task: object
    depends_on: List[str]


def _topo_order_kinds(nodes: Sequence[TaskSpec], edges: Sequence[Edge]) -> List[str]:
    kinds = [n.kind for n in nodes]
    deps: Dict[str, List[str]] = {k: [] for k in kinds}
    indeg: Dict[str, int] = {k: 0 for k in kinds}
    for e in edges:
        s = e.src.kind
        d = e.dst.kind
        if s not in deps:
            deps[s] = []
            indeg.setdefault(s, 0)
        if d not in deps:
            deps[d] = []
            indeg.setdefault(d, 0)
        deps[s].append(d)
        indeg[d] = indeg.get(d, 0) + 1
    # Kahn's algorithm
    ready = [k for k, v in indeg.items() if v == 0]
    out: List[str] = []
    while ready:
        k = ready.pop(0)
        out.append(k)
        for d in deps.get(k, []):
            indeg[d] -= 1
            if indeg[d] == 0:
                ready.append(d)
    # Fallback: if cycle or empty, return kinds as-is
    return out or kinds


def schedule(
    *,
    targets: Sequence[Target],
    nodes: Sequence[TaskSpec],
    edges: Sequence[Edge],
    kind_to_task: Dict[str, type],
    task_context_factory: Optional[callable] = None,
    target_adapter: Optional[callable] = None,
) -> List[ScheduledTask]:
    """Build a simple schedule in topological order of kinds.

    - Groups targets by kind and scope key for reproducible ordering.
    - For each Target, instantiates kind_to_task[kind](ctx, target=Target) if available.
    - Determines dependencies by mapping Edge src kinds within the same scope.
    """
    # Index nodes by kind
    node_by_kind: Dict[str, TaskSpec] = {n.kind: n for n in nodes}
    # Precompute topological order by kinds
    order = _topo_order_kinds(nodes, edges)
    # Group targets by kind then by a stable scope key
    from ..artifacts.models import Scope  # type: ignore  # type only
    def _scope_key(t: Target) -> Tuple:
        z = getattr(t.scope, 'zipcode', None)
        if z is None:
            return (None,)
        return (z.ifuslot, z.ifuid, z.specid, z.amp, z.controller)
    grouped: Dict[str, List[Target]] = {}
    for t in targets:
        grouped.setdefault(t.kind, []).append(t)
    for k in grouped:
        grouped[k].sort(key=_scope_key)
    # Map to scheduled tasks and compute intra-scope deps
    scheduled: List[ScheduledTask] = []
    name_target_to_id: Dict[Tuple[str, Tuple], str] = {}
    def _make_node_id(t: Target) -> str:
        w = t.window
        ws = getattr(w, 'start', None)
        we = getattr(w, 'end', None)
        z = getattr(t.scope, 'zipcode', None)
        zkey = (z.ifuslot, z.ifuid, z.specid, z.amp, z.controller) if z is not None else (None,)
        return f"{t.kind}:{':'.join(str(x) for x in zkey)}:{getattr(ws, 'isoformat', lambda: None)()}:{getattr(we, 'isoformat', lambda: None)()}"
    # Build in topo order by kinds
    for kind in order:
        for t in grouped.get(kind, []):
            node_id = _make_node_id(t)
            # Instantiate task if mapping provided; else, keep a placeholder object
            task_cls = kind_to_task.get(kind)
            if task_cls is None:
                task_obj = object()
            else:
                ctx = task_context_factory() if task_context_factory else None
                tgt_for_task = target_adapter(t) if target_adapter else t
                task_obj = task_cls(ctx, target=tgt_for_task)
            # Determine deps: for each incoming edge (src→dst with dst==kind), depend on src task of same scope
            deps_ids: List[str] = []
            scope_k = _scope_key(t)
            for e in edges:
                if e.dst.kind != kind:
                    continue
                dep_id = name_target_to_id.get((e.src.kind, scope_k))
                if dep_id and dep_id not in deps_ids:
                    deps_ids.append(dep_id)
            scheduled.append(ScheduledTask(id=node_id, kind=kind, task=task_obj, depends_on=deps_ids))
            name_target_to_id[(kind, scope_k)] = node_id
    return scheduled
