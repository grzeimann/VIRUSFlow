# MeasurementGroup Implementation Plan for Terra

> Execution profile: Terra, medium reasoning.
>
> Status: implementation-ready plan; no implementation is included here.
>
> Architectural basis:
> `docs/architecture/Coherent_Measurement_Groups.md`.

## Objective and boundaries

Implement persistent coherent cross-amplifier measurement grouping without
changing the numerical behavior of `fit_exposure_fiber_response`.

The implementation must preserve:

- authoritative amplifier Artifacts and exact concrete parentage;
- existing per-scope `CalibrationGroup` computation/applicability behavior;
- planner-only selection authority through `ReductionGraph` and `Edge.policy`;
- immutable declared group cohorts and insert-once slot realization;
- incomplete groups without cross-group member substitution;
- frozen existing Artifact IDs and scheduled task-node IDs at plan time;
- both group-selection provenance and ordinary Artifact parent provenance;
- existing Artifact, QA, validity, scope, publication, and registry machinery;
- PEP 8 compliance, including the repository's 100-character Ruff line limit.

Do not add an Artifact kind, numerical payload, product-specific group class,
new physical-scope vocabulary, general group-selection service, or parallel
provenance framework.

## Repository-validated starting point

These findings are implementation constraints, not design questions for Terra
to reopen:

1. `planning.targets.CalibrationGroup` is a transient per-scope computation
   identity. Its `computation_id` excludes applicability by design. Keep it and
   add only the shared coherence evidence needed for cross-scope grouping.
2. `planning.cadence._make_group` hashes raw IDs, ZipCode, algorithm version,
   parameters, and configuration references. Its `exposure_ids` already supply
   much of the shared root-measurement evidence.
3. Derived `CalibrationGroup` identities in `ReductionGraph.plan` hash parent
   groups and scope, but the current `calibration_build` branch independently
   chooses LDLS, wavelength, and optional science parents for each amplifier.
   That branch is the unphysical fan-in to replace.
4. `Target.parent_groups` and `scheduler.schedule` already bind exact planned
   per-scope groups. They remain useful and are not the persistent
   cross-amplifier entity.
5. The CLI schedules only `report.planned`; `report.existing` is not executed.
   A frozen group selection therefore needs concrete IDs for cached members and
   task-node IDs for planned members.
6. `PlanningTargetAdapter` currently forwards group metadata/raw IDs but not
   typed group declarations or selections. It must forward them explicitly.
7. `PlanningExecutor` passes dependency results keyed by task-node ID, which is
   sufficient for frozen scheduled-member bindings. It currently blocks a node
   after any failed dependency. Its indegree/release logic already waits for
   every dependency to become terminal, so incomplete group fan-in needs only
   a narrow success-tolerant subset for the failure-blocking check.
8. `artifact_records.metadata_json["calibration_group_id"]` and
   `RegistryAdapter.find_by_calibration_groups` are exact per-scope compatibility
   machinery. Keep them for callers such as `_ExtractedMasterSpectrumTask`; do
   not use them as the new cross-scope relationship.
9. `ArtifactRequest.parents`, `Provenance.parents`, `artifact_relations`, and
   `ArtifactService._stable_parent_identity` already provide exact numerical
   lineage and immutable parent identity. Extend rather than replace them.
10. `ArtifactService._logical_revision` already hashes effective inputs,
    task/algorithm identity, parameters, configuration references, scientific
    metadata, and component content. Add selected group IDs only; selection
    reasons and applicability stay out of computation identity.
11. `RegistryAdapter.register` writes an older Artifact shell and then calls
    `save_artifact_details`. Database connections use autocommit, and
    `save_artifact_details` currently lacks an explicit transaction. Group
    slots and group-input rows must join one explicit canonical-detail
    transaction, while existing shell cleanup remains the failure fallback.
12. QA usability and policy versions are already available in the planner's
    bulk evidence path. Group QA/coverage must be derived, not persisted.
13. `ExposureFiberResponseTask` in the working tree already avoids
    `select_best`, but still resolves cached parents through
    `_planned_parent_rows` and planner-selected per-amplifier group metadata.
    Replace only this task's use of that mechanism; the helpers remain needed
    by `_ExtractedMasterSpectrumTask` unless separately proven obsolete.
14. `PhysicalScope.INSTRUMENT_EPOCH` already exists, but MeasurementGroup does
    not need its own Product scope. Member scope keys use existing
    Target/Artifact scope identity.
15. `algorithms.fiber_response.fit_exposure_fiber_response` is already pure and
    covered by `tests/test_fiber_response.py`. Keep it numerically unchanged.
16. `PlanningExecutor.raise_on_failure=True` raises `WorkflowExecutionError`
    only after the ready/in-flight loop drains. CLI
    `--strict-task-failures` likewise changes exit status only after execution.
    There is no current executor fail-fast mode to preserve or extend.
