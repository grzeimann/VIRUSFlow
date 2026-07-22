# VIRUSFlow Agent Instructions

## Project Purpose

VIRUSFlow is an ontology-first, artifact-driven scientific reduction, calibration, QA, analytics, and knowledge system for the VIRUS instrument.

The repository must preserve both:

* scientifically valid behavior already present in the codebase;
* the canonical vocabulary, architecture, and contracts defined by the VIRUSFlow knowledge documents.

Treat the existing repository as scientific implementation evidence. Treat the knowledge and architecture documents as authoritative for future structure and vocabulary.

---

## Sources of Authority

Before making architectural or scientific changes, read:

```text
docs/architecture/VIRUSFlow_Knowledge_System_Coherence_Map.md
docs/architecture/VIRUSFlow_Target_Architecture.md
docs/architecture/VIRUSFlow_Implementation_Plan_From_Knowledge_Notes.md
docs/knowledge/VIRUSFlow_Knowledge_Note_*.md
```

Task-specific instructions live under:

```text
docs/tasks/
```

When a task document conflicts with this file or with an authoritative scientific note, report the conflict rather than silently choosing one.

---

## Scientific Design Principles

### Scientific Object Before Implementation

A Product represents a physical or inferential scientific object.

An algorithm is one method for estimating that Product.

Do not define canonical Product identity from one legacy implementation.

### Exposure Is Atomic

An Exposure is the atomic scientific measurement.

Observation, Shot, DitherSet, and ObservationSet are grouping entities.

Preserve exposure-specific:

* sky;
* seeing;
* transparency;
* mirror illumination;
* astrometry;
* effective exposure time;
* throughput;
* detector state.

Do not collapse these quantities automatically across an observation or dither set.

### Preserve Physical Scope

Every Product must have an explicit primary scope, such as:

```text
PIXEL
FIBER
AMPLIFIER
PHYSICAL_CCD
SPECTROGRAPH
IFU
EXPOSURE
DITHER_SET
OBSERVATION
OBSERVATION_SET
INSTRUMENT_EPOCH
```

Do not compute physical-CCD corrections independently per amplifier when the scientific model requires paired amplifiers.

### Preserve Complete Hardware Lineage

For amplifier-derived Products, preserve the complete hardware identity:

```text
IFUSLOT
IFUID
SPECID
AMP
CONTROLLER
```

Also preserve:

* hardware epoch;
* configuration versions;
* source Artifact IDs;
* raw-frame IDs;
* algorithm version;
* software version;
* active assumptions.

### Preserve Scientific Evidence

Do not persist only final scalar summaries when the following remain scientifically useful:

* residual arrays;
* trace samples;
* masks;
* matched-source tables;
* model components;
* rejected measurements;
* uncertainty estimates;
* comparison diagnostics.

Do not hide empirical corrections inside unrelated Products.

---

## Architectural Boundaries

### Algorithms

Algorithms:

* receive arrays and typed metadata;
* perform scientific calculations;
* return `AlgoResult`;
* do not query the registry;
* do not choose calibration Products;
* do not write files;
* do not create plots;
* do not assign QA status;
* do not depend on CLI globals.

Algorithms should be independently testable and deterministic for fixed inputs and parameters.

### Tasks

Tasks:

* resolve Targets;
* select input Products and configuration;
* load payloads through ArtifactService;
* invoke algorithms;
* publish scientific facts;
* run QA policy;
* construct ArtifactRequests;
* persist outputs.

Tasks should not contain substantial scientific calculations.

### ArtifactService

ArtifactService is the canonical boundary for:

* Product registration;
* Product loading;
* serializer selection;
* persistence;
* provenance;
* validity;
* checksums;
* immutable revisions;
* Product selection.

Direct serializer or file-format access inside Tasks is migration debt unless it is part of ArtifactService implementation.

### QA

Algorithms emit facts.

QA rules interpret facts.

Keep these concepts separate:

```text
metric
fact
rule
status
usability
```

Do not hard-code scientific QA policy inside algorithms.

A Product may be degraded but still usable for diagnostics, priors, or limited science. QA status is not a universal deletion decision.

### Analytics

Analytics is post-run and read-only with respect to source reduction Products.

Analytics may create new analytic Products, but it must not mutate source Products.

