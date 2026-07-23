# VIRUSFlow Migration Plan

Status: Steps 1–7 are complete and accepted. Steps 8–10 are implemented and passed their internal scientific and verification gates on 2026-07-22. Step 11 remains unauthorized and unstarted.

## 1. Migration strategy

Use additive, contract-first migration:

- Preserve scientifically useful current behavior behind characterization tests.
- Add canonical contracts and compatibility adapters before changing writers.
- Keep SQLite unless measured evidence demonstrates a need to change it.
- Read historical records without rewriting them in place.
- Write immutable new revisions alongside old Products.
- Roll back by selecting the prior writer/reader path, not by deleting evidence.
- Separate missing science knowledge, missing code, behavior needing characterization, and provisional configurable policy.

## 2. Ordered plan

| Step | Existing components | New components | Tests | Exit criterion | Safe rollback |
|---|---|---|---|---|---|
| 1. Freeze vocabulary, scopes, units and coordinates | `ZipCode`, string Artifact kinds, `Scope`, knowledge documents, legacy map | Registered entities, `PhysicalScope`, Artifact-kind specs, relations, units, coordinates, assumptions and legacy aliases under `ontology/` | Registry-schema validation; uniqueness; every baseline Product has kind/scope/units/coordinates; legacy aliases resolve without writes | Reviewed registries cover the complete Product map and no Task invents an unregistered new kind | Keep registries unused behind a feature flag; existing strings remain unchanged/readable |
| 2. Stabilize core contracts | `Target`, `AlgoResult`, `ArtifactRequest`, `Artifact`, `Provenance`, QA `Decision` | Typed Targets, first-class `Validity`, component descriptors, Artifact revision/checksum/relations, QAFact/QARule/QAStatus/Usability, one parent interface | Contract unit tests; serialization round trips; invalid scope/unit/component cases; legacy-object adapters | Synthetic algorithm→Task→Artifact request can express identity, components, validity, lineage and QA facts without storage decisions | Continue using current dataclasses through adapters; no persisted schema change yet |
| 3. Reconcile ArtifactService and serializer access | ArtifactService, materializer, publication, serializers, FITS writer, RegistryAdapter | Service-owned multi-component persist/load, explicit errors, immutable URI policy, checksum, complete describe/get, normalized relations adapter | Multi-component round trip; failure visibility; collision tests across ZipCodes/windows; validity/lineage query; backend substitution | Tasks can publish/load all components only through ArtifactService; parent and validity have one source | Retain current publication path as selectable compatibility writer; new rows remain separate revisions |
| 4. Load versioned hardware/configuration knowledge | Header parsing, ZipCode table, CCD constants, trace files, line list, planning YAML | Configuration registry/data for orientation, CCD transforms, controller history, gain, dead fibers, arc lines, dither and shutter policies | Resolution by identity/time; configuration hash/provenance; missing/ambiguous configuration behavior | A Target resolves every required configuration version explicitly and records it in provenance | Fall back to named legacy configuration profiles; never silently substitute constants |
| 5. Characterize current scientific behavior | CCD, Bias/Dark/Flat/Cmp/Twi/Sci, trace, wave, fiber and masks | Regression fixtures and scientific fact snapshots; no algorithm rewrite | All AMP orientations; overscan; gain/error; masks; Bias scatter; trace reference/samples; arc matching; sum-aperture weights/edges | Behavior required for migration has an accepted baseline or a documented intentional-change decision | Tests target current functions and can remain while adapters are abandoned |
| 6. Migrate the narrow Bias contract slice | Raw inventory, detector reducer, `step_bias`, `BiasTask`, current QA and analytics patterns | Array-input detector/Bias boundary; Master Bias + scatter; Bias QA facts/status/usability; Bias stability study | Synthetic and one-real-ZipCode parity; multi-component persistence; hard-QA propagation; selection/lineage/validity; stability query | Bias slice is queryable, reproducible and collision-free with complete components and no Task/algorithm I/O bypass | Select historical `master_bias` writer/reader; new immutable rows do not overwrite old files |
| 7. Expand to one amplifier | Current Dark/LDLS/Arc/Twilight/Trace/Wave functions and Tasks | Canonical amplifier graph and Products; masks/trace samples/arc IDs retained | One ZipCode end-to-end; dependency validation; units/masks/QA; old/new numerical parity | One amplifier produces complete calibration Products through canonical contracts | Run legacy task graph for the same inputs and compare; retain both revisions |
| 8. Expand to one physical CCD | Known LL+LU and RU+RL pairing; orientation behavior | `PhysicalCCDTarget`, indexed transform, joint gap-scatter model, scatter-subtracted Product | Seam convention, round-trip coordinate transform, gap-mask fit, cross-amp continuity, both CCD sides | One paired CCD is assembled and corrected jointly with explicit transform/configuration lineage | Bypass scatter correction using a versioned `none` model and retain amplifier Products |
| 9. Expand to one full exposure | Raw exposure IDs/details, amplifier graph, aperture extractor seed | Exposure entity/Target, extracted variance, normalization, astrometry, sky, response and effective-time Products | All amplifiers; missing AMP behavior; exact aperture variance; catalog fit; sky residuals; response/effective-time policy | Every available IFUSLOT in one Exposure produces queryable calibrated spectra and exposure Products | Keep per-amplifier/CCD Products; disable downstream exposure assembly without deleting them |
| 10. Expand to one three-dither observation | Exposure identity and future exposure Products | Observation/DitherSet relations, versioned nominal assignments, astrometric registration, coverage map | Three-exposure grouping; incomplete/extra exposure cases; nominal versus refined offsets; atomic exposure preservation | One real observation preserves each Exposure while providing dither relationships and coverage | Query exposures independently; remove only the grouping revision if classification is wrong |
| 11. Retire superseded paths after parity | Legacy kinds, direct loaders, compatibility writer, old graph | Deprecation/removal records and stable compatibility reader | Usage audit; persisted-row replay; parity/acceptance suite; rollback drill | No supported producer/consumer depends on a superseded path and historical Products remain readable | Restore prior adapter/writer release; never delete historical Product rows/files as part of rollback |

