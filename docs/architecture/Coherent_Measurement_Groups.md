# Coherent Measurement Groups for Cross-Amplifier Calibration

> Status: Proposed architecture. No implementation is included in this document.
>
> Scope: the smallest change needed to make cross-amplifier calibration
> selection fast, understandable, and scientifically sound.

## Decision

Add a persistent registry entity named `MeasurementGroup`, identified by a
stable `measurement_group_id`.

A `MeasurementGroup` is an immutable declaration that one product kind's
scope-local computation slots belong to the same coherent, wider-scope
measurement state. Its **declared cohort** is fixed at identity creation.
Concrete Artifacts realize those predetermined slots as scheduled products
successfully publish. It is a registry relationship and planner selection
unit. It is not an Artifact, has no numerical payload, is not a calibration
model, and does not replace any member Artifact.

For `ExposureFiberResponseTask`, the planner must select exactly one group for
each required input kind:

```text
one extracted_master_twilight_spectrum group
one extracted_master_ldls_spectrum group
one wavelength_map group
optionally one extracted_master_sci_spectrum group
```

The task then consumes only concrete members of those selected groups. It may
drop an amplifier that is missing from any required group or is unusable under
the selected QA policy. It must never fill that gap from a different group.

Retain the name `MeasurementGroup`. `CalibrationGroup` already has a different,
useful meaning in `virusflow.planning.targets`: it is the per-scope grouping of
raw or upstream inputs that defines one calibration computation and its
applicability. `MeasurementGroup` is the cross-scope relationship among the
published Artifacts produced by such computations. `ArtifactCollection` would
be too generic, while `CalibrationGroup` would conflate these two layers.

## Current architecture and the actual gap

Several existing mechanisms already solve parts of the problem and should be
kept:

- `planning.targets.CalibrationGroup` separates `computation_id` from
  applicability and carries exact raw membership into execution.
- `Target.parent_groups`, `ReductionGraph`, and `planning.scheduler.schedule`
  preserve exact planned per-scope dependencies.
- `_CanonicalTask._publish`, `ArtifactRequest.parents`, `Provenance.parents`,
  and `artifact_relations` preserve the exact concrete Artifact parents of a
  downstream Product.
- `ArtifactService._logical_revision` includes stable parent identities, task,
  algorithm, parameters, configuration references, and scientific metadata in
  immutable Artifact revision identity.
- `Scope`, `Validity`, the QA bundle, and `ArtifactService.select_best` already
  provide the vocabulary needed for applicability and policy-driven selection.
- `RegistryAdapter.find_by_calibration_groups` is an efficient exact lookup for
  the current per-scope `calibration_group_id` metadata convention.

The missing fact is a durable relationship across amplifier scopes. Today
`calibration_group_id` is JSON metadata on each Artifact, not a registered
group with normalized membership. The `calibration_build` branch in
`planning.graph.ReductionGraph.plan` starts from one upstream family and then
chooses other input kinds independently for each amplifier. Carrying those
choices into `ExposureFiberResponseTask` prevents an execution-time ambient
lookup, but it does not prevent the planner from creating a mixed-night or
otherwise incoherent instrument state.

This proposal replaces that cross-amplifier assembly behavior. It does not
replace the existing per-amplifier cadence, parent-group, Artifact, or
provenance mechanisms.

It therefore supersedes the cross-amplifier fan-in recommendation in section 2
of `Calibration_Time_Fiber_Response_Plan.md`, which proposed clustering
per-amplifier parents directly in `ReductionGraph.plan`. The older document's
per-amplifier extraction, numerical-model, and exact-parentage recommendations
remain compatible; only its claim that existing grouping identity is sufficient
for the wider-scope selection unit is replaced here.

## 1. Ontology

### Meaning

One `MeasurementGroup` answers one question:

> Which scope-local Artifacts of this single product kind belong to the same
> wider-scope measurement state under a named scientific coherence rule?

Each group is homogeneous in Artifact kind. A twilight group does not also
contain LDLS or wavelength Artifacts. This is important because the coherence
rule and selection policy can differ by product kind, and because the response
model deliberately selects one group for each scientific input role.

The group does not assert that every declared slot was realized, that every
realized member passed QA, that coverage is complete, or that it is the best
group for a downstream target. Those are selection-time questions. It asserts
only that the declared slots are scientifically coherent.

