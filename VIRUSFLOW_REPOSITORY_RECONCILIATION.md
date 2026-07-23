# VIRUSFlow Repository Reconciliation

Status: authoritative reconciliation; Migration Plan Steps 1–7 are accepted, and Steps 8–10 are implemented with their internal scientific and verification gates passed on 2026-07-22. Step 11 remains unauthorized and unstarted.

## 1. Evidence and decision rules

The existing repository is scientific implementation evidence. `docs/architecture/`, `docs/knowledge/VIRUSFlow_Knowledge_Note_*.md`, and `docs/AGENTS.md` define the target vocabulary and architectural direction.

Evidence labels used below:

- **Verified — source:** directly observed in the current working tree on 2026-07-22.
- **Verified — test:** asserted by a named test; inspected tests were also run as listed in section 11.
- **Verified — database:** read-only query of `virusflow.sqlite3` using `mode=ro&immutable=1`.
- **Target requirement:** stated by an architecture or knowledge document.
- **Inference:** a migration recommendation based on verified evidence; it is not a claim about current behavior.

The current working tree includes a manual change to `virusflow/algorithms/fiber.py:get_spectra`: it returns the weighted aperture **sum**. Its docstring still says that it averages/divides by `npix`; that text is documentation debt, not current behavior. This audit does not modify it.

Classification meanings are those in `docs/tasks/VIRUSFlow_Repository_Reconciliation.md`. Every major current subsystem has one primary classification. No subsystem is marked `REMOVE`: static evidence is not enough to prove that scientific behavior is disposable.

## 2. Executive reconciliation

### Strong foundations to preserve

1. `virusflow/core/identity.py:ZipCode` and `virusflow/registry/database.py:upsert_amplifier` preserve the complete `IFUSLOT + IFUID + SPECID + AMP + CONTROLLER` identity.
2. `virusflow/planning/graph.py:TaskSpec`, `Edge`, and `ReductionGraph`, together with `planning.scheduler.schedule` and `executors.planning_executor.PlanningExecutor`, establish a useful planning/execution boundary.
3. `virusflow/core/algo_result.py:AlgoResult`, `artifacts.requests.ArtifactRequest`, and `LogicalComponent` are good storage-neutral contract seeds.
4. `virusflow/artifacts/service.py:ArtifactService`, serializer dispatch, and `artifacts.io_fits.write_array_fits` provide a viable persistence facade and atomic FITS writer.
5. `virusflow/qa/engine.py:QAEngine` and `virusflow/analytics/queries.py:load_array` point toward separate QA policy and storage-neutral post-run analytics.

### Most important mismatches

1. Canonical ontology, physical scopes, relations, units, coordinates, assumptions, and configuration timelines are not implemented.
2. Algorithms receive file references and indirectly perform FITS/tar/SQLite I/O; trace and wavelength code also load configuration/files or plot.
3. Artifact publication supports only one persisted component and does not preserve complete validity, lineage relationships, immutable revisions, or checksums.
4. The task graph is amplifier-calibration-only and contains two verified defects: no `trace -> wave` edge, and `master_cmp` is planned from `master_flat` while `CmpTask` actually queries raw `cmp` frames.
5. Exposure, physical-CCD, observation/dither, astrometry, sky, response, and uncertainty Products are mostly absent.

## 3. Repository inventory and primary classifications

