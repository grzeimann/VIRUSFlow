# VIRUSFlow Calibration Cadence and `master_sci` Reimplementation

## Purpose

Implement calibration cadence from the scientific purpose of each master product rather than from a generic fixed-window policy.

This is a new implementation request. Do not restore the deleted `master_sci` implementation wholesale from Git history. Reintroduce `master_sci` cleanly through the current Phase 11 architecture and use this work to revisit calibration grouping, identity, validity, and selection.

Preserve the authoritative execution path:

```text
ReductionGraph.plan
    → schedule
    → PlanningExecutor
    → Task
    → algorithm
    → DefaultPublicationService
    → ArtifactService
```

Do not reintroduce legacy planning, publication, or dense-intermediate paths.

---

## 1. General cadence principles

Separate three concepts:

1. **Computation identity**  
   The effective raw inputs, ZIP code, algorithm version, parameters, and relevant configuration that determine the numerical product.

2. **Calibration grouping policy**  
   The scientific rule that decides which raw exposures belong together.

3. **Applicability or validity**  
   The observations, dates, temperatures, or conditions for which a computed calibration may be selected.

Nominal validity windows must not create duplicate computations when the effective inputs and configuration are identical.

Distinct raw input sets must remain distinct.

The planner must resolve precise timestamps and raw identities before deduplicating targets.

All cadence rules must be configurable, documented, and recorded in provenance.

---

## 2. `master_bias`

### Scientific purpose

`master_bias` tracks the electronic structure of each amplifier and is also a useful nightly diagnostic.

### Default grouping

- Group by amplifier ZIP code.
- Use a nightly or rolling 24-hour period.
- Preserve precise timestamps.
- Build one master for each effective 24-hour group.
- Deduplicate requests that resolve to the same raw bias frames.

### Applicability

The master is primarily applicable to the corresponding night or 24-hour period.

Record the temporal center and span of the contributing frames.

---

## 3. `master_dark`

### Scientific purpose

Dark structure is expected to vary more slowly than bias and does not require nightly reconstruction.

### Default grouping

- Group by amplifier ZIP code.
- Use a longer configurable interval.
- Initial default: one month.
- Permit a weekly policy through configuration for testing or known unstable periods.
- Deduplicate groups with identical effective raw inputs.

### Applicability

Record the temporal span and center of the contributing frames.

Selection should prefer a valid nearby dark model without rebuilding it for every night.

Do not assume dark stability indefinitely; retain time-based validity and QA history.

---

## 4. `master_twilight`

### Scientific purpose

Twilight products provide a calibration that can be accumulated over a moderate period without requiring nightly reconstruction.

### Default grouping

- Group by amplifier ZIP code.
- Use a weekly interval.
- Preserve exact frame membership and timestamps.
- Deduplicate identical effective input sets.

### Applicability

Record the contributing date range and use the nearest scientifically valid weekly product.

---

## 5. `master_ldls`

### Scientific purpose

LDLS observations constrain the trace and allow trace behavior to be associated with ambient temperature.

They should represent isolated calibration observations rather than broad weekly collections.

### Default grouping

- Group by amplifier ZIP code.
- Group LDLS exposures occurring within a three-hour interval.
- Require at least three exposures in the group.
- Do not merge unrelated LDLS observations merely because they fall on the same date.
- Preserve the observation grouping and exact raw membership.

### Temperature metadata

For every LDLS group, record:

- Ambient temperature for each contributing exposure when available
- Mean, median, minimum, maximum, and spread
- Temporal center and span
- Number of exposures
- Missing-temperature status

The resulting trace model must retain provenance to the LDLS group and its temperature metadata.

This creates the foundation for later trace selection or interpolation as a function of ambient temperature. Do not implement temperature interpolation in this request unless the current architecture already supports it cleanly.

---

## 6. Mercury and cadmium lamp masters

### Scientific purpose

Mercury and cadmium lamps provide complementary wavelength-calibration information. They are taken in separate observations but often consecutively.

### Canonical products

Support distinct canonical products:

```text
master_hg
master_cd
```

Do not collapse the raw Hg and Cd observations into one indistinguishable master payload.

### Default grouping