17. The default graph has a direct amplifier-local
    `master_bias -> exposure_fiber_response` QA-gate edge. Current same-scope
    scheduler matching makes it ineffective for a global target, but retaining
    it is misleading and could become globally blocking under generalized
    cross-scope binding. Remove it.
18. The current `calibration_build` branch contains an arbitrary
    `min_coverage_fraction = 0.9`. Delete it with the per-amplifier fan-in;
    generic planning/execution must not decide model sufficiency.

## Adjustments made to the architectural basis

The repository review required only these focused refinements to
`Coherent_Measurement_Groups.md`:

1. The declared cohort is explicitly immutable and identity-bearing; realized
   Artifact IDs fill declared slots but never define or mutate the group ID.
2. Frozen selections use scope-keyed existing-Artifact and scheduled-node
   bindings. This retains the exact slot association needed by the current
   scheduler/executor interface.
3. A selection carries the immutable group definition, while Artifact
   publication uses separate storage-neutral membership and group-input
   request records. Planning types do not leak into the Artifact layer.
4. Registry/service APIs provide declarations, realization, and bulk evidence
   queries only. `ReductionGraph` plus `Edge.policy` remains the sole ranking
   authority.
5. Formation stores only rule/key/cohort/anchor/material configuration facts.
   Existing Artifact, computation, configuration, QA, validity, publication,
   and provenance machinery remains authoritative for everything else.
6. Existing-revision and concurrent-revision publication fast paths must
   verify explicit group requests rather than bypassing them. No group is ever
   inferred from historical `calibration_group_id` JSON.
7. Same-run incomplete groups mark frozen member bindings as terminal-required
   and success-tolerant. Pending members still block readiness; terminal failed
   members are absent from task inputs but remain recorded failures.
8. No new physical scope is introduced. Existing ZipCode/`PhysicalScope`
   identity remains the member scope vocabulary.
9. The direct amplifier-local bias QA gate into the exposure-wide response is
   removed. Bias QA still blocks that amplifier's upstream measurement chain.
10. Executor and planner coverage constants are removed. Scientific
    sufficiency belongs to `ExposureFiberResponseTask`, its algorithm contract,
    and QA.

## Final minimal architecture

The final design has five small pieces:

1. A frozen `MeasurementGroup` definition contains one member kind, a named and
   versioned coherence rule/key, optional anchor group IDs and material grouping
   configuration, and an immutable ordered set of
   `(member_scope_key, member_computation_id)` slots.
2. Three registry tables store group definitions, declared slots with nullable
   realized Artifact IDs, and downstream Artifact-to-selected-group input
   provenance. Group slots move only from unrealized to realized.
3. One generic `MeasurementGroupingSpec` on `TaskSpec` forms root or inherited
   groups. Product differences are data in named/versioned rules and anchors.
4. `Edge.selection_unit` distinguishes ordinary Artifact selection from whole
   MeasurementGroup selection. `ReductionGraph` alone ranks candidates using
   `Edge.policy` and freezes scope-keyed cached IDs plus scheduled node IDs.
5. Publication realizes slots and persists group-input decisions. Every frozen
   scheduled member is a terminal-required dependency; member failures are
   success-tolerant only for aggregate readiness. The response task intersects
   successful usable members, applies scientific sufficiency, calls the
   unchanged algorithm, and publishes exact parent IDs plus selected group IDs.

Historical Artifacts are not automatically backfilled from per-scope JSON.
They remain valid for Artifact-unit edges but are not candidates for
MeasurementGroup-unit edges until explicit validated backfill or grouped
reprocessing.

## Ordered Terra task list

Execute these tasks sequentially. Each task should be a reviewable change with
its focused tests passing before the next begins.

### Task 1: Freeze the domain and identity contracts

Classification: **ADAPT** existing ontology/identity/Target machinery.

Inspect and modify:

- `virusflow/ontology/entities.py`
- `virusflow/ontology/__init__.py`
- `virusflow/core/identity.py`
- `virusflow/planning/targets.py`
- `virusflow/planning/__init__.py`

Work:

1. Add frozen `MeasurementGroupSlot` and `MeasurementGroup` value types. Do not
   import Artifact-layer `Scope` or planning types into ontology.
2. Add a canonical JSON/SHA-256 identity helper in `core.identity` or an
   equally low-level existing identity owner. Sort slots by scope key and use
   the full digest with an `mg:v1:` prefix.
3. Add `coherence_key` to `CalibrationGroup` as explicit shared evidence while
   retaining `group_id`, `computation_id`, and applicability semantics.
4. Add typed fields to `Target` for an output group declaration and frozen
   selected group inputs. Keep `parent_groups` unchanged.
5. Define the planning-only `MeasurementGroupSelection` with existing Artifact
   and scheduled-node bindings keyed by declared scope, the immutable group
   definition, requested scope keys, policy, match quality, and reason.

Expected result:

- identity is deterministic and independent of slot order, QA, realized
  Artifact IDs, creation time, and applicability;
- declared slots are immutable values;
- no new scope or Product concept is introduced.