## 3. Step details and decisions

### Step 1 — Canonical vocabulary

Create compact registries for:

- entities and identity types;
- `PIXEL`, `FIBER`, `AMPLIFIER`, `PHYSICAL_CCD`, `SPECTROGRAPH`, `IFU`, `EXPOSURE`, `DITHER_SET`, `OBSERVATION`, `OBSERVATION_SET`, and `INSTRUMENT_EPOCH` scopes;
- every Product in the reconciliation Product map;
- relations including `derived_from`, `calibrated_by`, `supersedes`, `valid_for`, `uses_configuration`, `member_of`, `measures`, `predicts`, and `refines`;
- units, coordinate conventions and assumptions;
- the reviewed legacy aliases.

`master_arc` is the initial canonical comparison-lamp aggregate. Separate `master_hg`/`master_cd` are optional when lamp identity exists. `master_hgcd` is not registered as a distinct kind until combined-lamp identity is demonstrated.

### Step 2 — Contract stabilization

Decisions that must be encoded:

- `AlgoResult` remains arrays + scalars + metadata + messages + timings + version, with registered names and units.
- `ArtifactRequest` is the sole Task-to-persistence intent object, including one authoritative relation list.
- `Validity` is first-class and is not hidden in provenance parameter dictionaries.
- Artifact identity separates scientific kind/scope from storage representation.
- Products are immutable revisions with checksum and typed supersession/derivation relationships.
- QA is `QAFact → QARule → QAStatus + Usability`; thresholds remain versioned configuration.

### Step 3 — ArtifactService boundary

Required behavior:

1. Persist every declared logical component, not only the first required component.
2. Load named components through ArtifactService; remove Task calls to `svc.serializers.get(...).load` only after equivalent tests pass.
3. Route serializer/backend decisions through policy and service rather than direct Task/publication FITS access.
4. Include scope identity, validity and immutable revision in collision-safe URIs.
5. Return explicit loader errors; do not silently return `None` for corrupt/missing payloads.
6. Populate normalized typed relationships while retaining historical comma-separated parents as a compatibility read source.
7. Reconstruct complete scope, provenance and diagnostics in `get`/`describe`.

SQLite is retained. Individual tables evolve through reviewed additive migrations, backfills and compatibility views/adapters; no engine replacement is planned.

### Step 4 — Versioned configuration

Baseline policies are data, not algorithm constants:

- complete hardware/controller assignments and epochs;
- amplifier orientation and physical-CCD transforms;
- gain values and provenance;
- fiber/dead-fiber maps;
- arc line lists and lamp composition;
- dither patterns;
- shutter/effective-exposure-time policies.

Provisional policies are versioned and testable:

- nominal standard dither assignment followed by astrometric refinement;
- `EXPTIME` primary, with `PEXPTIME - 8 seconds` for exposures classified as parallel by the versioned mode policy.

They are not implementation blockers and do not require hard-coding.

### Step 5 — Characterization suite

Add the smallest high-value tests before adapting algorithms:

- `orient_amplifier_image`: LL/LU/RL/RU and legacy `AMPNAME` cases.
- `reduce_raw_amplifier_frame`: overscan columns, last-30-column estimator, trim/orientation order, gain and error array.
- `step_bias`: robust master, `1.4826 × MAD` scatter, read-noise scalar and partial-input behavior.
- Dark/LDLS masks: exact thresholds and full-column heuristics.
- Trace: nearest-date reference, hardware exception, sample coordinates and residual fact.
- Wavelength: line matching, rejection, recovery and residual arrays without plotting side effects.
- `fiber.get_spectra`: current weighted **sum**, fractional boundary weights, exactly `npix` total weight, detector-edge behavior and future masked-pixel policy.

The stale averaging language in `get_spectra` is corrected only in a later reviewed documentation/code change, not in this audit.

### Step 6 — Bias contract-validation slice

Target flow:

```text
RawFrame identities
→ Task loads raw arrays/headers through I/O service
→ array-only detector stages
→ array-only Bias estimator
→ AlgoResult(master_bias, per_pixel_bias_scatter, read_noise facts)
→ ArtifactRequest with amplifier scope, validity, units and parents
→ separated QA evaluation
→ ArtifactService multi-component immutable publication
→ registry lineage/validity query
→ Bias-stability analytic Products
```

Required fixes captured by this slice:

- preserve `per_pixel_bias_scatter`;
- preserve target validity;
- eliminate path collision;
- use one parent interface and normalized relations;
- propagate hard-QA failure according to configured policy;
- stop Task/algorithm serializer/file bypass;
- distinguish QA status from usability.

### Step 7 — One-amplifier calibration graph

Canonicalize the current calibration behavior without expanding physical scope prematurely:

- `master_dark` plus dark defect evidence;
- `master_ldls` plus flat-response evidence;
- `master_arc` with explicit lamp-composition metadata when available;
- `master_twilight`;
- `trace_map` plus `trace_samples`;
- `wavelength_map` plus `arc_identification` and residual facts.

Correct the graph at this stage or earlier:

- add the missing `trace_map -> wavelength_map` dependency;
- make the Master Arc node raw-`cmp` driven, matching `CmpTask.query_inputs`, unless a reviewed upstream Product flow deliberately replaces it.

### Step 8 — One physical CCD

Known physical pairing is not open:

- left CCD: LL lower, LU upper reflected in y;
- right CCD: RU lower, RL upper reflected in y.

The canonical indexed seam materialization is exactly `upper_y = 2063 - y`. `2064 - y` is not an alternative convention. The transform and its configuration version must be tested before joint scatter fitting.

Produce separate `ccd_scattered_light_model` and `scatter_subtracted_image` Products. Never hide scatter inside an overwritten amplifier array.