### Minimal domain type

Add frozen `MeasurementGroup` and `MeasurementGroupSlot` definitions to
`virusflow.ontology.entities`:

```python
@dataclass(frozen=True)
class MeasurementGroup:
    measurement_group_id: str
    member_kind: str
    coherence_rule: str
    coherence_rule_version: str
    coherence_key: Mapping[str, object]
    declared_slots: tuple[MeasurementGroupSlot, ...]
    anchor_measurement_group_ids: tuple[str, ...] = ()
    grouping_parameters: Mapping[str, object] = field(default_factory=dict)
    configuration_references: tuple[Mapping[str, object], ...] = ()

@dataclass(frozen=True)
class MeasurementGroupSlot:
    member_scope_key: str
    member_computation_id: str
```

The mapping-valued inputs must be copied and canonically normalized when the
value is constructed; callers must not be able to mutate identity-bearing
state after `measurement_group_id` is computed. The tuple of declared slots is
sorted and duplicate scope keys are rejected.

The conceptual fields are:

- `measurement_group_id`: deterministic identity described below;
- `member_kind`: one canonical registered Artifact kind;
- `coherence_rule` and `coherence_rule_version`: the scientific rule that
  established the group, not the downstream selection policy;
- `coherence_key`: the physical/observational evidence shared by the cohort;
- `declared_slots`: the immutable expected scope/computation cohort;
- anchor group IDs, grouping parameters, and configuration references only
  when they materially affect formation or membership.

Do not put arrays, QA aggregates, coverage counts, realized Artifact IDs,
software/publication provenance, or a model payload on this entity.

### Reuse rather than duplication

- Member Artifacts retain their existing `Scope`, `Validity`, QA, lifecycle,
  revision, checksum, metadata, and exact Artifact/raw parentage.
- Group applicability uses the existing member `Validity` records and the
  edge's selection policy. Do not copy an intersection of member validity into
  the group row merely as a cache.
- `member_scope_key` is derived with the existing Target/Artifact scope
  identity machinery; for the motivating case it is the complete ZipCode key.
  Do not add group-specific physical scope vocabulary or assign a Product scope
  to this non-Product relationship.
- The complete ZipCode remains on each member Artifact. The group does not
  replace or weaken hardware identity.
- Artifact computation identity and `CalibrationGroup.computation_id` remain
  computation identities. A `measurement_group_id` is a coherent cohort
  identity, not another computation or Product revision.
- `RelationKind.MEMBER_OF` describes membership conceptually. Selected groups
  are recorded as group inputs to a downstream Artifact, not inserted into
  `Provenance.parents`, because a group is not an Artifact.

## 2. Registry persistence and identity

### Minimal schema

Extend `ARTIFACT_SCHEMA` and the existing idempotent initialization/migration
path in `virusflow.registry.database` with three small normalized tables. One
slot row represents both the immutable declaration and its optional realized
Artifact:

```sql
CREATE TABLE IF NOT EXISTS measurement_groups (
    measurement_group_id TEXT PRIMARY KEY,
    member_kind TEXT NOT NULL,
    coherence_rule TEXT NOT NULL,
    coherence_rule_version TEXT NOT NULL,
    coherence_key_json TEXT NOT NULL,
    anchor_group_ids_json TEXT NOT NULL DEFAULT '[]',
    grouping_parameters_json TEXT NOT NULL DEFAULT '{}',
    configuration_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS measurement_group_slots (
    measurement_group_id TEXT NOT NULL,
    member_scope_key TEXT NOT NULL,
    member_computation_id TEXT NOT NULL,
    artifact_id INTEGER,
    realized_at TEXT,
    PRIMARY KEY (measurement_group_id, member_scope_key),
    UNIQUE (measurement_group_id, artifact_id),
    FOREIGN KEY (measurement_group_id)
        REFERENCES measurement_groups(measurement_group_id),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS artifact_measurement_group_inputs (
    artifact_id INTEGER NOT NULL,
    input_name TEXT NOT NULL,
    measurement_group_id TEXT NOT NULL,
    selection_policy TEXT NOT NULL,
    match_quality TEXT,
    selection_reason_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (artifact_id, input_name),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id),
    FOREIGN KEY (measurement_group_id)
        REFERENCES measurement_groups(measurement_group_id)
);

CREATE INDEX IF NOT EXISTS measurement_group_kind_idx
    ON measurement_groups(member_kind);
CREATE INDEX IF NOT EXISTS measurement_group_slots_idx
    ON measurement_group_slots(measurement_group_id, artifact_id);
CREATE INDEX IF NOT EXISTS measurement_group_slot_artifact_idx
    ON measurement_group_slots(artifact_id);
```

