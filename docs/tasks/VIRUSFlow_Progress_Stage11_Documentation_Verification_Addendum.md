# VIRUSFlow Progress, Stage 11 Cleanup, Documentation, and Verification Addendum

## Purpose

This addendum supplements the authoritative stages 8–10 storage, materialization, sky-modeling, and parallel-execution specification.

The stages 8–10 implementation established the intended scientific and persistence boundaries:

- Compact calibration and physical models
- Temporary detector-level and pre-final spectral intermediates
- One final calibrated observation product
- `float32` storage for large non-astrometric arrays
- Scaled flux units through `BUNIT`
- Four-worker default execution
- Bounded post-run analysis materialization
- Versioned model validation and promotion

The next implementation phase should preserve that work while completing the operational and user-facing system around it.

This addendum requests four connected efforts:

1. Add reliable progress monitoring for long-running reductions.
2. Perform a full Stage 11 cleanup, including the CLI, based on the repository and architecture that now exist.
3. Rewrite the README and documentation only after the implementation and CLI are clean and stable.
4. Run representative end-to-end storage, scientific, serial-versus-parallel, progress, and cleanup verification.

This document intentionally does **not** prescribe an abstract replacement CLI command tree. Codex should inspect what it has already built, retain what is coherent, improve what is inconsistent, and make the CLI accurately reflect the repository’s real target, task, graph, artifact, model, analysis, and validation architecture.

Codex should complete the implementation without pausing for approval or checking in.

---

# 1. Precedence and implementation philosophy

This addendum supersedes the earlier Stage 11 document that proposed a specific command family such as `scan`, `plan`, `run`, `inspect`, `analyze`, `validate`, and `cleanup`.

Those may still be appropriate if they match the repository, but they are not mandated.

The governing rule is:

> The CLI and documentation should emerge from the implemented repository architecture, not force the repository into a speculative command taxonomy.

Codex should:

- Inspect the current implementation.
- Preserve coherent and tested interfaces.
- Consolidate duplicated or inconsistent behavior.
- Remove obsolete paths.
- Add missing capabilities only where the architecture requires them.
- Prefer the smallest clean public interface that exposes the real system.
- Avoid compatibility layers that perpetuate superseded concepts.
- Finish the implementation before documenting it.

The Stage 11 cleanup should include any incomplete architectural, CLI, lifecycle, migration, progress, validation, cleanup, or usability work that became visible during the stages 8–10 implementation.

---

# 2. Progress monitoring

Long-running reductions must provide clear progress information by default.

Progress should be implemented before the broader CLI cleanup is finalized, because the execution and graph interfaces should expose progress state cleanly rather than bolt a display onto task logs afterward.

## 2.1 Required user-facing behavior

For an interactive terminal, display a concise live progress view that communicates:

- Overall graph progress
- Completed work versus total work
- Currently running tasks
- Pending tasks
- Blocked tasks
- Failed tasks
- Skipped or cached tasks
- Current observation, exposure, detector, amplifier, target, or task identity when available
- Elapsed wall-clock time
- Recent task-completion rate
- Estimated time remaining when enough history exists to make that estimate meaningful
- Worker utilization relative to the configured worker count
- Final completion summary

The display must remain useful when four tasks execute concurrently.

Avoid emitting one permanent terminal line for every refresh. Use a modern terminal progress implementation or equivalent in-place rendering when a TTY is available.

## 2.2 Non-interactive and batch behavior

When output is redirected, running through a scheduler, or not attached to a TTY:

- Do not emit terminal-control characters.
- Emit periodic structured or plain progress messages.
- Include completed, total, running, failed, blocked, skipped, and elapsed values.
- Include active task identifiers.
- Use a configurable reporting interval.
- Keep logs readable alongside task output.
- Preserve a nonzero exit status for failed workflows.

This behavior should be appropriate for TACC batch logs.

## 2.3 Progress semantics

Progress must reflect task-graph state rather than a simple loop counter.

The implementation must:

- Count a documented unit of work consistently.
- Avoid double-counting tasks during parallel execution.
- Respect task dependencies.
- Handle cached, skipped, invalidated, blocked, retried, and failed tasks.
- Mark dependents as blocked when prerequisites fail.
- Avoid reporting 100 percent before required publication and cleanup complete.
- Work in serial and parallel modes.
- Avoid changing task identity or scientific output.
- Remain usable when the graph is constructed incrementally, if supported.

Where a task has meaningful internal work units, nested progress may be reported, but nested output must not overwhelm the primary graph-level display.

