# VIRUSFlow Scientific Knowledge Specification

# Working Note: Exposure, Observation, Shot, and Dither Identity

> Status: Initial scientific and architectural specification

This note defines how VIRUSFlow should represent individual VIRUS exposures,
three-position dither sequences, parallel observations, and larger scientific
groupings.

The central principle is:

> **The exposure is the atomic scientific measurement. A shot or observation is
> a grouping relationship among exposures, not a reason to erase their
> individual physical states.**

VIRUSFlow should preserve each exposure independently and allow observations,
shots, dither sets, and larger science collections to be assembled through
explicit relationships and query rules.

---

# Common VIRUS Observing Modes

VIRUS is commonly used in two modes:

```text
Single-Exposure / Parallel Mode
Three-Exposure Dithered Mode
```

These modes differ in:

- observing intent;
- exposure timing;
- focal-plane coverage;
- grouping semantics;
- and available calibration information.

---

# Atomic Entity: Exposure

An exposure is one continuous detector integration associated with one shutter
operation.

Each exposure has its own:

- raw detector frames;
- effective integration time;
- sky spectrum;
- seeing;
- transparency;
- mirror illumination;
- guider state;
- telescope track position;
- astrometric state;
- and detector or calibration conditions.

These properties should never be replaced automatically by one average value
for a multi-exposure observation.

A suitable identity is:

```text
Exposure(
    date,
    observation_id,
    exposure_number,
    instrument
)
```

with links to all amplifier and fiber Products produced from that exposure.

---

# Dithered VIRUS Mode

The native fiber coverage of one VIRUS IFU is approximately:

```text
one third of the IFU footprint
```

A standard VIRUS observation therefore uses three exposures at prescribed
relative offsets.

Together, the three dithers provide approximately:

```text
95 percent spatial coverage
```

of the IFU footprint.

The exact effective coverage depends on:

- realized guider offsets;
- seeing;
- fiber geometry;
- source location;
- astrometric accuracy;
- and masking.

---

# Guider-Driven Dithering

VIRUS does not dither through an internal IFU mechanism.

Instead, the telescope guider is moved through a prescribed three-position
pattern.

The nominal offsets are repeatable and reliable, but the realized positions are
not exactly identical from observation to observation.

Possible causes include:

- guider execution error;
- telescope response;
- slow evolution in the implemented pattern;
- astrometric fitting uncertainty;
- and environmental or tracking effects.

The nominal dither pattern should therefore be treated as:

```text
commanded configuration knowledge
```

while the measured relative offsets should be treated as:

```text
exposure-level inferred knowledge
```

---

# Nominal Dither Pattern

Historically, when an observation contains exactly three exposures, the
reduction assumes the standard dither pattern.

There is no sufficiently reliable header keyword that explicitly states:

```text
this observation is a standard three-position VIRUS dither
```

The current operational inference is therefore:

```text
number of exposures == 3
    →
assume standard dither sequence
```

This is practical but should remain an explicit inference rather than an
unexamined fact.

---

# Measured Dither Positions

The preferred long-term model is:

```text
Realized Position_e
    =
Nominal Dither Position_e
    +
Astrometric Residual_e
```

where `e` identifies one exposure.

When the astrometry is sufficiently constrained, VIRUSFlow should use measured
relative offsets rather than only the nominal pattern.

The nominal pattern remains the prior and fallback.

This allows the repository to study:

- repeatability of each dither position;
- long-term drift in the guider pattern;
- dependence on track position;
- and effects on final spatial coverage.

---

# Observation and Shot

The terms:

```text
observation
shot
```

have often been used interchangeably and somewhat loosely.

In the most common VIRUS usage, both refer to the three exposures that make up
one standard dither sequence.

For VIRUSFlow, the terms should be formalized.

A useful initial convention is:

```text
Observation
    =
operational grouping recorded by the observing system

Shot
    =
scientific grouping representing one intended VIRUS field visit
```

For a standard primary VIRUS observation:

```text
one Observation
    ≈
one Shot
    ≈
three dithered Exposures
```

The relationship should remain explicit because not every operational
observation necessarily has three exposures.

---

# Observation Is a Grouping, Not a Collapsed Measurement

The three exposures in a dither sequence are close in time and share:

- observing intent;
- approximate field center;
- instrument configuration;
- target identity;
- and commanded dither pattern.