Focused tests:

- add `tests/test_measurement_groups.py` identity tests;
- assert identity changes for coherence rule/version/key, anchor IDs, material
  grouping configuration, and any scope/computation slot change;
- assert identity does not change when input slot order changes;
- assert duplicate declared scope keys and post-construction mutation attempts
  are rejected;
- run `tests/test_calibration_grouping_policies.py` to protect existing
  computation/applicability separation.

Dependencies: none.

### Task 2: Add the minimal registry schema and read/write primitives

Classification: **ADAPT** the existing registry schema; no scientific backfill.

Inspect and modify:

- `virusflow/registry/database.py` (`ARTIFACT_SCHEMA`, `_init_schema`)
- `virusflow/artifacts/registry_adapter.py`

Work:

1. Add `measurement_groups`, `measurement_group_slots`, and
   `artifact_measurement_group_inputs` exactly as specified in the architecture
   document.
2. Add database functions and thin adapter wrappers to:
   declare a group and all slots; realize one declared slot; list candidate
   definitions by member kind in bulk; load all slots/member Artifact/QA/
   validity evidence for candidate IDs in bulk; save and list downstream group
   input records; list group memberships for one Artifact; and atomically apply
   explicit group relations to an already registered Artifact for the
   publication idempotency paths.
3. Implement slot realization as a conditional `NULL -> artifact_id` update.
   Accept idempotent repetition of the same Artifact and reject replacement or
   a computation/scope mismatch.
4. Do not rely on SQLite foreign-key enforcement alone; validate referenced
   group, slot, and Artifact rows explicitly because the current connection
   setup does not enable `PRAGMA foreign_keys=ON`.
5. Let normal `CREATE TABLE IF NOT EXISTS` initialization perform the schema
   migration. Do not infer historical groups from JSON metadata.

Expected result:

- immutable declarations and empty/realized slots are queryable without
  opening payloads;
- approximately 300 slots load through bounded bulk queries;
- no selection/ranking API exists in `RegistryAdapter` or `ArtifactService`.

Focused tests:

- schema creation on new and pre-existing temporary registries;
- byte-equivalent redeclaration is idempotent;
- conflicting redeclaration fails;
- empty slot realization succeeds once, repeats idempotently, and rejects a
  different Artifact;
- candidate/slot bulk query count is bounded using existing performance timing
  instrumentation.

Dependencies: Task 1.

### Task 3: Make canonical detail registration atomic

Classification: **REFACTOR** the existing canonical detail transaction only.

Inspect and modify:

- `virusflow/registry/database.py::save_artifact_details`
- `virusflow/artifacts/registry_adapter.py::RegistryAdapter.register`
- `tests/test_storage_materialization_revision.py`
- `tests/test_artifact_multicomponent.py`

Work:

1. Extend `save_artifact_details` parameters to accept group declarations,
   slot realizations, and selected group-input rows.
2. Wrap Artifact record, scientific metadata, components, Artifact relations,
   raw relations, group declarations/slots, slot realization, and group inputs
   in explicit `BEGIN IMMEDIATE`, `COMMIT`, and `ROLLBACK` handling.
3. Preserve `RegistryAdapter.register` cleanup of the older `artifacts` and
   `provenance` shell if canonical detail registration fails.
4. Do not include payload serialization in the database transaction and do not
   alter storage layout.

Expected result:

- a failed group/slot invariant leaves no partial canonical details, relations,
  group inputs, or realized slot;
- existing concurrent publication idempotency remains intact.

Focused tests:

- inject a slot conflict after component preparation and assert all canonical
  rows roll back and the shell is removed;
- rerun existing concurrent revision/publication tests;
- rerun multi-component lineage round-trip tests.

Dependencies: Task 2.

### Task 4: Extend ArtifactRequest, publication, and dual provenance

Classification: **ADAPT** existing Artifact publication and provenance.

Inspect and modify:

- `virusflow/artifacts/requests.py`
- `virusflow/artifacts/models.py` only if storage-neutral request value types
  need a shared owner
- `virusflow/publication/service.py::DefaultPublicationService`
- `virusflow/artifacts/service.py::ArtifactService.register`
- `virusflow/artifacts/service.py::ArtifactService.persist_request`
- `virusflow/artifacts/service.py::_logical_revision`
- `virusflow/artifacts/service.py::describe`

Work:

1. Add storage-neutral request records for output group membership and selected
   group inputs. Each relation request carries the immutable definition needed
   for idempotent declaration. Do not import planning-layer selection types
   into artifacts.
2. Validate that membership kind/scope/computation matches the declaration and
   Artifact request before persistence.
3. Feed the new rows through `RegistryAdapter.register` into the atomic detail
   transaction. Use explicit optional arguments through
   `ArtifactService.register`/`RegistryAdapter.register`; do not hide them on
   the Artifact as ad hoc attributes.
