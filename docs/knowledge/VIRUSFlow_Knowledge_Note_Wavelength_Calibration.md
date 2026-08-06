# VIRUSFlow Scientific Knowledge Specification

# Working Note: Wavelength Geometry, Arc-Line Identification, and Observation-Frame Corrections

> Status: Initial implementation specification based on the current wavelength module

This note separates three related but distinct concepts:

1. **instrument wavelength geometry**, the mapping from detector position to physical wavelength;
2. **comparison-lamp recovery**, the algorithm used to measure that mapping;
3. **observation-frame correction**, the transformation needed to interpret a science spectrum in a barycentric or other velocity frame.

The comparison lamps constrain the instrument. The barycentric correction
describes the motion of the observer during a science exposure. They must remain
separate Products and provenance layers.

---

# Scientific Object

For each fiber, the wavelength solution is a mapping:

```text
x → λ
```

where:

- `x` is detector column in the canonically oriented amplifier image;
- `λ` is physical wavelength;
- and the fiber trace supplies the corresponding detector row `y_f(x)`.

The full amplifier wavelength Product can therefore be understood as the
wavelength surface sampled along all fiber traces:

```text
λ_f(x) = Λ(x, y_f(x))
```

The scientific object is not merely an independent one-dimensional polynomial
for each fiber.

It is a smooth two-dimensional spectrograph geometry whose sampled values form
the fiber wavelength map.

---

# Physical Interpretation

The wavelength solution is governed primarily by:

- spectrograph optics,
- grating geometry,
- camera geometry,
- detector placement,
- amplifier location on the detector,
- fiber position within the spectrograph,
- focus,
- and environmental state.

The exact detector column of an arc line varies:

- from amplifier to amplifier,
- across fibers within an amplifier,
- with spectrograph state,
- and with time.

Therefore, the line-identification algorithm should not depend on one fixed
expected detector column for each wavelength.

The current approach instead identifies the internally consistent set of peaks
whose ordering and spacing can be represented by a smooth, low-order dispersion
model.

---

# Hardware and Product Identity

The reusable optical wavelength geometry is primarily determined by:

```text
IFUID
SPECID
AMP
```

and by the physical fiber identity within that configuration.

IFUSLOT should not ordinarily change the internal spectrograph dispersion
geometry, while CONTROLLER should not change the optical wavelength mapping.

However, the conservative measured Wavelength Product should preserve the
complete five-part ZipCode because:

- the Product was measured in that hardware configuration;
- detector readout behavior can affect line measurement;
- maintenance events may coincide with geometry changes;
- and the repository should not silently erase acquisition provenance.

As with trace geometry, the repository may eventually distinguish:

```text
configuration identity
```

from:

```text
measured Product identity
```

The exact reusable configuration key should be validated empirically rather than
assumed solely from the present filename conventions.

---

# Comparison-Lamp Construction

VIRUS uses mercury and cadmium comparison lamps.

The upstream comparison Product should be constructed in two stages:

```text
Master Hg = robust average of Hg exposures
Master Cd = robust average of Cd exposures
```

followed by:

```text
Master Comparison = Master Hg + Master Cd
```

Averaging by lamp kind before summing is important because it:

- prevents the number of exposures of one lamp from changing its relative
  contribution unintentionally;
- permits lamp-specific QA;
- preserves separate provenance for Hg and Cd;
- and produces one combined spectrum containing the required line set.

The wavelength algorithm should consume the combined Master Comparison Product,
but the repository should retain the two component masters.

---

# Reference Arc Lines

The current implementation uses nine laboratory wavelengths:

```text
3610.508 Å
3650.153 Å
4046.565 Å
4358.335 Å
4678.149 Å
4799.912 Å
4916.068 Å
5085.822 Å
5460.750 Å
```

These nine lines span most of the VIRUS detector wavelength range and provide
the anchors for the dispersion model.

The line list is configuration knowledge.

It should include, where available:

- lamp species,
- laboratory wavelength,
- wavelength convention,
- expected relative behavior,
- blend status,
- and version or source.

---

# Extracted Master-Comparison Spectra

The wavelength module accepts either:

- previously extracted comparison-lamp spectra, or
- a Master Comparison image plus the fiber trace map.

When given the image, the current implementation extracts comparison spectra
around the trace using an aperture width parameter.

This creates an important dependency:

```text
Trace Product
    →
Comparison-Lamp Extraction
    →
Wavelength Product
```

Trace errors can shift or distort the extracted arc peaks and therefore affect
line centroids and wavelength residuals.