Implementation gate passed on 2026-07-22. The canonical path is implemented by `PhysicalCCDTarget`, `ReducedScienceAmplifierTask`, `PhysicalCCDTask`, and the array-only `virusflow.algorithms.physical_ccd` functions. Real exposure `20260609T031649.6`, SPECID `206`, exercised LL+LU and RU+RL in an isolated registry. Both orientations preserved source amplifier Products, complete named components, checksums, normalized lineage, explicit seam/zero-row-gap evidence, fit/holdout masks, residuals, and QA. Detailed values are retained in `VIRUSFLOW_STEPS_8_10_ACCEPTANCE.md`.

### Step 9 — One exposure

Use the simplest accepted methods from the knowledge notes:

- sum-based fractional five-pixel aperture extraction;
- exact fractional-weight variance;
- center-track twilight normalization decomposed into within-amp and exposure-wide amp factors;
- header TAN astrometry plus catalog shift/rotation;
- native-grid oversampled common sky;
- versioned baseline relative response;
- versioned effective-exposure-time policy.

Amp-to-amp normalization is not a contract blocker. Infer an exposure-wide robust scale under the uniform twilight assumption, retain both factors and verify the statistic through characterization/analytics.

Implementation gate passed on 2026-07-22. `ExposureTask` executed real exposure `20260609T031649.6` through all 300 raw amplifiers and 75 IFUSLOTs in an isolated registry. All 300 amplifiers produced reduced detector Products and all 150 physical CCDs produced scatter Products. Exactly 299 amplifiers produced wavelength-dependent extraction Products; `095+004+426+RU+S/N 0048` is explicitly unavailable because every one of its eight real comparison-lamp frames is identically zero. This limitation is retained in coverage/QA and was not filled or propagated to its healthy physical-CCD partner. Live Pan-STARRS DR2 catalog refinement succeeded. Exact fractional weights and variance, decomposed normalization, astrometry, sky, response, effective time, checksums, revisions, configuration, usability, and normalized lineage are queryable. Detailed values are retained in `VIRUSFLOW_STEPS_8_10_ACCEPTANCE.md`.

### Step 10 — One observation/dither set

Exposure remains atomic. Observation and DitherSet rows relate exposures but never merge away per-exposure sky, seeing, transparency, illumination, astrometry, response, time or detector state.

Use versioned nominal dither assignments as the baseline and store astrometric refinements as evidence. Handle incomplete or nonstandard sets without fabricating missing exposures.

Implementation gate passed on 2026-07-22. `ObservationTarget`, `ObservationTask`, and the array-only dither algorithms preserve the three OBSID 6 exposures as independent Product-producing entities and publish explicit membership, assignment, registration, coverage, and observation-summary Products. All three exposures were reduced independently through 300 detector amplifiers, 150 physical CCDs, and 299 extractable amplifiers. Nominal and refined offsets are separate. The real 2.85951 arcsec registration-versus-nominal RMS exceeds the versioned 1.5 arcsec warning threshold, so registration and observation QA are degraded while the grouping remains queryable and usable for investigation. Incomplete, extra, ambiguous, repeated, missing-coverage, and immutable-state cases are tested. Observation, DitherSet, and query-defined ObservationSet reads use the canonical ArtifactService boundary. Detailed evidence is retained in `VIRUSFLOW_STEPS_8_10_ACCEPTANCE.md`.

### Step 11 — Retirement

No path is removed merely because canonical code exists. Retirement requires:

- zero supported runtime references;
- numerical/contract parity or a reviewed intentional difference;
- historical-row read tests;
- accepted scientific and operational tests;
- a rollback drill;
- explicit deprecation duration from the vocabulary map.

## 4. Acceptance test ladder

| Level | Required evidence |
|---|---|
| Unit | Pure algorithms, ontology registries, configuration resolution, QA rules and serializers. |
| Contract | Target/AlgoResult/ArtifactRequest/Artifact/Validity/QA serialization and invalid-case checks. |
| Integration | Task → ArtifactService → SQLite/FITS → query → QA/analytics with all components and relations. |
| Regression | Current detector, Bias, trace, wavelength and sum-aperture numerical fixtures. |
| Science acceptance | One amplifier, physical CCD, exposure and three-dither observation against reviewed tolerances. |
| Operational | Concurrency, URI collisions, retries, historical reads, partial inputs and rollback selection. |

