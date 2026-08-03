# Moving Fiber-Response Construction into Calibration Processing

> Status: Proposed plan. No code has been changed. Companion to
> [`ExposureTask_IO_Refactor_Plan.md`](ExposureTask_IO_Refactor_Plan.md), which
> this plan partially supersedes for the twilight/response portion of
> `ExposureTask` (see the note at the top of that document).

## 0. Fact-check carried over from the prior document

`ExposureTask.run()` currently performs a **duplicate** physical-CCD
scatter-light fit against `master_twilight` (`tasks/exposure.py:272-282`) —
`assemble_physical_ccd` + `fit_gap_scattered_light`, the same functions
`PhysicalCCDTask` already runs for the science frame, called a second time
purely so a scatter-subtracted twilight image can be extracted. This is real
code, not a misreading. §6 below removes it: twilight/LDLS/science extraction
at calibration time is a plain `extract_fractional_aperture` call with no
scatter-light step, matching the calibration-flow diagram that was specified
(no scatter-correction stage between "load image" and "extract spectrum").

## 1. Repository-supported conclusions (no new mechanism needed)

These are established by reading the current code, not proposed:

1. **A derived, multi-parent, per-amplifier calibration Task already exists
   twice** — `ExtractedMasterSciSpectrumTask` and `FiberWavelengthSpectralMaskTask`
   (`virusflow/tasks/calibs.py:322,381`). Both are direct structural templates
   for the new Tasks in §3: resolve required + optional upstream kinds via
   `_dependency()`, call a pure algorithm, publish via `_publish()`.
2. **`master_sci` extraction already happens at calibration time.**
   `extract_master_sci_spectrum()` (`algorithms/master_sci_spectrum.py`) wraps
   `extract_fractional_aperture` and is already published as
   `extracted_master_sci_spectrum`. Only twilight and LDLS extraction tasks are
   missing — no new extraction algorithm needs to be invented, only
   parameterized/reused.
3. **`build_model_spectra()` (`algorithms/utils/masks.py:113`) is the existing
   "sort by wavelength → bin → robust `biweight_location` per bin → interpolate
   back" primitive.** It already implements the sort/bin/biweight/mean-wavelength/
   interpolate steps (1–6) of the specified algorithm and is reused elsewhere
   (`build_master_sci_spectral_mask`). Steps 8 and 10's continuum fits
   (`get_continuum`, `nbins=5` and `nbins=25`) are the same operation at
   different bin counts — this is a parameterization of `build_model_spectra`,
   not a new algorithm.
4. **`within_amplifier_normalization`, `amplifier_normalization`, and
   `compact_fiber_response`** (`algorithms/exposure.py:75,260,283`) are pure
   functions with no I/O; they move unchanged in signature-shape (see §6) into
   the new calibration Tasks.
5. **Artifact identity/applicability separation, `select_best`, and
   `parent_groups` already do everything the plan needs for grouping and
   selection.** No new grouping or selection mechanism is required for the
   *per-amplifier* stage — it is the same `exact_parent_group`/`latest_valid`
   edge pattern used by `master_sci → master_sci_spectrum → master_sci_mask`
   (`planning/defaults.py:163`).
6. **`PhysicalScope.INSTRUMENT_EPOCH` is already the repository's convention
   for a calibration Product that is neither per-amplifier nor
   exposure-bound.** `baseline_relative_response` is already published at this
   scope (`ontology/artifact_kinds.py`). This resolves where the group-wide
   response Product should live (§4).
7. **`mad_std` is already the repository's robust-QA statistic**
   (`algorithms/utils/masks.py`, used in `make_spectral_mask`) — the requested
   master-science QA (`mad_std(science_residual, ignore_nan=True, axis=1)`)
   reuses this convention directly, no new statistic.
8. **No twilight-reference/lookup configuration exists anywhere in
   `virusflow/config/`.** `ConfigurationService.resolve_trace_reference()`
   (`config/service.py:67`) is the shape a future `resolve_twilight_reference()`
   would follow — correctly out of scope per the instruction not to build it
   now.

