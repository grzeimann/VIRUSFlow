# VIRUSFlow Scientific Knowledge Specification

# Working Note: Fiber Trace Geometry and Trace Recovery

> Status: Initial implementation specification based on the current trace algorithm

This note separates two concepts that must remain distinct in VIRUSFlow:

1. **trace geometry**, which is physical detector and fiber knowledge, and
2. **trace recovery**, which is the algorithm used to measure that geometry from
   an illuminated image.

The geometry is the scientific object. The algorithm is one implementation for
recovering it.

---

# Trace Geometry

After an amplifier image has been oriented into canonical VIRUS coordinates:

- wavelength increases from blue on the left to red on the right,
- detector `x` is the dispersion direction,
- detector `y` is the cross-dispersion direction,
- fibers are ordered from bottom to top,
- fiber 1 is at the bottom,
- and the final fiber is at the top.

For almost every amplifier, the trace map contains:

```text
112 fibers
```

There is one known detector-boundary exception in which the nominal allocation is
split as:

```text
111 fibers on one amplifier
113 fibers on the neighboring amplifier
```

This exception must be represented explicitly in instrument configuration rather
than hidden behind a universal 112-fiber assumption.

The scientific trace product is a function:

```text
y_f(x)
```

for each physical fiber `f`.

It records the cross-dispersion center of that fiber at every detector column.

---

# Scientific Objective

The goal of trace recovery is to identify and model the center of every fiber
profile as a function of detector column.

For each fiber, the algorithm should provide:

- discrete measured trace positions at representative detector columns,
- a smooth model evaluated across the full detector,
- residuals between the discrete measurements and the model,
- and diagnostics sufficient to determine whether the fiber was identified
  correctly.

The goal is not merely to detect a set of peaks.

The goal is to preserve the association:

```text
physical fiber identity
        ↕
measured detector peak
```

through the full wavelength range.

---

# Trace Configuration Identity

Trace-reference configuration is keyed by four hardware components:

- IFUSLOT
- IFUID
- SPECID
- AMP

The controller does not determine the optical or detector geometry of the trace.

Therefore, unlike calibration Products that conservatively use the complete
five-part ZipCode, trace-reference configuration may be reused when the only
change is CONTROLLER.

Conceptually:

```text
Trace Configuration Identity =
    (IFUSLOT, IFUID, SPECID, AMP)
```

This is an intentional and narrowly scoped exception to full-ZipCode identity.

A measured Trace Product should still preserve the complete five-part ZipCode in
its provenance because the controller was part of the acquisition hardware.

---

# Configuration Knowledge

The trace configuration provides at least two kinds of prior knowledge:

1. the expected fiber locations,
2. the known dead-fiber state.

The current reference table uses a status column to distinguish active and dead
fibers.

Dead-fiber knowledge is not inferred anew from every flat.

It comes from configuration and is used to interpret missing peaks.

The reference trace is a prior, not an unquestioned final solution.

Its purpose is to provide:

- expected fiber count,
- expected ordering,
- approximate spacing,
- dead-fiber locations,
- and a historical geometric anchor.

---

# Choice of Illumination Product

Trace recovery requires a high-signal frame with visible fiber profiles.

The current preferred Product is the Master Flat.

The Master Twilight is also a viable input.

## Master Flat Advantages

The laser-driven light source provides:

- high signal,
- a smooth continuum,
- strong fiber profiles,
- and relatively little wavelength-dependent spectral structure.

This makes it well suited for robust cross-dispersion peak detection.

## Master Twilight Tradeoff

Twilight illumination may also provide strong fiber profiles and more
sky-representative illumination.

However, the solar spectrum introduces absorption features along the dispersion
direction.

When detector columns are combined into chunks, these features may reduce signal
uniformity or complicate the cross-dispersion profile.

The trace algorithm should therefore operate on an abstract
**trace-illumination Product**, while the Task or planning layer selects the best
available source.

---

# Current Algorithm Overview

The current implementation performs the following sequence:

1. Load a trace-reference file selected for the four-part trace identity.
2. Determine the expected number and locations of active fibers.
3. Divide the detector into broad column chunks.
4. Collapse each chunk along the dispersion direction.
5. remove broad background structure,
6. smooth the cross-dispersion profile,
7. identify local maxima,
8. select the expected number of strongest peaks,
9. sort them spatially,
10. refine their centers to subpixel precision using the unsmoothed profile,
11. insert estimated locations for configured dead fibers,
12. fit an independent robust polynomial for every fiber,
13. evaluate each polynomial across the full detector,
14. measure robust per-fiber residual scatter.

The module explicitly describes itself as a trace-detection and polynomial-modeling
implementation and exposes sampled positions and residual diagnostics in addition
to the final trace map. 

---

# Reference Selection

The current reference-file layout is:

```text
<virusconfig>/
    Fiber_Locations/
        <YYYYMMDD>/
            fiber_loc_<SPECID>_<IFUSLOT>_<IFUID>_<AMP>.txt
```

The controller is intentionally absent from the filename.

Identifiers are normalized to three-character values before matching.

The current implementation finds all matching reference files and selects the
one with the smallest absolute date difference from the observation. 

## Important Assumption

The closest-in-time configuration is assumed to be the best available prior.

A future implementation should consider whether references should instead obey
an explicit validity interval or prefer the most recent reference not later than
the observation.

Absolute date proximity can otherwise select a configuration created after the
observation.

---

# Chunked Measurement

The detector is divided into:

```text
40 column chunks
```

For a 1032-column amplifier, this corresponds to roughly 25–26 columns per
chunk.

Each chunk is collapsed using the median along `x`, producing one
cross-dispersion profile as a function of `y`.

This is a central robustness decision.

It provides enough columns to:

- raise the fiber-profile signal-to-noise ratio,
- suppress individual bad pixels,
- suppress cosmic rays,
- reduce sensitivity to narrow spectral features,
- and avoid relying on a single detector column.

At the same time, the chunks remain narrow enough to sample trace curvature
throughout the detector.

The current implementation uses 40 chunks, records the mean `x` coordinate of
each, and median-combines every chunk into a one-dimensional profile.


---

# Profile Preprocessing

The collapsed profile is preprocessed before peak detection.

The current sequence is:

1. Estimate a low envelope with a fifth-percentile filter.
2. Use a wide 201-pixel window.
3. Fit a second-order polynomial to the percentile background.
4. Subtract the broad background model.
5. Smooth the residual profile with a Gaussian kernel of sigma 1.5 pixels.

The purpose is to remove broad illumination structure while preserving the
narrow fiber peaks.

The percentile background is intended to follow the inter-fiber floor rather
than the fiber peaks.

The Gaussian smoothing suppresses noise and small pixel defects before local
maximum detection. 

---

# Peak Detection

Local maxima are detected through derivative sign changes.

A candidate peak occurs where the profile difference changes from positive to
negative.

The algorithm then ranks candidate peaks by their preprocessed amplitudes.

If at least the expected number of active fibers is found, it retains the
strongest `N` candidates, where `N` is the configured number of non-dead fibers.

The retained peaks are then sorted by detector `y`.

This creates the working fiber association:

```text
lowest selected peak  → lowest active fiber
...
highest selected peak → highest active fiber
```

The algorithm therefore does not perform an independent nearest-neighbor match
against every configured reference position.

Instead, it relies primarily on:

- the expected count of active fibers,
- monotonic spatial ordering,
- and the assumption that the strongest selected peaks correspond one-to-one
  with the active physical fibers.

The relevant selection and assignment behavior is implemented directly in the
chunk loop.

---

# Subpixel Peak Localization

Peak candidates are selected from the background-subtracted, smoothed profile.

Their final positions are measured from the original unsmoothed chunk profile.

For every candidate at integer position `y`, the algorithm evaluates the
neighboring samples at:

```text
y - 1
y
y + 1
```

and uses a parabolic interpolation around the maximum.

This provides a subpixel estimate of the fiber center while avoiding the
centroid bias that could be introduced by the pre-detection smoothing.