They do not share exactly the same:

- sky;
- seeing;
- transparency;
- mirror illumination;
- astrometric solution;
- effective exposure time;
- detector state;
- or source position on the fiber pattern.

Therefore, VIRUSFlow should not automatically collapse them into one combined
Product.

Instead:

```text
Observation
    contains
Exposure 1
Exposure 2
Exposure 3
```

and each exposure retains its own calibrated Products and metadata.

---

# Parallel VIRUS Mode

The HET focal plane allows VIRUS, LRS2, and HPF to be available at the same
time.

When LRS2 or HPF is the primary instrument and exposes for more than
approximately five minutes, VIRUS can take a simultaneous exposure.

These are referred to as:

```text
parallel VIRUS observations
```

Characteristics include:

- VIRUS is not the primary instrument;
- the exposure duration is determined by LRS2 or HPF;
- the observation is normally undithered;
- the VIRUS object label is `parallel`;
- and only one native sparse-coverage exposure may be obtained.

Parallel exposures are scientifically valuable but should not be assigned the
coverage expectations of a standard three-dither shot.

---

# Determining Primary vs. Parallel Mode

The current operational distinction is based on the VIRUS object label:

```text
OBJECT == "parallel"
    →
VIRUS parallel mode
```

Any other object label is treated as VIRUS primary mode.

This rule should be preserved as explicit configuration or classification logic.

The raw label and the inferred observing mode should both be retained.

A future implementation may add supporting evidence from:

- active primary instrument;
- exposure-control metadata;
- observing logs;
- and simultaneous instrument records.

---

# HET Shutter

The HET shutter is a rotating device.

Approximately:

```text
one third of the rotating assembly is open
two thirds is closed
```

The shutter takes roughly:

```text
7.5 seconds to open
7.5 seconds to close
```

Because the detector receives light during portions of the shutter motion, the
effective integration is not simply the requested stationary-open duration.

The resulting integration correction is highly repeatable but depends on the
actual shutter motion.

Operationally, the net exposure contribution is approximately:

```text
7.5 to 8 seconds
```

The exact convention should be verified empirically and against shutter
telemetry where available.

---

# Primary-Mode Exposure Time

When VIRUS is the primary instrument:

```text
EXPTIME
```

is treated as the appropriate effective integration time.

This keyword is believed to represent the complete shutter-aware exposure
process sufficiently well for routine VIRUS calibration.

The repository should verify this assumption using primary observations.

---

# Parallel-Mode Exposure-Time Gotcha

When VIRUS is operating in parallel mode, the header keyword:

```text
EXPTIME
```

is inaccurate.

The origin of the discrepancy is currently unclear.

The operationally preferred estimate is:

```text
effective_exposure_time
    =
PEXPTIME - 8 seconds
```

where `PEXPTIME` is closer to the total requested-plus-shutter interval.

This should be represented as a named, testable policy:

```text
ParallelExposureTimePolicy
```

rather than hidden in an algorithm.

---

# Exposure-Time Decision Rule

The initial VIRUSFlow rule should be:

```text
if observing_mode == primary:
    effective_exposure_time = EXPTIME

if observing_mode == parallel:
    effective_exposure_time = PEXPTIME - 8 seconds
```

The Product should preserve:

- `EXPTIME`;
- `PEXPTIME`;
- object label;
- inferred observing mode;
- applied correction;
- final effective exposure time;
- and policy version.

No original header value should be overwritten or discarded.

---

# Exposure-Time Validation

The proposed validation is:

1. identify a large sample of primary VIRUS observations;
2. compare `EXPTIME`, `PEXPTIME`, requested time, and available shutter
   information;
3. determine whether `PEXPTIME - 8` reproduces primary-mode `EXPTIME`;
4. repeat the comparison for parallel observations;
5. test stability with time and shutter state.

If the approximation is correct, the primary observations provide an empirical
calibration for the parallel-mode rule.

The repository should also determine whether the appropriate subtraction is:

```text
7.5 seconds
8.0 seconds
```

or a more detailed shutter-dependent value.

---

# Physical Differences Among Dither Exposures

Even within one three-dither observation, each exposure has an independent
physical state.

## Sky

The incident sky spectrum and brightness can change between exposures.

## Seeing

Image quality can evolve over the dither sequence.