## 2.4 Configuration

Add CLI and configuration controls that fit the repository’s existing style.

Capabilities should include equivalents of:

```text
progress enabled or disabled
automatic TTY versus plain-log behavior
configurable reporting interval
optional structured progress output
```

The exact option names should be chosen during the CLI cleanup.

Defaults should be:

```text
progress enabled
display mode selected automatically
noninteractive updates emitted at a conservative interval
```

## 2.5 Progress tests

Add tests verifying:

1. Progress is enabled by default.
2. TTY output updates cleanly.
3. Non-TTY output contains no terminal-control corruption.
4. Parallel completion updates counters correctly.
5. Serial execution remains supported.
6. Cached and skipped tasks are represented correctly.
7. Failed and blocked tasks are represented correctly.
8. Completion is not reported before publication and cleanup.
9. Progress reporting does not alter scientific output.
10. Progress reporting does not alter deterministic task or artifact identity.
11. Structured progress output is parseable when supported.

---

# 3. Full Stage 11 cleanup

After the progress-state interface is established, perform a full Stage 11 cleanup across the repository.

This is not limited to the CLI. It should address incomplete or inconsistent implementation details exposed by the stages 8–10 work.

## 3.1 Repository inventory

Inspect the current repository and identify:

- Public CLI entry points
- Command groups and subcommands
- Target-selection paths
- Plan construction and execution paths
- Configuration sources and precedence
- Worker-count handling
- Progress and logging behavior
- Artifact and model inspection interfaces
- Validation paths
- Analysis-study interfaces
- Migration and cleanup commands
- Deprecated or unused code
- Duplicate orchestration paths
- Direct filesystem writes that bypass artifact lifecycle rules
- Dense artifact names or assumptions that remain in public interfaces
- Tests that preserve obsolete behavior
- Documentation that no longer reflects the code
- Incomplete stage boundaries
- Naming inconsistencies in targets, tasks, artifacts, models, or observations

Use the implementation as the source of truth, while preserving the intent of the authoritative architecture specifications.

## 3.2 CLI cleanup

Clean the CLI as Codex deems appropriate based on what it has built.

The final CLI should:

- Match the repository’s actual architecture.
- Present a coherent path from data selection through final products.
- Avoid exposing obsolete dense-intermediate concepts.
- Avoid requiring users to know internal task-class names.
- Use consistent names for dates, observations, exposures, targets, plans, workers, storage roots, scratch roots, and configuration.
- Apply defaults and configuration precedence consistently.
- Provide accurate help.
- Return meaningful exit codes.
- Support noninteractive use.
- Support dry-run for destructive or expensive actions where meaningful.
- Expose enough inspection to understand artifacts, models, provenance, lifecycle, dtype, units, size, validity, and run state.
- Expose validation and cleanup in a safe, discoverable way.
- Preserve serial execution.
- Preserve default parallel execution with four workers.
- Avoid nested oversubscription.
- Avoid introducing a second orchestration interface alongside the task graph.

Codex may:

- Retain the existing command structure if it is coherent.
- Rename commands.
- Merge commands.
- Split overloaded commands.
- Add subcommands.
- Remove obsolete commands.
- Add temporary compatibility aliases when justified.

Compatibility aliases should be minimal, tested, and emit clear deprecation guidance.

Do not preserve obsolete interfaces solely to avoid changing tests.

## 3.3 Configuration cleanup

Establish one consistent configuration model.

Document and test:

- Built-in defaults
- Configuration-file values
- Environment values, if supported
- Explicit CLI overrides
- Effective worker count
- Progress configuration
- Raw-data root
- Artifact root
- Registry or database location
- Scratch root
- Logging and output mode

The effective configuration should be inspectable in verbose or diagnostic output.

CLI options should override configuration values.

The default worker count remains four.

Explicit serial mode must reliably force one worker.

## 3.4 Logging and errors

Normalize logging across serial and parallel execution.

Logs should include useful task context such as:

- Task kind
- Target
- Observation or exposure
- Worker identity when useful
- Artifact publication result
- Failure context

Errors should:

- Surface the failing task.
- Preserve the root exception.
- Mark dependent work as blocked.
- Return a nonzero process status.
- Avoid reporting overall success after partial graph failure.
- Leave scratch according to the configured failure-retention policy.
- Provide a clear next step for inspection, cleanup, or rerun.

## 3.5 Artifact and model inspection

Ensure users can inspect the implemented system without direct database or filesystem archaeology.

The supported interface should expose, where applicable:

- Artifact identity
- Kind
- Lifecycle class
- State
- Payload path or locator
- Byte count
- Shape
- Dtype
- Units
- Creation time
- Producer
- Input provenance
- Model validity
- Supersession or migration state
- Analysis-study relationship
- Candidate versus accepted model state

The exact CLI surface should match the repository.

## 3.6 Cleanup and migration

Complete safe cleanup and migration behavior.

Support:

- Scratch cleanup
- Failed-run scratch cleanup
- Cache cleanup
- Identification of obsolete legacy dense artifacts
- Dry-run reporting
- Explicit destructive intent
- Protection of canonical artifacts and accepted models
- Retention of legacy payloads until validation succeeds
- Clear distinction between deactivating a registry record and deleting a payload

Do not automatically delete old dense products merely because the new implementation passes unit tests.

## 3.7 Analysis lifecycle

Ensure bounded analysis materialization is usable through the cleaned repository interfaces.

An analysis should be able to:

- Select a bounded dataset.
- Reuse production algorithms.
- Materialize normally temporary states.
- Retain none, selected, outlier, temporary, or permanent evidence according to policy.
- Record study provenance.
- Produce compact summaries.
- Produce candidate models.
- Validate candidates.
- Avoid automatic promotion.

The analysis system should not become a second uncontrolled artifact directory.

## 3.8 Code cleanup

Remove superseded code after replacements are tested.

This includes:

- Dense science-intermediate artifact writers
- Obsolete artifact kinds
- Duplicate execution paths
- Old serializers
- Old CLI branches
- Unused compatibility functions
- Documentation-only code paths
- Tests asserting retired behavior
- Dead imports and exports

Preserve unrelated pre-existing worktree changes.

---

# 4. README and documentation

Rewrite the README and documentation only after the Stage 11 implementation and CLI cleanup are stable.

The documentation must describe the software that actually exists after cleanup.

## 4.1 Fresh README

Replace or substantially rewrite `README.md` using modern scientific-software practices.

The README should include:

1. Project title and purpose
2. Current maturity and status
3. Supported scope
4. Core architecture
5. Installation
6. Minimal quick start
7. Principal CLI workflow
8. Targets, tasks, graphs, artifacts, and models
9. Production storage and materialization behavior
10. Final calibrated observation products
11. Analysis studies and model development
12. Four-worker default execution
13. Progress-monitor behavior
14. Links to detailed documentation
15. Testing
16. Repository layout
17. Troubleshooting
18. Development and contribution guidance
19. License information, if defined
20. Citation and acknowledgment guidance, if defined

Do not retain obsolete examples, serial-default instructions, dense artifact descriptions, or stale command names.

The README should distinguish clearly between:

- Raw data
- Accepted models
- Final products
- Scratch intermediates
- Caches
- QA
- Analysis studies
- Candidate and promoted models

## 4.2 Getting-started guide

Create a focused guide for a technically capable new user.

Cover:

- Prerequisites
- Environment creation
- Installation
- Configuration
- Data paths
- Artifact and scratch paths
- Discovering CLI help
- Selecting or scanning data
- Constructing or inspecting work
- Running with default parallelism
- Running serially
- Reading progress
- Locating final products
- Inspecting provenance and storage
- Understanding failures
- Safe rerun or resume behavior
- Cleaning scratch and caches
- Performing a small validation run

Use only commands that exist after Stage 11 cleanup.

## 4.3 Example usage

Add practical, tested examples.

At minimum include:

### Calibration example

- Select a small dataset or date range
- Build the required calibration work
- Execute it
- Inspect model artifacts and QA

### Single-exposure example

- Reduce one science exposure
- Observe progress
- Inspect provenance and storage
- Locate relevant outputs

### Complete-observation example

- Reduce a complete dithered observation
- Use default four-worker execution
- Force serial execution
- Locate the final calibrated observation product
- Confirm dense science intermediates were not persisted

Use observation membership from the repository. Do not introduce a new hard-coded three-exposure assumption.

### Analysis-materialization example

- Select a bounded dataset
- Materialize a temporary detector or spectral state
- Calculate a diagnostic such as scattered-light residual RMS or sky-line residuals
- Retain selected or outlier evidence
- Record study provenance
- Produce a candidate model
- Do not promote it automatically

### Model-inspection example

Inspect:

- Scattered-light representation
- Sky latent-grid sampling
- LSF reference or absence
- Response composition
- Lifecycle
- Size
- Dtype
- Units
- Provenance
- Validity