The interpolation assumes:

- the selected sample is a local maximum,
- both adjacent pixels are valid,
- and the peak is locally approximated by a parabola.

The code protects against a zero interpolation denominator by returning a
non-finite result for that peak. 

---

# Dead-Fiber Handling

The expected active-fiber count comes from the trace-reference status values.

When the algorithm finds exactly that many peaks:

- the measured positions are assigned to configured active fibers,
- and each configured dead fiber is assigned an estimated position.

The dead-fiber position is reconstructed from:

- the measured position of the nearest active fiber in fiber-index space,
- plus the difference between their configured reference positions.

This preserves a complete geometric trace map even when a fiber produces no
illumination peak.

The resulting position is geometric knowledge only.

It must not imply that the dead fiber has recovered or contains usable signal.

---

# Full-Peak Case

If the number of measured peaks equals the total number of reference fibers, the
current implementation assigns all measured peaks directly.

This provides a path for cases where a fiber marked dead nevertheless produces a
detectable peak.

However, it also means the configuration status is bypassed in that chunk.

A future implementation should record this explicitly as evidence that the
configured dead-fiber state may be stale or that an extra peak was detected.

---

# Fiber-Count Exception

The current code contains a hard-coded special case for:

```text
SPECID = 504
IFUID  = 018
AMP    = RU
```

For this case, the final trace and reference arrays are shortened by one fiber.

This appears to encode one side of the known 111/113 fiber allocation exception.

A future implementation should move this knowledge into instrument configuration
and represent the expected fiber membership explicitly for both affected
amplifiers.

Fiber-count exceptions should not remain hidden in algorithm conditionals.

---

# Trace Modeling

Each fiber is modeled independently as a function of detector `x`.

The current implementation fits a fourth-degree polynomial.

Before fitting:

- non-finite samples are excluded,
- samples with positions less than or equal to zero are excluded,
- and the polynomial degree is reduced if too few measurements are available.

Detector `x` is normalized to `[-1, 1]` before fitting to avoid numerical
instability across a 0–1031 pixel range.

The preferred estimator is:

```text
HuberRegressor + polynomial features
```

with:

```text
degree = 4
epsilon = 1.35
alpha = 0
```

If that fit fails, the algorithm falls back to `numpy.polyfit`.

If both fail, the predicted trace is non-finite.

This robust fitting strategy is intended to suppress incorrectly measured chunk
positions without allowing isolated failures to distort the full trace.

---

# Why the Algorithm Does Not Strictly Match the Reference

A strict configuration-to-peak matcher works well when:

- the reference is recent,
- detector placement is stable,
- trace shifts are small,
- and the expected configuration remains trustworthy.

VIRUS operates over long time ranges with:

- gradual trace drift,
- hardware resets,
- detector or optical changes,
- occasional larger shifts,
- and configuration references that may span long intervals.

Under these conditions, a matcher that tightly anchors every peak to one
historical position may confidently assign the wrong fiber.

The current algorithm therefore uses the reference more conservatively:

- to define the expected population,
- preserve dead-fiber positions,
- and preserve ordering,

while allowing the observed peak pattern to determine the current absolute
positions.

This sacrifices some explicit per-peak identity matching in favor of robustness
to long-term movement.

---

# Core Fiber-Association Assumption

The most important hidden assumption is:

> **The active fiber peaks remain monotonically ordered and no unmodeled peak is
> strong enough to enter the selected population while displacing a real fiber.**

Selecting the strongest expected number of peaks is robust when:

- every active fiber remains detectable,
- false peaks are weaker,
- adjacent fibers do not merge,
- and the peak order is preserved.

It can fail when:

- one active fiber becomes unusually faint,
- a defect produces a strong false peak,
- two fibers merge,
- a fiber profile becomes double-peaked,
- the expected dead-fiber configuration is wrong,
- or the detected peak count differs from both the active and total fiber count.

Any replacement algorithm must address this association problem explicitly.

---

# Per-Fiber Trace Residual