Plots belong in analytics or reporting, not in scientific algorithms.

### Configuration

Instrument geometry, hardware history, gains, line lists, dither patterns, shutter policies, fiber maps, and similar knowledge are versioned configuration.

Do not hard-code time-dependent instrument state inside algorithms.

---

## Canonical Vocabulary

Use registered:

* Product kinds;
* scopes;
* relations;
* units;
* coordinate conventions;
* assumptions.

Do not invent new Artifact kinds inside individual Tasks.

Legacy names may remain temporarily for compatibility, but new interfaces should use canonical names.

Examples:

```text
masterflt -> master_ldls
mastercmp -> master_arc
ftf -> fiber_normalization or a named component
plaw -> a specific scattered-light Product
```

Do not rename an ambiguous legacy quantity until its scientific meaning has been verified.

Preserve the legacy-to-canonical mapping in the repository vocabulary document.

---

## Repository Change Policy

### Inspect Before Editing

Before changing a subsystem:

1. inspect its implementation;
2. inspect its callers and dependencies;
3. inspect its tests;
4. identify its scientific responsibility;
5. compare it with the knowledge notes;
6. classify it as `KEEP`, `ADAPT`, `REFACTOR`, `REPLACE`, `RESEARCH`, or `REMOVE`.

### Preserve Working Scientific Behavior

Do not remove or rewrite scientific code without:

* characterization tests;
* a migration path;
* evidence that the replacement preserves or intentionally changes the scientific result.

Old, procedural, or inelegant code may contain important scientific behavior.

Static appearance alone is not sufficient reason for removal.

### Prefer Small, Reviewable Changes

Prefer changes that:

* have one clear architectural purpose;
* preserve a rollback path;
* can be reviewed independently;
* include focused tests.

Do not perform broad repository reorganizations unless explicitly authorized by an approved migration step.

### Persisted Data and Public Contracts

Do not change the following without explicit task authorization and a migration plan:

* persisted schemas;
* Artifact kinds;
* storage layout;
* public interfaces;
* Target identities;
* provenance semantics;
* unit conventions;
* coordinate conventions.

### Compatibility Code

Temporary adapters are acceptable when they support controlled migration.

Do not allow compatibility shims to become the canonical permanent interface.

Avoid creating a permanent parallel legacy architecture.

---

## Testing Expectations

Use the smallest test that proves the relevant contract.

### Before Refactoring Legacy Science Code

Add or identify characterization tests for current behavior.

### Test Categories

Use as appropriate:

```text
unit
contract
integration
regression
science acceptance
```

### Scientific Acceptance

Where relevant, test physical outcomes such as:

* bias residuals;
* read noise;
* trace accuracy;
* wavelength residuals;
* extracted-flux stability;
* scattered-light residuals;
* sky residuals;
* astrometric residuals;
* dither coverage;
* standard-star response.

### Test Integrity

Do not alter a scientific implementation solely to accommodate a monkeypatch or an unrelated test import path.

Move dependencies to their correct owner and update the tests.

Do not weaken tests merely to make a migration pass.

---

## Execution Behavior

Before editing, report:

* relevant files and symbols;
* intended changes;
* scientific behavior that must remain stable;
* tests that will be used.

During implementation:

* distinguish verified findings from inference;
* cite exact repository paths and symbols when explaining conclusions;
* stop when genuinely required scientific information is unavailable;
* do not ask for information already present in the repository or knowledge notes;
* do not silently choose among conflicting scientific conventions;
* do not make unrelated opportunistic changes.

After implementation, report:

* changed files;
* commands and tests run;
* results;
* assumptions;
* unresolved issues;
* recommended next migration step.

---

## Access and Safety

Do not expose, copy, or modify:

* credentials;
* API tokens;
* private keys;
* unrelated sensitive files.

Do not run destructive commands without explicit authorization.

Do not modify files outside the repository unless the task explicitly requires it.

Do not use full-access mode when repository-scoped access is sufficient.

---

## Definition of Done

A task is complete only when:

* implementation matches the approved scientific responsibility;
* canonical vocabulary and scope are used;
* provenance and configuration versions are preserved;
* QA facts are available where required;
* relevant tests pass;
* no unrelated behavior changed;
* assumptions and unresolved issues are documented;
* the repository remains in a reviewable, reversible state.