QA numeric thresholds are configured per Product/policy version. Acceptance requires the fact/rule/status/usability structure even while thresholds continue to evolve.

## 5. True blockers versus non-blockers

### Blockers before the Bias slice

- Canonical Product kind, physical scope, units and coordinate registries.
- First-class validity and one lineage/relation interface.
- Multi-component ArtifactService persistence/loading.
- Immutable, collision-safe URI/revision behavior.
- Separated QA facts/rules/status/usability and non-swallowed blocking behavior.
- Bias/detector characterization tests.

### Blocker before physical-CCD scatter

- Exact zero-indexed seam transform characterization. Pair ordering is already known.

### Not blockers

- Final numerical QA thresholds.
- Optimal amp-to-amp twilight statistic.
- Final dither history, provided the baseline policy is versioned and overridable.
- Validation of the provisional parallel exposure-time correction, provided its policy/version/evidence are retained.
- Profile/forward extraction, full covariance, advanced sky PCA or cube reconstruction.
- Replacing SQLite; no evidence currently requires it.

## 6. Unresolved scientific decisions

### Cannot currently be inferred

- Indexed physical-CCD seam convention.
- Lamp composition of existing unlabeled `cmp` inputs and therefore whether optional `master_hg`, `master_cd`, or `master_hgcd` Products can be built from them.
- Exact historical semantics/validity of legacy `AMPNAME=LR/UL` behavior.
- Missing authoritative historical configuration source data and epochs.

### Can be inferred and must be verified

- `master_arc` as the safe aggregate for current `cmp` data.
- Exposure-wide center-track amp normalization and its robust statistic.
- Current empirical trace/wavelength methods as baseline estimators.
- Weighted sum-aperture behavior and capture stability.
- Bias cadence/validity derived from stability analytics.

### Versioned provisional policy

- Standard nominal dither rule.
- `PEXPTIME - 8 seconds` for parallel mode, with `EXPTIME` otherwise primary.
- QA thresholds.

These categories prevent a missing implementation from being mislabeled as missing science knowledge.

## 7. First post-review implementation task

After explicit review approval, implement **Step 1 only**:

> Add validated, data-only canonical registries for Product kinds, physical scopes, relations, units, coordinate conventions, assumptions and legacy aliases, plus contract tests. Do not change Task behavior, Artifact writes, public names or persisted schema in that task.

Exit criterion: every Product in the reconciliation map has one reviewed specification, all legacy mappings resolve deterministically or remain explicitly ambiguous, and existing production paths run unchanged.

Only then proceed to core contracts and eventually the Bias validation slice.

## 8. Review gate

This plan is inert until all three Markdown deliverables have been reviewed together. Review must explicitly approve:

- subsystem classifications;
- Product map and comparison-lamp decision;
- canonical vocabulary direction;
- blockers and provisional policies;
- ordered migration steps;
- first post-review task.

Completion of documentation is not authorization to begin implementation.

## Approved Steps 1–7 implementation tranche

The three reconciliation documents have been reviewed together and approved.

Implementation is authorized for **Steps 1 through 7, inclusive**, as one continuous migration tranche. The implementation agent must work autonomously through the documented sequence and must not pause for approval between steps.

Each step's tests and exit criteria remain mandatory internal gates. A failed exit criterion must be corrected before proceeding, but successful completion of a step does not require a new human authorization.

This historical tranche ended after Step 7 acceptance and is superseded for Steps 8–10 by the authorization below. It never authorized Step 11.

The intended result is a full-width, moderately deep migration:

* Steps 1 through 5 establish the ontology, contracts, ArtifactService boundary, versioned configuration framework, and characterization coverage.
* Step 6 validates those architectural layers through the complete Bias slice.
* Step 7 extends the canonical implementation through the full one-amplifier calibration graph: Bias, Dark, LDLS, Arc, Twilight, Trace, and Wavelength.