After the full polynomial trace is evaluated, the model is sampled at the chunk
positions.

For each fiber:

```text
residual =
    measured chunk position
    - polynomial trace at that chunk
```

The current QA metric is the MAD-based robust standard deviation of those
residuals.

This is published as:

```text
per_fiber_trace_residual_rms
```

despite being a robust MAD-derived scatter rather than a classical root-mean-square.

The current implementation also publishes:

- full fiber trace map,
- sampled trace positions,
- trace sample columns,
- per-fiber robust residual scatter.

fileciteturn25file0L419-L456

---

# Interpretation of the Residual Metric

The per-fiber residual scatter is one of the most important trace-success
diagnostics.

A low value indicates that:

- chunk measurements are mutually consistent,
- the trace is smooth,
- and the polynomial adequately represents the measured geometry.

A high value may indicate:

- incorrect peak association,
- noisy or weak fiber profiles,
- an inappropriate polynomial model,
- isolated chunk failures,
- detector defects,
- or a real trace shape not captured by the chosen polynomial.

However, a low residual alone does not prove correct fiber identity.

A consistently misidentified sequence of peaks may still produce a smooth
polynomial with low residuals.

Trace QA therefore requires both:

1. model-fit consistency,
2. identity and ordering consistency.

---

# Required Success Diagnostics

At minimum, a Trace Product should expose:

- per-fiber robust trace residual scatter,
- number of valid chunk measurements per fiber,
- fraction of expected peaks recovered per chunk,
- measured peak count per chunk,
- expected active-fiber count,
- expected total-fiber count,
- dead-fiber assignments,
- rejected or non-finite chunk positions,
- trace displacement relative to the reference,
- local fiber spacing,
- minimum separation between adjacent traces,
- polynomial degree actually used,
- configuration reference and date.

Useful amplifier-level summaries include:

- median per-fiber residual,
- upper-percentile residual,
- number of fibers exceeding QA limits,
- largest reference displacement,
- number of chunks with count mismatch,
- and minimum trace separation.

---

# Failure Behavior in the Current Code

The current implementation contains several fallback patterns that should be
made more explicit in future code.

## Peak-Count Mismatch

When the measured count matches neither:

- the expected number of active fibers,
- nor the total reference length,

the chunk's trace array can remain at its initialized zero values.

Those zeros are later excluded from polynomial fitting.

This avoids immediate failure but can conceal why measurements were unavailable.

A new implementation should return structured diagnostics for each failed chunk.

## Broad Exception Handling

Several helper functions catch broad exceptions and return:

- zero arrays,
- original input profiles,
- non-finite predictions,
- or missing diagnostics.

These fallbacks improve operational resilience but can suppress the original
failure mode.

VIRUSFlow should preserve recoverable behavior while returning explicit messages
and quality status.

## Nearest-Date Reference

The current absolute-nearest-date selection has no explicit validity interval.

## Hard-Coded Fiber Exception

The 111/113 exception is embedded in code rather than configuration.

---

# Required Algorithm Contract

A new implementation must reproduce the following scientific behavior even if
its internal method differs.

## Input Contract

The algorithm requires:

- a high-signal, canonically oriented amplifier image,
- IFUSLOT,
- IFUID,
- SPECID,
- AMP,
- observation date,
- trace-reference configuration,
- and complete acquisition provenance, including CONTROLLER.

## Geometry Contract

The algorithm must assume and validate:

- blue-to-red increasing `x`,
- bottom-to-top fiber ordering,
- configured amplifier fiber membership,
- explicit handling of 111/112/113-fiber cases.

## Configuration Contract

The algorithm must obtain:

- expected fiber ordering,
- dead-fiber knowledge,
- approximate trace locations,
- reference validity information.

Configuration identity uses:

```text
(IFUSLOT, IFUID, SPECID, AMP)
```

not CONTROLLER.

## Measurement Contract

The algorithm must:

- combine enough neighboring columns to obtain robust profiles,
- sample the trace across the full dispersion range,
- suppress broad background structure,
- detect candidate fiber peaks,
- measure centers at subpixel precision,
- prevent silent fiber-order swaps,
- and retain discrete measurements.

