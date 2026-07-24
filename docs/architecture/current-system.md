# Current implementation flow

VIRUSFlow has one supported orchestration path for calibration, exposure, and observation work. `virusflow run` resolves a Target, builds graph state, and executes it with `PlanningExecutor`. Calibration runs pass through `ReductionGraph.plan` and `planning.schedule`; exposure and observation runs construct their graph nodes directly and use the same executor, progress state, timing report, failure propagation, and worker limit.

```text
CLI/configuration
  → Target and registry selection
  → ReductionGraph.plan (calibrations)
  → planning.schedule
  → PlanningExecutor
  → Task
  → storage-neutral algorithm
  → ArtifactRequest
  → DefaultPublicationService
  → ArtifactService.persist_request
  → serializer and registry
```

Tasks load Product components only through `ArtifactService.load_component`. `ArtifactService` owns selection, immutable revision identity, serialization, checksums, normalized provenance, validity, lifecycle state, and publication rejection for scratch-only or deprecated kinds. `AnalysisStudyService` is the bounded analysis-materialization entry point; it records study selection, retention, byte budget, lineage, and candidate validation without promoting a model.

The calibration graph supports `master_bias`, `master_dark`, `master_ldls`, separate `master_hg` and `master_cd`, composed `master_arc`, `master_twilight`, canonical `master_sci`, `trace_map`, `wavelength_map`, `extracted_master_sci_spectrum`, and `fiber_wavelength_spectral_mask`. Master Science construction has no trace or wavelength dependency; extraction depends on the image and trace, and masking depends on the extracted spectrum and wavelength map. The default mask path uses coarse self-normalization. A compatible twilight-derived fiber normalization may instead be supplied as an explicit, provenance-recorded mask parent; it is never discovered through an execution-time ambient lookup. A resolved calibration group carries exact raw IDs into the task, while applicability remains separate. Derived targets carry parent-group identities into scheduling, so multiple scientifically distinct groups in one ZIP cannot overwrite each other's dependency mapping. Legacy kind aliases are read-only registry and migration vocabulary. Dense per-exposure detector, extracted-spectrum, sky-prediction, and sky-subtracted intermediates are scratch-only and cannot be registered by normal publication. The Master Science spectrum is a canonical calibration diagnostic rather than a per-exposure scratch intermediate. The final normal dense observation Product is `calibrated_fiber_observation`.

Normal production does not persist the dense per-exposure scratch Products;
their canonical retained counterpart is the final calibrated observation.

Cleanup is lifecycle-specific. Scratch and cache cleanup are dry-run unless `--execute` is given. Legacy cleanup inventories active superseded records, deactivates only with `--deactivate`, and physically deletes payloads only when `--delete-payloads` and `--validation-succeeded` accompany deactivation. Registry deactivation never implies payload deletion.

Configuration precedence is built-in defaults, then planning YAML, then explicit CLI values. Four workers and progress are the defaults; `--serial` forces one. Planning YAML accepts canonical calibration kinds and explicit edges only. The precise cadence timestamps, calibration identity, chunked fixed-center biweight combination, task timing phases, and current scientific algorithms are unchanged by Stage 11 cleanup.

Cadence defaults follow scientific purpose: nightly bias, monthly dark, weekly
twilight, isolated three-hour LDLS/Hg/Cd observations, deterministic Hg/Cd
pairing, and sufficient long-exposure science grouped monthly or by an explicit
dark-time/observing interval. The cadence report is the supported pre-execution
inspection boundary. See [calibration cadence](../calibration-cadence.md).