For each lamp kind:

- Group by amplifier ZIP code.
- Group exposures occurring within a three-hour interval.
- Preserve exact raw membership.
- Require the repository's existing minimum exposure rule where one already exists; otherwise make the minimum configurable and document the default.
- Do not merge unrelated observations from the same date.

### Pairing for wavelength calibration

For downstream wavelength calibration:

- Pair compatible `master_hg` and `master_cd` groups whose temporal centers are within three hours.
- Preserve the identity and provenance of both masters.
- Do not require both lamp types to share the same observation identifier.
- If multiple candidates exist, use a deterministic, documented nearest-time selection rule.
- Record the time separation between the paired masters.
- Fail clearly or mark the wavelength target unresolved when a required pair cannot be selected.

If the current code uses `master_arc`, either:

- redefine it explicitly as a composed product referencing `master_hg` and `master_cd`, or
- remove it from the canonical model if it is only an obsolete alias.

Do not duplicate the underlying lamp arrays unnecessarily.

---

## 7. `master_sci`

### Scientific purpose

Reintroduce `master_sci` as a canonical scientific calibration product.

It is not a temporary reduced-science image.

`master_sci` combines suitable science exposures to achieve enough signal-to-noise to measure low-level structure in the amplifier and extracted spectra. It supports construction of the two-dimensional fiber–wavelength mask.

The two-dimensional fiber–wavelength mask and its scientific purpose must be documented in the architecture and calibration notes.

### Eligible exposures

A science exposure is eligible only when:

```text
exposure time > 300 seconds
```

Use the exact exposure duration from raw metadata.

Make the threshold configurable, but use 300 seconds as the default lower bound. Clarify that exactly 300 seconds is excluded.

### Grouping policy

Do not use a simple weekly cadence.

Science exposures often accumulate during the dark time of a lunar month. Group eligible exposures over a longer dark-time or lunation-oriented interval so sufficient signal-to-noise can accumulate.

Implement the grouping policy as follows:

- Group by amplifier ZIP code.
- Select only eligible science exposures longer than 300 seconds.
- Group by a configurable monthly or dark-time observing interval.
- Preserve exact raw membership.
- Record lunar or observing-block metadata when available.
- Avoid combining across arbitrarily long periods merely to reach a frame count.
- Deduplicate requests with identical effective raw inputs.

The repository may not currently contain a lunar-phase service. Do not introduce a fragile external dependency solely for this feature.

Preferred initial implementation:

- Use an explicit configurable date interval or named observing block.
- Support a calendar-month fallback.
- Design the policy interface so a future dark-time or lunation selector can be added without changing task identity or artifact contracts.

### Sufficiency criterion

The group must contain enough data to measure the intended low-level structure.

Do not invent a fixed exposure count as the permanent scientific rule.

Implement a configurable sufficiency policy that can use:

- Minimum number of eligible exposures
- Minimum total exposure time
- Measured aggregate signal-to-noise or robust illumination statistic
- A combination of these criteria

If the repository does not yet have a scientifically validated S/N threshold, expose the threshold in configuration and report the measured values without pretending the default is final.

An insufficient group must not silently publish a valid `master_sci`.

It should either:

- remain unresolved,
- fail with a clear scientific insufficiency reason, or
- publish only an explicitly marked candidate/insufficient analysis product if the lifecycle supports that distinction.

### Product and downstream use

The `master_sci` artifact should contain the scientifically required aggregate representation and metadata needed to construct or validate the two-dimensional fiber–wavelength mask.

It must include provenance for:

- Eligible science exposures
- Exposure times
- Total exposure time
- Observation dates or observing block
- ZIP code
- Combination estimator
- QA and sufficiency metrics
- Algorithm and configuration versions

Use the current robust master-frame combination implementation unless a separate estimator is scientifically justified.

Large arrays should follow the current `float32` persistence convention.

---

## 8. Planner and artifact behavior

The planner must:

- Resolve precise raw inputs before task deduplication.
- Produce one task for one scientifically distinct effective calibration set.
- Avoid nominal-window task inflation.
- Preserve genuinely distinct groups.
- Make grouping decisions inspectable before execution.
- Report why a frame was included or excluded.
- Report why two requests collapsed or remained separate.