The slot primary key is the critical anti-substitution invariant. Publication
may change `artifact_id` from `NULL` to the Artifact that realizes the declared
`member_computation_id`; it may never change one non-NULL Artifact ID to
another. The update must use `WHERE artifact_id IS NULL` and reject a zero-row
update unless it is an idempotent repeat of the same Artifact. A reprocessing
that changes a member computation belongs to a new group.

Do not add `coverage_count`, `coverage_complete`, group QA, member Artifact
kind, member validity, or member hardware columns. They are cheaply and safely
obtained by joining the approximately 300 slot rows to existing Artifact and
QA records. The slot rows themselves are required identity evidence, not a
derived coverage cache.

### Deterministic immutable identity

Compute the ID from canonical JSON, sorted by member scope key:

```text
measurement_group_id =
    "mg:v1:" + sha256(canonical_json({
        identity_schema: 1,
        member_kind,
        coherence_rule,
        coherence_rule_version,
        coherence_key,
        anchor_measurement_group_ids,
        material_grouping_parameters,
        material_configuration_references,
        declared_members: [
            {member_scope_key, member_computation_id}, ...
        ]
    }))
```

Use the full digest in persistence; a short prefix is acceptable only for
display. Applicability windows, QA results, creation time, and the set of
successfully published members do not participate in identity.

The declared member cohort, rather than the successful Artifact IDs, is in the
hash for three reasons:

1. the group identity is known before scheduled members publish;
2. missing or failed members produce an incomplete group without changing its
   identity; and
3. a later plan with different member computations produces a new immutable
   group rather than editing the old group.

This relies on `member_computation_id` being complete. Tighten the existing
derived-target computation identity in `planning.graph` so it includes output
kind, exact parent-group identities, member scope, algorithm/version,
parameters, and configuration references, matching the inputs already used by
`ArtifactService._logical_revision`. The root calibration path in
`planning.cadence._make_group` already follows most of this pattern.

The group definition and declared slots are insert-only. Redeclaring an
existing ID is accepted only when its identity-bearing canonical definition is
byte-equivalent. Realization is the one allowed transition: publication may
fill an empty declared slot, but may never replace an occupied slot. There is
no mutable "current member" pointer.

### Formation provenance

Persist only what cannot safely be reconstructed from member Artifacts and the
existing registry:

- named rule and version;
- canonical coherence key;
- rule parameters and configuration references;
- source/anchor measurement-group IDs for inherited groups;
- declared scope/computation cohort.

Do not duplicate the raw frame list, Artifact parent list, checksums, or member
QA. Do not add group-specific software, git, task, algorithm, or publication
provenance columns: those remain authoritative and queryable on the member
Artifacts through existing computation/publication provenance. `created_at` is
ordinary registry audit time, not a second provenance system.

### Owners and APIs

Keep SQL in `virusflow.registry.database` and expose thin methods from
`artifacts.registry_adapter.RegistryAdapter`:

```text
declare_measurement_group(definition)
realize_measurement_group_slot(group_id, artifact_id, scope_key, computation_id)
get_measurement_group(group_id)
list_measurement_group_candidates(kind)
get_measurement_group_slots(group_id)
list_measurement_groups_for_artifact(artifact_id)
save_artifact_measurement_group_inputs(artifact_id, selections)
```

The registry/Artifact service boundary may expose bulk candidate and slot
queries, but it must not expose a general `select_measurement_group` policy
method. `ReductionGraph`, using `Edge.policy`, is the only authority that ranks
and selects candidate groups.

## 3. Group formation

### Formation point

Form group declarations during graph planning, after the planner has resolved
the per-scope `CalibrationGroup`/parent identities and before any downstream
edge selects among wider-scope candidates. Carry the declaration on each
planned member Target. Persist definitions, slot realizations, and selected
group-input relations only through normal Artifact publication.

