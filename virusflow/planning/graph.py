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
from enum import Enum
from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..artifacts.models import Scope
from ..ontology.entities import MeasurementGroup, MeasurementGroupSlot
from ..ontology.scopes import PhysicalScope
from ..artifacts.service import ArtifactService
from ..registry import database as db

from .targets import (CalibrationGroup, CadencePolicy, MeasurementGroupSelection,
                      PurposeCadence, Target, TemporalWindow)


class SelectionUnit(str, Enum):
    ARTIFACT = "artifact"
    MEASUREMENT_GROUP = "measurement_group"


@dataclass(frozen=True)
class MeasurementGroupingSpec:
    coherence_rule: str
    coherence_rule_version: str
    anchor_input_kinds: tuple[str, ...] = ()
    grouping_parameters: Dict[str, Any] = field(default_factory=dict)


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
    measurement_grouping: Optional[MeasurementGroupingSpec] = None


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
    selection_unit: SelectionUnit = SelectionUnit.ARTIFACT


@dataclass
class PlanningReport:
    planned: List[Target] = field(default_factory=list)
    skipped: List[Target] = field(default_factory=list)
    existing: List[Target] = field(default_factory=list)
    terminal: List[Target] = field(default_factory=list)
    reasons: Dict[str, str] = field(default_factory=dict)  # target_key -> reason
    cadence_groups: List[dict] = field(default_factory=list)
    exclusions: List[dict] = field(default_factory=list)
    lamp_pairs: List[dict] = field(default_factory=list)