### Real-data acceptance dataset

Use observing date **20260609** as the primary real-data acceptance dataset for the one-amplifier end-to-end test. It is expected to contain all required raw calibration inputs.

The implementation must verify that the required files are actually present rather than assuming completeness. If an input is missing, report the exact missing frame type, identity, and expected dependency.

A second available observing date may be used for cross-date validity, selection, lineage, and compatibility testing. Do not assume that the second date is complete unless verified from the inventory.

## Resolved physical-CCD coordinate decision

The indexed-array transform for the reflected upper amplifier is:

```python
upper_y = 2063 - y
```

This is the authoritative zero-indexed Python-array convention.

The previously documented `2064 - y` expression reflected a one-indexed coordinate interpretation and must not be treated as an alternative implementation.

Characterization tests must verify:

* the first and last mapped rows;
* invertibility of the transform;
* absence of duplicated or omitted detector rows;
* correct LL/LU and RU/RL seam ordering;
* consistency between coordinate metadata and array indexing.

This decision removes the indexed seam convention from the unresolved-science and implementation-blocker lists.

## Revised blockers

### Blockers before the Bias slice

* Canonical Product-kind, physical-scope, unit, and coordinate registries.
* First-class validity and one lineage/relation interface.
* Multi-component ArtifactService persistence and loading.
* Immutable, collision-safe URI and revision behavior.
* Separated QA facts, rules, status, and usability with non-swallowed blocking behavior.
* Bias and detector characterization tests.

### Resolved decisions

* The zero-indexed upper-amplifier transform is `2063 - y`.
* Left CCD pairing is LL lower plus LU upper reflected in y.
* Right CCD pairing is RU lower plus RL upper reflected in y.

### Remaining non-blockers

* Final numerical QA thresholds.
* Optimal amp-to-amp twilight statistic.
* Final historical dither classification, provided the baseline policy remains versioned and overridable.
* Validation of the provisional parallel exposure-time correction, provided its policy, version, and evidence are retained.
* Profile or forward extraction, full covariance, advanced sky PCA, and cube reconstruction.
* Replacement of SQLite.

## Revised unresolved scientific decisions

### Cannot currently be inferred

* Lamp composition of existing unlabeled `cmp` inputs and therefore whether optional `master_hg`, `master_cd`, or `master_hgcd` Products can be selected.
* Exact historical semantics and validity of legacy `AMPNAME=LR/UL` behavior.
* Missing authoritative historical configuration source data and epochs.

The indexed physical-CCD seam convention is no longer unresolved.

## Revised Steps 1–7 review gate

Review of the three reconciliation documents is complete.

This approval authorized implementation of **Steps 1 through 7 as one autonomous tranche**. Those steps are now implemented and accepted. The Steps 8–10 authorization below supersedes its former stop-before-Step-8 language; Step 11 remains unauthorized.

Completion of an individual step does not require another review. Implementation must continue until the Step 7 exit criterion is satisfied, a true evidence-dependent blocker is encountered, or an environmental limitation prevents a required acceptance test from running.

## 8. Steps 1–7 completion ledger

| Step | Status | Implemented evidence | Gate result |
|---|---|---|---|
| 1. Vocabulary | COMPLETE | `virusflow/ontology/`; canonical aliases and validated kind/scope/unit/coordinate contracts | passed |
| 2. Core contracts | COMPLETE | first-class `Validity`, configuration references, revisions/checksums/relations, QA fact/rule/status/usability types | passed |
| 3. ArtifactService | COMPLETE | service-owned multi-component persist/load, immutable paths, checksums, normalized lineage, additive SQLite tables | passed |
| 4. Configuration | COMPLETE for amplifier tranche | versioned orientation, exact `2063`, unknown-evidence gain/read-noise fallbacks, provisional policies, trace-reference resolver | passed; unavailable epochs remain explicit unknown evidence |
| 5. Characterization | COMPLETE for Steps 1–7 | all AMP orientations, overscan/gain/variance, exact Bias MAD, masks, trace reference/exception/samples, arc recovery/rejection/residuals, sum aperture | passed |
| 6. Bias slice | COMPLETE | array-only raw/detector/Bias path, both components, validity/configuration/QA/usability, immutable revisions, stability analytic | passed synthetically and on 20260609 |
| 7. One amplifier | COMPLETE | canonical Bias/Dark/LDLS/Arc/Twilight/Trace/Wavelength graph, full components, corrected dependencies and raw cmp planning | passed synthetically and on 20260609 |