Artifact identity must include:

```text
calibration kind
ZIP code
effective raw parent identities
algorithm version
task version
relevant parameters
configuration references
```

Validity or applicability metadata must not independently force a duplicate numerical revision.

---

## 9. Cadence inspection report

Add or extend a supported plan-inspection report that shows, for every calibration target:

- Calibration kind
- ZIP code
- Group identifier
- Precise start and end timestamps
- Temporal center
- Number of raw exposures
- Raw exposure identities
- Exposure-time statistics
- Temperature statistics where relevant
- Paired lamp identity and time separation where relevant
- Sufficiency status for `master_sci`
- Computation identity
- Applicability or validity metadata
- Deduplication decision
- Downstream requesters

This report should make the scientific cadence policy review possible without executing the graph.

---

## 10. Tests

Add tests for:

### General grouping

- Precise timestamp boundaries
- Fractional-second preservation
- Identical effective raw sets collapsing
- Distinct raw sets remaining separate
- Validity metadata not duplicating numerical products
- Deterministic grouping and selection

### Bias

- One nightly or 24-hour group
- Boundary behavior across nights
- Correct ZIP separation

### Dark

- Monthly default grouping
- Configurable weekly grouping
- Nearest valid selection
- No nightly duplication

### Twilight

- Weekly grouping
- Correct boundary behavior
- Exact input provenance

### LDLS

- Three-hour grouping
- Minimum of three exposures
- Separate isolated observations
- Temperature metadata
- No same-date overmerging

### Hg and Cd

- Separate `master_hg` and `master_cd` products
- Three-hour grouping
- Deterministic nearest-time pairing
- Correct failure when no valid pair exists
- Preserved provenance for both lamps

### `master_sci`

- Canonical registration and publication
- Exposure time strictly greater than 300 seconds
- Exclusion of short exposures
- Monthly or configured observing-block grouping
- Sufficiency policy
- No publication when insufficient
- Exact parent provenance
- Construction or support of the two-dimensional fiber–wavelength mask
- `float32` large-array storage
- Full loading and QA behavior

### Regression

- No return of the 48/28 task inflation
- Current canonical execution path only
- No legacy publication path
- Full suite and architecture gate

---

## 11. Documentation

Document:

- The scientific purpose of every master calibration
- Default cadence or grouping rule
- Configurable parameters
- Artifact identity versus applicability
- LDLS temperature association
- Hg/Cd pairing
- `master_sci` exposure eligibility and sufficiency
- The two-dimensional fiber–wavelength mask
- How to inspect cadence decisions before execution

Update the README or getting-started guide only where necessary. Put detailed scientific cadence documentation in the calibration or architecture notes.

---

## 12. Implementation sequence

1. Inspect the current canonical calibration kinds and planner after Phase 11.
2. Design the grouping-policy interface around effective raw inputs.
3. Implement and test bias, dark, twilight, LDLS, Hg, and Cd grouping.
4. Reintroduce `master_sci` through the canonical architecture.
5. Implement `master_sci` eligibility and sufficiency policy.
6. Add cadence inspection reporting.
7. Run focused grouping and planner tests.
8. Run a limited real-data plan inspection without execution.
9. Confirm expected task counts and groups.
10. Run a small calibration execution.
11. Verify products, provenance, storage, and scientific behavior.
12. Run the full suite, architecture gate, documentation checks, and `git diff --check`.

---

## 13. Required final report

Report:

1. Final canonical master products
2. Grouping policy for each product
3. Configurable cadence parameters and defaults
4. Task counts before and after
5. Example resolved groups from real data
6. Deduplication behavior
7. `master_sci` schema and lifecycle
8. `master_sci` sufficiency behavior
9. Two-dimensional fiber–wavelength mask support
10. Hg/Cd pairing behavior
11. LDLS temperature metadata
12. Tests and checks run
13. Scientific assumptions that remain configurable or unresolved
14. Any real-data grouping that could not be validated

Do not restore legacy `master_sci` code paths merely because they existed previously. Reimplement the capability through the cleaned Phase 11 architecture.
