"""
Declarative reduction graph for VIRUSFlow planning.

This module defines TaskSpec, Edge, and ReductionGraph. The graph is a
high-level, product-agnostic DAG that expresses what can be built and how
it depends on raw inputs or prior artifacts. The planner emits Targets; the
scheduler/executor runs tasks.

Design constraints:
- No imports from algorithms or storage layers.
- Read-only interaction with registry via the ArtifactService or registry adapter APIs.
- No persistence here; purely planning/specification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..artifacts.models import Scope
from ..artifacts.service import ArtifactService
from ..registry import database as db

from .targets import Target, TemporalWindow, CadencePolicy


@dataclass(frozen=True)
class TaskSpec:
    """Declarative node that can produce artifacts of a given kind.

    Attributes
    - kind: artifact kind name (e.g., "master_bias", "trace").
    - task_cls: the Task subclass responsible for execution (not imported here; stored as type[Any]).
    - inputs_raw: list of raw frame types that feed this task (e.g., ["zro"]).
    - inputs_artifacts: list of upstream artifact kinds this task requires.
    - scope_mode: how this task is scoped ("per_zipcode", "per_exposure", "global").
    - cadence: cadence policy for calibration production (None for on-demand/science).
    - params_schema: optional lightweight schema for parameter validation (declarative only).
    """
    kind: str
    task_cls: type  # intentionally untyped to avoid importing tasks here
    inputs_raw: Sequence[str] | None = None
    inputs_artifacts: Sequence[str] | None = None
    scope_mode: str = "per_zipcode"
    cadence: Optional[CadencePolicy] = None
    params_schema: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class Edge:
    """Dependency edge with selection policy between TaskSpecs.

    - policy: artifact selection policy (e.g., "latest_valid").
    - tolerance_days: allowed temporal distance for mapping (science→calib).
    """
    src: TaskSpec
    dst: TaskSpec
    policy: str = "latest_valid"
    tolerance_days: int = 90


@dataclass
class PlanningReport:
    planned: List[Target] = field(default_factory=list)
    skipped: List[Target] = field(default_factory=list)
    existing: List[Target] = field(default_factory=list)
    reasons: Dict[str, str] = field(default_factory=dict)  # target_key -> reason


class ReductionGraph:
    """Product-agnostic reduction graph.

    Usage:
    - Construct with TaskSpec nodes and Edge dependencies.
    - Call plan() with a db_path and a set of scopes to obtain Targets.
    - A separate scheduler executes the resulting targets by instantiating
      the task classes referenced by TaskSpec.
    """

    def __init__(self, nodes: Sequence[TaskSpec], edges: Sequence[Edge]):
        self.nodes = list(nodes)
        self.edges = list(edges)
        # Index incoming edges by destination kind for tolerance/mapping policies
        self._incoming: Dict[str, List[Edge]] = {}
        for e in self.edges:
            self._incoming.setdefault(e.dst.kind, []).append(e)

    def plan(
        self,
        *,
        db_path: str,
        scopes: Sequence[Scope],
        when: Optional[TemporalWindow] = None,
        service_factory: Optional[callable] = None,
        force_replan: bool = False,
    ) -> Tuple[List[Target], PlanningReport]:
        """Emit a set of Targets to execute in topological order.

        - For nodes with cadence, enumerate calibration targets using cadence helpers.
        - For nodes without cadence, do not auto-emit (expect an external list of
          science exposures or ad-hoc targets for now).
        - Skip targets already satisfied according to ArtifactService.select_best,
          honoring tolerance where applicable.
        """
        svc = (service_factory or ArtifactService)(db_path)
        planned: List[Target] = []
        report = PlanningReport()

        def _edge_tolerance_for(kind: str) -> Optional[int]:
            # Use the strictest (minimum) tolerance across incoming edges to this kind, if any.
            es = self._incoming.get(kind) or []
            if not es:
                return None
            return min(int(getattr(e, "tolerance_days", 0) or 0) for e in es) or None

        # Track which windows were planned per kind and scope to enable
        # planning of artifact-driven nodes without raw inputs (e.g., trace, wave).
        planned_windows: Dict[Tuple[str, str], List[TemporalWindow]] = {}
        effective_raw_inputs: set[Tuple[str, str, Tuple[int, ...]]] = set()

        def _scope_key(scope: Scope) -> str:
            try:
                z = getattr(scope, "zipcode", None)
                if z is None:
                    return "__global__"
                k = getattr(z, "key", None)
                return str(k() if callable(k) else k or str(z))
            except Exception:
                return "__global__"

        def _emit(kind: str, scope: Scope, win: Optional[TemporalWindow], reason_forced: bool = False):
            # Shared emission with idempotency and tolerance handling
            tol_days = _edge_tolerance_for(kind)
            tgt = Target(kind=kind, scope=scope, window=win)
            if reason_forced or force_replan:
                planned.append(tgt)
                try:
                    report.reasons[_tkey(tgt)] = "forced_replan"
                except Exception:
                    pass
            else:
                # Idempotency: consult registry for an existing artifact
                at_time = getattr(win, "start", None) if win is not None else None
                existing = svc.select_best(kind=kind, scope=scope, at_time=at_time, policy="latest_valid")
                if existing is not None:
                    reason = "already_registered"
                    if tol_days is not None and at_time is not None:
                        try:
                            created = existing.get("created_at")
                            if isinstance(created, str):
                                from datetime import datetime as _dt
                                try:
                                    created_dt = _dt.fromisoformat(created)
                                except Exception:
                                    created_dt = _dt.strptime(created.split(".")[0], "%Y-%m-%d %H:%M:%S")
                            else:
                                created_dt = created  # assume datetime
                            delta_days = abs((created_dt - at_time).days) if (created_dt and at_time) else 0
                            if delta_days <= int(tol_days):
                                report.existing.append(tgt)
                                report.reasons[_tkey(tgt)] = "already_registered_within_tolerance"
                                return
                            else:
                                reason = "existing_outside_tolerance"
                        except Exception:
                            pass
                    report.existing.append(tgt)
                    report.reasons[_tkey(tgt)] = reason
                    return
                planned.append(tgt)
            # Record planned window for downstream propagation
            if win is not None:
                key = (kind, _scope_key(scope))
                planned_windows.setdefault(key, []).append(win)

        for node in self.nodes:
            has_raw = bool(node.inputs_raw)
            has_up = bool(node.inputs_artifacts)
            if node.cadence is None:
                # Non-cadence nodes that depend on artifacts: schedule per upstream windows
                if not has_up:
                    continue
                for scope in scopes:
                    # A derived Product is schedulable only where every declared
                    # upstream has a planned or already-valid Product window.
                    upstream_windows = []
                    for upstream in node.inputs_artifacts or []:
                        key_src = (upstream, _scope_key(scope))
                        wins = list(planned_windows.get(key_src, []))
                        if not wins:
                            existing = svc.select_best(
                                kind=upstream, scope=scope, at_time=None, policy="latest"
                            )
                            if existing is not None:
                                wins = [TemporalWindow(start=None, end=None)]
                        upstream_windows.append(wins)
                    if not upstream_windows or any(not wins for wins in upstream_windows):
                        continue
                    # Use the first input's windows as target validity; mapping policy
                    # resolves the other parents at execution time.
                    for win in upstream_windows[0]:
                        _emit(node.kind, scope, win)
                continue

            # Cadence-driven nodes
            for scope in scopes:
                windows: Sequence[TemporalWindow] = []
                if has_raw:
                    frame_type = (node.inputs_raw or [None])[0] or ""
                    try:
                        windows = node.cadence.windows(frame_type=frame_type, scope=scope, db_path=db_path)
                    except NotImplementedError:
                        from .targets import TimeCadence, ExposureCountCadence  # local import to avoid cycles
                        from .cadence import time_cadence_windows, exposure_count_windows
                        c = node.cadence
                        sd = ed = None
                        if when is not None:
                            try:
                                ws = getattr(when, "start", None)
                                we = getattr(when, "end", None)
                                if ws is not None:
                                    sd = ws.strftime("%Y%m%d")
                                if we is not None:
                                    ed = we.strftime("%Y%m%d")
                            except Exception:
                                sd = ed = None
                        if isinstance(c, TimeCadence):
                            windows = time_cadence_windows(db_path=db_path, scope=scope, frame_type=frame_type, every_days=c.every_days, min_n_inputs=c.min_n_inputs, start_date=sd, end_date=ed)
                        elif isinstance(c, ExposureCountCadence):
                            windows = exposure_count_windows(db_path=db_path, scope=scope, frame_type=frame_type, min_n=c.min_n, max_span_days=c.max_span_days, start_date=sd, end_date=ed)
                        else:
                            windows = [TemporalWindow(start=None, end=None)]
                elif has_up:
                    # Derive windows by propagating from first upstream kind planned for this scope
                    key_src = (node.inputs_artifacts[0], _scope_key(scope)) if node.inputs_artifacts else None
                    windows = list(planned_windows.get(key_src, [])) if key_src else []
                else:
                    windows = []

                for win in windows:
                    if has_raw:
                        rows = db.list_raw_files_scoped(
                            frame_type=frame_type,
                            start_date=(win.start.strftime("%Y%m%d") if win.start else "19000101"),
                            end_date=(win.end.strftime("%Y%m%d") if win.end else "21000101"),
                            zipcode=scope.zipcode,
                            db_path=db_path,
                            start_time=win.start,
                            end_time=win.end,
                        )
                        raw_ids = tuple(sorted(int(row_id) for row_id, _ in rows))
                        effective_key = (node.kind, _scope_key(scope), raw_ids)
                        if raw_ids and effective_key in effective_raw_inputs:
                            duplicate = Target(kind=node.kind, scope=scope, window=win)
                            report.skipped.append(duplicate)
                            report.reasons[_tkey(duplicate)] = "duplicate_effective_raw_inputs"
                            continue
                        if raw_ids:
                            effective_raw_inputs.add(effective_key)
                    _emit(node.kind, scope, win)

        report.planned = planned
        return planned, report


def _tkey(t: Target) -> str:
    w = t.window
    ws = w.start.isoformat() if (w and w.start) else "-"
    we = w.end.isoformat() if (w and w.end) else "-"
    return f"{t.kind}:{t.scope.zipcode}:{ws}:{we}"