This preserves the planner's current read-only behavior: an in-memory declared
group can participate in the current plan, while `ArtifactService` performs the
registry write. The first successful member publication, or the first
downstream publication that records the group as a selected input, inserts the
group definition. Subsequent publications verify the same immutable definition
and insert their relations. A run in which only 287 of 300 members publish
therefore leaves one valid, auditable, incomplete group with 287 members. A
declaration with no published member and no downstream Artifact remains only in
the persisted planning report because no Artifact relationship yet exists to
register.

Extend `ArtifactRequest` with explicit group data rather than hiding it in
generic metadata:

```python
measurement_group_memberships: tuple[MeasurementGroupMembershipRequest, ...]
measurement_group_inputs: tuple[MeasurementGroupInputRequest, ...]
```

These are storage-neutral publication request records, not planning types. A
membership request carries the immutable definition plus the one declared slot
being realized. A group-input request carries the selected immutable
definition, input role, policy, match quality, and reason. Carrying the
definition makes declaration idempotent and ensures that a selected optional
group can be audited even if none of its scheduled members realizes. It does
not turn the group into an Artifact parent.

`DefaultPublicationService` validates these requests.
`ArtifactService.persist_request` includes group-input identities in logical
revision identity and writes the Artifact, membership, and group-input rows as
part of the existing canonical detail-registration transaction.

The two early-return paths in `ArtifactService.persist_request` also matter:
the initial `find_by_revision` hit and the concurrent revision-race winner must
validate and idempotently apply any explicit group request before returning the
existing Artifact. They must never infer a request from
`metadata["calibration_group_id"]`; therefore this is not an automatic
historical backfill.

Repository detail: connections use `isolation_level=None`, and
`save_artifact_details` currently does not issue an explicit `BEGIN`. Extend
that function to wrap canonical Artifact details, components, Artifact
relations, group declaration/slots, slot realization, and selected-group input
rows in one explicit `BEGIN IMMEDIATE`/`COMMIT` block with rollback on failure.
Keep the existing `RegistryAdapter.register` cleanup of the older Artifact
shell if this canonical transaction fails. Do not broaden the transaction to
payload serialization.

### One generic formation hook

Add a small `MeasurementGroupingSpec` to `planning.graph.TaskSpec`, for
example:

```python
@dataclass(frozen=True)
class MeasurementGroupingSpec:
    coherence_rule: str
    coherence_rule_version: str
    anchor_input_kinds: tuple[str, ...] = ()
    grouping_parameters: Mapping[str, object] = field(default_factory=dict)
```

The graph owns one generic formation path. Tasks do not implement grouping.
`planning.defaults.default_calibration_graph` registers the appropriate spec
for kinds that are meaningful as cross-scope measurements.

There are two formation patterns:

1. **Root measurement group.** Extend
   `planning.cadence.resolve_calibration_groups` to emit a cross-scope
   `coherence_key` in addition to the existing per-amplifier computation
   identity. The key uses shared scientific evidence such as the sorted source
   exposure IDs, cadence/observing-block identity, track reference, and rule
   version. It deliberately excludes amplifier-local raw row IDs.
2. **Derived measurement group.** Inherit the physical coherence key from the
   named anchor parent group or groups, then form a new group for the derived
   member kind using the derived per-scope computation cohort. For example,
   extracted twilight spectra anchor on the selected Master Twilight group;
   Master Arc can anchor on the coherent Hg and Cd groups; wavelength maps can
   anchor on the coherent Master Arc group. Exact trace or other model parents
   remain in each Artifact's computation identity and provenance. A derived
   member is group-eligible only when all of its declared anchor parents resolve
   to the required coherent anchor group or anchor-group tuple. The planner may
   still publish an independently useful Artifact that is not group-eligible.

Initial registered rules should cover:

| Member kind/family | Coherence anchor |
|---|---|
| Master/extracted twilight | Same accepted center-track twilight exposure set |
| Master/extracted LDLS | Same accepted LDLS observation/cadence set |
| `wavelength_map` | Same coherent Hg/Cd `master_arc` measurement state |
| Master/extracted science diagnostic | Same accepted sufficient science exposure set |

The rule is product-specific and versioned. The downstream policy does not
create, repair, merge, or split groups; it only selects among already formed
candidates.

## 4. Planner and graph integration

### Edge contract

Make the selection unit explicit instead of encoding it in policy names:

```python
class SelectionUnit(str, Enum):
    ARTIFACT = "artifact"
    MEASUREMENT_GROUP = "measurement_group"

@dataclass(frozen=True)
class Edge:
    src: TaskSpec
    dst: TaskSpec
    policy: str = "latest_valid"
    tolerance_days: int = 90
    selection_unit: SelectionUnit = SelectionUnit.ARTIFACT
```

Existing edges retain Artifact behavior by default. The four edges into
`exposure_fiber_response` use `MEASUREMENT_GROUP`. Configuration parsing and
`planning.validate` should accept and validate the new field.

For every coherent twilight build, `ReductionGraph` can use that exact
twilight group as the destination build anchor, then apply `Edge.policy` to
choose one LDLS group, one wavelength group, and optionally one Master Science
group near the anchor time. Policies may consider applicability, QA, coverage
over the requested amplifier scopes, and time distance. Stable group ID is the
final tie-breaker. Registry and Artifact service code only supplies candidates
and evidence; it never performs this ranking.

The selection result should be explicit and serializable:

```python
@dataclass(frozen=True)
class MeasurementGroupSelection:
    input_name: str
    measurement_group: MeasurementGroup
    existing_members: tuple[tuple[str, int], ...]
    scheduled_members: tuple[tuple[str, str], ...]
    requested_member_scope_keys: tuple[str, ...]
    match_quality: str
    policy: str
    reason: Mapping[str, object]
```

`existing_members` contains `(member_scope_key, artifact_id)` pairs and
`scheduled_members` contains `(member_scope_key, task_node_id)` pairs. Their
scope keys must name slots in the embedded immutable definition; the slot
supplies the expected computation identity. This is still the requested exact
set of existing Artifact IDs and scheduled task-node identities, but it avoids
reconstructing their slot association at execution time.

Carry these selections on `Target`, through `PlanningTargetAdapter`, and into
the scheduler. `schedule` adds dependencies on all scheduled members of the
selected groups. The executor combines those exact outputs with the cached
Artifact IDs already frozen in the selection. It does not rerun selection in a
Task and does not query the persisted group for newly appearing members.

The current executor blocks a node when any dependency fails. Cross-amplifier
fan-in needs one narrow extension: mark the frozen selected member bindings as
`success_tolerant_dependencies`, a subset of the aggregate node's ordinary
`depends_on` set. These dependencies are **terminal-required and
success-tolerant**, not optional:

```text
aggregate runnable =
    every ordinary blocking dependency succeeded
    AND every success-tolerant member dependency is terminal
```

A pending or running selected member therefore still prevents aggregate
readiness. Once every frozen selected member is terminal, successful results
are passed to the task and failed or blocked member results are absent. Only
the frozen MeasurementGroup member bindings for this fan-in may be marked
success-tolerant; ordinary scientific dependencies and genuine
aggregate-level QA gates remain success-blocking.

Repository detail: `PlanningExecutor.run` currently drains the graph before
raising `WorkflowExecutionError`; `raise_on_failure=True` is deferred reporting,
not fail-fast behavior. The CLI likewise applies `--strict-task-failures` only
after the graph reaches terminal state. Preserve that ordering. A
success-tolerant member failure must never cause an early abort before its
aggregate dependent runs. The member remains failed in progress, performance,
the final execution report, `WorkflowExecutionError`, and strict CLI exit
semantics as applicable. Only dependent blocking is relaxed.

This is necessary for incomplete groups to work during the same run, not a new
selection mechanism. A failed member leaves its declared group slot unrealized
if it never published, and its frozen binding cannot be replaced.

### QA gates into the aggregate

Remove the direct amplifier-local
`master_bias -> exposure_fiber_response` `qa_gate` edge from the default graph.
Bias QA continues to block the affected amplifier's upstream twilight, LDLS,
arc/wavelength, trace, and extracted-spectrum work. The corresponding selected
MeasurementGroup member is then missing, unusable, failed, or blocked and is
absent from the aggregate input. It must not make the whole exposure-wide
response terminal.

More generally, do not attach an amplifier-local QA gate directly to a
cross-scope aggregate node. A genuine aggregate-scoped prerequisite may remain
success-blocking. This rule should be validated in the graph configuration so
an external planning YAML cannot accidentally restore the same global-blocking
behavior.

### Partial coverage