## 2. Resolved open question: the cross-amplifier fan-in mechanism

**Finding.** `ReductionGraph.plan()`'s generic derived-node branch
(`planning/graph.py`, `if node.cadence is None and has_up:`) only ever
resolves targets **within one `scope_key(scope)`** — i.e. one zipcode/amplifier
at a time — picking the nearest-in-time single candidate for each additional
upstream kind. The only existing cross-kind *fan-in* is the hard-coded
`master_arc` branch, which pairs exactly two kinds (`master_hg`, `master_cd`)
but still **within the same scope** (one amplifier's Hg paired with that same
amplifier's Cd). **There is no existing precedent in the planner for
combining artifacts across *different* scopes (different amplifiers) into one
Product.** This is a genuine gap, not a place where an existing mechanism was
missed.

**Resolution (repository-consistent, minimal new surface).** Add one more
hard-coded branch to `ReductionGraph.plan()`, following the exact shape of the
`master_arc` branch, for `node.kind == "amplifier_response_normalization"`:

- Instead of iterating `for scope in scopes` and resolving within each scope
  (as every other branch does), this branch iterates over **all**
  `AmplifierFiberResponseTask` targets emitted so far (`available[("amplifier_fiber_response", *)]`
  across every `scope_key`), and clusters them by nearest-center timestamp
  using the *same* clustering primitive `pair_lamp_groups` already uses
  (nearest-center matching within a configured tolerance) — generalized from
  pairwise to N-way by connected-component grouping over the pairwise nearest
  relationship, or more simply: bucket by each candidate's `master_twilight`
  parent-group `computation_id`'s cadence window (all amplifier-response
  targets whose `master_twilight` parent group shares the same
  `applicability` window are, by construction, one coherent night's build —
  this reuses the *existing* twilight cadence grouping instead of inventing a
  new clustering rule).
- This keeps the "identity vs. applicability" separation intact: the resulting
  group's identity is `parent_groups=tuple(("amplifier_fiber_response", g.group_id) for g in cluster)`
  (the same tuple-of-pairs shape every other derived Target already uses,
  just with N entries instead of 1–2), and applicability is inherited from the
  member `applicability` windows, matching `CalibrationGroup.applicability`
  semantics used everywhere else.
- This is one new ~30-line branch in `graph.py`, not a new planning framework,
  and it reuses `CalibrationGroup`, `Target`, `parent_groups`, and
  `emit()`/`already_has_inputs()` verbatim.

**Why not a new `scope_mode`.** `TaskSpec.scope_mode` (`"per_zipcode"`,
`"per_exposure"`, `"global"`) is a per-node label consumed only informationally
today — `plan()`'s branching is driven by `node.cadence`/`has_up`/`node.kind`,
not `scope_mode`. Adding a fourth `scope_mode` value without also adding the
branch above would do nothing; the branch above is the actual mechanism.
Marking the new node `scope_mode="global"` for documentation purposes is
harmless but not load-bearing.

## 3. New calibration Tasks

All four follow `_CanonicalTask` (`tasks/calibs.py:55`) exactly as
`ExtractedMasterSciSpectrumTask`/`FiberWavelengthSpectralMaskTask` do.

