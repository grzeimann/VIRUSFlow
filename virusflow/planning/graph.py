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

        for node in self.nodes:
            if node.cadence is None:
                # Non-cadence nodes (e.g., science or derived steps) are not enumerated
                # by default here.
                continue

            # Cadence-driven calibration nodes
            frame_type = (node.inputs_raw or [None])[0] or ""
            for scope in scopes:
                # Enumerate windows based on the cadence policy type
                windows: Sequence[TemporalWindow]
                try:
                    # Prefer explicit windows() if implementation provided
                    windows = node.cadence.windows(frame_type=frame_type, scope=scope, db_path=db_path)
                except NotImplementedError:
                    # Fallback to built-in helpers based on cadence type
                    from .targets import TimeCadence, ExposureCountCadence  # local import to avoid cycles
                    from .cadence import time_cadence_windows, exposure_count_windows
                    c = node.cadence
                    if isinstance(c, TimeCadence):
                        windows = time_cadence_windows(db_path=db_path, scope=scope, frame_type=frame_type, every_days=c.every_days, min_n_inputs=c.min_n_inputs)
                    elif isinstance(c, ExposureCountCadence):
                        windows = exposure_count_windows(db_path=db_path, scope=scope, frame_type=frame_type, min_n=c.min_n, max_span_days=c.max_span_days)
                    else:
                        windows = [TemporalWindow(start=None, end=None)]

                tol_days = _edge_tolerance_for(node.kind)
                for win in windows:
                    tgt = Target(kind=node.kind, scope=scope, window=win)
                    # Idempotency: consult registry for an existing artifact
                    existing = svc.select_best(kind=node.kind, scope=scope, at_time=win.start, policy="latest_valid")
                    if existing is not None:
                        # If a tolerance applies and we have a concrete window start,
                        # check whether the existing artifact is within tolerance.
                        reason = "already_registered"
                        if tol_days is not None and getattr(win, "start", None) is not None:
                            try:
                                created = existing.get("created_at")
                                if isinstance(created, str):
                                    from datetime import datetime as _dt
                                    try:
                                        created_dt = _dt.fromisoformat(created)
                                    except Exception:
                                        # Some rows may store dates without tz; attempt loose parsing
                                        created_dt = _dt.strptime(created.split(".")[0], "%Y-%m-%d %H:%M:%S")
                                else:
                                    created_dt = created  # assume datetime
                                ws = win.start  # type: ignore[assignment]
                                delta_days = abs((created_dt - ws).days) if (created_dt and ws) else 0
                                if delta_days <= int(tol_days):
                                    report.existing.append(tgt)
                                    report.reasons[_tkey(tgt)] = "already_registered_within_tolerance"
                                    continue
                                else:
                                    # Outside tolerance: plan a new one
                                    reason = "existing_outside_tolerance"
                            except Exception:
                                # On any parsing error, fall back to skipping as already registered
                                pass
                        # Default: treat as existing and skip
                        report.existing.append(tgt)
                        report.reasons[_tkey(tgt)] = reason
                        continue
                    planned.append(tgt)

        report.planned = planned
        return planned, report


def _tkey(t: Target) -> str:
    w = t.window
    ws = w.start.isoformat() if (w and w.start) else "-"
    we = w.end.isoformat() if (w and w.end) else "-"
    return f"{t.kind}:{t.scope.zipcode}:{ws}:{we}"