| Existing path | Current responsibility | Scientific domain | Architectural layer | Classification | Evidence | Required action |
|---|---|---|---|---|---|---|
| `virusflow/core/identity.py` | `ZipCode`, `RawFileId`, ZipCode parsing | Hardware/raw identity | ontology seed | **KEEP** | `ZipCode.as_tuple`, `ZipCode.key`; `test_arch_core_objects.py` imports core objects | Retain complete ZipCode; add separate canonical entity/scope types without weakening lineage. |
| `virusflow/core/algo_result.py` | Flexible result arrays, scalars, metadata, messages, timings, version | All algorithms | contracts | **ADAPT** | `AlgoResult`, `ensure_algo_result`; `test_algo_result_as_meta_includes_kind_and_version` | Register canonical component/fact names and validate units/coordinates without removing the flexible result container. |
| `virusflow/contracts/` | Minimal per-kind result and Artifact contracts | Calibration | contracts | **ADAPT** | `BiasResultContract`, `MasterBiasContract`, etc.; `test_result_contract_smoke_validation` | Expand contracts to canonical kinds, complete components, scopes, units, validity, and lineage. |
| `virusflow/planning/targets.py`, `cadence.py`, `adapter.py` | Generic Targets/windows and calibration cadence | Calibration targeting | targets | **ADAPT** | `Target`, `TemporalWindow`, `TimeCadence`, `PlanningTargetAdapter` | Introduce typed amplifier/CCD/exposure/observation targets while preserving the generic planning interface. |
| `virusflow/planning/graph.py`, `defaults.py`, `mapping.py` | Declarative graph, idempotent planning, default nodes/edges and selection | Calibration workflow | graphs | **REFACTOR** | `ReductionGraph.plan`; defaults include only `flat -> trace`, `cmp -> wave` | Add explicit canonical dependencies; correct `trace -> wave` and raw-`cmp` planning defects. |
| `virusflow/planning/config.py` | YAML node/cadence/edge overrides | Operational planning | configuration | **ADAPT** | `PlanningConfig.apply_overrides`, `load_planning_config` | Keep declarative override mechanism; separate planning policy from versioned scientific configuration. |
| `virusflow/planning/scheduler.py`, `validate.py`, `virusflow/executors/planning_executor.py` | Topological scheduling, validation, execution | Workflow runtime | graphs/execution | **KEEP** | `schedule`, `validate_graph`, `PlanningExecutor.run`; planning smoke test | Retain mechanics; strengthen failure/dependency semantics through tests when graphs expand. |
| `virusflow/tasks/base.py` | Task context, raw selection, artifact fallback selection, registration helper | Calibration orchestration | tasks | **REFACTOR** | `CalibrationTask.query_inputs`, `_resolve_artifact`, `Task.save_artifact` | Make target/config/product resolution explicit and consolidate publication, validity, and parent handling. |
| `virusflow/tasks/calibs.py` | Bias, Dark, Flat, Cmp, Twilight, Science diagnostic, Trace, Wave orchestration | Calibration/science diagnostics | tasks | **REFACTOR** | `BiasTask` through `WaveTask` | Remove serializer bypass, preserve all requested components, enforce QA blocking, and use canonical kinds without changing algorithms first. |
| `virusflow/tasks/mapping.py`, `tasks/__init__.py` | Kind-to-Task and name/version lookup | Workflow compatibility | tasks | **ADAPT** | `default_kind_to_task`, `get_task_class` | Provide legacy aliases while canonical task/product identifiers are introduced. |
| `virusflow/algorithms/ccd.py` | Overscan subtraction, trim, orientation, gain, error estimate, mask repair | Detector electronics/geometry | algorithms | **ADAPT** | `orient_amplifier_image`, `reduce_raw_amplifier_frame`, `repair_masked_columns` | Split array-only stages and return explicit overscan/orientation/variance evidence; load configuration outside algorithms. |
| `virusflow/algorithms/bias.py`, `dark.py`, `flat.py`, `cmp.py`, `twi.py`, `sci.py` | Robust amplifier-frame combination and masks/summary facts | Detector/calibration | algorithms | **ADAPT** | `step_bias`, `step_dark`, `step_flt`, `step_cmp`, `step_twi`, `build_master_science`; algorithm tests | Preserve scientific behavior with characterization tests; change inputs from paths to arrays/typed metadata. |
| `virusflow/algorithms/trace.py` | Trace-reference selection, empirical detection/fitting, residual facts | Fiber geometry | algorithms | **RESEARCH** | `get_trace_reference`, `_get_trace`, `fit_fiber_traces`; direct `glob`, `np.loadtxt`, FITS load and hard-coded exception | Characterize reference choice, exception, coordinates, and recovery before adapting to configuration and array-only contracts. |
| `virusflow/algorithms/wave.py` | Arc extraction/identification, wavelength fitting, residuals and debug plotting | Wavelength geometry | algorithms | **RESEARCH** | `fit_wavelength_solution`, `_identify_arc`, `_plot_identify_arc_summary` | Preserve matching behavior; externalize line list/configuration and move plots to analytics. |
| `virusflow/algorithms/fiber.py` | Fractional aperture extraction and peak finding | Extraction/trace | algorithms | **RESEARCH** | Current `get_spectra` returns `spec`; docstring still describes `spec / npix` | Characterize sum scale, boundary weights, masks, variance, and callers before contract migration; correct docs later. |
| `virusflow/algorithms/utils/masks.py` | Masked-column interpolation | Detector defects | algorithms | **ADAPT** | `interpolate_masked_detector_pixels`; used by `WaveTask` | Define mask semantics and keep repair as explicit derived data, not silent mutation. |
| `virusflow/algorithms/io.py` | FITS/tar loading and registry tar-index lookup | Raw access | misplaced I/O | **REFACTOR** | `read_fits`, `_lookup_tar_member_from_db`, global `_REG_DB_PATH` | Move responsibility behind the I/O/ArtifactService boundary; algorithms receive loaded arrays/headers. |
| `virusflow/artifacts/models.py`, `requests.py` | Artifact, scope, storage, provenance, relation and logical-component models | Product representation | artifacts | **ADAPT** | `Artifact`, `Scope`, `Provenance`, `ArtifactRelation`, `ArtifactRequest` | Add canonical scopes, identity, validity, units, coordinates, assumptions, revision, and relation vocabulary. |
| `virusflow/artifacts/service.py`, `materialize.py` | Registration, selection, describe/load, serializer dispatch, array materialization | Product lifecycle | artifacts | **REFACTOR** | `ArtifactService.load_payload`; `ArtifactMaterializer`; storage-neutral analytics test | Make the service own persistence and complete component loading; stop swallowing load errors and returning incomplete descriptions. |
| `virusflow/artifacts/serializers/`, `io_fits.py` | FITS describe/load and atomic array writing | Representation | artifacts/I/O | **KEEP** | `SerializerRegistry`, `array_fits.load`, `write_array_fits`; serializer tests | Retain behind ArtifactService; extend rather than bypass for tables/multi-component Products. |
| `virusflow/publication/`, `virusflow/persistence/policy.py` | Contract validation, representation/path choice, single-component write and registration | Product publication | artifacts/persistence | **REFACTOR** | `DefaultPublicationService._select_primary_component`; `DefaultPersistencePolicy.filename` | Publish every contract component, use service serializers, include identity/window/revision in paths, and use one parent interface. |
| `virusflow/artifacts/provenance.py`, `registry_adapter.py` | Provenance hash/build and translation to legacy registry schema | Lineage | artifacts/registry | **REFACTOR** | `build_provenance`, `RegistryAdapter.register` | Preserve hash/git capture; carry validity and normalized relations directly rather than mining provenance parameters. |
| `virusflow/artifacts/diagnostics.py`, `virusflow/qa/engine.py`, `docs/qa_default.yml` | YAML rules, metric extraction, status and persistence | QA | qa | **REFACTOR** | `QAEngine.evaluate`, `DiagnosticsFacade.evaluate_and_save`; QA tests | Separate facts, rules, status and usability. Thresholds remain configurable policy. Stop direct serializer load and broad exception suppression. |
| `virusflow/registry/database.py` (subsystem) | SQLite raw inventory, hardware identity, artifacts, provenance, selection and QA | Registry | registry | **REFACTOR** | `SCHEMA`, `register_raw_file`, `find_artifacts`, `save_artifact` | Preserve SQLite and reusable structures; evolve interfaces/tables incrementally with compatibility and migrations only after review. |
| `virusflow/analytics/queries.py` | Registry queries and storage-neutral payload loading | Cross-product studies | analytics/query | **KEEP** | `load_array` delegates to `ArtifactService.load_payload`; dedicated test | Use as the analytics access pattern; later move generic selectors to `query/`. |
| `virusflow/analytics/service.py`, `studies/`, `outputs.py` | Trace/wavelength/calibration/health/trending/report studies and static outputs | Analytics | analytics | **ADAPT** | `AnalyticsService.run`, `register_static_file` | Add Bias stability and canonical Product/fact queries; retain post-run read-only source behavior. |
| `virusflow/storage/filesystem.py` | Loose FITS and tar-member discovery | Raw discovery | io | **KEEP** | `FileSystemStorage.iter_raw_sources` | Retain and integrate with canonical `io/`; do not couple algorithms to it. |
| `virusflow/registry/database.py` raw-header/tar helpers | Filename/header parsing and tar indexing | Raw metadata | io/registry | **ADAPT** | `_parse_virus_member_name`, `ensure_tar_index`, header readers | Separate I/O from registry writes and add versioned identity/mode policy. |
| `virusflow/cli/` | Scan, query, QA, analytics, planning and execution commands | Operations | cli | **ADAPT** | `cmd_run`, `_run_planned`, command parsers | Preserve commands while routing through canonical services and vocabulary. |
| `tests/` | Unit, smoke and integration-style tests | All current domains | tests | **ADAPT** | 27 named tests across 13 files | Preserve existing tests; add contracts, regression/characterization and scientific acceptance layers before refactoring behavior. |

## 4. Registry reconciliation by table and interface

SQLite remains an acceptable implementation. No measured scaling or functional evidence justifies replacing the database engine.

| Registry element | Classification | Evidence and action |
|---|---|---|
| SQLite engine and transactional `connect`/`init_db` | **KEEP** | Existing WAL/retry behavior supports current workload. Benchmark before considering another engine. |
| `amplifiers` and `upsert_amplifier` | **KEEP** | Stores all five ZipCode components. Add configuration epoch relations alongside it rather than removing it. |
| `raw_files`, `tar_files`, `tar_members` and inventory APIs | **ADAPT** | Useful raw/tar inventory; add checksums, explicit raw identity and clearer I/O ownership. |
| `exposures` | **ADAPT** | Retains exposure ID/time/frame type but needs atomic Exposure identity and configuration/mode relations. |
| `exposure_details` | **ADAPT** | Captures `QOBJECT`, `QPROG`, `PEXPTIME`, date and pointing; lacks `EXPTIME`, mode classification, effective time and dither/observation relations. |
| `artifacts` | **REFACTOR** | Current columns are kind/name/path/amp/validity only. Add canonical kind, scope identity, representation, revision, checksum and immutable status through reviewed migrations. |
| `provenance` | **REFACTOR** | Preserves algorithm, version/hash/time and parent text. Normalize parameters/configuration/assumptions and use relation rows. |
| `dependencies` | **ADAPT** | Correct normalized seed, but verified database count is `0` and `save_artifact` never inserts it. Populate with typed relations after contract work. |
| `qa_results` | **REFACTOR** | Useful artifact link and JSON facts, but one status row conflates facts/status/usability and revision history. |
| `find_artifacts`/`ArtifactService.select_best` | **REFACTOR** | Current selection is newest matching kind/ZipCode within nullable validity; define explicit exact/valid/latest/fallback policies and QA/usability handling. |
| `RegistryAdapter` | **REFACTOR** | Valuable compatibility seam; expand it while old rows remain readable. |