For each candidate group, derive the usable scope-key set by joining membership
to the current QA policy. A failed published member stays a member and remains
auditable but is excluded from the usable set; a member that failed before
publication leaves its slot unrealized. Missing declared members are simply
absent. Planner policy may use coverage to rank otherwise valid groups, but it
never borrows a scope from another group and does not impose a generic hard
coverage threshold.

After the required groups are selected:

```text
participants =
    requested amplifier scopes
    ∩ usable twilight member scopes
    ∩ usable LDLS member scopes
    ∩ usable wavelength member scopes
```

The optional science group does not shrink this required intersection. If the
current algorithm requires aligned science coverage, use the optional input
only when it covers the full required participant set; otherwise record that
the optional validation input was unavailable. This preserves the task's
current all-or-nothing optional-science behavior.

The executor does not decide scientific sufficiency. A 296-of-300 required
intersection is a valid model input when it satisfies the model/task/QA
criteria. Any minimum participant count or fraction must be an explicit
scientific contract owned and reported by that layer, not an executor or
generic group-selection constant.

The planner must report selected group IDs, member counts, usable counts,
missing scopes, QA-excluded scopes, policy, and reason. These are report fields,
not mutable properties of the group.

### Replace the current special case

Replace the `scope_mode == "calibration_build"` logic that independently
chooses extra upstream kinds per member. Keep a cross-scope build mode if it is
useful for target construction, but make it consume edge-selected
`MeasurementGroupSelection` values. `parent_groups` remains useful for exact
per-scope scheduling and should not be repurposed as the persistent
cross-amplifier entity.

## 5. Artifact and provenance integration

A published `exposure_fiber_response` records two complementary facts:

1. **Scientific grouping/selection decision.** One row per input role in
   `artifact_measurement_group_inputs`, including group ID, policy, match
   quality, and reason.
2. **Exact numerical inputs.** Every concrete member Artifact actually loaded
   remains in `ArtifactRequest.parents`, producing the existing
   `Provenance.parents` and `artifact_relations` entries.

These facts must not be collapsed. Group identity explains why those
amplifier-local Artifacts were eligible to be used together; concrete parent
IDs say exactly what bytes and measurements the model consumed.

Extend `ArtifactService.describe` to return both:

```json
{
  "provenance": {
    "parents": [101, 102, 201, 202],
    "measurement_group_memberships": [],
    "measurement_group_inputs": [
      {
        "input_name": "twilight",
        "measurement_group_id": "mg:v1:...",
        "policy": "exact_parent_group",
        "match_quality": "EXACT"
      }
    ]
  }
}
```

Include selected group IDs in `ArtifactService._logical_revision`; they are
part of the effective scientific inputs. Preserve policy, match quality, and
reason in group-input provenance but keep them out of computation identity,
just as applicability is separate from `CalibrationGroup.computation_id`.

Member Artifact publication should no longer rely on
`metadata["calibration_group_id"]` as the cross-scope relationship. Keep that
field during migration because it remains useful for the existing per-scope
`CalibrationGroup` lookup. New cross-scope code uses normalized
`measurement_group_slots`.

The schema migration should create only the new empty tables and indexes. It
must not infer coherent groups automatically from historical
`calibration_group_id` JSON, because that metadata is per-scope and cannot
prove a cross-amplifier scientific cohort. Existing Artifacts remain valid and
independently selectable for Artifact-unit edges, but are ineligible for new
MeasurementGroup-unit edges until an explicit scientifically validated
backfill or normal grouped reprocessing publishes declarations and slots.

## 6. `ExposureFiberResponseTask`

`ExposureFiberResponseTask` must be a consumer, not a selector or group
builder. Its runtime shape should be approximately:

```python
def run(self, inputs):
    selected = self.target.measurement_group_inputs

    # Executor/planning bindings contain exact cached Artifact IDs and exact
    # outputs of scheduled member nodes. This performs no policy selection.
    rows = resolve_planned_group_members(selected, inputs)

    required = (
        "extracted_master_twilight_spectrum",
        "extracted_master_ldls_spectrum",
        "wavelength_map",
    )
    participants = set(self.target.requested_amplifier_keys)
    for kind in required:
        participants &= usable_scope_keys(rows[kind])
    participants = sorted(participants)

    if not participants:
        raise RuntimeError("no amplifier is usable in every selected required group")

    ldls = load_in_scope_order(rows["extracted_master_ldls_spectrum"], participants)
    twilight = load_in_scope_order(rows["extracted_master_twilight_spectrum"], participants)
    wavelength = load_in_scope_order(rows["wavelength_map"], participants)

    science = optional_complete_input(
        rows.get("extracted_master_sci_spectrum"), participants
    )
    result = fit_exposure_fiber_response(
        ldls, twilight, wavelength, science_spectrum=science, **params
    )

    return publish(
        result,
        parents=exact_loaded_artifact_ids(),
        measurement_group_inputs=selected,
    )
```

