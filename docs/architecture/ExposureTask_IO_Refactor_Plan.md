# `ExposureTask` Input/Output Refactor Plan

> Status: Proposed refactor plan. No code has been changed. This document
> extends [`ExposureTask_Evidence_Action_Flow.md`](ExposureTask_Evidence_Action_Flow.md)
> (the implementation map) and [`VIRUSFlow_Target_Architecture.md`](VIRUSFlow_Target_Architecture.md)
> (the long-range architecture) with a concrete, incremental refactor of
> `ExposureTask` and its delegated science Tasks.
>
> **Partially superseded by [`Calibration_Time_Fiber_Response_Plan.md`](Calibration_Time_Fiber_Response_Plan.md).**
> Fact-check on this document's twilight-usage claims: as written today,
> `ExposureTask.run()` *does* run a second `assemble_physical_ccd` +
> `fit_gap_scattered_light` scatter fit against `master_twilight` (§1/§4 item 5,
> lines 272–282) purely to extract a scatter-subtracted twilight spectrum for
> `within_amplifier_normalization`. That duplicate twilight scatter fit is real
> in the current code. The companion plan removes it entirely rather than
> fixing it in place: under the new plan, twilight/LDLS/science extraction all
> move to calibration time as plain per-amplifier aperture extraction with no
> physical-CCD scatter-light step, and §1's op 5, §3's "Calibration-source
> spectrum" row, and §4 item 8 (`fit_physical_ccd_scatter` twilight reuse) no
> longer apply once that plan lands. Sections 0–4, 5 (except `response.py`'s
> twilight/amp-normalization functions), and 9–11 of this document remain
> accurate for the astrometry/sky/illumination/completion parts of
> `ExposureTask` that the new plan does not touch.

## 0. Ground rules taken from the current repository

Before proposing anything, three repository facts constrain every later
section:

1. **`AlgoResult` already exists and is already the right shape.**
   [`virusflow/core/algo_result.py`](../../virusflow/core/algo_result.py)
   defines `AlgoResult(kind, meta, arrays, scalars, version, messages,
   timings)` plus `as_meta()`, `get_array()`, `to_dict()`, and a tolerant
   `ensure_algo_result()` coercion. This already matches the `AlgoResult`
   sketched in `VIRUSFlow_Target_Architecture.md` §7.1. **No new result type
   is needed anywhere in this refactor.**
2. **`AlgoResult` is already used correctly in `algorithms/physical_ccd.py`,
   but not in `algorithms/exposure.py`.** `assemble_physical_ccd()` and
   `fit_gap_scattered_light()` both return `AlgoResult` with clearly named
   `arrays`/`scalars`/`meta`. Every function in `algorithms/exposure.py`
   instead returns a bare tuple, a `namedtuple`-style frozen dataclass
   (`ExtractionResult`, `FiberResponseModel`, `LatentSkyModel`), or a raw
   `np.ndarray` tuple. This is the single largest source of "tuples whose
   meaning is not evident at the call site" in `ExposureTask.run()`.
3. **Publication mechanics are already centralized.** `ArtifactRequest` /
   `LogicalComponent` ([`artifacts/requests.py`](../../virusflow/artifacts/requests.py)),
   `DefaultPublicationService.publish()` ([`publication/service.py`](../../virusflow/publication/service.py)),
   and `_SciencePublisher._publish()` / `_qa()` ([`tasks/science.py`](../../virusflow/tasks/science.py))
   already do contract validation, persistence, and QA-bundle attachment.
   `ExposureTask._request()` is already a local helper around these. The gap
   is not "no publication helper exists" — it is that construction of
   `components=` dicts is repeated ad hoc at every one of the 14 call sites in
   `ExposureTask.run()` instead of living next to the algorithm that produced
   the values.

Given these three facts, the refactor is almost entirely: **(a)** make
`algorithms/exposure.py` return `AlgoResult` like `algorithms/physical_ccd.py`
already does, **(b)** move the small publication-component-building blocks
that currently sit inline in `ExposureTask.run()` next to each algorithm as a
`publish_<thing>(result, ...)` helper, and **(c)** split `algorithms/exposure.py`
into scientifically named modules so the algorithms are easy to find. Nothing
about calibration selection, numerical ordering, physical-CCD pairing, or
Product contents changes.

---

## 1. Current dataflow (concise)

```text
ExposureTask.run()
  → discover science raw rows, resolve config/fplane/offsets     (L0)
  → ExposureTask._ensure_calibrations()                          (L0)
  → per amplifier: ReducedScienceAmplifierTask.run()              (L1)
  → per physical-CCD pair: PhysicalCCDTask.run()                  (L2)
      → assemble_physical_ccd() → AlgoResult
      → fit_gap_scattered_light() → AlgoResult
      → publish ccd_scattered_light_model
  → per usable amplifier, INLINE in ExposureTask.run():           (L3)
      → assemble + fit + subtract twilight scatter (duplicated math)
      → extract_fractional_aperture() twice (science + twilight)
      → within_amplifier_normalization()
      → wavelength-row validation (inline arithmetic)
  → amplifier_normalization() → publish amp_to_amp_normalization  (L4)
  → inline normalization + concatenation into a global fiber frame
  → parse_header_pointing() + tan_fiber_coordinates()
    → publish initial_astrometry                                  (L5)
  → detect_fiber_sources() → publish source_detection_catalog
  → catalog cone-search + fit_catalog_astrometry()
    → publish catalog_match_table, final_astrometry, fiber_sky_coordinates
  → select_sky_fibers() → publish sky_fiber_mask                  (L6)
  → derive_sky_oversampling_factor() + oversampled_incident_sky()
  → inline illumination arithmetic → publish exposure_illumination_correction
  → LatentSkyModel(...) → publish sky_model
  → LatentSkyModel.evaluate() → inline sky subtraction
  → publish baseline_relative_response (explicit identity, always degraded)
  → compact_fiber_response() → publish fiber_response_model
  → inline final calibration arithmetic → CalibratedFiberState        (run-local)
  → classify_mode_and_effective_time()
    → publish exposure_mode_classification, effective_exposure_time   (L7)
  → inline coverage-matrix construction → publish exposure_completion_manifest
  → return dict of 13 Artifacts + calibrated_fiber_state
```

This matches the mermaid graph in `ExposureTask_Evidence_Action_Flow.md`
exactly; this document does not re-derive it. The point of this section is
narrower: **which of these steps already call a named algorithm, and which
perform arithmetic directly inside `ExposureTask.run()`.**

Steps that call a named algorithm today: detector reduction (delegated to
`ReducedScienceAmplifierTask`/`reduce_amplifier_array`), physical-CCD assembly
and scatter fit (delegated to `PhysicalCCDTask`/`assemble_physical_ccd`/
`fit_gap_scattered_light`), `extract_fractional_aperture`,
`within_amplifier_normalization`, `amplifier_normalization`,
`parse_header_pointing`, `tan_fiber_coordinates`, `detect_fiber_sources`,
`fit_catalog_astrometry`, `select_sky_fibers`, `derive_sky_oversampling_factor`,
`oversampled_incident_sky`, `compact_fiber_response`,
`classify_mode_and_effective_time`.

Steps that are pure arithmetic embedded directly in `ExposureTask.run()`
today (line numbers refer to the current file):