### Batch example

Provide a realistic noninteractive example appropriate for TACC or another scheduler, including:

- Progress logging
- Worker configuration
- Scratch handling
- Exit status
- Failure inspection
- Cleanup behavior

Mark site-specific scheduler values clearly.

## 4.4 Documentation organization

Use a maintainable structure appropriate to the repository.

A possible structure is:

```text
README.md
docs/
    getting-started.md
    cli-reference.md
    concepts/
        architecture.md
        artifacts-and-lifecycles.md
        models-and-materialization.md
        parallel-execution.md
    guides/
        calibration.md
        science-reduction.md
        analysis-studies.md
        validation.md
    examples/
        calibration.md
        single-exposure.md
        complete-observation.md
        model-analysis.md
        batch.md
    troubleshooting.md
    migration/
        stage-11.md
```

The exact structure may differ.

Avoid duplicating large sections.

Generate CLI reference material from the parser when practical, or test handwritten command documentation against the actual CLI.

---

# 5. Representative end-to-end verification

After Stage 11 cleanup, progress implementation, documentation, and tests, run a representative complete science observation through the revised pipeline.

This validation is mandatory before old dense artifacts are deleted or the full dataset is launched.

## 5.1 Storage verification

Record:

- Total persistent artifact size
- Artifact count by kind
- Bytes by kind
- Final calibrated observation-product size
- Largest individual artifacts
- Peak scratch usage when available
- Cache usage
- Confirmation that scratch was cleaned after success
- Progress-log output

Confirm that normal production does not persist:

```text
reduced_science_image
scatter_subtracted_image
full-CCD scattered-light evaluation
persistent aperture-extracted spectrum
separate persistent extracted variance
persistent per-fiber sky prediction
standalone persistent sky-subtracted spectrum
```

The final complete observation product should normally be approximately:

```text
2–4 GB
```

A different size must be explained by a scientifically justified payload.

## 5.2 Scientific verification

Compare the revised result against a previous validated reduction using scientifically meaningful quantities, including where applicable:

- Final calibrated flux
- Uncertainty or variance
- Masks
- Wavelength sampling
- Astrometric coordinates
- Sky-line residuals
- Integrated line flux
- Continuum behavior
- Valid-fiber counts
- QA metrics
- Observation membership
- Dither relationships
- Response behavior

Define tolerances explicitly.

Quantify differences caused by:

- `float32` persistence
- Flux-unit scaling
- Flux-conserving sky projection
- Sky latent-grid changes
- LSF-aware behavior
- Intentional model revisions

Do not merely assert that differences are harmless.

## 5.3 Parallel-versus-serial verification

Run the same representative observation with:

```text
default four-worker execution
explicit serial execution
```

Confirm:

- Scientifically equivalent final arrays
- Correct graph dependencies
- No duplicate publication
- Worker-safe scratch
- Correct progress behavior
- Correct exit status
- Correct cleanup
- Documented artifact-identity semantics
- Equivalent provenance except for execution metadata where appropriate

## 5.4 CLI and documentation verification

Exercise the actual cleaned CLI workflow used in the new documentation.

Confirm:

- Top-level help is accurate.
- Subcommand help is accurate.
- Examples execute.
- Configuration precedence is correct.
- Four workers are used by default.
- Serial mode works.
- Progress is readable interactively and in logs.
- Structured output is parseable where supported.
- Deprecated aliases warn correctly.
- Invalid combinations fail clearly.
- Cleanup dry-run is safe.
- Destructive operations require explicit intent.

## 5.5 Verification report

Produce both a human-readable and machine-readable report containing:

- Commands
- Effective configuration
- Input observation
- Software revision
- Calibration and model references
- Runtime
- Worker count
- Progress mode
- Peak scratch use
- Final storage
- Artifact-size table
- Scientific comparison metrics
- Tolerances
- Parallel-versus-serial comparison
- CLI checks
- Documentation example checks
- Warnings
- Unresolved limitations
- Pass or fail for each acceptance criterion

Preserve the report as a validation artifact.

Do not claim the representative validation was completed unless the real observation was actually run.

---

# 6. Testing

Add or update tests across the following areas.

## 6.1 Progress

Test:

1. Default enablement
2. TTY rendering
3. Non-TTY rendering
4. Parallel graph counters
5. Serial graph counters
6. Cached and skipped tasks
7. Failed and blocked tasks
8. Retry semantics
9. Completion after publication and cleanup
10. Structured output
11. Scientific-output invariance

## 6.2 CLI

Test:

1. Entry points
2. Top-level help
3. Subcommand help
4. Consistent selection syntax
5. Configuration precedence
6. Four-worker default
7. Serial override
8. Output modes
9. Logging modes
10. Exit codes
11. Dry-run behavior
12. Deprecation warnings
13. Invalid option combinations
14. Cleanup safeguards
15. Artifact and model inspection
16. Validation invocation

## 6.3 Lifecycle and cleanup

Test:

1. Scratch cleanup after success
2. Failure-retention policy
3. Cache deletion
4. Protection of canonical artifacts
5. Protection of accepted models
6. Legacy dense-artifact identification
7. Dry-run migration and cleanup
8. Explicit deletion requirements
9. Registry state after migration
10. Atomic concurrent publication

## 6.4 Documentation

Where feasible:

- Execute shell examples in a test environment.
- Verify referenced commands exist.
- Verify links.
- Verify the documented default worker count is four.
- Verify documentation does not describe retired dense intermediates as persistent.
- Verify CLI examples match parser behavior.
- Verify README quick start completes in the test fixture.

## 6.5 Verification support

Test:

- Validation report generation
- Machine-readable report schema
- Pass/fail status
- Preservation as an artifact
- Parallel-versus-serial comparison logic
- Storage-budget checks
- Scientific-tolerance evaluation

---

# 7. Implementation order

Use the following order.

## Phase 1: Progress state and monitor

- Define graph progress events or callbacks.
- Add interactive rendering.
- Add noninteractive reporting.
- Add configuration and tests.

## Phase 2: Stage 11 repository and CLI inventory

- Map what currently exists.
- Identify inconsistencies and obsolete behavior.
- Determine the cleanest CLI consistent with the repository.
- Continue without pausing for approval.

## Phase 3: Stage 11 cleanup

- Normalize CLI behavior.
- Normalize configuration.
- Normalize logging and errors.
- Complete inspection interfaces.
- Complete validation interfaces.
- Complete cleanup and migration behavior.
- Complete analysis lifecycle interfaces.
- Remove superseded code.
- Update tests.

## Phase 4: README and documentation

- Rewrite README.
- Write getting-started guide.
- Write examples.
- Write CLI reference.
- Write troubleshooting.
- Write migration notes.
- Validate all documented commands.

## Phase 5: Representative verification

- Run focused tests.
- Run the full suite.
- Run documentation checks.
- Run a representative observation in four-worker mode.
- Run the same observation in serial mode.
- Measure storage.
- Compare science outputs.
- Validate progress and cleanup.
- Preserve the verification report.

---

# 8. Completion criteria

This addendum is complete when:

```text
Graph-aware progress is enabled by default.

Progress works in interactive and batch execution.

The Stage 11 cleanup is based on the repository Codex actually built.

The CLI coherently represents the implemented target, task, graph, artifact,
model, analysis, validation, and cleanup architecture.

Obsolete interfaces and persistence paths are removed or explicitly deprecated.

Configuration and worker semantics are consistent.

Four workers remain the default.

Serial execution remains available.

Inspection exposes lifecycle, provenance, size, dtype, units, validity,
and state.

Cleanup is safe and supports dry-run.

Bounded analysis materialization remains available.

The README is rewritten after the implementation is stable.

Getting-started and example documentation uses real commands.

Documentation does not preserve obsolete dense-artifact assumptions.

CLI, progress, lifecycle, documentation, and validation tests pass.

A representative complete observation is run in parallel and serial modes.

Its final product and total persistent storage are measured.

Retired dense production artifacts are absent.

Parallel and serial results are scientifically equivalent.

Progress and scratch cleanup are verified in the real run.

A human-readable and machine-readable validation report is preserved.

Legacy payload deletion remains opt-in until validation succeeds.
```

---

# 9. Required final Codex report

At completion, report:

1. Progress-monitor implementation and behavior
2. The final CLI structure and why it fits the repository
3. Commands retained, renamed, deprecated, or removed
4. Configuration precedence
5. Stage 11 cleanup performed outside the CLI
6. Documentation files created or replaced
7. Tests run and results
8. Representative validation commands
9. Runtime and worker count
10. Artifact counts and sizes
11. Final observation-product size
12. Scientific comparison metrics and tolerances
13. Parallel-versus-serial results
14. Progress behavior in interactive or batch mode
15. Scratch and cleanup results
16. Validation-report location
17. Remaining limitations
18. Any work that could not be completed with the available data

Do not claim completion of any real-data verification step that was not actually performed.
