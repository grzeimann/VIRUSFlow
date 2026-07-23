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

The calibration graph supports `master_bias`, `master_dark`, `master_ldls`, `master_arc`, `master_twilight`, `trace_map`, and `wavelength_map`. Legacy kind aliases are read-only registry and migration vocabulary. Dense detector, extracted-spectrum, sky-prediction, and sky-subtracted intermediates are scratch-only and cannot be registered by normal publication. The final normal dense science Product is `calibrated_fiber_observation`.

Cleanup is lifecycle-specific. Scratch and cache cleanup are dry-run unless `--execute` is given. Legacy cleanup inventories active superseded records, deactivates only with `--deactivate`, and physically deletes payloads only when `--delete-payloads` and `--validation-succeeded` accompany deactivation. Registry deactivation never implies payload deletion.

Configuration precedence is built-in defaults, then planning YAML, then explicit CLI values. Four workers and progress are the defaults; `--serial` forces one. Planning YAML accepts canonical calibration kinds and explicit edges only. The precise cadence timestamps, calibration identity, chunked fixed-center biweight combination, task timing phases, and current scientific algorithms are unchanged by Stage 11 cleanup.