The trace Product used for extraction must be explicit provenance.

---

# Intended Arc-Identification Principle

The intended strategy is:

1. identify a larger set of the brightest candidate peaks;
2. assume the nine required lamp lines occur within that candidate population;
3. test possible associations;
4. find the association that produces the most physically plausible smooth
   dispersion relation;
5. refine the solution using all identified lines.

This avoids assuming fixed detector columns.

It instead relies on the much safer physical assumptions that:

- wavelength increases monotonically with detector column;
- the dispersion relation is smooth;
- and the same ordered line list must be explainable by a low-order polynomial.

---

# Exact Current Arc-Identification Mechanism

The present code differs slightly from the simple description of testing
`15 choose 9` associations.

Its exact procedure is:

1. Detect candidate peaks in one extracted arc spectrum.
2. Suppress candidates lying within 10 pixels of a stronger candidate.
3. Retain at most 15 candidate peaks.
4. Select the six brightest retained peaks.
5. Sort those six by detector column.
6. Test every ordered combination of six wavelengths drawn from the nine-line
   reference list.
7. Fit a second-order polynomial from the six selected peak positions to each
   candidate six-line combination.
8. Select the seed model with the smallest wavelength residual.
9. Use that seed model to match the full candidate-peak list to the complete
   nine-line list within a 12-pixel tolerance.
10. Refit the matched lines using a fourth-order polynomial.
11. Match the peaks to the full line list again.
12. Report the final line matches and wavelength RMS.

There are:

```text
9 choose 6 = 84
```

seed line combinations.

Thus, the 15 peaks define the candidate pool, but the six brightest peaks define
the initial combinatorial search.

The final model may recover up to all nine reference lines.

This is computationally efficient and has apparently been operationally robust,
but it should be documented accurately because its failure modes differ from a
literal nine-of-fifteen search.

---

# Why the Combinatorial Seed Works

The algorithm does not need to know where any one line should land.

Instead, it tests whether a collection of bright peaks has the relative ordering
and spacing expected from a smooth wavelength mapping.

This is robust to:

- amplifier-dependent dispersion,
- shifts in the detector position of the spectrum,
- fiber-dependent line positions,
- environmental motion,
- and long-term changes that make a fixed-column lookup dangerous.

The algorithm uses physical smoothness as the identification constraint.

---

# Candidate-Peak Suppression

Before line matching, the candidate list is filtered so that no two retained
peaks lie within approximately 10 pixels.

Candidates are considered from highest to lowest amplitude.

This suppresses:

- multiple detections of one broad line,
- shoulders,
- noise structure near a strong line,
- and closely spaced false candidates.

The assumption is that the selected reference features are separated by more
than this amount in detector space.

The minimum separation is an algorithm parameter and should be validated against
the smallest expected arc-line separation in every amplifier.

---

# Peak Detection

The current module delegates peak finding to the shared fiber utility.

Non-finite spectral samples are replaced with zero before detection.

The peak-finding threshold is currently passed as:

```text
thresh = 1
```

The exact physical meaning of this threshold depends on the implementation of
the shared `find_peaks` function.

A reimplementation must not copy the number without also preserving:

- normalization assumptions,
- noise interpretation,
- peak localization behavior,
- and returned amplitude semantics.

Peak-position uncertainty is not currently propagated into the wavelength fit.

---

# Seed Model and Final Model

Two model complexities are used.

## Seed Model

```text
second-order polynomial
```

The seed should be flexible enough to describe approximate dispersion while
remaining constrained enough to reject arbitrary peak associations.

## Final Fiber Model

```text
fourth-order polynomial
```

The final model is fitted to the selected line matches and evaluated over all
1032 detector columns.

This division is scientifically sensible:

- use a restrictive model for identification;
- use a more expressive model for final calibration.

The exact polynomial order should remain configurable and validated with
residual structure, not only total RMS.

---

# Matching Tolerance

After the seed model is selected, each reference wavelength is associated with
the nearest candidate peak predicted by the model.

The wavelength difference is converted into an approximate pixel difference
using the local derivative of the polynomial.

A match is accepted when the inferred pixel residual is within:

```text
12 pixels
```

and when the candidate peak has not already been assigned to another line.

This is intentionally permissive enough to recover from an approximate seed.

However, the final QA must ensure that this tolerance has not permitted a
self-consistent but incorrect solution.

---

# Unused Robust-Matching Logic

The module defines a MAD-based sigma-clipping function for line-match residuals.

The current identification path does not call it.

Therefore, the final fourth-order fit is not currently protected by that
explicit residual-clipping stage.

