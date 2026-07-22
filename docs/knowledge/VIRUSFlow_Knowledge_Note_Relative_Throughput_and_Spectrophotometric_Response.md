# VIRUSFlow Scientific Knowledge Specification

# Working Note: Relative Throughput, Mirror Evolution, and Spectrophotometric Response

> Status: Initial scientific and architectural specification

This note defines how VIRUSFlow should represent the wavelength-dependent
relative response of the complete HET + VIRUS system.

The central principle is:

> **The relative response is not permanently one fixed curve. It is a baseline
> system response that evolves with time, mirror state, track position, and
> illumination.**

Historically, one representative response curve has been sufficient for many
reductions. A knowledge-centered system should preserve that stable baseline
while learning the small but scientifically meaningful departures from it.

---

# Scientific Object

After detector correction, extraction, fiber normalization, illumination
correction, and sky subtraction, the measured source spectrum still contains the
wavelength-dependent response of the telescope and instrument.

Conceptually:

```text
Measured Source Spectrum_f(λ)
    =
Source Flux(λ)
    ×
Relative System Response(λ)
    ×
Absolute Throughput Scale
```

More completely:

```text
Counts_f(λ, t)
    =
Source SED(λ)
    ×
Atmospheric Transmission(λ, t)
    ×
Telescope Response(λ, t, track)
    ×
Instrument Response_f(λ, t)
    ×
Exposure Scale(t)
```

The relative-response calibration seeks to determine the wavelength-dependent
shape after all fibers have been placed onto one common instrumental reference
frame.

---

# Relative vs. Absolute Response

Relative and absolute response are related but distinct.

## Relative Response

The relative response describes the wavelength-dependent shape:

```text
R_rel(λ)
```

It answers:

```text
How does the system sensitivity at one wavelength compare with another?
```

The curve is normally normalized at one wavelength, over one wavelength
interval, or by an integrated convention.

## Absolute Response

The absolute response determines the physical flux scale.

It depends on:

- total collecting area;
- atmospheric transparency;
- mirror reflectivity;
- exposure time;
- guider throughput;
- source centering;
- and aperture losses.

A spectrum may have an accurate relative shape but an uncertain absolute scale.

---

# Calibration Reference Frame

The response curve is meaningful only after the spectra have been placed into a
consistent reference frame.

That reference includes:

- amplifier orientation;
- extraction method;
- trace and wavelength calibration;
- center-track twilight fiber normalization;
- amp-to-amp normalization;
- exposure illumination correction;
- scattered-light treatment;
- and sky subtraction.

A response curve derived under one extraction and normalization convention
should not be assumed valid under another without validation.

Conceptually:

```text
Response Calibration Identity
    =
Reduction Convention
    +
Illumination Reference
    +
Extraction Convention
    +
Instrument State
```

---

# Historical Single-Curve Model

Historically, VIRUS relative calibration has often been represented by one
wavelength-dependent curve for the complete system.

This has been operationally successful because much of the response shape is
stable.

The fixed curve should therefore remain the baseline model:

```text
R0(λ)
```

However, it should be interpreted as:

> **The long-term average relative response under a chosen reference state.**

It should not be treated as proof that the response is invariant.

---

# HET Primary Mirror

The HET primary mirror is composed of:

```text
94 approximately one-meter hexagonal mirror segments
```

The segments are cleaned on an attempted weekly schedule.

Because the segments are cleaned sequentially, the complete primary mirror
passes through a cleaning cycle over approximately two years.

During that interval:

- individual segments accumulate contamination;
- cleaned segments recover throughput;
- mirror reflectivity evolves;
- and the distribution of segment states across the primary changes.

The effective telescope response therefore evolves continuously rather than
changing only during major maintenance events.

---

# Mirror Degradation

The mirror segments are expected to degrade in broadly similar ways, but not
identically.

Potential differences include:

- cleaning date;
- cleaning effectiveness;
- contamination rate;
- coating condition;
- local environmental exposure;
- and measurement uncertainty.

Thus, the primary mirror has both:

```text
Common Temporal Evolution
```

and:

```text
Segment-to-Segment Perturbations
```

The repository should preserve mirror-monitoring measurements as direct evidence
about the telescope response.

---

# Track-Position Dependence

The HET tracker does not illuminate the same effective subset of primary-mirror
segments at all track positions.

As the telescope tracks:

- the illuminated pupil changes;
- tracker occultation changes;
- the contribution of individual mirror segments changes;
- and the effective collecting area and reflectivity change.

Therefore, two observations taken at different track positions may have
different:

- absolute throughput;
- relative spectral response;
- pupil illumination;
- and potentially instrumental PSF behavior.

The response should be allowed to depend on track position rather than only
observation date.

---

# Effective Mirror Response

The telescope contribution can be represented conceptually as a weighted sum of
mirror-segment responses:

```text
T_mirror(λ, t, track)
    =
Σ_m w_m(track) r_m(λ, t)
```

where:

- `m` indexes mirror segments;
- `w_m(track)` is the contribution of segment `m` at the observed track
  position;
- `r_m(λ, t)` is the reflectivity of that segment at time `t`.

This is the physically complete form.

An initial implementation may use a lower-dimensional approximation rather than
solving all segment responses independently.

---

# Low-Dimensional Throughput Model

The relative response appears sufficiently stable that a hierarchical model is
appropriate:

```text
R_rel(λ, t, track)
    =
R0(λ)
    ×
T_time(λ, t)
    ×
T_track(λ, track)
    ×
δR(λ, t, track)
```

where:

- `R0(λ)` is the long-term baseline;
- `T_time` describes slow temporal evolution;
- `T_track` describes repeatable track-position dependence;
- `δR` is a small residual perturbation.

The first implementation should keep the perturbations low dimensional.

Possible representations include:

- one gray normalization plus one color term;
- a small polynomial in wavelength;
- a few response basis components;
- or a spline with strong regularization.

The model should not permit arbitrary exposure-specific curves unless strongly
constrained by data.

---

# Relative and Gray Throughput Changes

Mirror degradation may produce both:

```text
Gray Throughput Change
```

and:

```text
Wavelength-Dependent Color Change
```

A purely gray loss changes the absolute scale but not the relative response.

A wavelength-dependent reflectivity change modifies the relative curve.

The repository should test how much of the observed response evolution is
explained by:

- one multiplicative amplitude;
- versus a true change in spectral shape.

If most evolution is gray, the historical single curve remains a strong relative
model and only the absolute scale needs frequent updating.

---

# Primary Calibration Sources

The most direct calibration sources are spectrophotometric standard stars with
known spectral energy distributions.

VIRUS standard-star observations are usually obtained every two or three days
during active VIRUS operations rather than every night.

A standard star is normally observed on one IFU.

The calibration therefore depends on the earlier normalization system correctly
mapping that one IFU onto the complete exposure-wide response frame.

---

# Standard-Star Measurement

For a standard star with known reference spectrum:

```text
F_ref(λ)
```

the measured response is approximately:

```text
R_obs(λ)
    =
C_obs(λ)
    /
F_ref(λ)
```

after accounting for:

- exposure time;
- atmospheric extinction;
- aperture or extraction losses;
- fiber normalization;
- exposure illumination;
- sky subtraction;
- and source centering.

The response should be smoothed or modeled at a scale appropriate to the
instrument.

Narrow stellar features should not be interpreted as response structure.

---

# Single-IFU Limitation

A standard star normally illuminates only one IFU and a limited number of
fibers.

Therefore, the observation does not directly measure the response of all VIRUS
fibers.

It constrains the complete system only through the assumed calibration chain:

```text
Observed Standard IFU
    ↓
Within-Amp Fiber Normalization
    ↓
Amp-to-Amp Normalization
    ↓
Exposure Illumination Model
    ↓
Complete-System Response
```

Residual errors in that chain can appear as apparent spectrophotometric response
errors.

Standard stars therefore test both:

- the response curve;
- and the validity of the normalization architecture.

---

# Abundant Secondary Standards

A much larger calibration sample can be built from stars observed incidentally
throughout VIRUS science data.

Useful candidates include:

- G-type stars;
- approximately G5 stars;
- stars selected from broadband photometry;
- stars with catalog-based spectral predictions;
- and stars whose VIRUS spectra can be classified reliably after preliminary
  calibration.

These objects may not have the absolute quality of primary spectrophotometric
standards.

However, their abundance provides broad coverage of:

- time;
- track position;
- IFUSLOT;
- IFUID;
- spectrograph;
- amplifier;
- and mirror state.

---

# Why G-Type Stars Are Useful

G-type stellar spectra have:

- common occurrence;
- well-characterized broad spectral shapes;
- recognizable absorption features;
- and extensive photometric and spectroscopic reference libraries.

They can serve as secondary relative-response constraints when:

- stellar type is predicted photometrically;
- the VIRUS spectrum confirms the classification;
- extinction is modeled or constrained;
- and quality criteria reject composite, variable, or unusual stars.

The stellar model uncertainty must remain part of the calibration evidence.

---

# Secondary-Star Validation

A candidate secondary standard should be evaluated through both external and
internal evidence.

## External Evidence

Possible inputs include:

- broadband photometry;
- colors;
- catalog stellar classification;
- proper motion;
- parallax;
- extinction estimates;
- and external spectra where available.

## Internal Spectral Evidence

The preliminary VIRUS spectrum should be checked for:

- expected stellar absorption features;
- approximate temperature or type;
- unusual emission;
- binary or composite signatures;
- reduction artifacts;
- and consistency with the predicted spectral model.

A star should not enter the response solution solely because its catalog color
resembles a G5 star.

---

# Repository-Scale Calibration Network

A large collection of primary and secondary standards creates a calibration
network.

Each observation links:

```text
Star
Time
Track Position
IFUSLOT
IFUID
SPECID
AMP
Mirror State
```

The repository can solve these observations jointly.

Conceptually:

```text
Observed Spectrum_s,e,f(λ)
    =
Stellar Model_s(λ)
    ×
System Response(λ, t_e, track_e)
    ×
Fiber/IFU Residual_f(λ)
    ×
Exposure Scale_e
```

The unknowns can be constrained across many repeated and overlapping
observations.

---

# Global Self-Calibration

The calibration problem is similar to a large self-calibration system.

Unknown terms may include:

- baseline response curve;
- temporal response perturbation;
- track-position perturbation;
- IFU residual response;
- spectrograph residual response;
- exposure transparency or gray scale;
- and secondary-star model corrections.

Repeated observations break degeneracies.

For example:

- one star observed at multiple track positions constrains track effects;
- multiple stars on one IFU constrain the IFU response;
- stars observed across many IFUs tie the focal plane together;
- primary standards anchor the physical flux scale.

---

# Degeneracies

The global system contains important degeneracies.

Examples include:

```text
Star normalization
versus
Exposure transparency
```

```text
IFU throughput
versus
Track illumination
```

```text
Temporal mirror degradation
versus
Atmospheric color term
```

```text
Stellar model error
versus
Instrument response shape
```

These must be controlled through:

- primary standards;
- reference normalizations;
- priors;
- repeated observations;
- external mirror monitoring;
- and restricted model complexity.

---

# Atmospheric Extinction and Transparency

Atmospheric effects should remain separate from the stable instrumental response
where possible.

The observed standard-star spectrum includes:

- wavelength-dependent atmospheric extinction;
- gray or nearly gray transparency changes;
- clouds;
- and airmass dependence.