| Task | Scope | Depends on | Publishes | Algorithm |
|---|---|---|---|---|
| `TwilightSpectrumExtractionTask` | per-amplifier | `master_twilight`, `trace_map` | `extracted_master_twilight_spectrum` (new kind, identical shape to `extracted_master_sci_spectrum`) | `extract_master_spectrum(image, trace, aperture_width=5.0)` — generalized from `extract_master_sci_spectrum` (rename `algorithms/master_sci_spectrum.py` → `algorithms/master_spectrum.py`, parameterize `kind`) |
| `LdlsSpectrumExtractionTask` | per-amplifier | `master_ldls`, `trace_map` | `extracted_master_ldls_spectrum` (new kind, identical shape) | same shared function |
| *(reused, unchanged)* `ExtractedMasterSciSpectrumTask` | per-amplifier | `master_sci`, `trace_map` | `extracted_master_sci_spectrum` | unchanged |
| `AmplifierFiberResponseTask` | per-amplifier | `extracted_master_twilight_spectrum`, `extracted_master_ldls_spectrum` (required); `extracted_master_sci_spectrum` (optional, QA-only), `wavelength_map` (required) | `within_amp_fiber_normalization` (extended schema, §4) | `fit_within_amplifier_response()` — the 11-step algorithm, moved verbatim (§5) |
| `AmplifierResponseNormalizationTask` | instrument-epoch (all amplifiers in one coherent build) | all sibling `within_amp_fiber_normalization` Products from the cluster resolved in §2 | `amp_to_amp_normalization` (rescoped, §4), `fiber_response_model` (rescoped, §4) | `amplifier_normalization()` + `compact_fiber_response()`, unchanged |

`AmplifierFiberResponseTask`'s optional `extracted_master_sci_spectrum`
dependency follows `FiberWavelengthSpectralMaskTask`'s documented rule:
resolved only via `_dependency()` (the planned parent-group edge), never via
an ambient `_resolve_artifact()`/`select_best()` lookup — for the same reason
already recorded in that task's comment (an ambient lookup could change
between planning and execution, making the plan nondeterministic). If no
`master_sci` extraction is planned for this build, the response is still
built and the QA summary records "unvalidated" (§5, master-science QA).

`AmplifierResponseNormalizationTask` resolves its N amplifier-response
parents the same way `master_arc` resolves its two lamp parents: by iterating
`target.parent_groups` set by the §2 graph branch, not by an ambient query —
identity stays exact-parent-group, matching the rule above.

## 4. Artifact kinds — extend existing kinds, no new ontology

**Per-amplifier Product: extend `within_amp_fiber_normalization`.** It is
already registered (`ontology/artifact_kinds.py`, `PhysicalScope.FIBER`) but
never published — this plan finally publishes it, with its required
components extended from `("raw_ratio", "normalization", "valid_mask", "common_twilight")`
to also carry the full result set requested:

```python
"within_amp_fiber_normalization": _spec(
    "within_amp_fiber_normalization", PhysicalScope.FIBER, Unit.DIMENSIONLESS.value,
    CoordinateConvention.FIBER_BY_DISPERSION_PIXEL,
    required=(
        "ftf",                       # final within-amplifier response (was "normalization")
        "ftf_ldls",                  # ftfflt: LDLS-derived fine structure
        "twilight_broad_correction", # z (5-bin continuum)
        "twilight_residual_correction",  # zr (25-bin continuum)
        "wavelength", "fiber_identity", "valid_mask",
        "twilight_reference_mode",   # "lookup_reference" | "available_fiber_average"
        "amplifier_twilight_level",  # scalar/array feeding amp-to-amp normalization
    ),
    optional=(
        "twilight_reference_identity",   # lookup path/identity when used
        "science_residual_per_fiber",    # mad_std QA summary; absent => unvalidated
    ),
),
```

This is additive to an already-empty, already-registered kind — not a new
kind and not a break to any consumer, since nothing publishes or reads it
today.

**Group-wide Products: rescope `amp_to_amp_normalization` and
`fiber_response_model` from `PhysicalScope.EXPOSURE` to
`PhysicalScope.INSTRUMENT_EPOCH`**, matching `baseline_relative_response`'s
existing scope choice (§1.6). `Scope(zipcode=None, physical_scope=INSTRUMENT_EPOCH)`
replaces `Scope(zipcode=None, exposure_id=..., physical_scope=EXPOSURE)`. Their
required components are unchanged in kind but the identity they carry changes:
`amp_to_amp_normalization.amplifier_identity` continues to record which
amplifiers contributed (unchanged field), and `_publish()`'s existing
`calibration_group_id`/`calibration_group` metadata convention
(`tasks/calibs.py:104`) carries the coherent-build identity — no new identity
field is needed on the `Scope` or kind spec itself; this is the same
mechanism every other calibration Task already uses.

