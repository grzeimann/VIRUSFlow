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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Sequence, Tuple, Optional

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
    group_target_to_id: Dict[Tuple[str, str], str] = {}
    scope_target_to_ids: Dict[Tuple[str, Tuple], List[str]] = {}
    def _make_node_id(t: Target) -> str:
        w = t.window
        ws = getattr(w, 'start', None)
        we = getattr(w, 'end', None)
        z = getattr(t.scope, 'zipcode', None)
        zkey = (z.ifuslot, z.ifuid, z.specid, z.amp, z.controller) if z is not None else (None,)
        group = getattr(t, "group", None)
        identity = getattr(group, "computation_id", None)
        return f"{t.kind}:{':'.join(str(x) for x in zkey)}:{identity or getattr(ws, 'isoformat', lambda: None)()}:{getattr(we, 'isoformat', lambda: None)()}"
    for target in targets:
        node_id = _make_node_id(target)
        scope_k = _scope_key(target)
        scope_target_to_ids.setdefault((target.kind, scope_k), []).append(node_id)
        if target.group is not None:
            group_target_to_id[(target.kind, target.group.group_id)] = node_id

    def _time_bounds(target: Target) -> Tuple[Optional[datetime], Optional[datetime]]:
        window = target.window
        if window is not None:
            return window.start, window.end
        return target.at_time, target.at_time

    def _center(target: Target) -> Optional[datetime]:
        start, end = _time_bounds(target)
        if start is not None and end is not None:
            return start + (end - start) / 2
        return start or end

    def _qa_gate_dependencies(edge: Edge, target: Target) -> List[str]:
        """Resolve planned QA gates without treating them as data parents."""

        scope_k = _scope_key(target)
        candidates = [
            candidate for candidate in grouped.get(edge.src.kind, [])
            if _scope_key(candidate) == scope_k
        ]
        if not candidates:
            # An existing, QA-accepted source is not in the planned target set
            # and therefore needs no execution dependency.
            return []
        target_start, target_end = _time_bounds(target)
        overlapping = []
        if target_start is not None and target_end is not None:
            for candidate in candidates:
                source_start, source_end = _time_bounds(candidate)
                if source_start is None or source_end is None:
                    continue
                if source_start <= target_end and target_start <= source_end:
                    overlapping.append(candidate)
        selected = overlapping
        if not selected:
            target_center = _center(target)
            centered = [
                (abs((_center(candidate) - target_center).total_seconds()), candidate)
                for candidate in candidates
                if _center(candidate) is not None and target_center is not None
            ]
            if centered:
                distance, nearest = min(centered, key=lambda item: (item[0], _make_node_id(item[1])))
                if distance <= max(0, int(edge.tolerance_days)) * 86400:
                    selected = [nearest]
        return [_make_node_id(candidate) for candidate in selected]

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
                task_obj = task_cls(
                    ctx, target=tgt_for_task,
                    params=dict(getattr(node_by_kind.get(kind), "params_schema", None) or {}),
                )
            # Determine deps: for each incoming edge (src→dst with dst==kind), depend on src task of same scope
            deps_ids: List[str] = []
            scope_k = _scope_key(t)
            if t.parent_groups:
                for parent in t.parent_groups:
                    dep_id = group_target_to_id.get(parent)
                    if dep_id and dep_id not in deps_ids:
                        deps_ids.append(dep_id)
            else:
                for e in edges:
                    if e.dst.kind != kind or e.policy == "qa_gate":
                        continue
                    candidates = scope_target_to_ids.get((e.src.kind, scope_k), [])
                    if len(candidates) == 1 and candidates[0] not in deps_ids:
                        deps_ids.append(candidates[0])
            for edge in edges:
                if edge.dst.kind != kind or edge.policy != "qa_gate":
                    continue
                for dep_id in _qa_gate_dependencies(edge, t):
                    if dep_id not in deps_ids:
                        deps_ids.append(dep_id)
            scheduled.append(ScheduledTask(id=node_id, kind=kind, task=task_obj, depends_on=deps_ids))
    return scheduled
