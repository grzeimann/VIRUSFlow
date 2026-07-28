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
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..artifacts.models import Scope
from ..artifacts.service import ArtifactService
from ..registry import database as db

from .targets import CalibrationGroup, PurposeCadence, Target, TemporalWindow, CadencePolicy


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
    cadence_groups: List[dict] = field(default_factory=list)
    exclusions: List[dict] = field(default_factory=list)
    lamp_pairs: List[dict] = field(default_factory=list)


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
        available: Dict[Tuple[str, str], List[Target]] = {}
        effective_raw_inputs: set[Tuple[str, str, Tuple[int, ...]]] = set()

        def scope_key(scope: Scope) -> str:
            zipcode = getattr(scope, "zipcode", None)
            return zipcode.key() if zipcode is not None else "__global__"

        def already_has_inputs(target: Target) -> bool:
            if target.group is None:
                return False

            def qa_accepts(row: dict) -> bool:
                qa = svc.adapter.get_qa_bundle(int(row["id"]))
                if qa and (
                    str(qa.get("status") or "").lower() == "fail"
                    or str(qa.get("usability") or "").lower() == "unusable"
                ):
                    return False
                # The read-noise gate changed from the original no-op bias
                # policy. Re-evaluate an otherwise identical bias when its
                # persisted decision predates the active policy.
                if target.kind == "master_bias":
                    expected = svc.diagnostics.policy_version_for(target.kind)
                    if not qa or str(qa.get("policy_version") or "") != expected:
                        return False
                return True

            for row in svc.adapter.list_all(kind=target.kind):
                if row.get("amp_key") != scope_key(target.scope):
                    continue
                registered_group_id = (row.get("metadata") or {}).get("calibration_group_id")
                if registered_group_id == target.group.group_id:
                    if qa_accepts(row):
                        return True
                    continue
                # Parent-only matching is a compatibility fallback for records
                # predating group identities.  A record with a different group
                # identity may represent a new algorithm/configuration revision
                # over the same raw frames and must not suppress that work.
                if registered_group_id is not None:
                    continue
                if not target.group.raw_ids:
                    continue
                wanted = list(target.group.raw_ids)
                parents = row.get("parents") or []
                if isinstance(parents, str):
                    parents = [int(value) for value in parents.split(",") if value]
                if sorted(int(value) for value in parents) == sorted(wanted):
                    if qa_accepts(row):
                        return True
                    continue
            return False

        def emit(target: Target) -> None:
            key = (target.kind, scope_key(target.scope))
            available.setdefault(key, []).append(target)
            if not force_replan and already_has_inputs(target):
                report.existing.append(target)
                report.reasons[_tkey(target)] = "already_registered_same_effective_inputs"
                return
            if force_replan:
                report.reasons[_tkey(target)] = "forced_replan"
            planned.append(target)

        def bounds() -> tuple[str | None, str | None]:
            if when is None:
                return None, None
            return (
                when.start.strftime("%Y%m%d") if when.start else None,
                when.end.strftime("%Y%m%d") if when.end else None,
            )

        for node in self.nodes:
            has_raw = bool(node.inputs_raw)
            has_up = bool(node.inputs_artifacts)
            if has_raw and isinstance(node.cadence, PurposeCadence):
                from .cadence import resolve_calibration_groups

                start_date, end_date = bounds()
                for scope in scopes:
                    result = resolve_calibration_groups(
                        kind=node.kind, cadence=node.cadence, scope=scope, db_path=db_path,
                        start_date=start_date, end_date=end_date,
                    )
                    report.exclusions.extend({"kind": node.kind, "zipcode": scope_key(scope), **item}
                                             for item in result.exclusions)
                    for group in result.groups:
                        applicable_start = datetime.fromisoformat(group.applicability["start"])
                        applicable_end = datetime.fromisoformat(group.applicability["end"])
                        emit(Target(
                            kind=node.kind, scope=scope,
                            window=TemporalWindow(applicable_start, applicable_end),
                            group=group,
                        ))
                continue

            if node.kind == "master_arc" and has_up:
                from .cadence import pair_lamp_groups

                pairing_hours = float(
                    getattr(node.cadence, "options", {}).get(
                        "maximum_pair_separation_hours", 3.0
                    )
                )
                for scope in scopes:
                    hg = [target.group for target in available.get(("master_hg", scope_key(scope)), [])
                          if target.group is not None]
                    cd = [target.group for target in available.get(("master_cd", scope_key(scope)), [])
                          if target.group is not None]
                    pairs, unresolved = pair_lamp_groups(
                        hg, cd, maximum_separation_hours=pairing_hours
                    )
                    report.exclusions.extend({"kind": "master_arc", "zipcode": scope_key(scope), **item}
                                             for item in unresolved)
                    for left, right, separation in pairs:
                        identity = hashlib.sha256(json.dumps({
                            "kind": "master_arc", "zipcode": scope_key(scope),
                            "master_hg": left.computation_id, "master_cd": right.computation_id,
                            "algorithm": "hg-plus-cd-1.0",
                        }, sort_keys=True).encode("utf-8")).hexdigest()[:24]
                        times = tuple(sorted((*left.timestamps, *right.timestamps)))
                        group = CalibrationGroup(
                            group_id=f"master_arc:{scope_key(scope)}:{identity[:12]}",
                            computation_id=identity, policy="paired_nearest_center",
                            raw_ids=(), exposure_ids=left.exposure_ids + right.exposure_ids,
                            timestamps=times,
                            metadata={
                                "master_hg_group": left.group_id, "master_cd_group": right.group_id,
                                "time_separation_seconds": separation,
                                "pairing_rule": "nearest center, one-to-one, stable group-id tie break",
                            },
                            applicability={"start": times[0].isoformat(), "end": times[-1].isoformat(),
                                           "pairing_tolerance_hours": pairing_hours},
                        )
                        target = Target(
                            kind="master_arc", scope=scope,
                            window=TemporalWindow(times[0], times[-1]), group=group,
                            parent_groups=(("master_hg", left.group_id), ("master_cd", right.group_id)),
                        )
                        report.lamp_pairs.append({
                            "zipcode": scope_key(scope), "master_arc_group": group.group_id,
                            "master_hg_group": left.group_id, "master_cd_group": right.group_id,
                            "time_separation_seconds": separation,
                        })
                        emit(target)
                continue

            if node.cadence is None and has_up:
                for scope in scopes:
                    sources = available.get(((node.inputs_artifacts or [""])[0], scope_key(scope)), [])
                    if not sources:
                        continue
                    for source in sources:
                        if source.group is None:
                            continue
                        parents = [(node.inputs_artifacts[0], source.group.group_id)]
                        compatible = True
                        center = source.group.timestamps[0] + (
                            source.group.timestamps[-1] - source.group.timestamps[0]
                        ) / 2
                        for upstream in (node.inputs_artifacts or [])[1:]:
                            choices = [candidate for candidate in available.get((upstream, scope_key(scope)), [])
                                       if candidate.group is not None]
                            if not choices:
                                compatible = False
                                break
                            choice = min(choices, key=lambda candidate: (
                                abs(((candidate.group.timestamps[0] +
                                      (candidate.group.timestamps[-1] - candidate.group.timestamps[0]) / 2)
                                     - center).total_seconds()), candidate.group.group_id
                            ))
                            parents.append((upstream, choice.group.group_id))
                        if not compatible:
                            continue
                        identity = hashlib.sha256(json.dumps({
                            "kind": node.kind, "parents": parents, "zipcode": scope_key(scope)
                        }, sort_keys=True).encode("utf-8")).hexdigest()[:24]
                        group = CalibrationGroup(
                            group_id=f"{node.kind}:{scope_key(scope)}:{identity[:12]}",
                            computation_id=identity, policy="derived_from_resolved_groups",
                            raw_ids=(), exposure_ids=source.group.exposure_ids,
                            timestamps=source.group.timestamps,
                            metadata={"parent_groups": parents},
                            applicability=dict(source.group.applicability),
                        )
                        emit(Target(
                            kind=node.kind, scope=scope, window=source.window,
                            group=group, parent_groups=tuple(parents),
                        ))
                continue

            # Compatibility for custom cadence policies used by existing callers.
            if node.cadence is not None and has_raw:
                from .targets import TimeCadence, ExposureCountCadence
                from .cadence import exposure_count_windows, time_cadence_windows

                for scope in scopes:
                    frame_type = (node.inputs_raw or [""])[0]
                    start_date, end_date = bounds()
                    try:
                        windows = node.cadence.windows(frame_type=frame_type, scope=scope, db_path=db_path)
                    except NotImplementedError:
                        if isinstance(node.cadence, TimeCadence):
                            windows = time_cadence_windows(
                                db_path=db_path, scope=scope, frame_type=frame_type,
                                every_days=node.cadence.every_days,
                                min_n_inputs=node.cadence.min_n_inputs,
                                start_date=start_date, end_date=end_date,
                            )
                        elif isinstance(node.cadence, ExposureCountCadence):
                            windows = exposure_count_windows(
                                db_path=db_path, scope=scope, frame_type=frame_type,
                                min_n=node.cadence.min_n, max_span_days=node.cadence.max_span_days,
                                start_date=start_date, end_date=end_date,
                            )
                        else:
                            windows = []
                    for window in windows:
                        rows = db.list_raw_files_scoped(
                            frame_type=frame_type,
                            start_date=window.start.strftime("%Y%m%d") if window.start else "19000101",
                            end_date=window.end.strftime("%Y%m%d") if window.end else "21000101",
                            zipcode=scope.zipcode, db_path=db_path,
                            start_time=window.start, end_time=window.end,
                        )
                        raw_ids = tuple(sorted(int(row_id) for row_id, _ in rows))
                        effective = (node.kind, scope_key(scope), raw_ids)
                        target = Target(kind=node.kind, scope=scope, window=window)
                        if raw_ids and effective in effective_raw_inputs:
                            report.skipped.append(target)
                            report.reasons[_tkey(target)] = "duplicate_effective_raw_inputs"
                            continue
                        effective_raw_inputs.add(effective)
                        emit(target)

        report.planned = planned
        requesters: dict[tuple[str, str], list[str]] = {}
        for target in [*report.planned, *report.existing]:
            child = target.group.group_id if target.group else _tkey(target)
            for parent in target.parent_groups:
                requesters.setdefault(parent, []).append(child)
        for target in [*report.planned, *report.existing, *report.skipped]:
            if target.group is None:
                continue
            group = target.group
            report.cadence_groups.append({
                "kind": target.kind, "zipcode": scope_key(target.scope),
                "group_id": group.group_id, "computation_identity": group.computation_id,
                "policy": group.policy,
                "start": group.timestamps[0].isoformat(timespec="microseconds"),
                "end": group.timestamps[-1].isoformat(timespec="microseconds"),
                "raw_ids": list(group.raw_ids), "exposure_ids": list(group.exposure_ids),
                "metadata": group.metadata, "applicability": group.applicability,
                "sufficient": group.sufficient, "decision": group.decision,
                "downstream_requesters": requesters.get((target.kind, group.group_id), []),
            })
        return planned, report


def _tkey(t: Target) -> str:
    w = t.window
    ws = w.start.isoformat() if (w and w.start) else "-"
    we = w.end.isoformat() if (w and w.end) else "-"
    identity = t.group.computation_id if t.group is not None else "-"
    return f"{t.kind}:{t.scope.zipcode}:{identity}:{ws}:{we}"