This is a **breaking rescope** for the two Products' `Scope` shape (dropping
`exposure_id`), which is why §7 lists `observation.py` and
`verify_steps_8_10.py` as required consumer changes rather than optional
follow-ups.

**No new Artifact kinds are introduced anywhere in this plan.**
`extracted_master_twilight_spectrum`/`extracted_master_ldls_spectrum` are the
only additions, and they are exact structural copies of the existing
`extracted_master_sci_spectrum` kind (same scope, same required components),
not new schema shapes.

## 5. The response algorithm, moved unchanged

New module `algorithms/fiber_response.py` (new file, following the
one-coherent-area-per-file convention already used by `algorithms/twi.py`,
`algorithms/physical_ccd.py`). One function,
`fit_within_amplifier_response(fltspec, twispec, scispec, wave_all, *, twilight_reference=None) -> AlgoResult`,
implementing the eleven steps exactly as specified, built on:

- `build_model_spectra(wavelengths, values, nbins=3000)` (existing, reused
  unmodified) for steps 1–6, called three times (science, LDLS, twilight) —
  or once per source since the primitive already handles flatten+sort+bin+biweight+interpolate.
- The **same** primitive at `nbins=5` for step 8's `get_continuum(ftftwi/ftfflt)`
  and at `nbins=25` for step 10's `get_continuum(ratio)` — `get_continuum` is
  not a new function, it is `build_model_spectra` used as a continuum fit
  rather than a common-spectrum fit; a thin same-file wrapper
  `get_continuum(x, y, nbins)` can alias it for readability without
  duplicating logic.
- Step 8's `twilight_reference` parameter: when provided (a wavelength/flux
  pair from the future lookup mechanism, out of scope here — §1.8), it
  replaces the *newly calculated* common twilight spectrum `T(wave_all)` in
  steps 6–10; when absent, `T(wave_all)` is the robust available-fiber average
  computed in steps 1–6 as today. The result's `meta` records
  `"twilight_reference_mode": "lookup_reference" if twilight_reference is not None else "available_fiber_average"`
  and, when used, `"twilight_reference_identity"` — directly populating the
  two new optional/required components in §4.
- Master-science QA, computed but never fed back into the fit:
  `science_residual = ftfsci / ftf - 1.0; science_residual_per_fiber = mad_std(science_residual, ignore_nan=True, axis=1)`,
  reusing the same `astropy.stats.mad_std` import already used by
  `make_spectral_mask`. `ftfsci` and `S(wave_all)` are computed whenever
  `scispec` is provided; when it is `None` (optional dependency absent),
  `science_residual_per_fiber` is omitted from `arrays` entirely rather than
  filled with NaN, giving `AmplifierFiberResponseTask`'s publication step a
  clean absent/present signal for the "unvalidated" QA state via
  `evaluate_qa()` (`tasks/base.py:212`) — no new QA-status vocabulary, reusing
  the existing `evaluate_and_save`/`should_block` pipeline with a rule keyed
  on `science_residual_per_fiber` presence and magnitude.

`within_amplifier_normalization()` and `amplifier_normalization()`
(`algorithms/exposure.py:260,283`) are retired from `algorithms/exposure.py`
once `AmplifierFiberResponseTask`/`AmplifierResponseNormalizationTask` exist;
`compact_fiber_response()` moves alongside them into `algorithms/fiber_response.py`
unmodified.

## 6. Changes to `ExposureTask`

Remove entirely:

- `tasks/exposure.py:272-282` (twilight physical-CCD assembly + scatter fit —
  eliminated, not moved; §0)
- `tasks/exposure.py:296-303`'s twilight extraction + `within_amplifier_normalization`
  call
- `tasks/exposure.py:342-368`'s `amplifier_normalization` call and
  `amp_to_amp_normalization` publication
- `master_twilight` from `CALIBRATION_KINDS`'s per-amplifier gate (twilight is
  no longer loaded by `ExposureTask` at all — only its calibration-time
  *derivative*, `fiber_response_model`, is)

