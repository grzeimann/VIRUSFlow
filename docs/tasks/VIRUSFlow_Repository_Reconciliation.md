# Junie Task: Reconcile the Existing VIRUSFlow Repository with the Scientific Knowledge Architecture

## Purpose

The existing VIRUSFlow repository contains substantial scientific and architectural work that should be preserved where it is correct and reusable.

The VIRUSFlow knowledge notes now define a more complete and authoritative model of the instrument, calibration system, science Products, provenance, QA, analytics, and intended architecture.

Your task in this phase is **not** to rebuild the repository from scratch and **not** to perform a broad cleanup. Your task is to reconcile the existing repository with the new specification and produce an implementation-ready migration plan.

Treat:

```text
the existing repository
```

as:

```text
scientific implementation evidence
```

and treat:

```text
the knowledge notes,
the coherence map,
the target architecture,
and the implementation plan
```

as:

```text
the authority for future structure and vocabulary
```

---

# Authoritative Inputs

Read these synthesis documents first:

```text
VIRUSFlow_Knowledge_System_Coherence_Map.md
VIRUSFlow_Target_Architecture.md
VIRUSFlow_Implementation_Plan_From_Knowledge_Notes.md
```

Then read all files matching:

```text
VIRUSFlow_Knowledge_Note_*.md
```

---

# Architectural Principles

Use these principles throughout the audit.

## Scientific object before implementation

A Product represents a physical or inferential scientific object. An algorithm is one method for estimating it. Do not define the repository around the current implementation of one algorithm.

## Exposure is atomic

Exposure is the atomic scientific measurement. Observation, Shot, DitherSet, and ObservationSet are grouping entities. Per-exposure sky, seeing, transparency, illumination, astrometry, effective exposure time, and throughput must remain distinct.

## Algorithms are storage neutral

Algorithms:

- receive arrays and typed metadata;
- perform scientific calculations;
- return `AlgoResult`;
- do not query the registry;
- do not select calibration Products;
- do not write files;
- do not create plots;
- and do not assign QA status.

## Tasks own orchestration

Tasks:

- resolve Targets;
- select input Products;
- load payloads through ArtifactService;
- invoke algorithms;
- publish scientific facts;
- run QA;
- construct ArtifactRequests;
- and persist outputs.

## ArtifactService is the persistence boundary

All Product loading and persistence should eventually pass through ArtifactService. Direct serializer or file-format access inside Tasks should be treated as a migration target unless it is part of ArtifactService itself.

## QA and analytics are distinct

QA interprets facts from one reduction context. Analytics performs post-run, read-only studies across accumulated Products. Algorithms should not contain QA thresholds or plotting behavior.

## Existing code is evidence

Do not discard scientifically valid behavior merely because it is procedural, old, or imperfectly organized. Characterize and test it before replacement.

## New contracts are authoritative

Legacy names may be temporarily adapted, but they must not remain the canonical vocabulary. Examples include:

```text
masterflt → master_ldls
mastercmp → master_arc
ftf → fiber_normalization or a more specific component
plaw → scattered_light_model or a more specific term
```

Do not force a rename until the legacy quantity is scientifically understood.

---

# Scope and Restraint

This is an **audit and migration-design phase**.

Do not:

- rewrite scientific algorithms;
- move or delete large portions of the repository;
- rename public interfaces;
- change persisted schemas;
- implement the complete vertical slice;
- or add speculative abstractions merely because they appear in the target architecture.

Permitted changes are limited to documentation, migration planning, and tiny inspection or characterization-test scaffolding that does not alter production behavior.

---

# Required Audit

Inspect the complete repository.

## Domain and vocabulary

Locate current representations of:

- entities;
- Targets;
- scopes;
- Product or Artifact kinds;
- ZipCode identity;
- hardware configuration;
- observation identity;
- exposure identity;
- and relationships.

## Algorithms

Locate implementations for:

- orientation;
- overscan;
- bias;
- read noise;
- gain;
- dark;
- pixel masks;
- trace;
- wavelength;
- fiber profiles;
- extraction;
- scattered light;
- variance;
- astrometry;
- sky;
- fiber normalization;
- relative response;
- and spatial reconstruction.

## Tasks and graphs

Locate:

- Task classes;
- TaskGraph or dependency mechanisms;
- planners;
- target resolution;
- calibration workflows;
- science workflows;
- and observation grouping.

## Artifacts and persistence

Locate:

- ArtifactService;
- registries;
- serializers;
- materializers;
- database models;
- storage paths;
- provenance;
- validity;
- and Product-selection logic.

## QA and analytics

Locate:

- QA facts;
- QA rules and YAML;
- status definitions;
- reports;
- plotting;
- analytic studies;
- and code that mixes these responsibilities.

## I/O

Locate:

- raw-file discovery;
- tar access and indexing;
- metadata extraction;
- hardware identity resolution;
- and filesystem abstractions.

## Tests

Inventory:

- unit tests;
- integration tests;
- regression tests;
- characterization tests;
- scientific acceptance tests;
- fixtures;
- and important untested behavior.

---

# Classification

Classify every major existing module or subsystem using exactly one primary decision:

```text
KEEP
ADAPT
REFACTOR
REPLACE
RESEARCH
REMOVE
```

Definitions:

- **KEEP:** already matches the intended responsibility and contracts closely.
- **ADAPT:** scientific behavior is useful, but it needs a new interface, name, Target, or Artifact contract.
- **REFACTOR:** responsibility is correct, but internal structure should change without changing scientific behavior.
- **REPLACE:** superseded by a scientifically or architecturally different design.
- **RESEARCH:** hidden assumptions or behavior must be characterized first.
- **REMOVE:** confirmed dead or duplicated and contains no required scientific behavior.

Do not classify code as `REMOVE` based only on static inspection.

---

# Deliverable 1: Repository Reconciliation

Create:

```text
VIRUSFLOW_REPOSITORY_RECONCILIATION.md
```

Include an inventory table:

| Existing path | Current responsibility | Scientific domain | Architectural layer | Classification | Evidence | Required action |
|---|---|---|---|---|---|---|

The Evidence column should reference code, tests, current behavior, or knowledge-note requirements.

Avoid vague statements such as “needs cleanup.” State the exact mismatch.

---

# Deliverable 2: Current-to-Target Architecture Map

Map the current repository onto:

```text
ontology
configuration
registry
artifacts
targets
algorithms
tasks
graphs
qa
analytics
query
io
cli
tests
```

Use a table:

| Target layer | Existing equivalent | Coverage | Main mismatch |
|---|---|---|---|

Use `ontology/` as the intended home for canonical entities, scopes, Artifact kinds, relations, units, coordinate conventions, and assumptions unless the audit reveals a clearly superior existing equivalent.

Do not move code during this task.

---

# Deliverable 3: Scientific Product Map

For every baseline Product required by the first vertical slice, document its current state.

At minimum include:

```text
oriented_detector_image
overscan_model
overscan_corrected_image
master_bias
read_noise
gain
master_dark
dark_rate
pixel_mask
detector_variance
master_ldls
master_hg
master_cd
master_arc
master_twilight
trace_map
trace_samples
wavelength_map
arc_identification
fiber_profile
spectral_psf_2d
within_amp_fiber_normalization
amp_to_amp_normalization
fiber_normalization
reduced_science_image
ccd_scattered_light_model
scatter_subtracted_image
aperture_extracted_spectrum
extracted_variance
initial_astrometry
catalog_match_table
final_astrometry
fiber_sky_coordinates
sky_fiber_mask
incident_sky_spectrum
exposure_illumination_correction
fiber_sky_prediction
sky_subtracted_spectrum
baseline_relative_response
final_exposure_response
effective_exposure_time
dither_assignment
dither_registration
dither_coverage_map
```

Use:

| Canonical Product | Existing implementation | Existing name | Scope | Persistence | QA | Migration need |
|---|---|---|---|---|---|---|

Mark each Product:

```text
implemented
partially implemented
implicit
missing
```

---

# Deliverable 4: Contract-Gap Analysis

Evaluate the current repository against:

## Target contract

- explicit identity;
- explicit scope;
- serialization and hashing;
- correct amplifier, CCD, exposure, and observation distinctions.

## AlgoResult contract

- arrays;
- scalars;
- metadata;
- messages;
- timings;
- version;
- canonical names;
- no persistence or plotting concerns.

## Task contract

- input selection and orchestration;
- explicit dependencies;
- no scientific algorithms embedded in Tasks;
- no ArtifactService bypass.

## Artifact contract

- immutable Products;
- complete provenance;
- validity;
- units;
- coordinate conventions;
- QA status.

## QA contract

- facts separate from rules;
- no hidden hard-coded policy;
- consistent statuses;
- explicit degraded usability.

## Analytics contract

- post-run and read-only;
- registry/ArtifactService access;
- analytic Products rather than source mutation.

Classify each gap as:

```text
BLOCKING
IMPORTANT
DEFERRED
```

---

# Deliverable 5: Legacy Vocabulary Map

Create:

```text
VIRUSFLOW_LEGACY_VOCABULARY_MAP.md
```

Use:

| Legacy term | Canonical term | Context | Migration strategy | Compatibility duration |
|---|---|---|---|---|

Investigate at least:

```text
masterflt
mastercmp
mastersci
ftf
plaw
maskspec
wave
trace
spec
res
rms_fibers
```

Where one legacy term represents several scientific objects, document the ambiguity instead of forcing a one-to-one rename.

---

# Deliverable 6: Proposed Repository Tree

Propose the target repository tree based on both the architecture documents and the actual code.

For each proposed top-level package, identify:

- current modules that would remain;
- current modules that would adapt or move;
- new modules required;
- and modules that should not move.

Do not physically reorganize the repository yet.

---

# Deliverable 7: Migration Plan

Create:

```text
VIRUSFLOW_MIGRATION_PLAN.md
```

Use this sequence unless the audit demonstrates a safer dependency order:

1. Freeze canonical vocabulary, Product kinds, scopes, units, and coordinates.
2. Stabilize Target, AlgoResult, Artifact, provenance, validity, and QA contracts.
3. Reconcile ArtifactService and direct serializer access.
4. Load versioned hardware and configuration knowledge.
5. Add characterization tests around current scientific behavior.
6. Migrate one narrow contract-validation slice.
7. Expand to one amplifier.
8. Expand to one physical CCD.
9. Expand to one full exposure.
10. Expand to one three-dither observation.
11. Remove superseded paths only after parity and acceptance tests.

For every step include:

| Step | Existing components | New components | Tests | Exit criterion | Safe rollback |
|---|---|---|---|---|---|

---

# Deliverable 8: Recommended First Slice

Evaluate whether the best contract-validation slice is:

```text
raw bias frames
→ Master Bias
→ QA facts and status
→ ArtifactService persistence
→ registry query
→ bias-stability analytics
```

Then map the first full scientific slice:

```text
one real science observation
→ detector reduction
→ physical-CCD scatter correction
→ aperture extraction
→ astrometry
→ sky subtraction
→ relative response
→ exposure and dither Products
```

Identify which existing components can support every stage.

---

# Deliverable 9: Missing Information

Conclude with three categories:

## Cannot be inferred

Items requiring Greg's scientific or operational input.

## Can be inferred but should be verified

Likely answers present in code or notes that need characterization tests.

## Non-blocking research questions

Items that should not delay the baseline implementation.

Do not ask questions already answered by the repository or notes.

---

# Specific Issues to Inspect

## Physical CCD transform

The legacy scatter logic implies:

```text
Left CCD:
LL lower
LU upper and reflected in y

Right CCD:
RU lower
RL upper and reflected in y
```

with continuous-coordinate behavior resembling:

```text
upper_y = 2064 - y
```

Determine how this maps to zero-indexed image assembly and whether the seam uses `2063 - y`, `2064 - y`, or an edge-coordinate convention.

Do not treat amplifier ordering as unknown.

## Overscan

Determine:

- overscan rows or columns;
- orientation behavior;
- fitting method;
- scope;
- and whether uncorrected overscan structure contaminated past scattered-light estimates.

## Artifact loading

Find every direct serializer or file load performed by Tasks or algorithms and identify its ArtifactService replacement.

## QA metric names

Find inconsistent names across algorithms, Tasks, QA, and analytics. Examples may include:

```text
rms_fibers
per_fiber_trace_residual_rms
```

Recommend one canonical fact name.

## Testing hacks

Find scientific algorithms that delegate to unrelated modules only for monkeypatching or tests. Recommend proper dependency ownership.

## Observation identity

Determine how the repository currently represents:

- Observation;
- Shot;
- Exposure;
- dither index;
- parallel mode;
- and effective exposure time.

## Product selection

Determine whether calibration selection is exact, nearest in time, inherited, interpolated, or ad hoc. Document every implicit fallback.

---

# Tests and Evidence

Do not rely only on static inspection.

Use existing tests, repository searches, import checks, fixtures, and small characterization runs where practical.

Do not run a full production reduction unless a fast test fixture already exists.

Where important behavior lacks tests, identify the smallest characterization test required before refactoring.

---

# Final Deliverables

Provide:

```text
VIRUSFLOW_REPOSITORY_RECONCILIATION.md
VIRUSFLOW_LEGACY_VOCABULARY_MAP.md
VIRUSFLOW_MIGRATION_PLAN.md
```

Also provide a concise completion summary stating:

1. what is already architecturally strong;
2. the most important mismatches;
3. the true blockers;
4. the recommended first migration slice;
5. and the first implementation task that should follow the audit.

Do not begin that implementation task until the reconciliation and migration plan have been reviewed.