4. Include only ordered `(input_name, measurement_group_id)` pairs in logical
   revision identity. Keep selection policy, match quality, and reason as
   provenance outside computation identity.
5. Extend `ArtifactService.describe` to return normalized group memberships and
   selected group-input provenance alongside ordinary `provenance.parents`.
6. Keep `metadata["calibration_group_id"]` and existing Artifact relations for
   compatibility and exact lineage.
7. On both the pre-serialization existing-revision path and the concurrent
   revision-race path, validate and idempotently persist explicitly supplied
   declarations/memberships/group inputs before returning the winner. Never
   infer those relations from legacy metadata.

Expected result:

- member publication realizes its declared slot;
- downstream publication records group decisions and exact Artifact parents as
  distinct, queryable provenance;
- no `ArtifactService.select_measurement_group` method is added.

Focused tests:

- extend `tests/test_service.py` or add focused publication tests for group
  membership and describe round trip;
- verify group IDs change logical revision while policy/reason-only changes do
  not;
- verify ordinary parent relations are unchanged;
- verify invalid membership fails atomically;
- verify both existing-revision fast paths leave the requested normalized
  group rows present and reject a conflicting occupied slot.

Dependencies: Tasks 1-3.

### Task 5: Emit shared root coherence keys from calibration cadence

Classification: **ADAPT** existing cadence grouping.

Inspect and modify:

- `virusflow/planning/cadence.py::_make_group`
- `virusflow/planning/cadence.py::resolve_calibration_groups`
- `virusflow/planning/cadence.py::pair_lamp_groups`
- `virusflow/planning/graph.py` Master Arc target construction
- `tests/test_calibration_grouping_policies.py`

Work:

1. Compute a canonical cross-scope coherence key from scientific evidence
   shared across amplifiers: member kind/purpose, sorted exposure IDs,
   cadence/observing-block identity, and material grouping configuration.
2. Exclude amplifier-local raw row IDs and ZipCode from this shared key; they
   remain in each `CalibrationGroup.computation_id` and Artifact provenance.
3. For Hg/Cd and Master Arc, preserve separate lamp identities and deterministic
   pairing. Represent the paired anchor group identities explicitly rather
   than reducing them to a timestamp alone.
4. Do not change raw grouping membership, sufficiency decisions, or
   applicability windows.

Expected result:

- independent amplifier computations from the same accepted calibration
  exposure set expose the same coherence key;
- an amplifier with a different accepted exposure set is not silently included
  in that cohort.

Focused tests:

- same exposure set across two ZipCodes gives equal coherence keys and distinct
  computation IDs;
- missing/different source exposure changes the coherence key;
- existing nightly/monthly/weekly/isolated and Hg/Cd pairing tests remain
  unchanged in scientific outcome.

Dependencies: Task 1.

### Task 6: Add generic grouping and edge declarations

Classification: **ADAPT** `TaskSpec`, `Edge`, defaults, and configuration.

Inspect and modify:

- `virusflow/planning/graph.py::TaskSpec`
- `virusflow/planning/graph.py::Edge`
- `virusflow/planning/defaults.py::default_calibration_graph`
- `virusflow/planning/config.py`
- `virusflow/planning/validate.py`
- `virusflow/planning/__init__.py`
- `tests/test_canonical_calibration_graph.py`

Work:

1. Add one `MeasurementGroupingSpec` with rule, version, anchor input kinds,
   and material grouping parameters. Do not add subclasses by product kind.
2. Add `SelectionUnit.ARTIFACT` and `MEASUREMENT_GROUP`; default every existing
   edge to Artifact behavior.
3. Register group formation for the required root/intermediate families:
   twilight, LDLS, Hg, Cd, Master Arc, Master Science, extracted twilight,
   extracted LDLS, extracted Master Science, and wavelength maps.
4. Mark only the four edges into `exposure_fiber_response` as
   `MEASUREMENT_GROUP`.
5. Remove the direct `master_bias -> exposure_fiber_response` `qa_gate`. Keep
   amplifier-local bias gates on the per-amplifier upstream product chain so a
   failed bias removes that member naturally.
6. Reject an amplifier-local QA-gate edge directly into a cross-scope aggregate
   during graph/config validation. Genuine aggregate-scoped blocking
   prerequisites remain legal.
7. Parse and validate `selection_unit` in planning YAML without changing other
   edge policy syntax.
8. Give each grouped TaskSpec a stable planned computation identity/version so
   a material task/algorithm/parameter/configuration change cannot attempt to
   occupy an old declared slot. Reuse `CalibrationGroup.computation_id`; do not
   invent a second Artifact revision system.

Expected result:

- graph declarations fully describe generic formation and group-vs-Artifact
  selection;
- existing non-group edges remain behaviorally unchanged.

Focused tests:

- default graph declares exactly the intended grouping specs and four group
  edges;
- default graph has no bias QA-gate edge into `exposure_fiber_response`;
- config round trip accepts valid selection units and rejects unknown ones;
- graph validation catches an anchor kind not present in inputs;
- graph validation rejects an amplifier-local QA gate into a cross-scope
  aggregate and accepts a genuine aggregate-scoped blocker;