Add: `fiber_response_model` to `_ensure_calibrations()`'s per-amplifier gate,
resolved via `service.select_best(kind="fiber_response_model", scope=Scope(zipcode=None, physical_scope=INSTRUMENT_EPOCH), at_time=exposure_mid_time, policy="latest_valid")`
— the same `select_best` applicability mechanism every other calibration kind
already uses, just against the new scope from §4. `compact_fiber_response()`'s
call site (`tasks/exposure.py:645` per the companion doc's §2 op 19) is
likewise removed — `ExposureTask` now *selects* the published
`fiber_response_model`, it does not build one.

## 7. Required consumer updates

- **`virusflow/tasks/observation.py:~152`** — `service.select_best(kind="fiber_response_model", scope=Scope(zipcode=None, exposure_id=exposure_id, physical_scope=EXPOSURE), ...)`
  must change to the `INSTRUMENT_EPOCH` scope from §4, selecting by
  `at_time` (the exposure's own timestamp) rather than `exposure_id` —
  identical in spirit to how every calibration-cadence Product is already
  selected relative to an exposure elsewhere in the codebase (e.g.
  `_resolve_artifact`, `tasks/base.py:80`).
- **`virusflow/cli/verify_steps_8_10.py`** — `expected["fiber_response_model"] = 3`
  (currently one-per-exposure in the verification fixture) must be
  recalculated against calibration-build cadence instead of exposure count;
  the exact expected count is a fixture-specific number that should be
  re-derived from the test data's twilight/LDLS/master_sci cadence, not
  guessed here.
- Any test asserting `amp_to_amp_normalization`/`fiber_response_model`
  `Scope.exposure_id` is set must be updated for the `INSTRUMENT_EPOCH`
  rescope (§4).

## 8. What is explicitly not done here

- No twilight-reference-lookup Artifact or `ConfigurationService` method is
  built — only the pass-through parameter and provenance fields in §5/§4, per
  the explicit instruction.
- No change to the eleven-step algorithm's numerical behavior — `get_continuum`
  is a naming/wrapper choice over the existing `build_model_spectra`, not a
  reimplementation.
- No new workflow framework, no new `Scope` fields, no new selection policy
  beyond the one new `ReductionGraph.plan()` branch in §2 (which is the one
  genuinely new piece of mechanism this plan requires, and is scoped as
  narrowly as the existing `master_arc` special case it mirrors).

## 9. Staged implementation order

1. Generalize `algorithms/master_sci_spectrum.py` into `algorithms/master_spectrum.py`
   (parameterize `kind`); add `extracted_master_twilight_spectrum`/
   `extracted_master_ldls_spectrum` kinds and their two extraction Tasks
   (structural copies of `ExtractedMasterSciSpectrumTask`).
2. Write `algorithms/fiber_response.py` (`fit_within_amplifier_response`,
   `get_continuum` wrapper, science-QA calculation) with unit tests
   characterizing it against the current `within_amplifier_normalization`
   output where inputs overlap (twilight-only case) before extending to the
   full LDLS+twilight+science algorithm.
3. Extend the `within_amp_fiber_normalization` kind spec (§4); add
   `AmplifierFiberResponseTask`.
4. Add the §2 `ReductionGraph.plan()` branch and `AmplifierResponseNormalizationTask`;
   rescope `amp_to_amp_normalization`/`fiber_response_model` to
   `INSTRUMENT_EPOCH`.
5. Wire the four new nodes into `default_calibration_graph()` with
   `exact_parent_group`/`latest_valid` edges mirroring
   `master_sci → master_sci_spectrum → master_sci_mask`.
6. Update `ExposureTask` (§6) and consumers (§7); remove the retired
   functions from `algorithms/exposure.py`.
7. Regression-test against `tests/test_exposure_task.py` and any calibration
   graph-planning tests, expecting `ExposureTask`'s twilight-scatter and
   within-amp/amp-to-amp Products to disappear from its per-run output and
   `fiber_response_model` selection to succeed against the new scope.