This distinction should be made clear in any migration:

- either incorporate and validate robust clipping;
- or remove unused logic so the effective algorithm is unambiguous.

---

# Sparse Fiber Seeds

The current code does not independently solve every fiber.

It attempts seed solutions at approximately every eighth fiber:

```text
2, 10, 18, ...
```

plus a fiber near the upper edge.

For each selected seed fiber, it median-combines a five-fiber neighborhood:

```text
j - 2 through j + 2
```

before identifying arc lines.

This improves signal and robustness.

It also means that the seed solution corresponds to a local fiber region rather
than a pure measurement of one physical fiber.

The wavelength map is subsequently reconstructed from the smooth
two-dimensional geometry.

---

# Seed Acceptance

A seed-row solution is considered usable when its line-fit residual satisfies:

```text
0 < RMS < 1.0 Å
```

At least seven accepted seed rows are required.

If fewer than seven good seed rows are available, the amplifier wavelength model
fails.

This is an amplifier-level sufficiency rule.

The threshold and required count should be QA configuration rather than hidden
algorithm constants.

---

# Constructing the Two-Dimensional Wavelength Surface

The current algorithm constructs the full map in two smoothing stages.

## Stage 1: Across Fibers at Sparse Columns

Detector columns are sampled approximately every 24 pixels, including the final
column.

At each sampled column:

- the trace provides the cross-dispersion `y` coordinate of each fiber;
- accepted seed solutions provide wavelength values;
- a fourth-order polynomial is fitted as wavelength versus trace `y`;
- that model is evaluated at the trace position of every fiber.

Conceptually:

```text
at fixed x:

λ = P_x(y_trace)
```

This uses the physical assumption that wavelength changes smoothly across the
fiber positions of the spectrograph.

It also fills fibers for which no direct comparison-lamp seed was measured,
including configured dead or weak fibers.

## Stage 2: Along Dispersion for Every Fiber

For each fiber, a fourth-order polynomial is fitted through the wavelength
values established at the sparse detector columns.

Conceptually:

```text
for fixed fiber f:

λ_f = Q_f(x)
```

The result is evaluated over all detector columns.

Together, these stages produce a smooth wavelength map over fiber and
dispersion coordinates.

---

# Smoothness Assumption

The reconstruction relies on a critical physical assumption:

> **The spectrograph wavelength surface is smooth across both detector column
> and fiber position.**

This assumption permits the algorithm to:

- reject noisy local behavior;
- interpolate over fibers without direct line detections;
- fill dead fibers;
- and produce a complete map from sparse reliable seeds.

The assumption should be tested using residual maps for each detected arc line.

A low per-seed polynomial RMS alone is not sufficient to verify global
two-dimensional smoothness.

---

# Filling Missing Fibers

Missing or dead fibers can receive wavelength solutions because the map is
constructed from the smooth spectrograph surface rather than from an isolated
fit to every fiber.

This is physically justified when:

- fiber geometry is correctly represented by the trace;
- the spectrograph surface is smooth;
- and the missing fiber lies within the domain constrained by neighboring
  fibers.

The Product should distinguish:

- directly constrained seed fibers;
- spatially interpolated fibers;
- and extrapolated edge fibers.

A filled wavelength solution does not imply that the corresponding fiber
produced detectable comparison-lamp flux.

---

# Current Nearest-Neighbor Fallback

If one fiber does not have enough valid sparse-column values for its
dispersion-direction fit, the current code copies the wavelength solution from
the nearest accepted seed row.

This is a recovery mechanism, not a physical geometric interpolation.

It can produce a complete array while hiding a local modeling failure.

A new implementation should instead:

- report the failure explicitly;
- distinguish copied from fitted values;
- and preferably evaluate the smooth two-dimensional surface directly at that
  fiber position.

---

# Per-Fiber Residual Metric

The current Product returns an array named:

```text
per_fiber_wavelength_residual_rms
```

However, the current implementation populates RMS values only for the sparse
seed rows that were attempted.

Unattempted rows remain zero.

Therefore, this is presently more accurately described as:

```text
seed_region_arc_fit_rms
```

It is not a direct per-fiber residual measurement for all fibers.

The future Product should avoid using zero to represent “not measured.”

Use:

- `NaN`,
- an explicit attempted mask,
- or a structured seed table.

---

# Important Current-Code Caveats

## Fourth-Order Fit Support

The final fitter requests a fourth-order polynomial without explicitly checking
that at least five valid line matches exist.

A poorly matched seed can therefore produce a weakly constrained or
rank-deficient final solution.