- existing default graph node/edge tests remain green.

Dependencies: Tasks 1 and 5.

### Task 7: Form immutable declared cohorts in ReductionGraph

Classification: **ADAPT** the planner; keep it read-only.

Inspect and modify:

- `virusflow/planning/graph.py::ReductionGraph.plan`
- `virusflow/planning/graph.py::PlanningReport`
- `virusflow/planning/graph.py::target_node_id`
- `virusflow/planning/targets.py`
- `virusflow/cli/virusflow.py::_run_planned` report serialization

Work:

1. After per-scope Targets for a grouped kind are resolved, cluster them by the
   registered coherence rule/anchor group identities.
2. Build an immutable declared slot for every eligible planned computation or
   existing computation already associated with that coherent cohort, sorted
   by scope key, and compute the group ID from Task 1. A legacy existing
   Artifact with no normalized group slot is not eligible and must not seed an
   inferred group.
3. Attach the declaration and current member slot to each planned member Target
   so normal publication can realize it.
4. Load persisted candidate definitions/slots in bulk and represent them with
   the same in-memory candidate shape as newly declared groups.
5. Keep planning read-only. Newly declared groups are persisted only through a
   normal member publication or downstream group-input publication.
6. Add declarations, realized/missing slot counts, and coherence evidence to
   `PlanningReport`; do not persist derived coverage on the group.
7. For derived groups, require every configured anchor parent, including both
   sides of a physical-CCD pair where applicable, to resolve to the declared
   anchor group or anchor-group tuple. Leave independently valid but
   incoherent outputs ungrouped.

Expected result:

- group identity exists before any scheduled member publishes;
- partial success cannot change the declared cohort or ID;
- no task contains formation logic.

Focused tests:

- group declaration is stable across input ordering;
- a planned cohort contains expected empty slots before execution;
- a persisted partial group and an in-memory scheduled group load through the
  same planner candidate path;
- planning report JSON serializes group declarations deterministically.

Dependencies: Tasks 2, 5, and 6.

### Task 8: Replace per-amplifier fan-in with planner-only whole-group selection

Classification: **REPLACE** the current `calibration_build` selection branch.

Inspect and modify:

- `virusflow/planning/graph.py::ReductionGraph.plan`, especially the
  `scope_mode == "calibration_build"` branch
- `virusflow/planning/defaults.py` response edges
- `virusflow/planning/targets.py::Target`
- `tests/test_canonical_calibration_graph.py`

Work:

1. For each coherent twilight group used as a response-build anchor, run
   `Edge.policy` once per input kind over group candidates, never once per
   amplifier.
2. Rank candidates using existing applicability/validity, current QA evidence,
   requested-scope coverage, tolerance, and stable group-ID tie-breaking.
   Keep policy implementation private to `ReductionGraph`; registry and
   Artifact service code supplies data only. Coverage may rank candidates but
   must not act as a generic minimum-fraction filter.
3. Freeze existing Artifact IDs, scheduled node IDs, and requested scope keys
   as scope-keyed bindings in each `MeasurementGroupSelection`.
4. Never add a member from the second-best group to repair the selected group.
5. Required groups may be incomplete. Require structural overlap sufficient to
   construct a candidate aggregate, but do not impose a planner coverage
   threshold. Optional science does not reduce the required intersection.
6. Remove the current loop that picks each additional dependency kind nearest
   to each amplifier member, along with its synthesized `parent_groups_list`,
   `coverage_complete`, `coverage_count`, and planner-time
   `excluded_amplifier_keys` group metadata. Report equivalent derived evidence
   in `PlanningReport`/selection reasons instead.
7. Retain `scope_mode="calibration_build"` only as a cross-scope output label if
   still useful; it must no longer contain selection semantics.
8. Delete `min_coverage_fraction = 0.9` and its healthy-majority branch. Neither
   planner nor executor owns model sufficiency.

Expected result:

- the plan contains one selected group identity per input role;
- two valid nights can never be mixed amplifier by amplifier;
- selection is frozen and auditable before execution.

Focused tests:

- construct two twilight groups on different nights with complementary
  amplifier coverage and prove the planner chooses one incomplete group rather
  than their union;
- prove a 296-of-300 intersection is not rejected by planner coverage logic;
- assert one LDLS, one wavelength, and at most one science group ID per target;
- assert deterministic policy tie-breaking;
- assert no registry/service group-selection method is called.

Dependencies: Task 7.

### Task 9: Bind frozen selections through adapter, scheduler, and executor

Classification: **ADAPT** existing scheduling; no new executor framework.

Inspect and modify:

- `virusflow/planning/adapter.py::PlanningTargetAdapter`
- `virusflow/planning/scheduler.py::ScheduledTask` and `schedule`
- `virusflow/executors/planning_executor.py::_Node`
- `virusflow/executors/planning_executor.py::PlanningExecutor.add_task`
- `virusflow/executors/planning_executor.py::PlanningExecutor.run`
- `virusflow/cli/virusflow.py::_run_planned`
- `tests/test_planning_smoke.py`
- `tests/test_progress_monitor.py`
- `tests/test_cli_execution_summary.py`

Work:

1. Forward output group declaration, current slot, requested scopes, and frozen
   group selections through `PlanningTargetAdapter` without hiding them in
   `group_metadata`.
2. Add every frozen scheduled member node to the response node's dependency
   list. Cached members remain exact IDs on the selection and are not scheduled.
3. Add an explicit `success_tolerant_dependencies` collection to
   `ScheduledTask`, executor `_Node`, and `add_task`. Require it to be a subset
   of `depends_on` and populate it only with frozen selected MeasurementGroup
   member nodes for cross-scope fan-in. Do not call these optional dependencies.
4. Preserve normal indegree accounting for every dependency. The aggregate is
   not ready while any success-tolerant member is pending, running, or retrying.
5. When readiness is reached, block only if an unsuccessful dependency is not
   in that node's success-tolerant set. Pass results only for dependencies that
   succeeded; failed or transitively blocked success-tolerant members remain
   absent.
6. Update the CLI's `add_task` call to forward
   `success_tolerant_dependencies`.
7. Preserve failed-member progress, traceback, performance, execution-report,
   `WorkflowExecutionError`, and strict CLI exit behavior. With the current
   deferred `raise_on_failure`, the aggregate must run before the final error is
   raised. Do not introduce or enable early fail-fast termination for a
   success-tolerant member failure.

Expected result:

- execution can realize 287 of 300 frozen slots and still run the response
  task on the successful required intersection;
- every frozen scheduled member reaches a terminal state before the response
  starts;
- no member that appeared after planning can enter the inputs;
- failure behavior for every non-group task remains unchanged.

Focused tests:

- scheduler binds exactly the frozen node IDs and cached IDs;
- an unrelated new group member published after planning is ignored;
- add executor-focused tests in `tests/test_progress_monitor.py` proving a
  pending success-tolerant member still blocks readiness;
- prove an early terminal failed success-tolerant member does not release the
  aggregate until every other frozen member is terminal;
- prove several terminal failed success-tolerant members still permit the
  aggregate and only successful results are passed;
- prove an ordinary dependency failure still blocks the aggregate;
- with `raise_on_failure=True`, prove the aggregate runs and only then is the
  retained member failure raised as `WorkflowExecutionError`;
- preserve CLI strict-mode tests showing exit semantics are evaluated after
  graph completion;
- cycle detection and existing planning smoke tests remain green.

Dependencies: Task 8.

### Task 10: Realize group slots through normal calibration publication

Classification: **ADAPT** the shared calibration publication path.

Inspect and modify:

- `virusflow/planning/adapter.py`
- `virusflow/tasks/calibs.py::_CanonicalTask._publish`
- `virusflow/tasks/calibs.py::_RawCalibrationTask` publication path
- any direct calibration `ArtifactRequest` publication used by grouped kinds
- `virusflow/publication/service.py`

Work:

1. Convert a planned output declaration/current slot into the storage-neutral
   membership request added in Task 4.
2. Make `_CanonicalTask._publish` attach it generically for every TaskSpec whose
   output is grouped. Do not duplicate logic in twilight, LDLS, wavelength, or
   extraction subclasses.
3. Ensure only the primary grouped scientific Artifact realizes the slot.
   Auxiliary `ccd_scattered_light_model` publications from
   `_ExtractedMasterSpectrumTask` are not members of the extracted-spectrum
   group.
4. Preserve current `calibration_group_id`, parent IDs, raw parents,
   configuration references, validity, and QA evaluation.

Expected result:

- successful scheduled outputs fill only their predetermined slots;
- failed/missing outputs leave slots empty;
- grouped publication remains generic across product kinds.

Focused tests:

- publish two amplifier members and inspect realized slots;
- fail one publication and verify its slot remains empty with no replacement;
- verify an auxiliary scatter Artifact is not a group member;
- rerun existing calibration publication and lineage tests.

Dependencies: Tasks 4, 7, and 9.

### Task 11: Convert ExposureFiberResponseTask to a frozen-group consumer

Classification: **REFACTOR** orchestration only; **KEEP** the numerical
algorithm unchanged.

Inspect and modify:

- `virusflow/tasks/calibs.py::ExposureFiberResponseTask.run`
- `virusflow/tasks/calibs.py::_CanonicalTask._publish`
- `tests/test_exposure_fiber_response_task.py`
- `tests/test_fiber_response.py`

Work:

1. Resolve cached inputs only from frozen existing Artifact IDs and scheduled
   inputs only from frozen node IDs. Do not query current group slots.
2. Resolve scheduled outputs directly by their frozen node IDs, then verify
   Artifact kind and scope against the corresponding declared slot. The slot's
   computation identity was frozen when the node binding was formed; do not
   reconstruct it through a runtime group query.
