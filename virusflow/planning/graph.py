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
        self._deps: Dict[str, List[Edge]] = {}
        for e in self.edges:
            self._deps.setdefault(e.dst.kind, []).append(e)

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
        - Skip targets already satisfied according to ArtifactService.select_best.
        """
        svc = (service_factory or ArtifactService)(db_path)
        planned: List[Target] = []
        report = PlanningReport()

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

                for win in windows:
                    tgt = Target(kind=node.kind, scope=scope, window=win)
                    # Idempotency: skip if already satisfied
                    existing = svc.select_best(kind=node.kind, scope=scope, at_time=win.start, policy="latest_valid")
                    if existing is not None:
                        report.existing.append(tgt)
                        report.reasons[_tkey(tgt)] = "already_registered"
                        continue
                    planned.append(tgt)

        report.planned = planned
        return planned, report


def _tkey(t: Target) -> str:
    w = t.window
    ws = w.start.isoformat() if (w and w.start) else "-"
    we = w.end.isoformat() if (w and w.end) else "-"
    return f"{t.kind}:{t.scope.zipcode}:{ws}:{we}"