def target_node_id(target: Target) -> str:
    """Return the stable execution identity shared by planner and scheduler."""

    window = target.window
    start = getattr(window, "start", None)
    end = getattr(window, "end", None)
    zipcode = getattr(target.scope, "zipcode", None)
    zkey = (
        zipcode.ifuslot, zipcode.ifuid, zipcode.specid,
        zipcode.amp, zipcode.controller,
    ) if zipcode is not None else (None,)
    identity = getattr(getattr(target, "group", None), "computation_id", None)
    start_value = getattr(start, "isoformat", lambda: None)()
    end_value = getattr(end, "isoformat", lambda: None)()
    return (
        f"{target.kind}:{':'.join(str(value) for value in zkey)}:"
        f"{identity or start_value}:{end_value}"
    )


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
        raw_db_path: Optional[str] = None,
    ) -> Tuple[List[Target], PlanningReport]:
        """Emit a set of Targets to execute in topological order.

        - For nodes with cadence, enumerate calibration targets using cadence helpers.
        - For nodes without cadence, do not auto-emit (expect an external list of
          science exposures or ad-hoc targets for now).
        - Skip targets already satisfied according to ArtifactService.select_best,
          honoring tolerance where applicable.
        """
        raw_db_path = raw_db_path or db_path
        svc = (service_factory or ArtifactService)(db_path)
        planned: List[Target] = []
        report = PlanningReport()
        available: Dict[Tuple[str, str], List[Target]] = {}
        effective_raw_inputs: set[Tuple[str, str, Tuple[int, ...]]] = set()
        terminal_groups: set[Tuple[str, str]] = set()
        terminal_by_scope: Dict[Tuple[str, str], List[Target]] = {}

        def scope_key(scope: Scope) -> str:
            zipcode = getattr(scope, "zipcode", None)
            return zipcode.key() if zipcode is not None else "__global__"

        def optional_input(dst_kind: str, src_kind: str) -> bool:
            return any(
                edge.src.kind == src_kind
                and edge.dst.kind == dst_kind
                and edge.policy.startswith("optional_")
                for edge in self.edges
            )

        evidence_loader = getattr(svc.adapter, "list_planning_evidence", None)
        evidence_snapshot = evidence_loader() if callable(evidence_loader) else None
        evidence_by_scope: Dict[Tuple[str, str], List[dict]] = {}
        if evidence_snapshot is not None:
            for row in evidence_snapshot:
                evidence_by_scope.setdefault(
                    (str(row.get("kind") or ""), str(row.get("amp_key") or "")), []
                ).append(row)
        failure_loader = getattr(svc.adapter, "list_terminal_task_failures", None)
        terminal_task_failures = {
            str(row["task_id"]): row
            for row in (failure_loader() if callable(failure_loader) else [])
        }

        def qa_disposition(row: dict, target: Target) -> str:
            if "qa_status" in row:
                status = str(row.get("qa_status") or "").lower()
                usability = str(row.get("qa_usability") or "").lower()
                policy_version = str(row.get("qa_policy_version") or "")
                has_qa = bool(status or usability or policy_version)
            else:
                qa = svc.adapter.get_qa_bundle(int(row["id"]))
                status = str((qa or {}).get("status") or "").lower()
                usability = str((qa or {}).get("usability") or "").lower()
                policy_version = str((qa or {}).get("policy_version") or "")
                has_qa = bool(qa)
            expected_policy = str(svc.diagnostics.policy_version_for(target.kind) or "")
            failed = status == "fail" or usability == "unusable"
            if failed:
                if has_qa and policy_version == expected_policy:
                    return "terminal_qa_failure"
                return "replan"
            # The read-noise gate changed from the original no-op bias policy.
            if target.kind == "master_bias" and (
                not has_qa or policy_version != expected_policy
            ):
                return "replan"
            return "usable"

        def rows_for(target: Target) -> List[dict]:
            key = (target.kind, scope_key(target.scope))
            if evidence_snapshot is not None:
                return evidence_by_scope.get(key, []) or (
                    evidence_by_scope.get((target.kind, ""), [])
                    if key[1] == "__global__" else []
                )
            return [
                row for row in svc.adapter.list_all(kind=target.kind)
                if row.get("amp_key") == key[1]
            ]

        def already_has_inputs(target: Target) -> str:
            if target.group is None:
                return "missing"

            matched_terminal = False
            for row in rows_for(target):
                registered_group_id = (row.get("metadata") or {}).get("calibration_group_id")
                if registered_group_id == target.group.group_id:
                    disposition = qa_disposition(row, target)
                    if disposition == "usable":
                        return "existing"
                    matched_terminal |= disposition == "terminal_qa_failure"
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
                parents = row.get("raw_parent_ids") or row.get("parents") or []
                if isinstance(parents, str):
                    parents = [int(value) for value in parents.split(",") if value]
                if sorted(int(value) for value in parents) == sorted(wanted):
                    disposition = qa_disposition(row, target)
                    if disposition == "usable":
                        return "existing"
                    matched_terminal |= disposition == "terminal_qa_failure"
                    continue
            return "terminal_qa_failure" if matched_terminal else "missing"

        def target_center_of(target: Target) -> datetime | None:
            if target.window is None:
                return target.at_time
            start, end = target.window.start, target.window.end
            if start is not None and end is not None:
                return start + (end - start) / 2
            return start or end

        def terminal_blocker(target: Target) -> Tuple[str, str] | None:
            for parent in target.parent_groups:
                if parent in terminal_groups:
                    return parent
            target_center = target_center_of(target)
            for edge in self._incoming.get(target.kind, []):
                if edge.policy != "qa_gate":
                    continue
                candidates = terminal_by_scope.get(
                    (edge.src.kind, scope_key(target.scope)), []
                )
                for candidate in candidates:
                    if target.window is not None and candidate.window is not None:
                        target_start, target_end = target.window.start, target.window.end
                        source_start, source_end = candidate.window.start, candidate.window.end
                        if (
                            target_start is not None and target_end is not None
                            and source_start is not None and source_end is not None
                            and source_start <= target_end and target_start <= source_end
                        ):
                            return (edge.src.kind, candidate.group.group_id)
                    candidate_center = target_center_of(candidate)
                    if target_center is None or candidate_center is None:
                        return (edge.src.kind, candidate.group.group_id)
                    if abs((candidate_center - target_center).total_seconds()) <= (
                        max(0, int(edge.tolerance_days)) * 86400
                    ):
                        return (edge.src.kind, candidate.group.group_id)
            return None

        def mark_terminal(target: Target, reason: str) -> None:
            report.terminal.append(target)
            report.reasons[_tkey(target)] = reason
            if target.group is not None:
                terminal_groups.add((target.kind, target.group.group_id))
            terminal_by_scope.setdefault(
                (target.kind, scope_key(target.scope)), []
            ).append(target)

        def emit(target: Target) -> None:
            key = (target.kind, scope_key(target.scope))
            available.setdefault(key, []).append(target)
            if not force_replan:
                prior_failure = terminal_task_failures.get(target_node_id(target))
                if prior_failure is not None:
                    mark_terminal(
                        target,
                        "already_recorded_terminal_task_failure:"
                        f"{prior_failure.get('error') or prior_failure.get('task_kind')}",
                    )
                    return
                blocker = terminal_blocker(target)
                if blocker is not None:
                    mark_terminal(
                        target,
                        f"blocked_by_terminal_qa_failure:{blocker[0]}:{blocker[1]}",
                    )
                    return
                disposition = already_has_inputs(target)
                if disposition == "existing":
                    report.existing.append(target)
                    report.reasons[_tkey(target)] = "already_registered_same_effective_inputs"
                    return
                if disposition == "terminal_qa_failure":
                    mark_terminal(target, "already_registered_terminal_qa_failure")
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

        start_date, end_date = bounds()
        requested_scopes = {scope_key(scope) for scope in scopes}
        raw_grouping_by_scope: Dict[str, List[dict]] = {}
        if any(bool(node.inputs_raw) and isinstance(node.cadence, PurposeCadence)
               for node in self.nodes):
            for row in db.list_calibration_grouping_rows_bulk(
                db_path=raw_db_path, start_date=start_date, end_date=end_date,
            ):
                amp_key = str(row.get("amp_key") or "")
                if amp_key in requested_scopes:
                    raw_grouping_by_scope.setdefault(amp_key, []).append(row)

        for node in self.nodes:
            has_raw = bool(node.inputs_raw)
            has_up = bool(node.inputs_artifacts)
            if has_raw and isinstance(node.cadence, PurposeCadence):
                from .cadence import resolve_calibration_groups

                for scope in scopes:
                    result = resolve_calibration_groups(
                        kind=node.kind, cadence=node.cadence, scope=scope, db_path=raw_db_path,
                        start_date=start_date, end_date=end_date,
                        source_rows=raw_grouping_by_scope.get(scope_key(scope), ()),
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

            if (
                node.cadence is None and has_up
                and node.scope_mode == "calibration_build"
            ):
                upstream = (node.inputs_artifacts or [""])[0]
                sources = [
                    target
                    for scope in scopes
                    for target in available.get((upstream, scope_key(scope)), [])
                    if target.group is not None
                ]
                builds: Dict[tuple, List[Target]] = {}
                for source in sources:
                    identity = tuple(source.group.exposure_ids) or (
                        source.window.start.isoformat() if source.window and source.window.start else None,
                        source.window.end.isoformat() if source.window and source.window.end else None,
                    )
                    builds.setdefault(identity, []).append(source)
                for exposure_ids, members in sorted(builds.items(), key=lambda item: str(item[0])):
                    members.sort(key=lambda target: scope_key(target.scope))
                    # An exposure-scoped product may consume several
                    # per-amplifier dependency kinds.  Carry every
                    # planner-resolved parent group forward so the task can
                    # resolve only graph-selected scheduled/cached rows.
                    parent_groups_list = []
                    for member in members:
                        parent = (upstream, member.group.group_id)
                        if parent not in parent_groups_list:
                            parent_groups_list.append(parent)
                        for dependency_kind in (node.inputs_artifacts or [])[1:]:
                            choices = [
                                candidate
                                for candidate in available.get(
                                    (dependency_kind, scope_key(member.scope)), []
                                )
                                if candidate.group is not None
                            ]
                            if not choices:
                                continue
                            exact = [
                                candidate for candidate in choices
                                if tuple(candidate.group.exposure_ids)
                                == tuple(member.group.exposure_ids)
                            ]
                            choice = min(exact or choices, key=lambda candidate: (
                                abs(((candidate.group.timestamps[0] +
                                      (candidate.group.timestamps[-1] - candidate.group.timestamps[0]) / 2)
                                     - (member.group.timestamps[0] +
                                        (member.group.timestamps[-1] - member.group.timestamps[0]) / 2)
                                     ).total_seconds()),
                                candidate.group.group_id,
                            ))
                            parent = (dependency_kind, choice.group.group_id)
                            if parent not in parent_groups_list:
                                parent_groups_list.append(parent)
                    parent_groups = tuple(parent_groups_list)
                    amplifier_keys = [scope_key(member.scope) for member in members]
                    identity = hashlib.sha256(json.dumps({
                        "kind": node.kind,
                        "parents": parent_groups,
                        "amplifier_keys": amplifier_keys,
                    }, sort_keys=True).encode("utf-8")).hexdigest()[:24]
                    timestamps = tuple(sorted({
                        timestamp for member in members for timestamp in member.group.timestamps
                    }))
                    starts = [
                        member.window.start for member in members
                        if member.window is not None and member.window.start is not None
                    ]
                    ends = [
                        member.window.end for member in members
                        if member.window is not None and member.window.end is not None
                    ]
                    start = max(starts) if starts else timestamps[0]
                    end = min(ends) if ends else timestamps[-1]
                    group = CalibrationGroup(
                        group_id=f"{node.kind}:build:{identity[:12]}",
                        computation_id=identity,
                        policy="coherent_calibration_build",
                        raw_ids=(),
                        exposure_ids=tuple(str(value) for value in exposure_ids if value),
                        timestamps=timestamps,
                        metadata={
                            "parent_groups": list(parent_groups),
                            "amplifier_keys": amplifier_keys,
                            "calibration_build_id": identity,
                        },
                        applicability={
                            "start": start.isoformat(), "end": end.isoformat(),
                            "selection_domain": "coherent_center_track_twilight_build",
                        },
                    )
                    selections = []
                    for dependency_kind in node.inputs_artifacts or ():
                        candidates = [candidate for scope in scopes for candidate in
                                      available.get((dependency_kind, scope_key(scope)), [])
                                      if candidate.group is not None and
                                      tuple(candidate.group.exposure_ids) == tuple(exposure_ids)]
                        if not candidates:
                            continue
                        definition = MeasurementGroup(
                            member_kind=dependency_kind,
                            coherence_rule="shared_exposure_set",
                            coherence_rule_version="1",
                            coherence_key={"exposure_ids": sorted(exposure_ids)},
                            declared_slots=tuple(MeasurementGroupSlot(
                                scope_key(candidate.scope), candidate.group.computation_id
                            ) for candidate in candidates),
                        )
                        edge = next(edge for edge in self._incoming[node.kind]
                                    if edge.src.kind == dependency_kind)
                        # Persisted normalized groups are candidates too.  The
                        # registry merely supplies facts; this planner owns the
                        # deterministic whole-cohort choice.
                        persisted = svc.adapter.list_measurement_groups(dependency_kind)
                        matching = [item for item in persisted if
                                    item.get("coherence_key", {}).get("exposure_ids")
                                    == sorted(exposure_ids)]
                        if matching:
                            slot_rows = svc.adapter.list_measurement_group_slots(
                                [item["measurement_group_id"] for item in matching]
                            )
                            by_group = {}
                            for slot in slot_rows:
                                by_group.setdefault(slot["measurement_group_id"], []).append(slot)
                            ranked = []
                            for item in matching:
                                slots = by_group.get(item["measurement_group_id"], [])
                                usable = [slot for slot in slots if slot.get("artifact_id") is not None
                                          and str(slot.get("state") or "active") == "active"
                                          and str(slot.get("qa_usability") or "usable") != "unusable"]
                                ranked.append((len(usable), str(item["measurement_group_id"]), item, slots))
                            _, _, selected_item, selected_slots = max(ranked, key=lambda value: (value[0], value[1]))
                            definition = MeasurementGroup(
                                member_kind=selected_item["member_kind"],
                                coherence_rule=selected_item["coherence_rule"],
                                coherence_rule_version=selected_item["coherence_rule_version"],
                                coherence_key=selected_item["coherence_key"],
                                declared_slots=tuple(MeasurementGroupSlot(
                                    slot["member_scope_key"], slot["member_computation_id"]
                                ) for slot in selected_slots),
                                anchor_measurement_group_ids=tuple(selected_item.get("anchor_group_ids") or ()),
                                grouping_parameters=selected_item.get("grouping_parameters") or {},
                                configuration_references=tuple(selected_item.get("configuration_refs") or ()),
                                measurement_group_id=selected_item["measurement_group_id"],
                            )
                            existing = {slot["member_scope_key"]: int(slot["artifact_id"])
                                        for slot in selected_slots if slot.get("artifact_id") is not None}
                        else:
                            existing = {}
                        selections.append(MeasurementGroupSelection(
                            input_name=dependency_kind, group=definition,
                            existing_artifact_ids=existing,
                            scheduled_node_ids={scope_key(candidate.scope): target_node_id(candidate)
                                                for candidate in candidates if candidate in planned},
                            requested_scope_keys=tuple(scope_key(member.scope) for member in members),
                            policy=edge.policy, reason={"coherence_key": definition.coherence_key},
                        ))
                    emit(Target(
                        kind=node.kind,
                        scope=Scope(zipcode=None, physical_scope=PhysicalScope.EXPOSURE),
                        window=TemporalWindow(start, end),
                        group=group,
                        parent_groups=parent_groups,
                        selected_measurement_groups=tuple(selections),
                    ))
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
                                if optional_input(node.kind, upstream):
                                    continue
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
                        paired_keys = None
                        if node.scope_mode == "physical_ccd_pair":
                            zipcode = scope.zipcode
                            partner_amp = {"LL": "LU", "LU": "LL", "RU": "RL", "RL": "RU"}[
                                zipcode.amp
                            ]
                            partner_key = type(zipcode)(
                                zipcode.ifuslot, zipcode.ifuid, zipcode.specid,
                                partner_amp, zipcode.controller,
                            ).key()
                            paired_keys = sorted([scope_key(scope), partner_key])
                            for upstream in node.inputs_artifacts or []:
                                choices = [
                                    candidate for candidate in available.get((upstream, partner_key), [])
                                    if candidate.group is not None
                                ]
                                if not choices:
                                    if optional_input(node.kind, upstream):
                                        continue
                                    compatible = False
                                    break
                                exact = [
                                    candidate for candidate in choices
                                    if tuple(candidate.group.exposure_ids)
                                    == tuple(source.group.exposure_ids)
                                ]
                                pool = exact or choices
                                choice = min(pool, key=lambda candidate: (
                                    abs(((candidate.group.timestamps[0] +
                                          (candidate.group.timestamps[-1] - candidate.group.timestamps[0]) / 2)
                                         - center).total_seconds()),
                                    candidate.group.group_id,
                                ))
                                parent = (upstream, choice.group.group_id)
                                if parent not in parents:
                                    parents.append(parent)
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
                            metadata={
                                "parent_groups": parents,
                                "paired_amplifier_keys": paired_keys,
                            },
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
                        windows = node.cadence.windows(frame_type=frame_type, scope=scope, db_path=raw_db_path)
                    except NotImplementedError:
                        if isinstance(node.cadence, TimeCadence):
                            windows = time_cadence_windows(
                                db_path=raw_db_path, scope=scope, frame_type=frame_type,
                                every_days=node.cadence.every_days,
                                min_n_inputs=node.cadence.min_n_inputs,
                                start_date=start_date, end_date=end_date,
                            )
                        elif isinstance(node.cadence, ExposureCountCadence):
                            windows = exposure_count_windows(
                                db_path=raw_db_path, scope=scope, frame_type=frame_type,
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
                            zipcode=scope.zipcode, db_path=raw_db_path,
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
        for target in [*report.planned, *report.existing, *report.terminal]:
            child = target.group.group_id if target.group else _tkey(target)
            for parent in target.parent_groups:
                requesters.setdefault(parent, []).append(child)
        for target in [
            *report.planned, *report.existing, *report.terminal, *report.skipped,
        ]:
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