Read-only database evidence:

- Raw rows: `cmp=4368`, `drk=2496`, `flt=1872`, `zro=3744`.
- Artifacts: 312 each for `master_bias`, `master_dark`, `master_flat`, `master_cmp`, and `trace`; 1,248 each for `trace_preview` and `trace_row_dispersion`.
- `dependencies` contains zero rows.
- `cmp` raw paths and schema carry only `frame_type="cmp"`; joined `exposure_details.qobject` is null for all 4,368 rows. The repository cannot distinguish Hg-only, Cd-only, or combined Hg+Cd inputs from current registry metadata.
- Persisted artifact paths such as `./work/master_cmp_013+043+412+LL+S_N_0021_20260604_20260604.fits`, populated validity, `cmp:v1`, and legacy QA metrics were produced by a historical publication path. Current `DefaultPersistencePolicy.filename` instead produces `<workdir>/<kind>/<task>-<version>/<kind>__<component>.fits` and current publication drops validity. Historical rows must not be described as proof of current code behavior.

## 5. Current-to-target architecture map

| Target layer | Existing equivalent | Coverage | Main mismatch |
|---|---|---|---|
| `ontology` | `core.identity.ZipCode`; string kinds; `artifacts.models.Scope` | Partial | No registered entities, physical scope enum, kinds, relations, units, coordinates or assumptions. |
| `configuration` | `PlanningConfig`, task dicts, header values, constants in CCD/trace/wave | Partial | Scientific hardware state is not versioned or resolved by exposure time. |
| `registry` | `registry.database`, `RegistryAdapter` | Substantial seed | Product/lineage/validity/QA model is thin; observation and configuration timelines absent. |
| `artifacts` | models, requests, contracts, service, serializers, publication | Partial | Service does not own writes; one component only; no checksum/revision/supersession. |
| `targets` | generic `Target`, `TemporalWindow`, adapter | Partial | Only calibration ZipCode/window use is operational; no typed CCD/exposure/observation/study targets. |
| `algorithms` | detector/calibration/trace/wave/fiber modules | Calibration-heavy | File/config/plot access violates array-only boundary; most science domains missing. |
| `tasks` | `CalibrationTask` and eight task classes | Calibration-only | Direct serializer loads, split publication logic, missing exposure/CCD/observation tasks. |
| `graphs` | `ReductionGraph`, defaults, scheduler, executor | Narrow | Missing scientific graph and verified dependency/input inconsistencies. |
| `qa` | YAML engine and diagnostics facade | Partial | Metrics/rules/status/usability not separate; blocking is swallowed. |
| `analytics` | six study families | Partial | No Bias stability; legacy names; analytic outputs exist but coverage is narrow. |
| `query` | analytics queries and registry list/find calls | Minimal | No reusable collection, observation-set, lineage or physical-scope query API. |
| `io` | filesystem storage, registry tar/header helpers, `algorithms.io` | Functional but split | I/O is duplicated across registry, storage and algorithms. |
| `cli` | monolithic `cli/virusflow.py` plus formatting | Functional | Commands expose legacy kinds and current service limitations. |
| `tests` | 13 test files | Useful core smoke coverage | No full scientific acceptance, CCD assembly, observation, lineage, validity or aperture-scale regression suite. |

## 6. Specific audit findings

### Observation and exposure identity

- `registry.database.SCHEMA` has `exposures(id, when_utc, frame_type)` and `exposure_details(exposure_id, tar_path, expnum, qobject, qprog, pexptime, date, qra, qdec)`.
- `register_raw_file` derives exposure and observation-number-like metadata from paths/headers, but there are no `Observation`, `Shot`, `DitherSet` or `ObservationSet` entities or relationships.
- `QOBJECT` can support a future versioned `OBJECT == "parallel"` mode classifier, but current registration does not store primary `EXPTIME`, effective exposure time or dither index.
- Therefore Exposure identity is partially represented; observation grouping and exposure-state Products are missing implementations, not wholly missing scientific policies.

### Product selection and implicit fallbacks

- `ArtifactService.select_best(policy="latest_valid")` selects the newest row matching kind/ZipCode whose nullable validity contains `at_time`; `policy="latest"` omits the time predicate.
- `CalibrationTask._resolve_artifact` first selects `latest_valid` at the target midpoint, optionally through `planning.mapping.select_for_edge` with tolerance. If nothing is found, it falls back to unrestricted `latest` for the ZipCode.
- `ReductionGraph.plan` tests idempotency at the window start, not midpoint, and uses the incoming edge's minimum tolerance only when evaluating an already registered Product.
- `trace.get_trace_reference` chooses the absolute nearest dated reference, not necessarily the latest reference at or before the exposure, and has no first-class validity interval.
- `ccd.reduce_raw_amplifier_frame` uses header gain and then an unversioned `0.85` fallback.
- No interpolation or configuration-epoch inheritance policy is implemented.

### Additional graph/task coverage gaps

- `default_calibration_graph` has no Twilight or science node.
- `default_kind_to_task` maps legacy kind `twi` to `TwiTask`, while the Task publishes `master_twi`; `SciTask` is imported by the mapping module but is not returned in the mapping and is not registered in `tasks/__init__.py`.
- Trace and Wave are propagated from planned upstream windows, but the missing `trace -> wave` edge means the scheduler can run Wave after Cmp without waiting for Trace.

### I/O and serializer bypass inventory

- Tasks: `TraceTask.run` at `virusflow/tasks/calibs.py` directly calls `svc.serializers.get("array", "fits").load`; `WaveTask.run` does the same in its local `_load_array`.
- Algorithms: `ccd.reduce_raw_amplifier_frame` calls `algorithms.io.read_fits`; `trace.get_trace_reference` uses `glob.glob` and `np.loadtxt`; `trace.fit_fiber_traces` can call `artifacts.io_fits.read_array_fits`; `wave._plot_identify_arc_summary` creates a directory and saves a figure.
- Artifact/QA internals may use serializers, but `DiagnosticsFacade.evaluate_and_save` currently names the FITS serializer directly rather than requesting a component from ArtifactService.
- Replacement boundary: Tasks and QA request named components from ArtifactService; raw Tasks request arrays/headers from the consolidated I/O layer; only ArtifactService internals select serializers.

### QA fact names

- `trace.fit_fiber_traces` emits `per_fiber_trace_residual_rms`.
- `TraceContract` and `TraceTask` use `per_fiber_trace_residual_rms_ds` for a downsampled summary.
- `AlgoResult` documentation still cites `rms_fibers` as an example.
- Canonical fact: `per_fiber_trace_residual_rms`; downsampling belongs in representation metadata. Wavelength uses the distinct `per_fiber_wavelength_residual_rms`.

### Test-only dependency patterns