Commits comprising the tranche:

- `5b469d1` — canonical ontology and core contracts;
- `85ba08c` — immutable multi-component ArtifactService persistence;
- `ff98864` — array-only scientific characterization baseline;
- `e1f348c` — canonical amplifier Task and graph implementation;
- `b05102a` — mask, Trace, and Wavelength characterization gates;
- `3a2e426` — canonical QA facts/status/usability persistence;
- `9c0503a` — legacy calibration public-entry adapters.

The full suite increased from the frozen `30 passed` baseline to `55 passed`. Real acceptance used an isolated temporary SQLite registry and observing-night window `20260609..20260610` for ZipCode `060+003+206+LL+S/N 0039`. It produced all seven canonical Products, complete components, normalized lineage, immutable revisions/checksums, configuration evidence, and usable/pass QA decisions. Detailed inventory and numerical results are recorded in `VIRUSFLOW_REPOSITORY_RECONCILIATION.md`.

The 20260604 secondary dataset is incomplete because it contains no twilight frames. It was used only for a real cross-date Bias validity/revision selection check; no full-graph claim is made.

All Step 1–7 exit criteria are satisfied. The documented rollback remains additive: historical rows/files are untouched, legacy read aliases and public entry points remain, and canonical revisions can be selected independently. No Step 8–11 code was added.

## 9. Steps 8–10 authorization and scientific acceptance policy

The Steps 1–7 implementation record and acceptance evidence have been reviewed and accepted.

Implementation of **Steps 8 through 10, inclusive, is authorized as one autonomous tranche**. Each documented test set and exit criterion is an internal gate: implement, verify scientifically, correct failures, commit the cohesive milestone, and continue without another approval between steps. **Do not begin Step 11.** Do not retire legacy readers, Tasks, aliases, database rows, or files.

The canonical zero-indexed upper-amplifier transform is exactly:

```python
upper_y = 2063 - y
```

`2064 - y` is not an alternative convention.

Pre-refactor numerical behavior is characterization evidence, not scientific truth. Intentional differences are acceptable when justified by retained old/new comparisons where practical, quantitative evidence, QA, analytics, and documented scientific reasoning. Every intentional difference must identify its changed assumption, mask, model, or policy; expose relevant QA facts; retain sufficient intermediate evidence; and record algorithm and configuration versions. Uncertain non-blocking scientific choices must be implemented as versioned, configurable policies rather than silent hard-coded assumptions.

The tranche must use isolated registries and artifact directories for acceptance and must not modify the historical workspace registry. Completion of Step 10 does not authorize Step 11.

## 10. Committed Steps 1–7 verification command

Run the accepted 20260609 amplifier path in a new isolated temporary registry and artifact directory with:

```bash
python -m virusflow.cli.verify_steps_1_7
```

The command verifies ZipCode `060+003+206+LL+S/N 0039`, the exact raw inventory, all seven canonical Products, named-component ArtifactService loading and checksums, normalized lineage, QA status/usability, revision, and validity. It prints the manifest and writes a unique temporary report directory containing:

- `steps_1_7_manifest.json`;
- `steps_1_7_scientific_acceptance.md`;
- `steps_1_7_scientific_acceptance.png` with Bias, Dark, LDLS, Arc, Twilight, Trace, and Wavelength diagnostics.

Use `--data-root`, `--configuration-root`, or `--output-dir` only when the default accepted paths are unavailable.