A response Product should state whether it represents:

```text
Telescope + Instrument
```

or:

```text
Atmosphere + Telescope + Instrument
```

The preferred architecture separates:

```text
Instrument Relative Response
Atmospheric Extinction Model
Exposure Transparency
```

even if the first implementation uses a combined empirical curve.

---

# Source Centering and Aperture Capture

A standard star may not be centered perfectly on the fiber bundle.

Measured counts depend on:

- seeing;
- guiding;
- source position relative to fibers;
- differential atmospheric refraction;
- aperture extraction;
- and reconstruction of the total stellar flux.

A standard-star response should therefore include a source-capture model or a
well-defined aperture convention.

Otherwise, centering losses can be confused with throughput changes.

For relative response, wavelength-dependent centering and DAR are especially
important.

---

# Role of Astrometry

Accurate astrometry is required to determine:

- where the standard star fell within the IFU;
- which fibers contain source flux;
- how source centering changes with wavelength;
- and how much light lies outside the observed fibers.

The response calibration therefore depends on the astrometric and DAR Products.

This is another example of a calibration that cannot be isolated as one serial
amplifier-level step.

---

# Track and Mirror Metadata

Every standard-star constraint should retain:

- observation time;
- track position;
- azimuth;
- pupil or tracker state where available;
- mirror-segment cleaning history;
- mirror reflectivity monitoring;
- airmass;
- seeing;
- transparency diagnostics;
- and guider throughput.

This metadata allows analytics to distinguish instrumental evolution from
atmospheric variation.

---

# Product Decomposition

A useful set of response Products includes:

```text
baseline_relative_response
temporal_response_perturbation
track_response_perturbation
exposure_transparency
absolute_response_scale
final_exposure_response
```

with:

```text
final_exposure_response
    =
baseline_relative_response
    ×
temporal_response_perturbation
    ×
track_response_perturbation
    ×
exposure_transparency
    ×
absolute_response_scale
```

Not every factor must be independently solved in the initial implementation.

The decomposition should nevertheless guide metadata and future analytics.

---

# Baseline Relative-Response Product

Recommended arrays:

```text
response_wavelength
baseline_relative_response
response_uncertainty
```

Recommended metadata:

```text
reference_normalization
extraction_method
fiber_normalization_reference
illumination_reference
standard_stars
validity_interval
model_basis
algorithm_version
```

---

# Temporal Response Product

Recommended representation:

```text
temporal_response_coefficients
```

or:

```text
temporal_response_curve
```

Recommended metadata:

```text
time_interval
mirror_cleaning_state
mirror_monitoring_inputs
number_of_standards
regularization
baseline_response_product
```

---

# Track-Response Product

Recommended representation:

```text
track_response_coefficients
```

or:

```text
track_response_curve
```

Recommended metadata:

```text
track_coordinate
azimuth
pupil_model
mirror_weight_model
training_observations
validity_domain
```

---

# Standard-Star Measurement Product

Recommended arrays:

```text
observed_standard_spectrum
reference_standard_spectrum
raw_response_ratio
smoothed_response_ratio
response_residual
```

Recommended metadata:

```text
star_id
standard_type
primary_or_secondary
stellar_model
model_uncertainty
astrometry_product
DAR_product
source_capture_method
track_position
mirror_state
airmass
seeing
transparency
IFUSLOT
IFUID
SPECID
AMP
complete ZipCode
```

---

# Required QA

## Primary-Standard QA

Evaluate:

- agreement with the reference SED;
- centering and source capture;
- wavelength residual structure;
- atmospheric conditions;
- track-position coverage;
- and consistency with neighboring standards in time.

## Secondary-Standard QA

Evaluate:

- stellar classification confidence;
- photometric-model agreement;
- extinction uncertainty;
- spectral-feature consistency;
- multiplicity or variability;
- and residuals after the current response model.

## Global-Model QA

Evaluate:

- residuals by wavelength;
- residuals by IFU and spectrograph;
- residuals by track position;
- residuals by mirror cleaning state;
- temporal smoothness;
- and predictive performance on held-out standards.

---

# Validation Tests

## Repeat-Star Test

Compare the same star observed:

- on different dates;
- at different track positions;
- and on different IFUs.

## Holdout Test

Exclude selected primary or secondary standards and predict their response.

## Mirror-Cycle Test

Measure whether response evolution follows the expected cleaning and degradation
cycle.

## Track Test

Test whether track-dependent terms reduce residuals without absorbing
atmospheric changes.

## IFU Transfer Test

Verify that a response measured on one IFU predicts calibrated stars observed on
other IFUs.

## Relative-vs-Absolute Test

Separate residual color errors from gray normalization errors.

---

# Important Assumptions

The initial model assumes:

- the baseline response shape is broadly stable;
- mirror-segment responses are similar enough for a low-dimensional model;
- track dependence can be represented with limited complexity;
- center-track twilight normalization maps fibers onto a common response frame;
- standard-star source capture is measurable;
- secondary-star spectral models are accurate enough for relative constraints;
- and accumulated observations can break the principal calibration
  degeneracies.

These assumptions should be tested directly.

---

# Initial Implementation Decisions

- Preserve one historical-style baseline relative-response curve.
- Reinterpret the fixed curve as a reference state, not an invariant truth.
- Separate relative response shape from absolute throughput scale.
- Retain mirror cleaning and monitoring metadata.
- Preserve track position for every calibration observation.
- Begin with low-dimensional temporal and track perturbations.
- Use primary spectrophotometric standards as authoritative anchors.
- Build a larger secondary-standard sample from suitable stellar observations.
- Require photometric selection plus spectral verification for secondary
  standards.
- Preserve single-star measurements as Products before global fitting.
- Solve response corrections across many observations at repository scope.
- Keep atmospheric extinction and exposure transparency separate where
  possible.
- Do not allow flexible exposure-specific curves to fit arbitrary noise.
- Preserve the calibration reference frame, including extraction and fiber
  normalization conventions.
- Validate transfer from a standard observed on one IFU to the complete VIRUS
  system.

---

# Open Questions

- How stable is the relative response shape compared with the gray throughput
  scale?
- Which wavelength basis best represents temporal evolution?
- How accurately are individual mirror-segment reflectivities measured?
- Can a physical mirror-weight model predict track dependence?
- How many primary standards are required to anchor the global solution?
- Which stellar types are best as abundant secondary standards?
- How accurately can photometry predict a G-type stellar SED?
- How should stellar extinction be separated from instrument response?
- What source-capture model is required for single-IFU standards?
- How should atmospheric transparency be inferred for non-photometric
  exposures?
- Which calibration terms should be exposure-, night-, track-, or epoch-scoped?
- How should response uncertainty be propagated into science spectra?
- Can science observations themselves identify response drift through repeated
  stellar sources?
- How much does improved response modeling benefit sky subtraction and stacked
  depth?

---

# Repository Goals

VIRUSFlow should:

- preserve the historical stable response curve as a transparent baseline;
- measure real temporal evolution rather than assuming invariance;
- connect response changes to mirror cleaning and reflectivity monitoring;
- quantify track-position dependence from changing mirror illumination;
- separate gray throughput changes from relative spectral-shape changes;
- use primary standards to anchor physical calibration;
- discover and validate large samples of secondary stellar standards;
- connect standards observed across IFUs, spectrographs, dates, and tracks;
- solve a global self-calibration system with controlled complexity;
- distinguish atmospheric, telescope, instrument, and source-capture effects;
- predict an exposure-specific relative response with uncertainty;
- improve calibration for fields without a contemporaneous primary standard;
- and turn the accumulated stellar archive into a continuously improving model
  of the full HET + VIRUS spectrophotometric response.