The algorithm tests monkeypatch module-level detector reducers (`bias._ccd.reduce_raw_amplifier_frame`, equivalent Dark/Cmp/Sci bindings, and Flat's imported reducer). No confirmed production function delegates to an unrelated scientific module solely for tests. The real migration issue is that the detector reducer accepts file paths and owns multiple scientific stages. Replace it with explicit array-input dependencies rather than adding test-only branches.

## 7. Scientific Product map

Status describes repository implementation, not whether the scientific method is known.

| Canonical Product | Status | Existing implementation/name | Target scope | Current persistence / QA | Migration need |
|---|---|---|---|---|---|
| `oriented_detector_image` | implicit | `ccd.orient_amplifier_image` output | AMPLIFIER/raw frame | Not persisted; no QA | Return as named evidence with orientation/config version. |
| `overscan_model` | implicit | row vector `O` inside `reduce_raw_amplifier_frame` | AMPLIFIER/raw frame | Discarded | Separate model, region definition and residual facts. |
| `overscan_corrected_image` | implicit | intermediate in `reduce_raw_amplifier_frame` | AMPLIFIER/raw frame | Not persisted | Name stage and coordinate convention. |
| `master_bias` | partially implemented | `step_bias.arrays["master"]`; `master_bias` | AMPLIFIER + validity | Primary FITS persisted; default QA has no checks | Preserve complete inputs, validity, units, revisions and scatter component. |
| `read_noise` | partially implemented | scalar median of Bias scatter | AMPLIFIER/PIXEL evidence | Scalar summary only | Preserve scalar plus per-pixel scatter and method/configuration. |
| `gain` | implicit | header gain or `0.85` fallback in `reduce_raw_amplifier_frame` | AMPLIFIER + hardware epoch | Not a Product | Version configured gain and record source/fallback. |
| `master_dark` | partially implemented | `step_dark.master_dark` | AMPLIFIER + validity | Master only; mask dropped; empty default QA | Clarify exposure normalization and preserve mask/evidence. |
| `dark_rate` | missing | No exposure-time-normalized rate output | PIXEL/AMPLIFIER + epoch | None | Implement after units/exposure-time policy is fixed. |
| `pixel_mask` | partially implemented | `dark_pixel_mask`, `flat_response_mask`, mask repair | PIXEL/AMPLIFIER + epoch | Optional contracts but publication drops masks | Define bit vocabulary, provenance and immutable revisions. |
| `detector_variance` | implicit | error array from `reduce_raw_amplifier_frame` | PIXEL/AMPLIFIER/raw frame | Returned internally then discarded | Make variance array and units explicit; propagate exact weights. |
| `master_ldls` | partially implemented | legacy `master_flat` from raw `flt` | AMPLIFIER + validity | Primary FITS; `flat_response_mask` dropped | Canonical alias/migration; retain LDLS meaning and evidence. |
| `master_hg` | missing/conditional | No Hg discriminator in current `cmp` inventory | AMPLIFIER + validity | None | Optional Product only if distinct Hg inputs can be identified. |
| `master_cd` | missing/conditional | No Cd discriminator in current `cmp` inventory | AMPLIFIER + validity | None | Optional Product only if distinct Cd inputs can be identified. |
| `master_arc` | partially implemented | `master_cmp` / `master_comparison_lamp` | AMPLIFIER + validity | Primary FITS, p95 QA in historical rows | Safe canonical aggregate for current data; record lamp composition when known. |
| `master_twilight` | partially implemented | `step_twi.master_twilight` / `master_twi` | AMPLIFIER inputs; exposure reference | Primary FITS; no meaningful default QA | Add exposure-wide grouping, track-state/configuration and normalization lineage. |
| `trace_map` | partially implemented | `fit_fiber_traces.fiber_trace_map` / `trace` | AMPLIFIER + configuration validity | Primary map persisted | Canonical name, coordinates, validity and reference lineage. |
| `trace_samples` | partially implemented | `trace_sample_columns`, `sampled_trace_positions` | FIBER/AMPLIFIER | Computed then dropped | Persist samples and rejection evidence as components. |
| `wavelength_map` | partially implemented | `fit_wavelength_solution.wavelength_map` / `wave` | FIBER/AMPLIFIER + validity | Primary map persisted | Canonical name, units, coordinates and complete arc/trace lineage. |
| `arc_identification` | implicit | matches inside `_identify_arc` | FIBER/AMPLIFIER | Reduced to summary; matches not persisted | Return line/match/rejection tables. |
| `fiber_profile` | missing in current package | Knowledge notes describe empirical profile method; no current Product producer | FIBER/AMPLIFIER | None | Implement as measurement Product after scatter/extraction interfaces. |
| `spectral_psf_2d` | missing | No producer | FIBER/AMPLIFIER | None | Measurement Product; not required to block baseline aperture extraction. |
| `within_amp_fiber_normalization` | missing | No current normalization algorithm | FIBER/AMPLIFIER | None | Baseline smoothed twilight-to-common-model ratio; characterize implementation choices. |
| `amp_to_amp_normalization` | missing | No current exposure-wide implementation | AMPLIFIER/EXPOSURE | None | Infer exposure-wide scale under uniform center-track twilight assumption; verify statistic. |
| `fiber_normalization` | missing | No multiplied final Product | FIBER/EXPOSURE | None | Preserve both factors and final Product with scatter/extraction lineage. |
| `reduced_science_image` | partially implemented/diagnostic only | `build_master_science.master_science` / `master_sci` | Current: AMPLIFIER/window; target: AMPLIFIER/EXPOSURE | Diagnostic stack persisted | Do not equate stacked diagnostic with exposure detector reduction. |
| `ccd_scattered_light_model` | missing | No physical-CCD scatter implementation | PHYSICAL_CCD/EXPOSURE | None | Implement joint LL+LU or RU+RL model after indexed seam characterization. |
| `scatter_subtracted_image` | missing | No producer | PHYSICAL_CCD/EXPOSURE | None | Derived Product retaining source image and scatter-model lineage. |
| `aperture_extracted_spectrum` | partially implemented | `fiber.get_spectra`, currently weighted sum | FIBER/EXPOSURE | Used inside wavelength fitting; no Product | Add explicit units, masks, effective width and stable sum-scale regression. |
| `extracted_variance` | missing | No exact aperture-weight propagation | FIBER/EXPOSURE | None | Propagate detector variance with exact fractional weights. |
| `initial_astrometry` | missing | No current code | EXPOSURE | None | Implement versioned header TAN prior. |
| `catalog_match_table` | missing | No current code | EXPOSURE | None | Preserve detections, matches, rejected matches and catalog version. |
| `final_astrometry` | missing | No current code | EXPOSURE | None | Baseline shift/rotation fit with QA facts. |
| `fiber_sky_coordinates` | missing | No current code | FIBER/EXPOSURE | None | Derive from final astrometry and versioned fiber map. |
| `sky_fiber_mask` | missing | No current code | FIBER/EXPOSURE | None | Explicit selection Product, not a hidden array mask. |
| `incident_sky_spectrum` | missing | No current code | EXPOSURE | None | Native-grid oversampled common-sky baseline. |
| `exposure_illumination_correction` | missing | No current code | EXPOSURE | None | Separate exposure illumination from baseline response. |
| `fiber_sky_prediction` | missing | No current code | FIBER/EXPOSURE | None | Evaluate incident sky on each fiber sampling/response. |
| `sky_subtracted_spectrum` | missing | No current code | FIBER/EXPOSURE | None | Preserve sky model and variance lineage. |
| `baseline_relative_response` | missing | No current code | INSTRUMENT_EPOCH/configuration | None | Load versioned baseline curve with units/reference convention. |
| `final_exposure_response` | missing | No current code | EXPOSURE | None | Combine baseline, temporal/track/transparency factors explicitly. |
| `effective_exposure_time` | missing | Registry stores `PEXPTIME` only | EXPOSURE | None | Versioned policy: `EXPTIME` primary; provisional `PEXPTIME - 8 s` for parallel mode. |
| `dither_assignment` | missing | No dither entity/index | EXPOSURE/DITHER_SET | None | Versioned nominal pattern plus evidence and override provenance. |
| `dither_registration` | missing | No current code | DITHER_SET/OBSERVATION | None | Astrometric refinement relative to versioned nominal offsets. |
| `dither_coverage_map` | missing | No current code | DITHER_SET/OBSERVATION | None | Derive after exposure coordinates and registration exist. |

`master_hgcd` is not selected as canonical now. Current repository/data-model evidence cannot tell whether each `cmp` exposure is combined Hg+Cd or merely lacks lamp labels. `master_arc` is the safe physical aggregate. `master_hg`, `master_cd`, or `master_hgcd` become valid optional kinds only when lamp composition is explicit.

## 8. Contract-gap analysis

| Contract area | Severity | Verified gap | Required contract change |
|---|---|---|---|
| Product identity/scope | **BLOCKING** | String kinds and ZipCode-only `Scope` cannot represent PIXEL/CCD/EXPOSURE/OBSERVATION Products. | Canonical kind registry, physical scope enum and typed identity. |
| Units/coordinates/assumptions | **BLOCKING** | Artifact contracts do not require them. | Registered units, coordinate frames and named assumptions in every applicable Product. |
| AlgoResult computation boundary | **IMPORTANT** | Calibration algorithms accept paths and call `reduce_raw_amplifier_frame -> read_fits`; trace loads files; wave plots. | Tasks/I/O load arrays and configuration; algorithms return all scientific evidence. |
| AlgoResult shape | **IMPORTANT** | Container has arrays/scalars/meta/messages/timings/version, but canonical names and structural validation are weak. | Preserve container; strengthen registered component/fact contracts. |
| Task dependencies | **BLOCKING** | Missing `trace -> wave`; `master_cmp` node/task input mismatch. | One authoritative graph with raw and Product inputs validated against Task declarations. |
| Task loading boundary | **IMPORTANT** | `TraceTask.run` and `WaveTask.run` call `svc.serializers.get(...).load`. | Use ArtifactService component loading exclusively. |
| Multi-component artifacts | **BLOCKING** | `_select_primary_component` writes one required component; masks/scatter/samples are discarded. | Persist and reload all declared components atomically/logically. |
| Validity and path identity | **BLOCKING** | Publication omits target window; filename omits ZipCode/window and can overwrite another target. | First-class validity and collision-safe immutable revision paths. |
| Provenance/relations | **BLOCKING** | `ArtifactRequest.parents` is ignored in favor of context parents; dependencies table unused. | One parent/relation interface and normalized typed relationships. |
| Artifact loading | **IMPORTANT** | `load_payload` swallows errors; `get(include_payload=True)` discards `_payload`; description omits scope/provenance/diagnostics. | Explicit component result/errors and complete Artifact record. |
| QA separation | **BLOCKING** | One YAML decision/status path; no usability model; Task catches intended hard failure. | Separate QAFact, rule, result/status and context-specific usability; keep thresholds configurable. |
| Analytics | **IMPORTANT** | Storage-neutral queries exist, but no Bias stability and legacy kinds dominate. | Add canonical studies after source contracts; analytics remains read-only to source Products. |
| Immutable revision/supersession/checksum | **IMPORTANT** | No implementation. | Add without rewriting old rows; retain historical Products. |
| Full covariance/cube reconstruction | **DEFERRED** | Missing by design from baseline slice. | Preserve interfaces and research separately after aperture/variance path. |

## 9. Required verified-defect register

| Required defect | Exact current evidence | Migration consequence |
|---|---|---|
| Missing `trace -> wave` dependency | `planning.defaults.default_calibration_graph` declares `wave.inputs_artifacts=["master_cmp", "trace"]`, but edges contain only `flat -> trace` and `cmp -> wave`. | Add and validate the trace edge before relying on scheduler dependency enforcement. |
| Inconsistent `master_cmp` planning/task inputs | The `master_cmp` `TaskSpec` is artifact-driven from `master_flat`; `CmpTask.frame_type="cmp"` and `CalibrationTask.query_inputs` select raw comparison frames. | Make the node raw-`cmp` driven unless a reviewed upstream Product design replaces it. |
| Direct serializer loading from Tasks | `TraceTask.run` and `WaveTask.run` call `svc.serializers.get("array", "fits").load`. | Add named-component ArtifactService loading and migrate both callers. |
| Publication persists only one component | `DefaultPublicationService._select_primary_component` selects the first required component and `_publish_one` writes only it. | Implement complete logical multi-component publication. |
| Lost `per_pixel_bias_scatter` | `step_bias` returns it; `BiasTask` constructs only `LogicalComponent("master")`. | Publish scatter with Master Bias and expose it to QA/analytics. |
| Dropped validity interval | `BiasTask` does not place target/window in `PublicationContext.parameters`; `RegistryAdapter.register` only extracts validity from provenance params. | Make validity first-class from Target through ArtifactService/registry. |
| Filename collision risk | `DefaultPersistencePolicy.filename` produces `<base>/<kind>/<task>-<version>/<kind>__<component>.fits`, omitting ZipCode/window/revision. | Use immutable collision-safe identity/revision paths. |
| Swallowed QA blocking failures | Every calibration Task catches the `RuntimeError` raised after `DiagnosticsFacade.should_block`; outer blocks also catch all QA exceptions. | Separate QA-evaluation errors from policy failures and allow configured blocking to propagate. |
| Parent provenance split | Tasks populate `ArtifactRequest.parents` and `PublicationContext.parent_ids`; publication constructs provenance only from the latter. | Define one authoritative typed relation interface. |
| Normalized dependencies unpopulated | `dependencies` exists, database count is zero, and `save_artifact` only writes comma-separated `provenance.parents`. | Populate typed normalized relations while retaining legacy-parent reads. |
| Historical database publication differs from current source | Historical rows include identity/date filenames and validity with `bias:v1`/`cmp:v1`; current policy uses generic nested filenames and drops validity. Referenced sample files are absent. | Treat database rows as historical evidence; do not infer current behavior or rewrite them in place. |

## 10. Verified defects in the current Master Bias path

Current path:

`cli.virusflow.cmd_run` → `_run_planned` → `planning.defaults.default_calibration_graph` → `ReductionGraph.plan` → `schedule` → `PlanningExecutor.run` → `BiasTask.run` → `CalibrationTask.query_inputs` → `registry.database.list_raw_files_scoped` → `bias.step_bias` → `ccd.reduce_raw_amplifier_frame` → `algorithms.io.read_fits` → `DefaultPublicationService.publish` → `write_array_fits` → `ArtifactService.register` → `RegistryAdapter.register` → `database.save_artifact` → `DiagnosticsFacade.evaluate_and_save` → `QAEngine.evaluate` → `save_qa_results`.

Specific defects:

1. `step_bias` accepts path/tar-member dictionaries, so its claim of no file I/O is false at the transitive boundary.
2. `reduce_raw_amplifier_frame` mixes overscan, orientation, gain and error construction; `step_bias` discards the error array.
3. `step_bias` returns `per_pixel_bias_scatter`, but `BiasTask` requests only `master`; publication cannot retain the scatter.
4. `BiasTask` puts `n_inputs` in summaries while publication reads `req.metadata["n_inputs"]`; current FITS output receives `NINPUTS=0`.
5. `ArtifactRequest.parents` and `PublicationContext.parent_ids` duplicate the same relationship; publication uses only the latter.
6. Publication context omits the target/window. `RegistryAdapter` looks for validity inside provenance parameters, so the current validity interval is dropped.
7. `DefaultPersistencePolicy.filename` omits ZipCode and dates, so concurrent/sequential targets share one path.
8. `BiasTask` raises a hard-QA `RuntimeError` inside an immediately catching `except Exception`; the outer QA block also catches everything. Hard blocking cannot propagate.
9. `database.save_artifact` stores comma-separated parents but does not populate `dependencies`.
10. `docs/qa_default.yml` has no Master Bias metrics/checks. Custom `test_master_bias_like_rule_passes_for_readnoise_near_three` does not test the checked-in default.
11. No Bias-stability analytic study exists.

## 11. Proposed repository tree (no moves in this audit)

```text
virusflow/
  ontology/        # new: entities, scopes, kinds, relations, units, coordinates, assumptions
  config/          # new: versioned hardware and scientific policy data
  registry/        # keep SQLite; split inventory, products, configuration, lineage, validity, selection
  artifacts/       # adapt current models/service/requests/serializers/materializers/storage
  targets/         # adapt planning targets into typed hardware/calibration/exposure/CCD/observation targets
  algorithms/      # adapt current behavior into detector/calibration/geometry/extraction; add science domains
  tasks/           # adapt current tasks; add detector, exposure, physical-CCD, observation and study tasks
  graphs/          # adapt planning graph/scheduler/validation; keep executor mechanics
  qa/              # split facts, rules, policy, status, usability and versioned configs
  analytics/       # retain studies/reports/plots; add Bias stability and later model learning
  query/           # new reusable selectors, lineage, collections and observation sets
  io/              # consolidate filesystem, tar index, raw headers and catalogs
  cli/             # adapt existing command behavior into narrower entry modules
tests/
  unit/ contracts/ integration/ regression/ science_acceptance/
```

Modules should not move before contract tests exist. In particular, keep `ArtifactService`, serializers, registry compatibility, planner/scheduler, and algorithms in place while new contracts are introduced alongside them.

| Proposed package | Current modules to retain/adapt later | New requirement | Do not move during contract introduction |
|---|---|---|---|
| `ontology/` | `core.identity`; string kinds/scopes as compatibility inputs | Entities, physical scopes, kind/relation/unit/coordinate/assumption registries | Keep `ZipCode` import path stable. |
| `config/` | Planning YAML ideas; CCD/trace/wave constants as evidence | Versioned hardware, orientation, gain, trace reference, line, dither and shutter data | Do not extract constants until characterization tests exist. |
| `registry/` | Current SQLite database and adapter | Split inventory/product/configuration/lineage/validity/selection interfaces | Do not replace SQLite or rewrite historical rows. |
| `artifacts/` | Models, requests, service, serializers, materializer, FITS writer | Complete record, components, immutable revision/checksum/storage policy | Do not move serializers or change current writer before round-trip tests. |
| `targets/` | Planning Target/window/cadence/adapter | Typed calibration, hardware, CCD, Exposure, Observation and study Targets | Preserve existing planning imports through adapters. |
| `algorithms/` | All current scientific functions as characterized baselines | Science-domain algorithms for scatter, variance, astrometry, sky, response and reconstruction | Do not reorganize modules while behavior remains uncharacterized. |
| `tasks/` | Base/calibration Tasks and mapping | Detector, calibration, physical-CCD, exposure, observation and analytics Tasks | Keep public Task names and legacy mapping during migration. |
| `graphs/` | Planning graph/defaults/mapping/scheduler/validation and executor | Canonical calibration/exposure/observation graph definitions | Correct graph through compatibility definitions before moving modules. |
| `qa/` | QAEngine and YAML rules | Facts, rules, policy, status, usability and configs | Keep existing engine until equivalent policy tests pass. |
| `analytics/` | Queries, service, studies, reports and outputs | Bias stability and later learned-model studies | Preserve post-run source immutability and output registration. |
| `query/` | `analytics.queries`, registry find/list APIs | Generic selectors, lineage, collections and observation sets | Keep analytics query imports stable initially. |
| `io/` | Filesystem storage, tar/header helpers, algorithms I/O behavior | Raw source/header/catalog interfaces and tar index service | Do not move raw readers until Tasks can supply array-only algorithm inputs. |
| `cli/` | Current commands and formatting | Narrow scan/plan/run/query/study modules | Keep command surface stable until canonical services are usable. |
| `tests/` | All current tests | Unit/contract/integration/regression/science-acceptance layers | Do not reorganize tests before additions can import stable interfaces. |

## 12. Recommended slices

### First contract-validation slice after prerequisite contracts

The recommended narrow slice remains:

```text
raw bias frames
→ loaded amplifier arrays and metadata
→ Master Bias + per-pixel scatter + read-noise facts
→ separated QA facts/rules/status/usability
→ ArtifactService multi-component persistence
→ normalized lineage and validity query
→ Bias-stability analytics
```

Existing support: `list_raw_files_scoped`, `reduce_raw_amplifier_frame`, `step_bias`, `AlgoResult`, `BiasTask`, `ArtifactRequest`, FITS serializer, ArtifactService, registry selection, QA engine, and analytics query/output patterns.

Prerequisites: canonical kinds/scopes/units, target validity, one parent interface, component persistence, collision-safe immutable paths, and characterized Bias output. This is why the first post-review implementation task is contract/ontology work, not running the slice immediately.

### First complete scientific slice

```text
one real Observation
→ per-exposure detector reduction
→ joint physical-CCD scatter correction
→ sum-aperture spectrum + exact variance
→ initial/catalog/final astrometry
→ exposure sky model and subtraction
→ baseline/final response
→ effective exposure time
→ dither assignment, registration and coverage
```

Current reusable components cover raw discovery/identity, detector reduction, masks, trace, wavelength, aperture extraction seed, ArtifactService/registry seeds, QA engine, planning and analytics. Physical-CCD scatter, variance Product, astrometry, sky, response, exposure/observation identity and dither processing require new implementation after contracts.

## 13. Tests and evidence coverage

Current tests are unit and smoke/integration-style; there is no explicit regression fixture or full scientific acceptance test.

Audit execution result: 22 focused tests passed across `test_arch_core_objects.py`, `test_algorithms_basic.py`, `test_publication_infra.py`, `test_service.py`, `test_planning_smoke.py`, `test_planning_mapping_idempotency.py`, `test_qa_engine.py`, `test_analytics_storage_agnostic.py`, and `test_serializers.py`. Pytest bytecode and cache generation were disabled. The only warning was SciPy's expected small-fixture median-filter zero-padding warning in the Flat test. A non-writing characterization import also verified that a constant unit image with `npix=5` produces `5.0` per sample from the current sum-based `get_spectra`.

- Algorithms: `test_step_bias_returns_algo_result`, Dark/Flat/Cmp equivalents, and science-stack tests.
- Contracts/publication: `test_arch_core_objects.py`, `test_publication_roundtrip_master_bias`, provenance test.
- Service/serialization: `test_service_register_describe_and_select_best`, `test_array_fits_describe_and_load`, analytics storage-neutrality test.
- Planning: cadence tests, planner idempotency/mapping tests, scheduler/executor smoke test.
- QA: rule/reducer/status priority, custom Bias-like rule, and Cmp all-zero rule.
- Boundary guard: `test_algorithms_do_not_reference_write_array_fits_or_output_path`; it does not detect transitive file reads, registry reads, or plotting.

Minimum characterization additions before behavior refactoring:

1. Detector orientation/overscan/gain/error arrays for every AMP and relevant legacy `AMPNAME`.
2. Bias master, scatter, read-noise, units and invalid/partial input behavior.
3. Current trace reference selection, hardware exception, samples and residual facts.
4. Arc-lamp composition metadata and wavelength matches/rejections.
5. `get_spectra` fractional weights, exact sum scale, detector edges and future masked-pixel policy.
6. Validity selection, fallbacks, immutable revisions, relations and hard-QA behavior.
7. Zero-indexed physical-CCD seam assembly.

## 14. Missing knowledge, implementation gaps and policies

### Cannot be inferred from current evidence

- The physical size of any non-imaging inter-amplifier separation; it is represented explicitly as configuration/metadata and never as an invented detector row.
- Lamp composition of current `cmp` rows: the registry has no Hg/Cd/combined discriminator, so `master_hg`, `master_cd`, and `master_hgcd` cannot be selected from current data-model evidence.
- Exact historical meaning and validity of legacy `AMPNAME=LR/UL` behavior.
- Authoritative historical configuration datasets/timelines where the notes specify entities but provide no source data.
- Reproducibility of historical persisted artifacts whose referenced files are absent and whose publication path differs from current source.

### Can be inferred but should be verified

- Amp-to-amp normalization baseline: exposure-wide center-track twilight scaling under the uniform-field assumption. The exact robust statistic should be characterized; it is not an architecture blocker or a required Greg decision before contracts.
- Current trace and wavelength algorithms as baseline estimators, including nearest-date trace reference and hard-coded exceptions.
- Sum-aperture extraction as the current baseline, including its five-pixel fractional boundary convention.
- Bias read-noise estimator and likely useful validity cadence through Bias-stability analytics.
- Master Arc as the safe aggregate canonical Product for unlabeled `cmp` inputs.

### Provisional, versioned baseline policy

- Standard nominal dither pattern followed by astrometric refinement.
- `EXPTIME` as primary exposure time and `PEXPTIME - 8 seconds` for classified parallel mode.
- Header TAN astrometry prior with catalog shift/rotation refinement.
- Configurable QA thresholds; the architecture requires fact/rule/status/usability separation now, not final numeric thresholds.

### Non-blocking research

- Twilight field-gradient separation and optimal amp-to-amp statistic.
- Profile/forward extraction, full covariance and flux-conserving cubes.
- PCA sky residuals and advanced response self-calibration.
- Long-term validity of gains, traces, aperture capture and calibration cadence.

Missing repository implementations are not missing scientific knowledge: physical-CCD scatter, aperture variance, baseline astrometry, sky, response and dither methods already have safe baseline policies in the knowledge documents. Their absence is implementation scope, not a request for policy decisions.

## 15. Review gate

No ontology code, schema migration, ArtifactService change, graph correction, characterization-test addition, or Bias slice may begin until this document, `VIRUSFLOW_LEGACY_VOCABULARY_MAP.md`, and `VIRUSFLOW_MIGRATION_PLAN.md` are complete and reviewed.


## Resolved indexed physical-CCD mapping

The indexed-array seam mapping is now an approved design decision rather than an unresolved item.

For the reflected upper amplifier:

```python
upper_y = 2063 - y
```

This is the correct zero-indexed Python-array transform. The former `2064 - y` expression came from applying a one-indexed coordinate convention to a zero-indexed array.

The implementation must characterize and test this mapping before physical-CCD Products are introduced, but no further scientific-policy decision is required.

Update all blocker, missing-knowledge, Product-map, and test-coverage sections accordingly:

* remove `2063-y` versus `2064-y` from “cannot be inferred”;
* remove the indexed seam convention from the implementation blockers;
* retain seam round-trip, boundary, ordering, and continuity tests as required characterization;
* state `2063-y` as the canonical configuration value and record its configuration version in provenance.

## Approved implementation scope

The repository audit, vocabulary map, Product map, comparison-lamp decision, migration order, compatibility strategy, and provisional policies have been reviewed and approved.

The initial authorized implementation scope was **Migration Plan Steps 1 through 7, inclusive**; those steps are implemented and accepted.

The succeeding authorized scope is **Migration Plan Steps 8 through 10, inclusive**, as one autonomous tranche without approval pauses between steps. Step 11 remains outside the approved scope.

The target implementation depth is the complete canonical one-amplifier calibration graph, not only the Bias proof of concept.

## Real-data acceptance

Observing date **20260609** is the designated primary real-data acceptance date. It is expected to contain the complete set of files required to exercise one amplifier through the canonical calibration graph.

The implementation must inventory and verify the inputs before running the acceptance path. Missing data must be reported precisely and must not be replaced with fabricated fixtures in a test described as real-data acceptance.

The second available date should be used where useful to test:

* validity-window selection;
* nearest or applicable calibration behavior;
* immutable revisions;
* cross-date lineage;
* alias and historical-read compatibility.

Completeness of the second date must be verified rather than assumed.

## Revised implementation gate

Ontology work, additive schema changes, ArtifactService changes, graph corrections, characterization tests, the Bias slice, and the one-amplifier calibration graph were completed and accepted in Steps 1–7.

Physical-CCD scatter implementation, full exposure processing, and observation/dither processing are now authorized through Step 10. The implementation agent must stop before Step 11 and legacy-path retirement.

## Steps 1–7 implementation record

Verified — source and test on 2026-07-22:

- `virusflow/ontology/` now registers canonical kinds, scopes, relations, units, coordinates, assumptions, and read aliases. `UPPER_AMPLIFIER_REFLECTION_INDEX` is exactly `2063`; no `2064-y` implementation alternative exists.
- `ArtifactService.persist_request` and `load_component` own immutable, collision-safe, named multi-component persistence and loading. Additive tables retain revisions, checksums, units, coordinates, configuration references, normalized relations, QA facts/status/usability, and the first-class validity policy. Historical `artifacts`, `provenance`, and `dependencies` remain readable.
- `RawFrameLoader` is the Task-side FITS/tar boundary. `reduce_amplifier_array`, Bias, Dark, LDLS, Arc, Twilight, Trace, and Wavelength canonical algorithms receive arrays only. `TraceTask` and `WaveTask` contain no direct serializer calls.
- `BiasTask` preserves `master` and `per_pixel_bias_scatter`; hard QA failure propagates after the failed Product is recorded as unusable. `BiasStabilityStudy` loads both named components through ArtifactService and writes normalized source lineage.
- `default_calibration_graph` contains `master_bias`, `master_dark`, `master_ldls`, `master_arc`, `master_twilight`, `trace_map`, and `wavelength_map`. `master_arc.inputs_raw == ["cmp"]`; the graph contains `master_ldls -> trace_map`, `master_arc -> wavelength_map`, and the formerly missing `trace_map -> wavelength_map` edge.
- Trace publication retains the map, sample columns, sampled positions, and per-fiber residual RMS. Wavelength publication retains the map, per-fiber residual RMS, and arc-identification table.
- Legacy kind aliases, Task class names, explicit `v1` Task selectors, Task output aliases, `reduce_raw_amplifier_frame`, and the array-only legacy call shape of `fit_fiber_traces(raw_inputs, params)` remain available. Historical rows and files were not rewritten.

The pre-implementation baseline was `30 passed`. The completed suite is `55 passed`; focused contract, persistence, characterization, QA, planner, and one-amplifier integration gates also passed.

### 20260609 acceptance evidence

The isolated registry `/tmp/virusflow-acceptance-20260609.n7Kukw/registry.sqlite3` was created for acceptance; the workspace `virusflow.sqlite3` was not modified. Scanning 25 tar files registered 14,100 FITS members across 300 ZipCodes:

| Frame type | Files |
|---|---:|
| `zro` | 4,200 |
| `drk` | 900 |
| `flt` | 900 |
| `cmp` | 2,400 |
| `twi` | 1,500 |
| `sci` | 4,200 |

The accepted amplifier was `060+003+206+LL+S/N 0039`, selected only after verifying a matching `Fiber_Locations/20210531/fiber_loc_206_060_003_LL.txt` reference and raw counts of 14 zro, 3 dark, 3 LDLS, 8 comparison, and 5 twilight frames. Two darks have civil date 20260610, so the observing-night execution window was `20260609..20260610`.

The real graph completed all seven Products. Latest acceptance revisions were artifact IDs 16–22; each had a checksum, explicit validity policy, configuration references, separated QA status/usability, and one or more QA facts. Every status was `pass` and usability was `usable`. `trace_map` was `(112, 1032)` with median residual RMS `0.0441934 pixel`. `wavelength_map` was `(112, 1032)`, with nine matched lines and best RMS `0.0484820 Angstrom`. Normalized lineage was exactly `master_ldls -> trace_map` and `master_arc + trace_map -> wavelength_map`.

Compatibility-path recomputation agreed after FITS serialization rounding:

| Product | Maximum absolute difference | `np.allclose` |
|---|---:|---|
| Master Bias | `2.17e-7` | yes |
| Master Dark | `1.66e-5` | yes |
| Master LDLS | `1.22e-4` | yes |
| Master Arc | `6.10e-5` | yes |
| Master Twilight | `9.77e-4` | yes |
| Trace map | `3.05e-5 pixel` | yes |
| Wavelength map, with Task mask policy | `2.44e-4 Angstrom` | yes |

An unmasked Wavelength recomputation differed by up to `0.1634 Angstrom`. This is an intentional reviewed difference: canonical `WaveTask` can now load and apply the persisted LDLS/dark masks that the former single-component publication path dropped. Recomputing with that declared Task policy is numerically equivalent.

The secondary 20260604 inventory had 12 tar files and no twilight data: 3,744 zro, 2,496 dark, 1,872 flat, and 4,368 cmp files. It was therefore not represented as a complete graph. For the accepted amplifier it supplied 12 zro, 8 dark, 6 flat, and 14 cmp frames. A separate real 20260604 Bias revision was produced, and `latest_valid` selected the 20260604 revision at that date and the 20260609 revision at the primary date.

### Remaining evidence limits

No Step 1–7 exit criterion remains unsatisfied. These evidence gaps remain explicit and do not invalidate the amplifier graph:

- unlabeled `cmp` rows do not establish Hg, Cd, or combined Hg+Cd lamp composition;
- authoritative gain/read-noise/controller history and configuration epochs remain unavailable, so fallback configuration references are recorded with `evidence_state="unknown"`;
- historical `AMPNAME=LR/UL` semantics remain characterized behavior rather than proven hardware history;
- final numerical QA thresholds remain versioned policy.

Steps 8–10 are authorized as one autonomous tranche; Step 11 remains prohibited. The canonical zero-indexed upper-amplifier transform is exactly `upper_y = 2063 - y`; `2064 - y` is not an alternative convention. Pre-refactor numerical behavior is characterization evidence, not scientific truth. Intentional differences are acceptable when justified by retained comparisons, quantified differences, QA, analytics, algorithm/configuration versions, and documented scientific reasoning. No legacy reader, Task, alias, database row, or file may be retired in Steps 8–10.

## Step 8 implementation record

Verified — source, focused tests, full suite, and real-data execution on 2026-07-22:

- `PhysicalCCDTarget` enforces LL+LU for the left CCD and RU+RL for the right CCD.
- `assemble_physical_ccd` maps every reflected upper row with exactly `upper_y = 2063 - y`; endpoint, inverse, unique-row, source-coordinate, seam, and zero-imaging-gap evidence are retained.
- `fit_gap_scattered_light` implements the versioned robust quadratic gap-constrained baseline with explicit core exclusion, fit mask, deterministic holdout mask, coefficients, residual image, and boundary/cross-amplifier QA.
- `PhysicalCCDTask` loads source and trace components only through ArtifactService and publishes immutable, separate `ccd_scattered_light_model` and `scatter_subtracted_image` Products without modifying the amplifier Products.
- The isolated real run inventoried 14,100 raw members and used exposure `20260609T031649.6`, SPECID `206`. Left/right holdout robust residuals were `2.99245/2.94504 electron`; seam model discontinuities were `2.28016e-4/1.74002e-4 electron`; model/source p95 ratios were `0.08230/0.07745`. Both were pass/usable and every named component checksum-loaded through ArtifactService.
- The complete suite is `61 passed` after the Step 8 additions. No Step 9, Step 10, or Step 11 completion claim is made here.

## Step 9 implementation record

Verified on 2026-07-22 using isolated real exposure `20260609T031649.6`: all 300 raw amplifiers produced detector reductions, all 150 physical CCDs produced scatter Products, and 299 amplifiers across all 75 IFUSLOTs produced exact-weight aperture extraction and the approved exposure Products. The sole unavailable wavelength path is `095+004+426+RU+S/N 0048`, for which all eight real comparison frames are identically zero. Live Pan-STARRS DR2 refinement retained 3,682 catalog rows, 53 candidates, five accepted matches, and 0.724692 arcsec residual RMS. The exposure remains degraded rather than falsely complete; detailed QA, scientific differences, and component evidence are in `VIRUSFLOW_STEPS_8_10_ACCEPTANCE.md`. Step 10 remained uncommitted at this milestone and Step 11 remained unstarted.

## Step 10 implementation record

Verified on 2026-07-22 using the three real OBSID 6 exposures. Each independently produced 300 detector reductions, 150 physical CCDs, 299 extracted-amplifier Products, and its own astrometry, sky, response, time, QA, and lineage. First-class Observation/DitherSet Products retain membership, provisional nominal offsets, measured offsets, rejected/fallback facts, registration residuals, and a coverage map. The 2.85951 arcsec real registration-versus-nominal RMS is intentionally reported as degraded rather than hidden. No exposure state was collapsed, no member was fabricated, and no Step 11 retirement was begun.