| Embedded arithmetic | Lines |
|---|---|
| Twilight physical-CCD assembly + scatter fit + subtraction (duplicates `PhysicalCCDTask`'s own math for the twilight reference instead of reusing it) | 272–282 |
| Wavelength-row validity check (finite + strictly increasing) and fiber exclusion bookkeeping | 306–327 |
| Final per-amplifier normalization (`within × amp_factor`) and global fiber-frame concatenation | 379–418 |
| Exposure illumination factor (per-amp median sky level ÷ global median) | 564–573 |
| Sky evaluation and subtraction (`sky_model.evaluate(...)`, `spectrum - sky_prediction`) | 625–630 |
| Final calibrated flux/variance/mask construction (division by illumination, mask bit assembly, scale constants) | 671–698 |
| Coverage-matrix construction for the completion manifest | 724–763 |

These seven blocks are the concrete refactor targets for "move array
arithmetic out of the task." They are not new findings — they are the same
items flagged as **REFACTOR** in `ExposureTask_Evidence_Action_Flow.md`
("Flow findings" §6 and "Architectural disposition" table) — but here each is
named so it can be turned into one function with one `AlgoResult`.

---

## 2. Full current-operation input/output map

Scope legend: `EXP` = exposure, `PCCD` = physical CCD, `AMP` = amplifier,
`FIB` = fiber, `IE` = instrument epoch.

| # | Scientific operation | Code location | Scope | Inputs | Algorithm | Current output | Persisted output | Run-local output | Downstream consumer | Failure behavior | Information object |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Detector reduction (overscan, orient, gain, variance init) | `algorithms/ccd.reduce_amplifier_array` via `ReducedScienceAmplifierTask.run` (`tasks/science.py:89`) | AMP | raw frame array + header | `reduce_amplifier_array` | `AlgoResult` (arrays: `oriented_detector_image`, `detector_variance`; scalars: `gain`, `read_noise`) | none (scratch) | `ReducedAmplifierState` | Bias/dark calibration (op 2), physical-CCD assembly (op 3) | try/except per amplifier in `ExposureTask.run`; failure recorded, amplifier skipped | Exposure time / observing state (partially — gain/read-noise are detector health, not this list) |
| 2 | Bias+dark calibration, mask union | `algorithms/calibration_detector.correct_response_calibration_frames` via `ReducedScienceAmplifierTask.run` | AMP | oriented image/variance, `master_bias`, selected `master_dark` array/mask/required reference time and bias convention, `master_ldls`, science EXPTIME | `correct_response_calibration_frames` | `AlgoResult` detector state, then `ReducedAmplifierState` (image, variance, mask, parent_ids, summaries) | none (scratch) | `ReducedAmplifierState` | Physical-CCD assembly (op 3) | `RuntimeError` if bias/dark missing; `ValueError` if the selected dark physical state or science EXPTIME is invalid (aborts this amplifier via caller's try/except) | not in the requested list explicitly; closest is scattered-light precursor |
| 3 | Physical-CCD assembly | `algorithms/physical_ccd.assemble_physical_ccd` via `PhysicalCCDTask.run` (`tasks/science.py:255-264`) | PCCD | paired `ReducedAmplifierState.image/variance/pixel_mask` | `assemble_physical_ccd` | `AlgoResult` (arrays: `image`, `variance`, `pixel_mask`, `seam_mask`, `source_amplifier_map`, ...) | none (feeds op 4 only) | `PhysicalCCDState.assembly` | Scatter fit (op 4) | Missing partner ⇒ both amplifiers recorded as failed in `ExposureTask.run`, pair skipped | Scattered-light background (precursor) |
| 4 | Scattered-light fit + subtraction | `algorithms/physical_ccd.fit_gap_scattered_light` via `PhysicalCCDTask.run` (`tasks/science.py:265-272`) | PCCD | assembled image/mask, paired trace maps | `fit_gap_scattered_light` | `AlgoResult` (arrays: `model`, `scatter_subtracted_image`, masks; scalars: residual sigmas) | **`ccd_scattered_light_model`** (compacted via `compact_scattered_light_payload`) | `PhysicalCCDState.scatter` (dense `scatter_subtracted_image` stays run-local) | Extraction (op 6), twilight normalization (op 5) | `ValueError` if <30 clean gap samples (propagates as pair failure); QA `fail/unusable` if holdout sigma non-finite, but `ExposureTask` does not check this before continuing | **Scattered-light background** |
| 5 | Twilight physical-CCD assembly + scatter fit + subtraction (duplicated math) | `tasks/exposure.py:272-282` (inline, calls ops 3–4's *functions* directly, not `PhysicalCCDTask`) | PCCD | paired `master_twilight` components, paired trace maps | `assemble_physical_ccd` + `fit_gap_scattered_light` (same functions, second call site) | `AlgoResult` (twilight scatter, discarded after `scatter_subtracted_image` is read) | none — not persisted at all, unlike op 4's near-identical science-CCD scatter model | `twi_subtracted` array only | Twilight extraction (op 6) | No dedicated guard; `KeyError` if `master_twilight` missing despite being in the 7-kind gate (documented gap in Evidence-Action-Flow §8) | Calibration-source spectrum precursor (twilight is the calibration source here) |
| 6 | Science + twilight fractional-aperture extraction | `algorithms/exposure.extract_fractional_aperture` (`tasks/exposure.py:296-300`) | AMP | scatter-subtracted amp image/variance/mask (science and twilight), trace | `extract_fractional_aperture` | `ExtractionResult` (plain dataclass: spectrum, variance, valid_pixel_fraction, ...) — **not `AlgoResult`** | none (scratch) | spectrum/variance/valid_fraction per amp | Wavelength-row validation (op 7), within-amp normalization (op 8) | No exception guard around this call itself; `extract_fractional_aperture` marks invalid samples NaN rather than raising | Astronomical source spectrum + Sky spectrum (raw, pre-sky-separation) |
| 7 | Within-amplifier fiber-to-fiber normalization | `algorithms/exposure.within_amplifier_normalization` (`tasks/exposure.py:301-303`) | AMP | twilight extraction spectrum | `within_amplifier_normalization` | 4-tuple `(raw_ratio, within, normalization_valid, common_twi)` — **anonymous tuple, no `AlgoResult`** | none — only `within` (smoothed) reaches `amp_results`; `raw_ratio`, `normalization_valid`, and the twilight-fit diagnostic sigma are computed then dropped, even though `within_amp_fiber_normalization` is a registered kind | `within` array carried in `amp_results[key]["within"]` | Response compaction (op 14), final normalization (op 9) | none | **Fiber-to-fiber relative response** |
| 8 | Wavelength-row validity + fiber exclusion | `tasks/exposure.py:306-327` (inline arithmetic) | AMP → FIB | extracted spectrum shape, `wavelength_map` component | arithmetic only, no algorithm function | boolean arrays + `wavelength_fiber_exclusions` dict entry | recorded into `exposure_completion_manifest.metadata["wavelength_fiber_exclusions"]` (op 18) | `valid_wavelength_rows` used to filter fibers in global concatenation (op 9) | Shape mismatch ⇒ amplifier failure recorded, amplifier skipped; no valid rows ⇒ same; some invalid rows ⇒ those fibers excluded, amplifier still contributes | not a listed information object; supports Focal-plane fiber mapping downstream |
| 9 | Amplifier-to-amplifier normalization | `algorithms/exposure.amplifier_normalization` (`tasks/exposure.py:346`) | EXP | per-amp twilight levels | `amplifier_normalization` | 2-tuple `(factors, reference_level)` — **anonymous tuple** | **`amp_to_amp_normalization`** | `amp_factors` broadcast into final normalization (op 10) | none (NaN propagates for non-positive levels) | **Amplifier-to-amplifier relative response** |
| 10 | Final per-fiber normalization + global fiber-frame concatenation | `tasks/exposure.py:379-418` (inline arithmetic) | EXP | per-amp spectrum/variance/within/wavelength/valid_fraction, `amp_factors`, fplane, fiber offsets | arithmetic only, no algorithm function | 7 concatenated global arrays (`spectrum`, `spectrum_variance`, `valid_fraction`, `wavelength`, `fiber_identity`, `focal`, `within_response`) | none directly (feeds every downstream Product) | all 7 arrays, used throughout L5–L7 | IFUSLOT absent from fplane ⇒ amplifier excluded from global frame, recorded as failure | Focal-plane fiber mapping (identity+focal), Fiber-to-fiber + amp response composition |
| 11 | Initial (header) astrometry | `algorithms/exposure.parse_header_pointing` + `tan_fiber_coordinates` (`tasks/exposure.py:420-421`) | EXP | representative header, focal coordinates | `parse_header_pointing`, `tan_fiber_coordinates` | 4-tuple + 3-tuple (both anonymous) | **`initial_astrometry`** | `initial_ra`, `initial_dec` used for source-detection RA/Dec columns | **Astrometric mapping** |
| 12 | Source detection | `algorithms/exposure.detect_fiber_sources` (`tasks/exposure.py:434`) | EXP | broadband (median) spectrum, IFUSLOT index, focal coords | `detect_fiber_sources` | raw `np.ndarray` (8 columns after RA/Dec appended) | **`source_detection_catalog`** | `detections` used for sky-fiber source rejection (op 15) | Detections is empty array on no candidates (no exception) | Astronomical source spectrum (detection only, not extraction) |
| 13 | Catalog astrometric fit | `algorithms/exposure.fit_catalog_astrometry` (`tasks/exposure.py:463`) | EXP | detections, catalog cone-search rows | `fit_catalog_astrometry` | 3-tuple `(match_table, fit_parameters, success)` | **`catalog_match_table`**, **`final_astrometry`**, **`fiber_sky_coordinates`** | `final_ra`, `final_dec` used in calibrated state and completion manifest | Catalog provider exception ⇒ empty catalog, `astrometry_success=False`; <4 coherent matches ⇒ same; either ⇒ header TAN retained, Products marked `warn/degraded` | **Astrometric mapping** (refined) |
| 14 | Sky-fiber selection | `algorithms/exposure.select_sky_fibers` (`tasks/exposure.py:530`) | FIB | global spectrum, valid_fraction, source mask | `select_sky_fibers` | 4-tuple `(sky_mask, broadband_flux, center, sigma)` | **`sky_fiber_mask`** | `sky_mask`, `broadband_flux` used in illumination (op 15) and incident-sky combination (op 16) | none (falls back to `nan` center/sigma if no good fibers, not raised) | Sky spectrum (fiber selection precursor) |
| 15 | Exposure illumination | `tasks/exposure.py:564-573` (inline arithmetic) | EXP/AMP→FIB | `fiber_identity`, `sky_mask`, `broadband_flux` | arithmetic only, no algorithm function | `amp_illumination`, `fiber_illumination` arrays | **`exposure_illumination_correction`** | `fiber_illumination` used in sky-model persistence (op 16), response compaction (op 14b/op 17), final calibration (op 19) | none (NaN if an amp has zero sky fibers) | **Field/exposure illumination** |
| 16 | Sky sampling + latent sky model | `algorithms/exposure.derive_sky_oversampling_factor` + `oversampled_incident_sky` + `LatentSkyModel` (`tasks/exposure.py:546-624`) | EXP | wavelength, spectrum, sky_mask | `derive_sky_oversampling_factor`, `oversampled_incident_sky` | 4-tuple `(sky_wave, incident_sky, sky_variance, sky_counts)`; then `LatentSkyModel` instance | **`sky_model`** | `sky_model` object (`.evaluate()` called in op 17) | `ValueError` if no finite sky-fiber wavelength samples (propagates, aborts Exposure) | **Sky spectrum** |
| 17 | Sky evaluation + subtraction | `tasks/exposure.py:625-630` (inline; calls `LatentSkyModel.evaluate`) | FIB | `sky_model`, `wavelength_bin_edges(wavelength)`, `fiber_illumination` | `LatentSkyModel.evaluate` + subtraction arithmetic | `sky_prediction`, `sky_subtracted`, `residual_sigma` | not persisted directly; `residual_sigma` recorded in `CalibratedFiberState.metadata` | `sky_subtracted` feeds final calibration (op 19) | none | Sky spectrum (applied) |
| 18 | Baseline relative response (explicit identity) | `tasks/exposure.py:632-644` (inline construction, no algorithm function) | IE | `sky_wave` shape only | none — `np.ones(...)` | **`baseline_relative_response`** (always `warn/degraded`) | not used in the arithmetic (illustrative note in Evidence-Action-Flow §5) | none consumed downstream numerically; ID recorded in `fiber_response_model` metadata | none | **Spectral throughput/sensitivity** (provisional placeholder) |
| 19 | Compact fiber-response model | `algorithms/exposure.compact_fiber_response` (`tasks/exposure.py:645`) | EXP | wavelength, within_response, amp_factors, fiber_illumination, fiber_identity | `compact_fiber_response` | `FiberResponseModel` dataclass — **not `AlgoResult`** | **`fiber_response_model`** (always `warn/degraded`) | not consumed further by `ExposureTask` (its `.evaluate()` is unused, per Evidence-Action-Flow §5) | **Spectral throughput/sensitivity** (factorized record) |
| 20 | Final calibrated flux/variance/mask | `tasks/exposure.py:671-698` (inline arithmetic) | FIB | `sky_subtracted`, `fiber_illumination`, `spectrum_variance`, `valid_fraction`, `wavelength` | arithmetic only, no algorithm function | `CalibratedFiberState` (run-local dataclass) | none (explicitly run-local; feeds observation assembly) | entire `CalibratedFiberState` | Observation-assembly Tasks (outside `ExposureTask`) | mask bits recorded, not raised | Astronomical source spectrum (calibrated) |
| 21 | Observing mode + effective exposure time | `algorithms/exposure.classify_mode_and_effective_time` (`tasks/exposure.py:700`) | EXP | representative header | `classify_mode_and_effective_time` | 3-tuple `(mode, effective_seconds, time_evidence)` | **`exposure_mode_classification`**, **`effective_exposure_time`** | `mode`/`effective_seconds` not reused elsewhere in this task | Header parse exception aborts (uncaught) | **Exposure time and observing state** |
| 22 | Completion manifest | `tasks/exposure.py:724-763` (inline arithmetic) | EXP | `failures`, `wavelength_fiber_exclusions`, per-amp calibration coverage, other artifact QA counts | arithmetic only, no algorithm function | coverage matrix + summary dict | **`exposure_completion_manifest`** | none | terminal (returned to caller) | Encodes degraded status, never raises | Environmental/tracker/QA rollup |

This table is a description of the implemented code, matching op-for-op the
mermaid graph in `ExposureTask_Evidence_Action_Flow.md`; it adds the explicit
"does this call return an `AlgoResult`" column that the mermaid graph does not
carry, which is the load-bearing fact for this refactor.

---

## 3. Information-object map

| Information object | Status | Evidence today | Producer | Persisted Product | Discarded measurements | Run-local arrays | Downstream consumers | Local fitted model? | Future model entry point | In scope for this refactor? |
|---|---|---|---|---|---|---|---|---|---|---|
| **Scattered-light background** | Implemented, persisted | Robust degree-2 gap fit, holdout residual sigma, boundary continuity | `fit_gap_scattered_light` (already `AlgoResult`) | `ccd_scattered_light_model` | none of note — `compact_scattered_light_payload` already keeps bounded evidence | `scatter_subtracted_image` (PCCD-scope, run-local) | Extraction (op 6), twilight scatter (op 5, duplicated) | Yes — degree-2 polynomial (`ScatteredLightModel`) | Model already *is* a small fitted model; a future "approved scattered-light model" would replace the per-exposure fit with a selected prior — natural seam is `fit_gap_scattered_light` → could accept an optional prior model input | Yes — rename/relocate only, already well-formed |
| **Fiber-to-fiber relative response** | Implemented, **partially in-memory only** | `within_amplifier_normalization` computes raw ratio, smoothed ratio, valid mask, common twilight | `within_amplifier_normalization` (plain tuple) | Registered kind `within_amp_fiber_normalization` exists but **is never published** | raw ratio, `normalization_valid` mask, twilight-fit holdout sigma (all computed, all dropped after `within_amplifier_normalization` returns) | `within` (smoothed) only, inside `amp_results[key]` | Final normalization (op 10), `compact_fiber_response` (op 19) | Yes — 51-pixel median-filtered smoothing is a light non-parametric fit | Natural seam: publish the raw/valid/diagnostic evidence as `within_amp_fiber_normalization`, later replace `within_amplifier_normalization`'s internal smoothing with an approved response-shape model | Yes — this is the clearest ADAPT item; publishing the already-registered kind closes a real gap without new science |
| **Amplifier-to-amplifier relative response** | Implemented, persisted | Per-amp twilight level, robust reference level, factors | `amplifier_normalization` (plain tuple) | `amp_to_amp_normalization` | none of note | `amp_factors` used inline in op 10 | Global normalization (op 10), `compact_fiber_response` (op 19) | Yes — simple ratio-to-median factor | Could later accept an inherited/prior amp-normalization Product for exposures with no usable twilight (mentioned as a degraded mode in Target Architecture §18) | Yes — wrap return in `AlgoResult`, no behavior change |
| **Field/exposure illumination** | Implemented, persisted | Per-amp median sky broadband ÷ global median | inline arithmetic (`tasks/exposure.py:564-573`) — **no algorithm function exists** | `exposure_illumination_correction` | none additional beyond what's already computed | `fiber_illumination` | Sky model persistence (op 16), response compaction (op 19), final calibration (op 20) | Yes — this ratio *is* the fitted quantity | Natural seam for a future flat-field/illumination-model Product informing the ratio instead of raw per-exposure sky | Yes — extract into `measure_exposure_illumination()` returning `AlgoResult`; pure move, no new math |
| **Spectral throughput/sensitivity** | **Partially implemented — provisional placeholder** | `baseline_relative_response` is an explicit `np.ones(...)` identity array, always `warn/degraded`; `fiber_response_model` records within-amp knots + amp/illumination factors but its `evaluate()` is never called | inline construction (baseline) + `compact_fiber_response` (compaction) | `baseline_relative_response`, `fiber_response_model` | none — this *is* the explicit placeholder documented as provisional | none consumed numerically (Evidence-Action-Flow §5) | Response Products are terminal in this task; not consumed by the final calibrated flux division | No — response model is not applied | This is exactly the seam the target architecture calls "models come later": `fiber_response_model` already has the shape (`measurement → compact record`); applying an *approved* absolute-response model is future scientific work, not this refactor | **Not in scope** beyond preserving the existing (degraded) publication path unchanged |
| **Calibration-source spectrum** (twilight) | Implemented, but **not persisted as its own Product** | Twilight physical-CCD assembly + scatter fit + subtraction (op 5), duplicated math from op 3–4 | inline in `ExposureTask.run` (calls `assemble_physical_ccd`/`fit_gap_scattered_light` a second time) | none — unlike the science-CCD scatter model, the twilight scatter model/fit evidence is discarded after `scatter_subtracted_image` is read | scatter fit evidence for the twilight CCD (residual sigmas, holdout mask) — computed, then dropped except for `twilight_scatter_sigma` which is retained only inside `amp_results` metadata, not published | `twi_subtracted` (dense, run-local, correctly so) | Twilight extraction (op 6), within-amp normalization (op 7) | Yes (the twilight scatter fit) | If twilight scatter evidence were persisted it would parallel `ccd_scattered_light_model`; but see §4 open finding — this may indicate the science and twilight scatter fits should share one function call, not duplicate it | Yes, narrowly: fold op 5 into a reusable `fit_physical_ccd_scatter()` helper shared by science and twilight paths (see §5); whether to *persist* the twilight scatter model is a research question, not decided here |
| **Sky spectrum** | Implemented, persisted | Latent oversampled flux-density grid + variance + sample counts | `oversampled_incident_sky` + `LatentSkyModel` | `sky_model` | none of note | `sky_model` object, `sky_prediction`, `sky_subtracted` (all correctly run-local) | Final calibration (op 20) | Yes — the latent grid is itself a compact continuous model | `LatentSkyModel` already has a clean measurement→model shape; a future accepted sky-line/LSF model plugs into `.evaluate(lsf_model=...)`, which already exists as an unused parameter | Yes — wrap `oversampled_incident_sky`'s return in `AlgoResult`; no behavior change |
| **Astronomical source spectrum** | Implemented, persisted only as detections/catalog match, not as an extracted per-source spectrum Product | `detect_fiber_sources`, `fit_catalog_astrometry` | both (plain tuple/array returns) | `source_detection_catalog`, `catalog_match_table` | per-detection extracted broadband spectrum is discarded after threshold classification (only `flux[index]` scalar retained in the detections row) | `detections` array | Astrometric fit (op 13) | No | Not applicable to this refactor — source spectrum extraction as its own Product is future scientific work | Not in scope beyond wrapping in `AlgoResult` |
| **Focal-plane fiber mapping** | Implemented, **fully in-memory only** | fplane + per-IFUID fiber offsets → `focal` array; `fiber_identity` array | inline in op 10 | none — `fiber_identity`/`focal` are embedded as *components* inside several exposure-scope Products (`fiber_sky_coordinates`, `sky_fiber_mask`, etc.) but never published as their own focal-plane-mapping Product | `IFUID`/`CONTROLLER` are dropped from the compressed `fiber_identity` columns (documented gap, Evidence-Action-Flow §9) | `fiber_identity`, `focal` (both propagated everywhere) | every downstream Exposure-scope Product | No | Not applicable — this is a configuration lookup, not a fit | Not in scope; the compressed-identity gap is a **RESEARCH** item, not an I/O refactor item |
| **Astrometric mapping** | Implemented, persisted (initial + final) | header pointing fallback chain, TAN projection, catalog-match rigid fit | `parse_header_pointing`, `tan_fiber_coordinates`, `fit_catalog_astrometry` | `initial_astrometry`, `catalog_match_table`, `final_astrometry`, `fiber_sky_coordinates` | none of note | `final_ra`, `final_dec` | Sky-fiber selection is IFUSLOT-based, not sky-coordinate-based, so limited further consumption | Yes — the rigid shift/rotation fit | Not applicable — already a clean measurement chain | Yes — wrap the three astrometry functions' returns in `AlgoResult`; no behavior change |
| **Differential atmospheric refraction** | **Not currently implemented** | none | none | none | n/a | n/a | n/a | No | Not applicable | **Not in scope** — do not invent |
| **Spatial point-spread function** | **Not currently implemented** in `ExposureTask` (fiber aperture width is fixed at 5.0 pixels, not a measured PSF) | fixed `width=5.0` parameter only | `extract_fractional_aperture` (parameter, not a measurement) | none | n/a | n/a | n/a | No | Not applicable | **Not in scope** — do not invent |
| **Atmospheric transmission (telluric/extinction)** | **Not currently implemented** | none | none | none | n/a | n/a | n/a | No | Not applicable | **Not in scope** — do not invent |
| **Exposure time and observing state** | Implemented, persisted | header EXPTIME/PEXPTIME, primary/parallel classification | `classify_mode_and_effective_time` | `exposure_mode_classification`, `effective_exposure_time` | none of note | none reused elsewhere | terminal within this task | No (classification, not a fit) | Not applicable | Yes — wrap in `AlgoResult`; no behavior change |
| **Environmental and tracker state** | **Partially represented** | `scientific_metadata_from_header` extracts `rho_start`, `theta_start`, `phi_start`, `x_start`, `y_start`, ambient temp/humidity/pressure into `scientific_metadata` attached to every Product in this Exposure | `core/scientific_metadata.scientific_metadata_from_header` (called once in `ExposureTask.run`, not part of the algorithms module) | attached as `scientific_metadata` on every persisted Product (not its own Product) | none of note | `self._exposure_scientific_metadata` (instance attribute, set once) | attached to every `_request()` call via `self._exposure_scientific_metadata` | n/a | No | Not applicable | Not in scope — this is intentionally cross-cutting metadata, not a per-step algorithm |

**Summary against the requested classification:** most requested information
objects with numerical support in the repository are *already implemented and
persisted*. The two concrete gaps worth closing in this refactor are (1)
**fiber-to-fiber relative response** evidence retention (`within_amp_fiber_normalization`
is registered but never published), and (2) making **exposure illumination**
and the **twilight/calibration-source scatter fit** each go through a named
algorithm function instead of inline arithmetic. Differential refraction,
spatial PSF, and atmospheric transmission are genuinely absent and are
correctly left alone — proposing algorithms for them would violate the "do not
invent" constraint.

---

## 4. Hidden or unclear boundaries (with disposition)

| # | Issue | Where | Disposition |
|---|---|---|---|
| 1 | `within_amplifier_normalization` returns an anonymous 4-tuple `(raw_ratio, within, normalization_valid, common_twi)`; call site destructures positionally | `algorithms/exposure.py:260`, used at `tasks/exposure.py:301` | Return `AlgoResult` with named arrays `raw_ratio`, `within_amp_response`, `valid_mask`, `common_twilight` |
| 2 | `amplifier_normalization` returns anonymous 2-tuple `(factors, reference_level)` | `algorithms/exposure.py:283` | Return `AlgoResult` with `arrays={"amplifier_factors": ...}`, `scalars={"reference_level": ...}` |
| 3 | `parse_header_pointing` returns 4-tuple; `tan_fiber_coordinates` returns 3-tuple; both destructured positionally at two call sites each | `algorithms/exposure.py:294,348` | Return `AlgoResult`s (`scalars`: ra0/dec0/pa; `meta`: header_evidence; `arrays`: ra/dec/rotation) |
| 4 | `fit_catalog_astrometry` returns 3-tuple `(match_table, fit_parameters, success)` — the boolean success is easy to lose track of at the call site | `algorithms/exposure.py:383` | Return `AlgoResult` with `arrays={"matches": ...}`, `scalars={"astrometry_refined": bool(success), ...}`, `meta={"fit_parameters": ...}` |
| 5 | `select_sky_fibers` returns anonymous 4-tuple | `algorithms/exposure.py:433` | Return `AlgoResult` |
| 6 | `oversampled_incident_sky` returns anonymous 4-tuple, immediately fed into `LatentSkyModel(...)` positionally | `algorithms/exposure.py:448` | Return `AlgoResult`; `LatentSkyModel` construction becomes a thin adapter reading named arrays |
| 7 | `classify_mode_and_effective_time` returns 3-tuple including a raw `dict` of header evidence with no schema | `algorithms/exposure.py:552` | Return `AlgoResult` (`scalars`: mode/effective_seconds; `meta`: time_evidence dict, already well-named internally) |
| 8 | Twilight physical-CCD assembly/scatter/subtraction is inlined in `ExposureTask.run` and duplicates the exact two function calls `PhysicalCCDTask` already wraps, but bypasses `PhysicalCCDTask` and its publication | `tasks/exposure.py:272-282` | Split into a small algorithm-adjacent helper `fit_physical_ccd_scatter(lower, upper, ..., persist=False)` reusable by both the science path (via `PhysicalCCDTask`) and the twilight path (inline, no publication) — see §5/§6 |
| 9 | Wavelength-row validity check is 20 lines of inline boolean-array arithmetic with no named function | `tasks/exposure.py:306-327` | Extract to `validate_wavelength_rows(wavelength, extraction_shape) -> AlgoResult` in a new `algorithms/wavelength.py` (or `algorithms/exposure.py` if no split — see §5) |
| 10 | Global fiber-frame concatenation mixes normalization arithmetic (`within * amp_factor`, division) with focal-plane lookup and array concatenation in one 40-line loop | `tasks/exposure.py:379-418` | Split into two functions: `normalize_amplifier_spectrum(spectrum, variance, within, amp_factor) -> AlgoResult` (pure arithmetic, testable in isolation) and a thin loop in the task that calls it per amplifier then concatenates — concatenation itself is orchestration, not science, and can stay in the task |
| 11 | Exposure illumination has no algorithm function at all — 10 lines of inline per-amp median/ratio arithmetic | `tasks/exposure.py:564-573` | New `measure_exposure_illumination(broadband_flux, sky_mask, amp_index) -> AlgoResult` |
| 12 | Sky evaluation + subtraction is inline (`sky_model.evaluate(...)`, then a bare subtraction and residual-sigma computation) | `tasks/exposure.py:625-630` | New `predict_and_subtract_sky(sky_model, wavelength, fiber_illumination, spectrum, sky_mask) -> AlgoResult`; note a `predict_sky()` free function already exists at `algorithms/exposure.py:503` but is unused by `ExposureTask` — consolidate rather than duplicate |
| 13 | Final calibrated-state construction mixes three concerns: response division, mask-bit assembly, and unit-scale application (`FLUX_SCALE`/`VARIANCE_SCALE`) | `tasks/exposure.py:671-698` | New `apply_relative_response(sky_subtracted, spectrum_variance, valid_fraction, wavelength, fiber_illumination) -> AlgoResult`; scale constants and `CalibratedFiberState` construction stay in the task since they are storage/state-shaping, not science |
| 14 | Completion-manifest coverage matrix is 40 lines of inline bookkeeping mixed with an SQL-adjacent scan (`service.adapter.list_all()`) inside the science task | `tasks/exposure.py:724-763` | Extract the pure coverage-matrix construction (`build_completion_coverage(zipcodes, reduced, calibration, amp_results, failures) -> AlgoResult`); leave the QA-status scan as task-level orchestration since it queries the registry, which algorithms must not do (Target Architecture §7: "do not query the database") |
| 15 | `within_amp_fiber_normalization` kind is registered in `ontology/artifact_kinds.py` but has zero `_request()` call sites — a Product that exists in the ontology but can never be produced | `ontology/artifact_kinds.py:96-100` vs. `tasks/exposure.py` (absent) | Publish it once `within_amplifier_normalization` returns a named `AlgoResult` (item 1) — a small new `publish_within_amp_normalization()` helper next to the algorithm |
| 16 | `_component()`/`_mask_component()` module-level helpers in `tasks/exposure.py` are called ~40 times inline with hand-built `components={...}` dicts per Product — the mapping from `AlgoResult` fields to `LogicalComponent`s is implicit and repeated | `tasks/exposure.py:53-64` and every `_request()` call | Keep `_component`/`_mask_component` (they are already the right level of abstraction — thin, storage-neutral, no framework); add one small `publish_<information_object>()` function per Product next to its algorithm that takes the algorithm's `AlgoResult` and returns the `components=` dict, so `ExposureTask.run()` calls `self._request(components=publish_scattered_light_components(result), ...)` instead of hand-listing fields — see §5 for exact placement |
| 17 | `PhysicalCCDTask.run()`'s `_components()` static method already demonstrates exactly this "AlgoResult → components dict" helper pattern for `ccd_scattered_light_model` | `tasks/science.py:195-213` | Use this as the template; do not invent a different mechanism |
| 18 | `baseline_relative_response`'s `np.ones(...)` array is built inline in the task instead of via any function, despite being conceptually an algorithm ("provisional identity response") | `tasks/exposure.py:632` | Small function `baseline_relative_response(grid_shape) -> AlgoResult` for symmetry and testability; trivial, low priority |
| 19 | The result dict returned by `ExposureTask.run()` (14 keys) is built by hand at the very end with string keys matching (but not type-checked against) the kinds requested throughout the method | `tasks/exposure.py:775-791` | Leave as-is — this is the task's public contract and is already flat and readable; do not wrap in a new "TaskResult" type per the constraint against parallel result types, unless a future repository-wide `TaskResult` (Target Architecture §8.2) is adopted for *all* tasks, which is out of scope here |

---

## 5. Proposed algorithm-module organization

Current state: `algorithms/exposure.py` (580 lines) mixes extraction,
normalization, astrometry, sky, and response algorithms in one file;
`algorithms/physical_ccd.py` (341 lines) is already scientifically coherent
(assembly + scatter fit only) and should not be split further. The target
architecture doc (§3) proposes a much larger `algorithms/{detector,
calibration, geometry, extraction, scatter, astrometry, sky, response,
reconstruction}/` tree, but that is a repository-wide target for the *whole*
calibration + exposure system, most of which (bias/dark/flat/trace/wave) is
unaffected by this refactor. Splitting only what `ExposureTask` touches, and
only as far as current file-size conventions in this repository already go
(single-file-per-coherent-area, e.g. `algorithms/physical_ccd.py`,
`algorithms/twi.py`, `algorithms/robust.py`), gives:

| New/kept module | Scientific cohesion | Functions moved in | Task-embedded arithmetic promoted to a function here | Import/compatibility notes |
|---|---|---|---|---|
| `algorithms/extraction.py` (new) | Fractional-aperture spectral extraction, independent of what's being extracted (science or twilight) | `fractional_aperture_geometry`, `extract_fractional_aperture`, `ExtractionResult` | — | `tasks/exposure.py` and `tasks/science.py` (if it ever extracts) import from here instead of `algorithms.exposure`; `ExtractionResult` becomes an `AlgoResult` (breaking change to its shape — see §6 migration) |
| `algorithms/response.py` (new) | Fiber-to-fiber, amp-to-amp, and illumination response — the three factors that compose into `fiber_response_model` | `within_amplifier_normalization`, `amplifier_normalization`, `compact_fiber_response`, `FiberResponseModel` | `measure_exposure_illumination()` (item 11), `normalize_amplifier_spectrum()` (item 10), `baseline_relative_response()` (item 18) | `PhysicalCCDTask`/`ExposureTask` import from here; no cross-module cycles since this module only depends on `numpy`/`scipy` |
| `algorithms/astrometry.py` (new) | Pointing, TAN projection, source detection, catalog fit — all of the astrometric mapping chain | `parse_header_pointing`, `tan_fiber_coordinates`, `detect_fiber_sources`, `fit_catalog_astrometry` | — | none; already self-contained (`astropy`-only deps) |
| `algorithms/sky.py` (new) | Sky selection, oversampled latent-sky construction, and evaluation | `select_sky_fibers`, `wavelength_bin_edges`, `derive_sky_oversampling_factor`, `oversampled_incident_sky`, `LatentSkyModel`, `predict_sky`, `sky_sampling_convergence` | `predict_and_subtract_sky()` (item 12) — consolidates with the existing but currently-unused `predict_sky()` | `wavelength_bin_edges` is also used by extraction-adjacent code; keep it here since sky code is its heaviest consumer, and have `extraction.py` import it if ever needed (currently it is not) |
| `algorithms/exposure_state.py` (new, small) | Observing-mode classification and effective exposure time — genuinely its own small scientific area, currently bundled into the generic `exposure.py` grab-bag | `classify_mode_and_effective_time` | — | depends on `core/exposure_metadata.interpret_virus_exposure_header`, unchanged |
| `algorithms/completion.py` (new, small) | Pure coverage/manifest bookkeeping, not physics, but currently indistinguishable from science arithmetic because it lives inline in the task | — | `build_completion_coverage()` (item 14) | no dependency on any other `algorithms/*` module; could equally live as a `tasks/exposure.py`-local helper since it is orchestration bookkeeping rather than a scientific transform — see open question in §8 |
| `algorithms/exposure.py` (kept, shrunk) | Whatever does not cleanly fit the above — after the split, mainly the `CalibratedFiberState` dataclass and `apply_relative_response()` (item 13), since final calibration touches sky, response, and extraction outputs together and is the Exposure's own terminal synthesis step, not a sub-area's algorithm | `CalibratedFiberState`, `apply_relative_response()` (item 13), wavelength-row validation `validate_wavelength_rows()` (item 9, unless promoted to `extraction.py` — either is defensible; recommend `extraction.py` since it validates extraction-adjacent wavelength alignment) | — | this becomes the "exposure-level synthesis" module rather than a grab-bag; version constants `EXTRACTION_VERSION`, `NORMALIZATION_VERSION`, `ASTROMETRY_VERSION`, `SKY_VERSION`, `RESPONSE_VERSION` move with their respective functions |
| `algorithms/physical_ccd.py` (unchanged) | Physical-CCD assembly + scatter fit | already correct | `fit_physical_ccd_scatter()` convenience wrapper (item 8) — assemble + fit in one call, reusable by the twilight path without duplicating two call sites | `tasks/science.py` and `tasks/exposure.py` (twilight path) both import from here |

This keeps the module count small (six new files, all scientifically named,
matching the existing one-coherent-area-per-file convention already used by
`algorithms/twi.py`, `algorithms/robust.py`, `algorithms/physical_ccd.py`)
rather than one file per function. A developer looking for "the sky
algorithm" finds `algorithms/sky.py`; for "the astrometry algorithm",
`algorithms/astrometry.py`; for "the response algorithm",
`algorithms/response.py`. This directly satisfies the stated goal without
adopting the full `detector/calibration/geometry/extraction/scatter/
astrometry/sky/response/reconstruction/` package-per-area layout from the
target architecture, which would be premature for one task's worth of
algorithms and would force unrelated bias/dark/flat modules to move for no
reason connected to this refactor.

---

## 6. Target orchestration sketch

This section shows the **shape** `ExposureTask.run()` should take. It uses
real repository names throughout. It is illustrative of structure, not a
line-exact final diff — exact parameter lists will be finalized during
implementation to match the current call signatures precisely.

```python
# --- L0: discovery, calibration selection (unchanged orchestration) ---
raw_rows, zipcodes = self._discover_science_rows(exposure_id)
header, config, fplane, fiber_offsets, exposure_refs = self._resolve_configuration(raw_rows, zipcodes)
calibration, failures = self._ensure_calibrations(zipcodes, at)

# --- L1: per-amplifier detector reduction (unchanged) ---
reduced = self._reduce_all_amplifiers(zipcodes, calibration, failures)

# --- L2: per-pair physical-CCD scatter correction (unchanged, already AlgoResult) ---
physical = self._correct_all_physical_ccds(reduced, calibration, failures)

# --- L3: per-amplifier twilight normalization + science extraction ---
for identity, side, lower, upper in physical_pairs(physical):
    twilight_scatter = fit_physical_ccd_scatter(          # algorithms/physical_ccd.py
        calibration[lower]["master_twilight"], calibration[upper]["master_twilight"],
        side=side, lower_amp=..., upper_amp=..., lower_trace=..., upper_trace=...,
    )
    twilight_image = twilight_scatter.get_array("scatter_subtracted_image")

    for zipcode, trace in (lower, upper):
        science_extraction = extract_fractional_aperture(         # algorithms/extraction.py
            self._amp_from_physical(physical[side].scatter.get_array("scatter_subtracted_image"), zipcode.amp),
            self._amp_from_physical(physical[side].assembly.get_array("variance"), zipcode.amp),
            trace, pixel_mask=..., width=5.0,
        )                                                          # → AlgoResult(arrays={spectrum, variance, valid_pixel_fraction, ...})

        twilight_extraction = extract_fractional_aperture(
            self._amp_from_physical(twilight_image, zipcode.amp), ..., trace, width=5.0,
        )
        within_amp = within_amplifier_normalization(               # algorithms/response.py
            twilight_extraction.get_array("spectrum"),
        )                                                          # → AlgoResult(arrays={raw_ratio, within_amp_response, valid_mask, common_twilight})

        within_amp_artifact = self._publish_within_amp_normalization(within_amp, zipcode, parents=...)
        #   ^ NEW: closes the "registered but never published" gap (§4 item 15)

        wavelength_check = validate_wavelength_rows(                # algorithms/extraction.py
            calibration_wavelength, science_extraction.get_array("spectrum").shape,
        )                                                           # → AlgoResult(arrays={valid_rows}, scalars={excluded_count})
        # record exclusions, skip amplifier if wavelength_check fails guard (unchanged behavior)

        amp_results[zipcode.key()] = _AmpResult(
            science=science_extraction, within_amp=within_amp, wavelength_check=wavelength_check,
            twilight_level=..., parent_ids=[...],
        )

# --- L4: exposure-wide normalization ---
amp_normalization = amplifier_normalization(                        # algorithms/response.py
    [item.twilight_level for item in amp_results.values()],
)                                                                    # → AlgoResult(arrays={amplifier_factors}, scalars={reference_level})
amp_artifact = self._publish_amp_to_amp_normalization(amp_normalization, parents=...)

global_frame = self._assemble_global_fiber_frame(                   # thin orchestration loop; calls normalize_amplifier_spectrum() per amp
    amp_results, amp_normalization, fplane, fiber_offsets,
)                                                                    # → plain namedtuple/dataclass of concatenated arrays (not an AlgoResult — this is task-local state assembly, not a scientific measurement)

# --- L5: astrometry ---
initial = parse_header_pointing(header)                             # algorithms/astrometry.py → AlgoResult
initial_coords = tan_fiber_coordinates(initial, global_frame.focal)  # → AlgoResult
initial_artifact = self._publish_initial_astrometry(initial, initial_coords, parents=...)

detections = detect_fiber_sources(global_frame, threshold_sigma=...)  # → AlgoResult
detection_artifact = self._publish_source_detection_catalog(detections, parents=...)

catalog = self._query_catalog(initial.scalars["ra0"], initial.scalars["dec0"])
astrometry_fit = fit_catalog_astrometry(detections, initial, catalog)  # → AlgoResult
match_artifact, final_artifact, coordinates_artifact = self._publish_astrometry_fit(
    astrometry_fit, initial_artifact, detection_artifact, parents=...,
)

# --- L6: sky, illumination, response, calibrated state ---
sky_selection = select_sky_fibers(global_frame, source_mask=detections)   # algorithms/sky.py → AlgoResult
sky_mask_artifact = self._publish_sky_fiber_mask(sky_selection, parents=...)

illumination = measure_exposure_illumination(                              # algorithms/response.py (NEW, §4 item 11)
    sky_selection.get_array("broadband_flux"), sky_selection.get_array("mask"), global_frame.amp_index,
)                                                                           # → AlgoResult(arrays={fiber_factor, amplifier_factor})
illumination_artifact = self._publish_exposure_illumination(illumination, parents=...)

latent_sky = oversampled_incident_sky(global_frame, sky_selection, ...)   # → AlgoResult
sky_model_artifact = self._publish_sky_model(latent_sky, parents=...)


sky_subtraction = predict_and_subtract_sky(                                 # algorithms/sky.py (NEW, §4 item 12)
    latent_sky, global_frame.wavelength, illumination.get_array("fiber_factor"), global_frame.spectrum,
)                                                                           # → AlgoResult(arrays={sky_prediction, sky_subtracted}, scalars={residual_sigma})

baseline = baseline_relative_response(latent_sky.get_array("latent_wavelength").shape)  # algorithms/response.py (NEW, §4 item 18)
baseline_artifact = self._publish_baseline_response(baseline, parents=...)

response_model = compact_fiber_response(                                    # algorithms/response.py
    global_frame.wavelength, amp_results, amp_normalization, illumination,
)
response_artifact = self._publish_fiber_response_model(response_model, baseline_artifact, illumination_artifact, amp_artifact, parents=...)

calibrated = apply_relative_response(                                       # algorithms/exposure.py (kept module, §5)
    sky_subtraction.get_array("sky_subtracted"), global_frame.spectrum_variance,
    global_frame.valid_fraction, global_frame.wavelength, illumination.get_array("fiber_factor"),
)                                                                            # → AlgoResult(arrays={flux, variance, mask})
calibrated_fiber_state = CalibratedFiberState.from_algo_result(calibrated, global_frame, model_artifact_ids=(...))

# --- L7: mode, effective time, completion ---
observing_state = classify_mode_and_effective_time(header)                  # algorithms/exposure_state.py → AlgoResult
mode_artifact, effective_artifact = self._publish_observing_state(observing_state, initial_artifact, parents=...)

coverage = build_completion_coverage(                                       # algorithms/completion.py (NEW, §4 item 14)
    zipcodes, reduced, calibration, amp_results, failures, wavelength_exclusions,
)
completion_artifact = self._publish_completion_manifest(coverage, sky_model_artifact, response_artifact, effective_artifact, final_artifact)

return {
    "exposure_completion_manifest": completion_artifact,
    "initial_astrometry": initial_artifact,
    ...  # unchanged 14-key contract
    "calibrated_fiber_state": calibrated_fiber_state,
}
```

Each `_publish_<information_object>()` helper (e.g. `_publish_within_amp_normalization`,
`_publish_amp_to_amp_normalization`, `_publish_scattered_light`) is a small
method on `ExposureTask` (or a shared `_SciencePublisher` mixin method,
following the existing `PhysicalCCDTask._components()` pattern at
`tasks/science.py:195-213`) that takes an `AlgoResult` and returns the
`components=` dict for `self._request(...)`. This is the "small publication
helper" explicitly permitted by the task constraints — it does not hide the
scientific order, it only removes the repeated `_component(name, value,
units, coordinates)` boilerplate from the inline call sites.

**What this sketch preserves exactly:** every guard, failure branch, and
numerical operation identified in §1–§4; the physical-CCD pairing; the
partial-amplifier and partial-Exposure completion behavior; QA calls;
artifact parent lineage; and the returned 14-key dict plus
`calibrated_fiber_state`. **What it changes:** every algorithm now returns a
named `AlgoResult`; publication component-building is colocated with each
algorithm's result rather than hand-written per call site; the seven blocks
of inline arithmetic identified in §1/§4 become named, independently testable
functions.

---

## 7. What is saved vs. kept in memory (unchanged, made explicit)

This refactor does not change persistence decisions. The following table
restates the current (correct) policy so it is visible next to the
orchestration sketch rather than requiring a trace through the whole task:

| Persisted (Artifact/Product) | Run-local only (never persisted) |
|---|---|
| `ccd_scattered_light_model` | `scatter_subtracted_image` (science + twilight), `ReducedAmplifierState.image/variance/pixel_mask` |
| `amp_to_amp_normalization` | raw twilight extraction, `common_twi`, twilight scatter fit evidence (op 5) |
| `initial_astrometry`, `catalog_match_table`, `final_astrometry`, `fiber_sky_coordinates` | intermediate WCS objects |
| `source_detection_catalog` | per-fiber broadband array used only for thresholding |
| `sky_fiber_mask` | — |
| `sky_model` | `sky_prediction`, `sky_subtracted`, `LatentSkyModel` Python object |
| `exposure_illumination_correction` | — |
| `baseline_relative_response`, `fiber_response_model` | — |
| `exposure_mode_classification`, `effective_exposure_time` | — |
| `exposure_completion_manifest` | `failures`, `wavelength_fiber_exclusions` dicts (embedded as metadata, not separately persisted) |
| *(none — always run-local by design)* | `CalibratedFiberState` (entire object, per completion-manifest metadata `"persistent_science_intermediates": []`) |
| **Newly published by this refactor:** `within_amp_fiber_normalization` (closing §4 item 15) | raw ratio and valid-mask remain inside the `AlgoResult` only until published — after this refactor they are persisted, closing the retention gap |

No other array crosses from "run-local" to "persisted" or vice versa. The
`within_amp_fiber_normalization` publication is the one deliberate content
change in this plan, and it is additive (a Product that was always supposed
to exist, per the registered kind, starts being produced) rather than a
change to existing Product contents.

---

## 8. Staged implementation plan

Each stage should land as its own PR/commit, be independently testable
against the existing `tests/test_exposure_task.py` fixture (which asserts
exact numerical/shape outcomes), and preserve all currently-passing
assertions without modification unless a stage explicitly adds a new
assertion for newly-published data.

**Stage 1 — `AlgoResult`-ify `algorithms/exposure.py` in place (no moves, no task changes).**
Convert every function in `algorithms/exposure.py` to return `AlgoResult`
instead of a tuple/dataclass, keeping the file and function names unchanged.
Update `ExposureTask.run()` and `tests/test_exposure_algorithms.py` call
sites to read named `arrays`/`scalars` instead of positional tuple unpacking.
This is the highest-value, lowest-risk stage: it makes every call site in
`ExposureTask.run()` self-describing without moving any code or changing any
number. `ExtractionResult`, `FiberResponseModel`, and `LatentSkyModel` can
either become thin wrappers that also expose `.arrays`/`.scalars` (to avoid
breaking their existing attribute-style access at `response_model.wavelength_knots`
etc.) or be replaced outright — recommend keeping `FiberResponseModel` and
`LatentSkyModel` as-is (they are stateful models with an `.evaluate()` method,
not one-shot measurement results) and only converting the free functions that
currently return raw tuples (`within_amplifier_normalization`,
`amplifier_normalization`, `parse_header_pointing`, `tan_fiber_coordinates`,
`fit_catalog_astrometry`, `select_sky_fibers`, `oversampled_incident_sky`,
`classify_mode_and_effective_time`) plus `extract_fractional_aperture`
(replacing `ExtractionResult`, which has no methods and is a pure
measurement result — the cleanest `AlgoResult` conversion in the file).

**Stage 2 — Promote the seven inline-arithmetic blocks (§1, §4 items 8–14) to named functions**, in the same file, same task call structure. No module moves yet. Each new function gets a focused unit test mirroring its extracted arithmetic exactly (characterization tests, per Target Architecture §19's migration process: legacy arithmetic → characterization test → pure function → typed `AlgoResult`).

**Stage 3 — Publish `within_amp_fiber_normalization`** (§4 item 15, §7). Add
the `_publish_within_amp_normalization()` helper and one `_request()` call.
Add a test asserting the Product now exists with the expected components,
extending `tests/test_exposure_task.py` rather than replacing its existing
assertions.

**Stage 4 — Consolidate the twilight physical-CCD path (§4 item 8).** Add
`fit_physical_ccd_scatter()` to `algorithms/physical_ccd.py` as a thin
`assemble_physical_ccd` + `fit_gap_scattered_light` composition; use it from
both `PhysicalCCDTask` and the twilight path in `ExposureTask.run()`, with no
change to the science-path publication and no new twilight publication
(decided as a research question, not resolved in this stage — see below).

**Stage 5 — Split `algorithms/exposure.py` per §5** into `extraction.py`,
`response.py`, `astrometry.py`, `sky.py`, `exposure_state.py`, and a shrunk
`exposure.py`. Pure move + import-path updates; no logic changes. Re-run the
full test suite (`tests/test_exposure_algorithms.py`,
`tests/test_exposure_task.py`, `tests/test_exposure_metadata.py`) after every
file move.

**Stage 6 — Extract `algorithms/completion.py`'s `build_completion_coverage()`** (§4 item 14). This is the one item where the "algorithm vs. task orchestration" boundary is genuinely ambiguous (it touches the registry via `service.adapter.list_all()`), so land it last and be willing to leave the registry-scanning portion in the task while only extracting the pure coverage-matrix arithmetic.

**Stage 7 — Rewrite `ExposureTask.run()` orchestration** to the shape in §6,
using the now-available named `AlgoResult`s and publication helpers. This
stage should produce no numerical or Artifact-content diff at all — it is a
pure readability rewrite riding on top of Stages 1–6. This is also the stage
where `ExposureTask.run()`'s line count should visibly drop, since the
seven inline-arithmetic blocks are now function calls and the ~40 repeated
`_component(...)` constructions are now `publish_<thing>()` one-liners.

**Explicitly deferred / research, not part of this refactor:**
- Whether to persist the twilight/calibration-source scatter model as its own
  Product (§3, calibration-source spectrum row) — flagged as a **RESEARCH**
  item already in `ExposureTask_Evidence_Action_Flow.md`; this refactor only
  removes the code duplication, not the persistence decision.
- The compressed `fiber_identity` gap (missing `IFUID`/`CONTROLLER` columns) —
  unchanged, out of scope.
- Any application of `fiber_response_model.evaluate()` or a non-identity
  `baseline_relative_response` — explicitly "models come later."
- Any new information object (differential refraction, spatial PSF,
  atmospheric transmission) — explicitly not implemented, not invented here.

---

## 9. Tests needed to prove scientific and Artifact compatibility

1. **Golden-output regression test** (new): run
   `tests/test_exposure_task.py`'s existing fixture before and after each
   stage and assert byte-for-byte (or exact float) equality of every
   persisted component and of `calibrated_fiber_state.flux/variance/mask` —
   not just presence of keys as the current assertions do. This is the single
   most important test to add before Stage 1 begins, since it is the
   regression guard for the entire refactor.
2. **Per-function characterization tests** for each of the seven newly named
   functions from §4 items 8–14 (`fit_physical_ccd_scatter` reuse,
   `validate_wavelength_rows`, `normalize_amplifier_spectrum`,
   `measure_exposure_illumination`, `predict_and_subtract_sky`,
   `apply_relative_response`, `build_completion_coverage`), each asserting
   the extracted arithmetic reproduces the exact current inline computation
   on representative inputs (extend `tests/test_exposure_algorithms.py`).
3. **`AlgoResult` shape tests** for every converted function in
   `algorithms/exposure.py` (and its post-split successors), asserting the
   expected `arrays`/`scalars`/`meta` keys exist — this both documents the
   contract and catches accidental field drops during Stage 1.
4. **`within_amp_fiber_normalization` publication test** (new, Stage 3):
   assert the Product now exists after `ExposureTask.run()`, with
   `raw_ratio`, `normalization`, `valid_mask`, `common_twilight` components
   matching the values already computed (and previously discarded) by
   `within_amplifier_normalization`.
5. **Import-path smoke test** after Stage 5: a single test that imports every
   public name from the new modules (`extraction.py`, `response.py`,
   `astrometry.py`, `sky.py`, `exposure_state.py`, `completion.py`) to catch
   circular-import or missed-reexport errors immediately, independent of the
   full fixture test's runtime cost.
6. **Existing tests that must keep passing unmodified**, as the acceptance
   bar for "no scientific behavior changed": `tests/test_exposure_task.py`
   (both tests), `tests/test_exposure_algorithms.py`,
   `tests/test_exposure_metadata.py`, and any calibration/physical-CCD tests
   that exercise `algorithms/physical_ccd.py` or `tasks/science.py`.
7. **Artifact-kind coverage test** (new): iterate `ontology.artifact_kinds.ARTIFACT_KINDS`
   for every kind whose scope/lifecycle indicates it belongs to the Exposure
   flow (`amp_to_amp_normalization`, `within_amp_fiber_normalization`,
   `initial_astrometry`, `source_detection_catalog`, `catalog_match_table`,
   `final_astrometry`, `fiber_sky_coordinates`, `sky_fiber_mask`, `sky_model`,
   `baseline_relative_response`, `exposure_illumination_correction`,
   `fiber_response_model`, `exposure_mode_classification`,
   `effective_exposure_time`, `exposure_completion_manifest`,
   `ccd_scattered_light_model`) and assert each one is actually produced by
   the fixture run — this turns the "registered but never published" class of
   bug (§4 item 15) into a standing regression test rather than a one-time
   fix.

---

## 10. Artifact transparency near the task flow

To let a reader know what is saved without opening every information-object
implementation, add one small, repository-consistent mechanism rather than a
framework:

- A **module-level table at the top of `tasks/exposure.py`**, immediately
  below the imports, listing every kind this task can publish alongside the
  algorithm and information object that produces it:

  ```python
  # Kind                              → algorithm                                → information object
  PUBLISHED_KINDS = {
      "ccd_scattered_light_model":        "physical_ccd.fit_gap_scattered_light",
      "amp_to_amp_normalization":         "response.amplifier_normalization",
      "within_amp_fiber_normalization":   "response.within_amplifier_normalization",
      "initial_astrometry":               "astrometry.tan_fiber_coordinates",
      ...
  }
  ```

  This is data, not a runner — it does not execute anything and does not
  replace the explicit `_request()` call sites; it is a single place to look
  up "what does this task persist" and can be asserted against in the
  Artifact-kind coverage test (§9 item 7), keeping the table and the tests
  from silently drifting apart.
- Keep using `ontology/artifact_kinds.ARTIFACT_KINDS` as the authoritative
  schema for each kind's required components (already exists, already
  imported by `PhysicalCCDTask` via `kind_spec`) — the new table above is a
  *task-level index into* that registry, not a replacement for it.
- No new base class, decorator, or plugin registry. This matches the
  constraint against hiding the flow behind a generic runner.

---

## 11. Summary of what does *not* change

- No new result type; `AlgoResult` is extended in usage, not in shape (no
  field additions were found necessary during this analysis — every current
  return value fits `arrays`/`scalars`/`meta` cleanly).
- No new workflow engine, plugin registry, or generic `run_information_object()`.
- No change to calibration selection policy, physical-CCD pairing rules,
  QA thresholds, or failure/abort conditions.
- No change to any persisted Artifact's components or lineage, except the
  single additive `within_amp_fiber_normalization` publication (§7), which
  fills a pre-existing registered-but-unused kind rather than altering any
  existing Product.
- No dense array (`scatter_subtracted_image`, extracted spectra, sky
  prediction, sky-subtracted spectrum, final exposure response) becomes
  persisted; all remain run-local exactly as `exposure_completion_manifest`'s
  `persistent_science_intermediates: []` already documents.