## Modeling Contract

The algorithm must:

- fit a smooth trace for each fiber,
- resist isolated bad measurements,
- report insufficient-data cases,
- preserve the actual model basis and parameters,
- and evaluate the model over all detector columns.

## QA Contract

The algorithm must return enough evidence to determine:

- whether the correct number of fibers was found,
- whether fiber identities were preserved,
- whether the trace model fits the measurements,
- whether adjacent traces remain correctly ordered,
- and whether the result is suitable for extraction.

---

# Product Contract

The Trace Product should contain at least:

## Arrays

```text
fiber_trace_map
sampled_trace_positions
trace_sample_columns
per_fiber_trace_residual_rms
per_fiber_valid_sample_count
```

## Metadata

```text
trace_map_shape
expected_fiber_count
expected_active_fiber_count
trace_configuration_identity
trace_reference_file
trace_reference_date
trace_reference_validity
illumination_product_kind
chunk_count
profile_preprocessing
peak_selection_method
subpixel_method
trace_model
controller
algorithm_version
```

## Optional Diagnostic Arrays

```text
detected_peak_positions_by_chunk
detected_peak_amplitudes_by_chunk
peak_count_by_chunk
reference_displacement_by_fiber
adjacent_fiber_spacing
trace_fit_residuals
```

---

# Separation of Responsibilities

## Configuration

Owns:

- physical fiber membership,
- dead-fiber state,
- approximate reference locations,
- known fiber-count exceptions,
- validity intervals.

## Algorithm

Owns:

- profile construction,
- peak detection,
- fiber association,
- subpixel localization,
- smooth trace fitting,
- measurement diagnostics.

## Task

Owns:

- selecting the trace-illumination Product,
- materializing inputs,
- supplying identity and configuration,
- constructing the Trace Product request,
- invoking QA.

## QA

Owns:

- thresholds,
- pass/warn/fail decisions,
- amplifier-level summaries,
- comparison with historical behavior.

## Analytics

Owns:

- long-term drift,
- reference aging,
- trace stability,
- configuration validity,
- comparison across hardware lineages.

---

# Initial Implementation Decisions

Until a demonstrably better method is validated:

- Use canonical oriented detector coordinates.
- Use Master Flat as the preferred trace-illumination Product.
- Permit Master Twilight as an alternative input.
- Key trace configuration by IFUSLOT, IFUID, SPECID, and AMP.
- Preserve CONTROLLER in measured Product provenance.
- Use configured dead-fiber knowledge.
- Measure trace positions in broad column chunks.
- Use robust median collapse within each chunk.
- Remove broad profile background before peak detection.
- Smooth only for detection, not for final subpixel localization.
- Select the expected active-fiber population.
- Preserve bottom-to-top fiber ordering.
- Reconstruct geometric positions for configured dead fibers.
- Fit each fiber independently with a robust smooth model.
- Publish discrete samples as well as the final trace.
- Use per-fiber robust residual scatter as a primary QA metric.
- Move fiber-count exceptions from hard-coded logic into configuration.
- Never treat low residual scatter alone as proof of correct fiber identity.

---

# Repository Goals

VIRUSFlow should:

- preserve trace geometry independently from any one recovery algorithm,
- define trace configuration using the four physical components that determine
  geometry,
- retain complete five-part ZipCode provenance for every measured Trace Product,
- replace hard-coded fiber-count exceptions with explicit configuration,
- measure how traces drift over days, months, and hardware events,
- determine the useful validity interval of each trace reference,
- quantify when a historical reference becomes dangerous for strict matching,
- distinguish poor polynomial fits from incorrect fiber association,
- develop diagnostics that can detect smooth but misidentified traces,
- compare Master Flat and Master Twilight trace performance,
- optimize chunk width, preprocessing scale, and polynomial complexity,
- identify dead-fiber configuration changes from accumulated evidence,
- and eventually determine whether trace references should remain static files
  or become time-aware geometric models learned from the repository.