The task may load registry rows by already selected Artifact ID and load their
components and QA evidence through `ArtifactService`. It may also consume the
exact successful outputs named by frozen scheduled node IDs. It must verify
each result against the selected definition and compute usability only for
those frozen Artifacts. It must not call `select_best`, search by kind/time,
call `find_by_calibration_groups`, query current group membership, or substitute
a missing amplifier. The existing working-tree change that removes
`select_best` from this task is directionally correct; the remaining cached
lookup and grouping must move fully into the frozen planner contract above.

## Minimal code ownership changes

| Existing owner | Minimal extension |
|---|---|
| `ontology/entities.py` | Frozen `MeasurementGroup` and declared-slot definitions; no new scope type |
| `planning/targets.py` | Planned group declaration/selection references on `Target`; keep `CalibrationGroup` |
| `planning/cadence.py` | Emit versioned cross-scope coherence keys from existing calibration grouping evidence |
| `planning/graph.py` | `MeasurementGroupingSpec`, `SelectionUnit`, generic group formation, whole-group edge selection; remove per-amplifier fan-in selection |
| `planning/defaults.py` | Register grouping specs/group-selection edges and remove the amplifier-local bias QA gate into the aggregate |
| `planning/config.py`, `planning/validate.py` | Parse and validate `selection_unit` |
| `planning/adapter.py`, `planning/scheduler.py` | Carry selections and bind all exact scheduled/cached members |
| `executors/planning_executor.py` | Wait for terminal success-tolerant members, pass successes, and preserve deferred failure reporting |
| `registry/database.py` | Three tables, explicit canonical-detail transaction, and bulk group/slot/input queries |
| `artifacts/registry_adapter.py` | Thin persistence/query wrappers |
| `artifacts/requests.py` | Explicit membership and selected-group input requests |
| `artifacts/service.py` | Group persistence/query support, logical-revision inclusion, and description; no group policy selector |
| `publication/service.py` | Validate and publish group relations with normal Artifact publication |
| `tasks/calibs.py` | Consume frozen selections, intersect members, preserve exact parents; no selection or formation |

No new Artifact kind, payload serializer, numerical model, scope vocabulary,
QA vocabulary, executor framework, or task-specific grouping implementation is
required. All Python additions and touched code must remain PEP 8 compliant;
run the focused tests and the repository's available style/static checks before
handoff.

## Required contract tests

The smallest implementation proof should cover:

1. group ID is order-independent and changes when rule version, coherence key,
   anchor identity, material grouping configuration, or member computation
   changes;
2. group declaration is immutable and a second member for the same scope is
   rejected;
3. a partial group remains queryable and rankable without a generic hard
   coverage threshold;
4. planner selection chooses one whole group and never creates a per-amplifier
   mixture from two groups;
5. required-group intersection drops missing/failed amplifiers without filling
   them from another group;
6. `ExposureFiberResponseTask` makes no registry policy-selection call;
7. the output stores every selected group decision and every exact consumed
   Artifact parent; and
8. group candidate/slot loading uses bounded bulk queries, not approximately
   300 `select_best` calls;
9. a pending success-tolerant member blocks readiness, while terminal failed
   success-tolerant members release the aggregate only after every frozen
   member is terminal;
10. several success-tolerant member failures still permit the aggregate, but
    an ordinary failed dependency blocks it;
11. deferred strict/`raise_on_failure` reporting does not prevent the aggregate
    from running before the recorded workflow failure is raised; and
12. one amplifier-local bias QA failure removes that amplifier from the
    intersection instead of blocking the exposure-wide response.

At VIRUS scale, one indexed group-slot join and set intersections over
roughly 300 keys are negligible compared with loading and fitting the spectra.
The resulting behavior is explicit in the graph, compact in the registry, and
scientifically incapable of silently assembling a mixed measurement state.