The algorithm should require enough unique lines for the selected model order.

## No Explicit Monotonicity Check

The physical wavelength solution must increase monotonically with detector
column in canonical VIRUS orientation.

The current code does not explicitly reject a non-monotonic polynomial.

## No Explicit Dispersion Bounds

The local dispersion should remain within a physically plausible range.

The current code uses a fallback local slope of 1.95 Å per pixel for matching,
but does not use a broader physical prior to validate the final model.

## QA Plotting Is Not Integrated

A plotting helper exists, but the main amplifier-fitting path does not currently
produce the diagnostic plot.

Algorithms should return diagnostic data, while analytics or QA rendering should
create plots outside the algorithm.

## Legacy and Unused Parameters

The module retains unused `T_array` and `qa` parameters.

These should be removed or assigned a clear contract.

## Broad Exception Handling

Individual seed failures are silently skipped.

Operational robustness is valuable, but each failure should be represented in
messages or structured diagnostics.

## Failure Summary Counting

Because unattempted RMS entries are initialized as finite zeros, the current
failure summary can overstate the number of rows tried.

An explicit attempted mask is required.

---

# Required Line-Level Diagnostics

For every attempted seed region, retain:

- all candidate peak positions;
- candidate amplitudes;
- the six peaks used for seed identification;
- the selected six-line seed combination;
- initial polynomial coefficients;
- all final matched lines;
- unmatched reference lines;
- unmatched candidate peaks;
- wavelength residual by line;
- pixel residual by line;
- final polynomial coefficients;
- number of matches;
- RMS;
- and success or failure reason.

These details are necessary to distinguish:

- missing lamp lines;
- false peak selection;
- incorrect global association;
- weak extraction;
- and polynomial-model failure.

---

# Required Two-Dimensional Diagnostics

The final Wavelength Product should expose:

- detected line-position maps across fibers;
- smoothed line-position maps;
- residual maps between detected and modeled line locations;
- seed-fiber mask;
- interpolated-fiber mask;
- extrapolated-fiber mask;
- valid-line count per seed;
- per-line residual statistics;
- local dispersion map;
- monotonicity checks;
- and wavelength-surface curvature diagnostics.

The most informative test is whether each identified physical line forms a
smooth detector-space curve across the amplifier.

---

# Comparison-Lamp Wavelength vs. Science Observation Frame

The comparison-lamp solution answers:

```text
What physical wavelength corresponds to this detector location?
```

A science observation adds another question:

```text
In what velocity reference frame should the observed spectrum be interpreted?
```

The detector wavelength map should remain an instrument calibration.

It should not be permanently modified by the barycentric velocity of one science
observation.

Instead, the science Product should retain:

- the instrument wavelength map;
- the observer-frame definition;
- the barycentric correction;
- the correction reference time;
- the reference sky coordinate;
- and, when materialized, the transformed barycentric spectral coordinate.

---

# Barycentric Velocity Correction

The barycentric correction depends on:

- observation time;
- observatory location;
- target direction on the sky;
- and the adopted ephemeris and time conventions.

It accounts for the observer’s velocity relative to the Solar System barycenter,
including both Earth’s orbital motion and observatory motion from Earth’s
rotation.

For ordinary VIRUS processing, one correction evaluated at the field center and
the exposure midpoint should be sufficient.

The field-center approximation should nevertheless be recorded explicitly so
that specialized analyses can request corrections at individual source
coordinates if needed.

The correction should be computed at observation or extracted-spectrum scope,
not calibration scope.

---

# Applying the Barycentric Correction

The repository should distinguish at least:

```text
topocentric wavelength
```

from:

```text
barycentric wavelength or barycentric velocity interpretation
```

The Product should record the sign convention and application convention.

A velocity correction should not be applied by merely adding the same wavelength
offset at every wavelength.

For low velocities, the wavelength shift scales approximately with wavelength.

A robust implementation should use a well-defined spectral-coordinate or
redshift transformation and preserve the original wavelength grid.

---

# Barycentric Product Scope

A suitable correction target is conceptually:

```text
BarycentricCorrectionTarget(
    exposure_id,
    observatory,
    exposure_midpoint,
    field_center
)
```

One exposure-level correction may be attached to all VIRUS fibers for routine
processing.

For extracted objects or specialized radial-velocity work, a source-coordinate
correction can supersede the field-center value.

The correction Product should contain:

```text
barycentric_velocity_correction
reference_coordinate
observation_time
time_reference
observatory_location
ephemeris
convention
software_version
```

---