3. Load QA/usability only for those exact frozen Artifacts and compute the
   sorted intersection of requested scopes and usable realized members from all
   three required groups. Reuse a bulk evidence query keyed by those Artifact
   IDs; do not issue one QA query per amplifier and do not query group slots.
   Do not substitute missing scopes.
4. Apply explicit model/task/QA sufficiency criteria after computing the exact
   intersection. Do not add an executor or generic planner coverage threshold;
   a 296-of-300 intersection is valid when it meets those scientific criteria.
5. Preserve optional science all-or-nothing behavior over that required
   participant set.
6. Load components through `ArtifactService`, call
   `fit_exposure_fiber_response` with the existing arguments, and keep its
   numerical code untouched.
7. Publish all exact loaded Artifact IDs through `ArtifactRequest.parents` and
   every selected group decision through the new group-input requests: the
   three required groups and optional science when it was selected.
8. Remove this task's calls to `_planned_parent_rows` and its dependence on
   `group_metadata["parent_groups"]`/`["amplifier_keys"]`. Do not remove
   `_planned_parent_rows` or `_dependency_artifacts` globally because
   `_ExtractedMasterSpectrumTask` still uses them.

Expected result:

- the task performs no selection, formation, kind/time search, group query, or
  fallback;
- participant and exclusion metadata reflects only the frozen required-group
  intersection;
- exact parent and group provenance are both complete.

Focused tests:

- adapt the existing untracked `tests/test_exposure_fiber_response_task.py`
  characterization tests to typed frozen selections;
- assert `select_best`, `find_by_calibration_groups`, and group-slot queries are
  never called by the task;
- assert missing required members are excluded without substitution;
- assert 296 usable scopes out of 300 are accepted when the scientific
  sufficiency contract accepts them;
- assert incomplete optional science is omitted;
- assert exact parent IDs and selected group IDs are published;
- rerun `tests/test_fiber_response.py` unchanged to prove numerical stability.

Dependencies: Tasks 4, 9, and 10.

### Task 12: Complete integration, cleanup, migration behavior, and performance checks

Classification: **REMOVE** obsolete fan-in behavior; **ADAPT** reports/tests.

Inspect and modify:

- `tests/test_canonical_calibration_graph.py`
- `tests/test_service.py`
- `tests/test_planning_smoke.py`
- `tests/test_calibration_grouping_policies.py`
- `tests/test_storage_materialization_revision.py`
- `virusflow/cli/virusflow.py` planning-report serialization
- `docs/architecture/current-system.md`
- `docs/calibration-cadence.md` if its report schema is documented there

Work:

1. Replace tests that expect `calibration_build` to synthesize
   `amplifier_keys`, `coverage_complete`, or per-amplifier cross-kind
   `parent_groups` with group declaration, frozen selection, and derived report
   assertions.
2. Replace the current one-amplifier QA-gate characterization with an
   instrument-scale test: one amplifier-local bias/QA failure blocks that
   amplifier's upstream products, leaves its selected group slots
   missing/unusable, and still permits the exposure response from the remaining
   required intersection.
3. Delete dead response-only helper paths after Task 11. Keep per-scope
   `calibration_group_id`, `parent_groups`, and exact lookup code still used by
   extraction/other tasks.
4. Verify a no-op rerun recognizes existing member Artifacts and persistent
   groups without changing identities or adding duplicate slots.
5. Verify no automatic historical backfill occurs and document the explicit
   reprocessing/backfill requirement.
6. Measure planning query counts for approximately 300 synthetic slots and
   assert bounded bulk queries rather than per-amplifier selection queries.
7. Update current-system documentation only after tests describe the final
   implemented behavior.
8. Run focused tests, then the full suite and style checks:

```text
pytest -q tests/test_measurement_groups.py
pytest -q tests/test_calibration_grouping_policies.py
pytest -q tests/test_canonical_calibration_graph.py
pytest -q tests/test_exposure_fiber_response_task.py
pytest -q tests/test_service.py tests/test_artifact_multicomponent.py
pytest -q tests/test_storage_materialization_revision.py
pytest -q tests/test_planning_smoke.py tests/test_progress_monitor.py
pytest -q tests/test_cli_execution_summary.py tests/test_fiber_response.py
ruff check virusflow tests
black --check virusflow tests
pytest -q
```

Expected result:

- obsolete cross-kind per-amplifier fan-in is gone;
- repository documentation matches implemented behavior;
- planning remains fast at instrument scale;
- all touched Python is PEP 8 compliant.

Dependencies: Tasks 1-11.

## Critical final invariants

All of the following must hold before implementation is complete:

1. A `MeasurementGroup` is a registry/ontology relationship, never an
   Artifact, model, payload, or substitute provenance parent.
2. Group identity is the hash of one immutable coherence definition and
   declared scope/computation cohort, not realized Artifact IDs, QA, validity,
   selection policy, or creation time.