## Transparency

Clouds or atmospheric transparency may change between exposures.

## Mirror Illumination

The illuminated primary-mirror pattern changes with track position and time.

## Astrometry

The realized guider offset differs slightly from the nominal dither position.

## Throughput

The net response includes exposure-specific transparency, mirror illumination,
and source-capture effects.

These quantities should remain exposure scoped.

---

# Benefits of Grouping Exposures

Although exposures must remain separate, their relationships are scientifically
valuable.

A three-dither group helps constrain:

- realized dither geometry;
- source location;
- spatial coverage;
- relative throughput;
- seeing changes;
- source reconstruction;
- artifact rejection;
- repeated spectral detections;
- and sky variability.

The grouping supplies evidence without requiring immediate coaddition.

---

# Single Exposure vs. Dither Set

A single exposure measures approximately one-third spatial coverage.

A dither set samples almost the complete IFU footprint.

These are different observation Products even before any spectra are combined.

Useful coverage Products include:

```text
nominal_fiber_footprint
realized_exposure_footprint
dither_set_coverage_map
coverage_fraction
```

Coverage should be computed from measured astrometry where possible.

---

# Larger Scientific Groupings

Many science programs combine:

- multiple shots;
- repeated fields;
- visits on different nights;
- different tracks;
- and many individual exposures.

VIRUSFlow does not need to natively collapse these into one monolithic Product.

Instead, it should support query-defined collections such as:

```text
all exposures within a sky region
all exposures belonging to one target
all exposures with acceptable seeing
all exposures in one dither set
all exposures from multiple observations
```

This allows scientific objects to be constructed according to the needs of each
experiment.

---

# Observation Set

A useful higher-level entity is:

```text
ObservationSet
```

An ObservationSet is a deliberate scientific collection of exposures or shots.

Membership may be defined by:

- target;
- sky region;
- proposal;
- date range;
- dither relationship;
- wavelength coverage;
- quality criteria;
- or an explicit saved query.

An ObservationSet should not require all members to share identical observing
conditions.

It should preserve the reason and rule by which members were selected.

---

# Recommended Entity Relationships

```text
Program
    contains
Observation

Observation
    contains
Exposure

Shot
    groups
one or more Exposures

DitherSet
    groups
nominally three Exposures

ObservationSet
    selects
Exposures and/or Shots
```

In the common primary VIRUS case:

```text
Observation
    =
Shot
    =
DitherSet
```

but the data model should not require these identities universally.

---

# Dither Assignment

Each exposure in a standard dither set should retain:

```text
dither_index
nominal_offset_x
nominal_offset_y
measured_offset_x
measured_offset_y
offset_uncertainty
assignment_method
```

The assignment method may be:

```text
header
sequence inference
nominal three-exposure rule
astrometric measurement
manual correction
```

This makes the inference reproducible.

---

# Parallel Exposure Grouping

Parallel VIRUS exposures may still belong to:

- an operational observation;
- a simultaneous LRS2 or HPF exposure;
- a target field;
- or a larger ObservationSet.

However, they should normally have:

```text
dither_mode = none
coverage_mode = sparse
VIRUS_primary = false
```

A sequence of parallel exposures should not automatically be interpreted as a
standard dither set merely because three files exist.

The object label and observing context must be considered.

---

# Product Scope

## Exposure Scope

Owns:

- detector data;
- effective exposure time;
- sky;
- seeing;
- transparency;
- mirror illumination;
- astrometry;
- throughput;
- and per-fiber Products.

## Dither-Set Scope

Owns:

- member exposures;
- nominal and measured offsets;
- coverage map;
- dither consistency;
- and relative-registration diagnostics.

## Observation or Shot Scope

Owns:

- target intent;
- operational grouping;
- observing mode;
- member exposures;
- and shared proposal metadata.

## Observation-Set Scope

Owns:

- scientific selection rule;
- membership;
- combined-analysis intent;
- and aggregate QA summaries.

---

# Exposure Product Contract

Recommended metadata:

```text
date
observation_id
exposure_number
object
observing_mode
primary_instrument
VIRUS_primary
EXPTIME
PEXPTIME
effective_exposure_time
exposure_time_policy
shutter_correction
track_position
seeing
transparency
mirror_illumination
astrometry_product
sky_product
```

Recommended classification fields:

```text
is_parallel
is_dithered
dither_index
dither_assignment_method
```

---

# Dither-Set Product Contract

Recommended metadata:

```text
dither_set_id
observation_id
member_exposure_ids
nominal_pattern
pattern_version
assignment_method
astrometric_refinement_used
```

Recommended arrays or table:

```text
exposure_id
dither_index
nominal_dx
nominal_dy
measured_dx
measured_dy
dx_uncertainty
dy_uncertainty
```

Recommended derived Products:

```text
coverage_map
coverage_fraction
registration_residuals
```

---

# Required QA

## Exposure-Time QA

Evaluate:

- consistency of `EXPTIME` and `PEXPTIME`;
- primary vs. parallel classification;
- stability of the minus-eight-second rule;
- negative or implausible corrected times;
- and historical changes in header behavior.

## Dither QA

Evaluate:

- number of member exposures;
- order of exposures;
- deviation from nominal offsets;
- astrometric uncertainty;
- achieved coverage;
- and guider-pattern evolution.

## Exposure-State QA

Compare among dither members:

- seeing;
- transparency;
- mirror illumination;
- sky brightness;
- effective throughput;
- and astrometric quality.

Large differences should not invalidate the group, but should remain visible to
downstream analyses.

---

# Important Assumptions

The initial model assumes:

- a standard primary VIRUS shot normally contains three exposures;
- three exposures normally correspond to the standard dither sequence;
- the guider pattern is repeatable;
- realized offsets can be refined astrometrically;
- parallel VIRUS observations are identified by `OBJECT = parallel`;
- primary-mode `EXPTIME` is accurate;
- parallel-mode `PEXPTIME - 8` is a useful effective-time approximation;
- and exposures should remain scientifically independent until explicitly
  combined.

Each assumption should be testable from accumulated data.

---

# Initial Implementation Decisions

- Make Exposure the atomic measurement entity.
- Preserve Observation and Shot as grouping entities.
- Represent DitherSet explicitly rather than inferring it repeatedly downstream.
- Use the nominal three-position pattern as a prior.
- Refine dither offsets with exposure astrometry when available.
- Preserve all three exposures separately.
- Do not average seeing, sky, transparency, illumination, or throughput into the
  exposure Products.
- Classify `OBJECT = parallel` as parallel VIRUS mode.
- Use `EXPTIME` for primary VIRUS exposures.
- Initially use `PEXPTIME - 8 seconds` for parallel exposures.
- Preserve both original time keywords and the applied policy.
- Create a validation study for the shutter-time rule.
- Do not require VIRUSFlow to coadd exposures natively.
- Support query-defined ObservationSets for scientific combination.
- Preserve grouping provenance and selection rules.

---

# Open Questions

- What is the authoritative nominal dither pattern and its validity history?
- How much has the guider pattern evolved with time?
- What are the distributions of measured dither residuals?
- Should dither assignment use observation logs in addition to exposure count?
- Are there three-exposure parallel sequences that could be misclassified?
- Is the effective shutter correction 7.5 or 8.0 seconds?
- Does the shutter correction vary with time or motion state?
- Why is `EXPTIME` inaccurate in parallel mode?
- Can shutter telemetry provide a direct exposure integral?
- When should a three-exposure sequence be rejected as an invalid DitherSet?
- How should incomplete two-exposure dither sequences be represented?
- Should a Shot always correspond to one operational Observation?
- Which physical quantities are most useful for joint inference across dither
  members?
- How should coverage be calculated when one exposure or several fibers are
  missing?

---

# Repository Goals

VIRUSFlow should:

- preserve every exposure as an independent physical measurement;
- distinguish primary, parallel, single-exposure, and dithered observing modes;
- represent observations, shots, and dither sets through explicit relationships;
- verify the realized guider dither pattern over the lifetime of VIRUS;
- calculate spatial coverage from measured rather than only nominal positions;
- validate and version the parallel exposure-time correction;
- connect exposure timing to shutter physics and telemetry;
- retain per-exposure sky, seeing, transparency, mirror illumination, and
  throughput;
- support scientific grouping without forced coaddition;
- allow arbitrary ObservationSets to be built through reproducible queries;
- determine which exposure relationships improve calibration and inference;
- and make the distinction between operational grouping and scientific
  measurement explicit throughout the repository.