# Product Contract

## Wavelength Arrays

```text
wavelength_map
```

Recommended supporting arrays:

```text
seed_wavelength_solutions
seed_region_arc_fit_rms
seed_region_attempted_mask
seed_region_success_mask
detected_line_columns
modeled_line_columns
line_position_residuals
interpolated_fiber_mask
extrapolated_fiber_mask
local_dispersion_map
```

## Metadata

```text
reference_arc_wavelengths
lamp_kinds
master_hg_product
master_cd_product
master_comparison_product
trace_product
extraction_aperture
candidate_peak_limit
seed_peak_count
minimum_peak_separation
seed_polynomial_order
final_polynomial_order
matching_tolerance_pixels
seed_rms_limit
minimum_good_seed_regions
sampled_fiber_rows
sampled_detector_columns
wavelength_map_shape
algorithm_version
```

## Barycentric Metadata or Linked Product

```text
observer_frame
barycentric_correction
barycentric_reference_coordinate
barycentric_reference_time
observatory_location
ephemeris
velocity_convention
```

---

# Separation of Responsibilities

## Comparison-Lamp Construction

Owns:

- averaging Hg exposures;
- averaging Cd exposures;
- combining the two lamp masters;
- lamp-specific QA;
- Product provenance.

## Trace Algorithm

Owns:

- fiber centers used for comparison-spectrum extraction;
- physical `y` coordinates for wavelength-surface reconstruction.

## Comparison Extraction

Owns:

- extracting the Master Comparison spectrum for each fiber or seed region.

## Wavelength Algorithm

Owns:

- candidate-peak detection;
- arc-line association;
- seed wavelength fits;
- smooth two-dimensional wavelength reconstruction;
- line-level and surface-level diagnostics.

## QA

Owns:

- line-count requirements;
- residual thresholds;
- monotonicity checks;
- dispersion bounds;
- seed sufficiency;
- and pass/warn/fail decisions.

## Observation Correction

Owns:

- exposure midpoint;
- observatory location;
- sky-coordinate reference;
- barycentric velocity correction;
- observer-frame metadata.

## Analytics

Owns:

- long-term wavelength drift;
- ambient-temperature dependence;
- line-specific residual structure;
- spectrograph stability;
- reference aging;
- and evaluation of whether a low-dimensional temporal correction can replace
  repeated full solutions.

---

# Initial Implementation Decisions

Until a better validated approach is available:

- Average Hg and Cd exposures separately before summing their masters.
- Retain the nine-line laboratory list as versioned configuration.
- Use the Master Comparison and Trace Products to extract arc spectra.
- Do not assume fixed detector columns for physical lines.
- Retain at most 15 separated candidate peaks.
- Use the six brightest candidates for the current combinatorial seed search.
- Use a restrictive low-order seed model.
- Match the seed against the complete nine-line list.
- Require adequate line support before a fourth-order final fit.
- Require monotonic wavelength with detector column.
- Solve only a sparse set of robust seed regions.
- Use the trace coordinate to reconstruct a smooth wavelength surface across
  fibers.
- Fit the dispersion direction smoothly for every fiber.
- Mark direct, interpolated, and extrapolated solutions separately.
- Publish all line-association evidence needed for QA.
- Represent unattempted measurements with `NaN` or masks, never zero.
- Keep the comparison-lamp wavelength map separate from the barycentric
  observation correction.
- Compute routine barycentric corrections at exposure midpoint and field center.
- Preserve the original detector wavelength map even when a barycentric spectral
  coordinate is materialized.

---

# Repository Goals

VIRUSFlow should:

- preserve wavelength calibration as a physical spectrograph geometry;
- document the exact arc-identification mechanism rather than an approximate
  verbal description;
- determine whether six-peak seeding remains optimal;
- test whether direct nine-of-fifteen matching provides additional robustness;
- quantify line-identification confidence;
- learn wavelength drift as a function of ambient temperature and instrument
  state;
- measure the smooth two-dimensional trajectories of all nine lines;
- distinguish directly measured, interpolated, and extrapolated fibers;
- identify coherent residual structure that total RMS conceals;
- determine the minimum polynomial complexity required across VIRUS;
- validate monotonicity and physical dispersion bounds automatically;
- retain Hg and Cd lamp behavior separately;
- attach barycentric velocity corrections to every science exposure;
- support source-specific barycentric corrections when scientifically required;
- and eventually construct a time-aware wavelength model that uses comparison
  lamps, environmental metadata, and accumulated repository evidence without
  sacrificing reproducibility or line-identification transparency.