3. A declared slot is never deleted, changed, or redirected. Its only state
   transition is `artifact_id: NULL -> exact Artifact ID`; repetition is
   idempotent and replacement fails.
4. Individual amplifier Artifacts retain exact raw/Artifact parentage, scope,
   validity, QA, revision, checksum, configuration references, and complete
   ZipCode identity.
5. `ReductionGraph` plus `Edge.policy` is the sole group-selection authority.
   Registry, `RegistryAdapter`, `ArtifactService`, publication, executor, and
   Tasks do not rank group candidates.
6. Every planned selection freezes existing member Artifact IDs, scheduled
   member node IDs, and requested scope keys. Execution never consumes a member
   that appeared later.
7. Selecting one group never permits an amplifier slot from another group to
   repair missing or failed coverage.
8. Frozen group-member dependencies are terminal-required and
   success-tolerant. Pending/running/retrying members block readiness; terminal
   failed or blocked members are absent from task inputs without blocking the
   aggregate.
9. Required participants are exactly the sorted usable-scope intersection of
   the selected required groups and requested scopes. Optional science never
   shrinks that intersection.
10. A downstream model stores selected group IDs/policy reasons in normalized
    group-input provenance and every exact loaded Artifact ID in ordinary
    parent provenance. Neither replaces the other.
11. Selected group IDs participate in downstream logical revision identity;
    selection policy/reason, QA, validity, and applicability do not.
12. For a newly registered Artifact, group definition, slot
    declaration/realization, Artifact details, components, Artifact relations,
    and group-input rows commit or roll back as one canonical registry
    transaction after payload serialization. An existing-revision fast path
    applies its explicitly requested group relations in one relation-only
    transaction.
13. Group coverage/QA/validity summaries are computed from existing rows and
    are not stored as mutable group state.
14. Historical per-scope `calibration_group_id` metadata is not treated as
    proof of a cross-amplifier group and is not automatically backfilled.
15. `fit_exposure_fiber_response` remains numerically unchanged and all Python
    changes pass focused tests, the full suite, Ruff, and Black checks.
16. Existing-revision and revision-race fast paths honor explicit group
    requests, while legacy JSON metadata alone can never create a group.
17. Success-tolerant member failures remain visible as failures and do not
    weaken ordinary dependency, aggregate-level QA-gate, or strict-run
    reporting behavior.
18. `raise_on_failure` and CLI strict behavior remain deferred until graph
    completion, so a success-tolerant member failure cannot abort before its
    aggregate dependent runs.
19. No amplifier-local QA gate directly targets `exposure_fiber_response`; the
    gate blocks only that amplifier's upstream chain and removes that amplifier
    from the required intersection.
20. Neither planner nor executor imposes a generic coverage threshold. Model,
    task, and QA contracts own scientific sufficiency, and 296-of-300 is valid
    when those contracts accept it.

## Execution progress

- [x] Task 1 — Added immutable ontology group/slot values, deterministic
  `mg:v1` identity, calibration coherence evidence, and typed target selection
  carriers.
- [x] Task 2 — Added normalized registry tables, immutable declaration,
  conditional slot realization, and bulk read primitives.
- [x] Task 3 — Made canonical detail persistence explicitly transactional and
  included group declarations, memberships, and group inputs in that boundary.
- [x] Task 4 — Added storage-neutral group membership/input requests, logical
  revision participation, describe output, and existing/concurrent revision
  relation handling.
- [x] Task 5 — Added deterministic cadence coherence keys from shared exposure
  evidence.
- [x] Task 6 — Added grouping/selection declarations, YAML parsing validation,
  measurement-group response edges, and removed the misleading direct bias QA
  gate.
- [x] Task 7 — `ReductionGraph.plan` forms immutable planned cohorts and bulk
  hydrates persisted normalized group definitions and slots.
- [x] Task 8 — Response inputs are selected as whole coherent cohorts with
  deterministic whole-group ranking and frozen cached/scheduled bindings;
  the obsolete 90% coverage behavior is removed.
- [x] Task 9 — Scheduler/executor support frozen success-tolerant dependencies;
  planner binding of those selections remains dependent on Tasks 7–8.
- [x] Task 10 — The planning adapter forwards declarations/selections and
  `_CanonicalTask._publish` generically translates them into normalized
  membership and selected-input publication requests.
- [x] Task 11 — `ExposureFiberResponseTask` consumes typed frozen scheduled and
  existing bindings when present, performs the required-group intersection,
  and retains the legacy path only for legacy targets/tests.
- [x] Task 12 — Obsolete planner coverage metadata is removed and focused
  planner/response integration regressions are green. Full-suite execution is
  still recommended before release.

Completed focused checks: `test_calibration_grouping_policies`, service,
multi-component, planning-smoke, progress-monitor, CLI-summary, and
fiber-response regressions; touched-file Ruff checks passed. Existing graph
expectations were updated for the removed response bias gate.
