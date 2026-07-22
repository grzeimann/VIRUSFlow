# VIRUSFlow Scientific Knowledge Specification

# Working Note: Spectral Extraction

> Status: Initial scientific and architectural specification

This note defines how VIRUSFlow converts calibrated detector pixels into fiber
spectra.

The central design principle is:

> **The default extraction should minimize model risk while preserving a clear
> path toward higher-fidelity forward modeling.**

For VIRUS, the safest baseline is a trace-centered, fractionally weighted
five-pixel aperture extraction. Profile-weighted and simultaneous forward
extraction should also be supported, but they should be treated as optional
estimators whose increased precision must justify their increased dependence on
profile models.

---

# Scientific Object

For each fiber `f` and detector column `x`, spectral extraction estimates the
fiber signal:

```text
F_f(x)
```

from the two-dimensional detector image:

```text
D(y, x)
```

using knowledge of:

- the fiber trace,
- detector orientation,
- pixel masks,
- detector variance,
- fiber profiles,
- local crosstalk,
- long-range scattered light,
- and wavelength geometry.

The extraction Product is initially sampled in detector-column space.

The wavelength Product provides the associated spectral coordinate:

```text
x → λ_f(x)
```

Extraction and wavelength calibration should therefore remain separate but
linked Products.

---

# Detector-Space Forward Model

The complete detector image can be represented conceptually as:

```text
D(y, x)
    =
Σ_f F_f(x) P_f[y - trace_f(x), x]
    +
S_long(y, x)
    +
B(y, x)
    +
N(y, x)
```

where:

- `F_f(x)` is the spectrum of fiber `f`,
- `P_f` is the local cross-dispersion fiber profile,
- `trace_f(x)` is the fiber center,
- `S_long` is the CCD-scale long-range scattered-light contribution,
- `B` contains any other additive background,
- and `N` represents detector noise.

A complete extraction would infer all fiber spectra from this forward model.

In practice, the required physical knowledge is imperfect, so VIRUSFlow should
support multiple extraction modes with different levels of model dependence.

---

# Extraction Modes

VIRUSFlow should support three conceptually distinct modes:

```text
Fractional Aperture Extraction
Constrained Profile Extraction
Simultaneous Forward Extraction
```

They should share a common Product contract where practical, but their
assumptions and uncertainties must remain explicit.

---

# Default Mode: Fractional Five-Pixel Aperture

The routine VIRUS extraction uses a fixed-width aperture centered continuously
on the fiber trace.

For the standard setting:

```text
npix = 5
```

the aperture spans:

```text
trace_f(x) - 2.5 pixels
    to
trace_f(x) + 2.5 pixels
```

The detector pixels at the two aperture boundaries receive fractional weights.

The interior pixels receive unit weight.

Thus, depending on the subpixel trace position, the extraction may touch six
detector rows:

```text
fractional boundary pixel
+ four full pixels
+ fractional boundary pixel
```

The two fractional boundary weights sum to one pixel, so the total aperture
weight remains exactly five pixels.

---

# Exact Aperture Geometry

The current implementation:

1. rounds the floating-point trace position to the nearest detector row;
2. selects rows around that integer location;
3. assigns unit weights to interior rows;
4. computes the exact overlap of the first and last rows with the continuous
   five-pixel aperture;
5. sums the weighted detector values;
6. divides by the aperture width.

Conceptually:

```text
S_f(x)
    =
(1 / 5)
∫ from trace_f(x)-2.5 to trace_f(x)+2.5
D(y, x) dy
```

using discrete pixel-overlap weights.

This is not a nearest-five-pixel sum.

It is a continuous top-hat aperture integrated over the detector pixel grid.

---

# Extracted Quantity and Units

The returned  quantity is the total
integrated aperture counts.

For a fixed five-pixel aperture, this scale factor does not affect relative
fiber normalization.

However, the Product definition and units should be explicit.

A future implementation should choose and document one convention:

```text
integrated aperture counts
```

The current behavior should be preserved until downstream assumptions are
audited.

---

# Why Fractional Pixel Weighting Matters

Trace positions vary continuously across the detector and can drift with
temperature and instrument state.

If extraction were tied only to integer detector rows, a small trace shift could
cause a discontinuous change in the included pixels.

Fractional boundary weights allow the aperture to move continuously with the
trace.

Therefore:

> **If the trace correctly follows the fiber center, subpixel image motion should
> not by itself create a large discontinuity in extracted flux.**

Residual changes arise mainly from profile-shape changes rather than from the
pixel-boundary placement of the aperture.

---

# Aperture Capture Fraction

The five-pixel aperture does not capture the full fiber profile.

The enclosed fraction is expected to be approximately:

```text
70% to 90%
```

depending on:

- fiber,
- wavelength,
- profile width,
- profile shape,
- trace centering,
- illumination source,
- temperature,
- and instrument state.

Define the aperture capture fraction as:

```text
ε_f(x, t, I)
```

where:

- `t` represents time or hardware/environmental state,
- `I` represents illumination type.

The aperture-extracted spectrum can then be described as:

```text
S_ap,f(x)
    =
ε_f(x, t, I) F_f(x)
    +
C_f(x)
```

up to the fixed convention of dividing by aperture width.

`C_f(x)` contains additive contamination from neighboring fibers and scattered
light.

---

# Coupling to Fiber Normalization

The aperture loss is intentionally calibrated through fiber normalization.

Twilight normalization and science extraction should use the same:

- trace convention,
- aperture width,
- fractional boundary weighting,
- detector orientation,
- and extraction implementation.

The twilight-derived normalization therefore contains both:

- the relative instrument response,
- and the repeatable aperture capture fraction.

Conceptually:

```text
Aperture Extraction
    measures
Fiber Signal × Aperture Capture Fraction

Twilight Fiber Normalization
    calibrates
Relative System Response × Aperture Capture Fraction
```

This is scientifically valid if the aperture capture fraction transfers from
center-track twilight illumination to science illumination with sufficient
accuracy.

That transfer is an explicit assumption.

---

# Stability Assumption

The baseline extraction model assumes:

> **For a given fiber and wavelength, a correctly centered fractional
> five-pixel aperture captures a stable fraction of the fiber profile through
> time, ordinarily to approximately one percent or better.**

This assumption is expected to hold when:

- trace drift is measured correctly,
- the profile shape evolves slowly,
- the science profile resembles the twilight reference sufficiently,
- and local crosstalk remains modest.

The repository should test this rather than treat it as permanent truth.

---

# Aperture Capture Monitoring

Empirical fiber profiles can estimate the fraction of the direct profile within
the extraction aperture:

```text
ε_f(x)
    =
∫ A_f(y, x) P_f[y - trace_f(x), x] dy
```

where `A_f` is the fractional top-hat aperture.

The resulting diagnostic Product may be named:

```text
aperture_capture_fraction
```

or:

```text
extraction_throughput
```

Initially, this should be used for monitoring rather than automatically
correcting spectra.

A cleanup correction may later be defined as:

```text
q_f(x, t)
    =
ε_f(x, t)
/
ε_f,twilight(x)
```

and applied only when evidence shows a significant and reproducible departure
from unity.

---

# Why Aperture Extraction Is the Baseline

The five-pixel aperture has several operational advantages:

- rapid execution,
- deterministic behavior,
- low sensitivity to profile-model errors,
- continuous response to subpixel trace motion,
- simple masking behavior,
- suitability for preliminary spectra,
- suitability for iterative scattered-light estimation,
- and strong historical stability.

Its principal systematic losses are repeatable and can be absorbed into the
twilight fiber-normalization Product.

The aperture is therefore best understood as:

> **A low-model-dependence estimator with repeatable throughput losses calibrated
> elsewhere.**

It should not be dismissed merely as a crude approximation.

---

# Flat-Topped VIRUS Fiber Profiles

VIRUS fiber profiles are relatively flat topped.

This reduces the theoretical advantage of strongly profile-weighted extraction.

For a sharply peaked Gaussian profile, optimal weighting can substantially
improve signal-to-noise.

For a flatter profile, nearly uniform weights across the central five pixels may
already be close to an efficient estimator.

The extraction choice is therefore closer to:

```text
robust, nearly appropriate weighting
```

versus:

```text
somewhat more efficient but more model-dependent weighting
```

rather than a simple comparison between an inefficient aperture and an
obviously superior profile fit.

---

# Core and Scattered-Light Boundary

The direct profile core dominates near the trace.

The long-range scattered component becomes dominant approximately:

```text
4 to 5 pixels from the trace center
```

The five-pixel aperture spans only:

```text
-2.5 to +2.5 pixels
```

and therefore remains within the bright profile core.

This provides a useful computational separation:

```text
Central core
    →
spectral extraction

Long-range power-law wings
    →
CCD-scale scattered-light model
```

The boundary between the local profile and long-range scatter must remain
explicit to avoid double counting.

---

# Local Crosstalk

Neighboring fibers are separated by approximately:

```text
8 to 8.5 pixels
```

The five-pixel apertures do not normally overlap geometrically.

However, the fiber profiles themselves extend beyond the aperture.

Therefore, one fiber can still contribute light within the aperture of a
neighbor.

This local crosstalk is most important when:

- neighboring fibers have large brightness contrast,
- a bright star or emission line illuminates one fiber,
- profiles broaden,
- the trace is offset,
- or intermediate wings are stronger than expected.

The baseline aperture extraction does not explicitly deblend this contamination.

---

# Covariance

The aperture extraction treats fiber spectra as independent measurements.

Full covariance is not currently propagated.

This approximation is often reasonable because:

- the aperture remains within the core,
- the extraction is not a coupled inversion,
- ordinary neighboring-fiber contamination is modest,
- and the long-range scattered component is modeled separately.

The approximation becomes weaker for:

- bright stars,
- saturated fibers,
- strong emission lines,
- high-contrast neighboring spectra,
- broad or asymmetric profiles,
- and unusually strong scattered light.

Instead of carrying full covariance through all downstream Products, the
baseline pipeline should preserve compact diagnostics such as:

```text
neighbor_contamination_estimate
local_brightness_contrast
profile_overlap_fraction
scatter_fraction
covariance_risk_flag
```

Higher-fidelity extraction modes may optionally return local or banded
covariance.

---

# Dead Fibers

Configured dead fibers retain geometric traces but should not ordinarily receive
a freely fitted spectrum.

For aperture extraction, their detector regions may still be sampled for:

- background diagnostics,
- scattered-light validation,
- or detector residual analysis.

However, their direct spectral Product should be:

- absent,
- fixed to zero where scientifically appropriate,
- or explicitly marked unavailable.

A geometrically inferred trace does not imply a measurable fiber spectrum.

---

# Weak Fibers

Weak fibers are measurable but may have poor signal-to-noise.

Aperture extraction remains stable because it does not allow neighboring fibers
to determine a fitted amplitude through an ill-conditioned inversion.

Profile extraction may improve statistical precision, but the weak-fiber
amplitude can become degenerate with:

- neighboring profile wings,
- scattered light,
- additive background,
- profile-model errors,
- and bad pixels.

Weak-fiber Products should therefore retain explicit confidence and QA status.

---

# Optional Mode: Constrained Profile Extraction

A profile extraction uses a known or lightly perturbed fiber profile to weight
detector pixels.

At one detector column:

```text
D(y)
    ≈
Σ_f F_f P_f[y - trace_f]
    +
B(y)
```

If trace and profile are fixed, solving for `F_f` is a linear inverse problem.

The extraction can be performed:

- one fiber at a time,
- in overlapping local fiber groups,
- or for all 112 fibers at once.

Historically, all 112 fibers have been solved together in some implementations.

Local groups may reduce computational cost and improve conditioning, but they
must include enough neighboring profiles to capture significant overlap.

---

# Profile-Source Risk

The baseline empirical profile is measured from the Master LDLS.

The LDLS provides:

- high signal,
- smooth continuum,
- clean profile measurement.

However, its illumination may not reproduce the near-field or far-field output
of:

- twilight,
- diffuse night sky,
- point sources,
- off-center point sources,
- or science observations obtained at different telescope positions.

A colleague previously suggested that calibration-unit and science profiles do
not agree sufficiently well for unrestricted profile extraction.

This remains an open empirical question.

Twilight may provide a more physically representative profile, but its sharp
solar absorption structure complicates profile measurement.

Science exposures provide the correct illumination state but often lack the
signal and uniformity needed to determine a free profile.

---

# Hierarchical Profile Model

Rather than treating one illumination source as perfectly authoritative, a
future profile extractor should use a hierarchical model:

```text
High-S/N LDLS Baseline Profile
    +
Constrained Twilight Correction
    +
Optional Exposure-Level Science Correction
```

The corrections should initially be low dimensional, such as:

- centroid offset,
- width change,
- asymmetry,
- shoulder strength,
- wing amplitude,
- wavelength dependence.

A freely varying empirical profile for every science exposure would be too
degenerate with the unknown spectra.

---

# Optional Mode: Simultaneous Forward Extraction

The highest-fidelity local extraction should solve multiple fibers together.

At each detector column or narrow wavelength interval:

```text
D = P F + B + N
```

where:

- `D` is the detector-pixel vector,
- `P` is the matrix of shifted fiber profiles,
- `F` is the vector of fiber amplitudes.

Because the direct cores are local, `P` is sparse or nearly banded.

This makes local simultaneous extraction computationally more practical than a
single dense global fit.

The long-range scattered-light kernel should not be included in the same local
matrix.

It belongs to the paired-amplifier CCD-scale scatter model.

---

# Spectro-Perfectionism

Spectro-perfectionism and related forward-model extraction techniques can, in
principle:

- preserve resolution,
- handle overlapping profiles,
- estimate fluxes with high statistical efficiency,
- and propagate a more complete response model.

However, their scientific success depends on:

- accurate trace geometry,
- accurate profiles,
- accurate detector variance,
- valid pixel masks,
- correct scattered-light subtraction,
- good conditioning,
- and computational feasibility.

A sophisticated inversion using the wrong profile can produce lower statistical
noise but larger systematic error than the aperture baseline.

Therefore, VIRUSFlow should not equate algorithmic complexity with scientific
superiority.

---

# Iterative Extraction Strategy

Aperture extraction should serve as both a valid Product and the initializer for
more complete processing.

A practical iterative sequence is:

```text
Reduced Detector Images
    ↓
Fast Fractional Aperture Spectra
    ↓
CCD-Scale Scattered-Light Estimate
    ↓
Scatter-Subtracted Detector Images
    ↓
Optional Constrained Profile Extraction
    ↓
Updated Fiber Spectra
    ↓
Updated Scattered-Light Model
    ↓
Final Extraction
```

This allows the pipeline to move forward rapidly while reducing model ignorance
in stages.

Failure of an advanced extractor should not prevent production of the stable
aperture Product.

---

# Extraction Product Scope

The basic extraction algorithm operates at amplifier scope because it uses:

- one amplifier image,
- its trace map,
- its pixel mask,
- and its local fiber set.

However, its inputs may depend on broader-scope Products:

- scattered light is modeled at physical CCD scope,
- fiber normalization is finalized at exposure scope,
- barycentric correction is defined at science-exposure or object scope.

The extraction Product should preserve the complete ZipCode and exposure
provenance.

---

# Input Contract

The aperture extractor requires:

```text
reduced_amplifier_image
fiber_trace_map
pixel_mask
```

Recommended additional inputs include:

```text
variance_map
scattered_light_model
profile_product
fiber_status_configuration
```

The image must be in canonical amplifier orientation.

The trace and image must have compatible shapes and coordinate conventions.

---

# Aperture Algorithm Contract

Any reimplementation of the baseline aperture extractor must:

- use the floating-point trace center;
- integrate a continuous fixed-width aperture;
- assign fractional weights to boundary pixels;
- keep the total aperture width exactly equal to `npix`;
- reject fibers whose apertures cross detector boundaries;
- preserve masked or invalid pixel information;
- document whether output is integrated counts or mean counts;
- use the identical operator for twilight and science extraction;
- and return diagnostic information rather than silently producing zeros.

---

# Profile Extraction Contract

A profile extractor must:

- specify the profile source and illumination kind;
- define whether the profile is fixed or perturbed;
- identify the fiber group solved together;
- preserve model residuals;
- report conditioning;
- distinguish direct from regularized measurements;
- identify weak and dead-fiber treatment;
- and return covariance or a compact uncertainty approximation where feasible.

---

# Product Contract

## Primary Arrays

```text
extracted_spectrum
```

Recommended supporting arrays:

```text
extracted_variance
valid_pixel_fraction
effective_aperture_width
aperture_capture_fraction
neighbor_contamination_estimate
extraction_residual
```

For profile or forward extraction:

```text
local_covariance_or_banded_covariance
model_detector_image
profile_fit_residual
conditioning_metric
```

## Metadata

```text
extraction_method
aperture_width
fractional_boundary_weighting
output_scale_convention
trace_product
pixel_mask_product
variance_product
scattered_light_product
profile_product
profile_illumination_kind
fiber_normalization_compatibility
algorithm_version
```

## Fiber-Level Status

```text
fiber_status
extraction_success
edge_rejection
weak_fiber_flag
dead_fiber_flag
covariance_risk_flag
profile_model_risk_flag
```

---

# Required QA

## Aperture QA

Evaluate:

- effective aperture width;
- trace-to-profile centering offset;
- estimated aperture capture fraction;
- temporal stability of capture fraction;
- masked-pixel fraction;
- edge rejections;
- sensitivity to aperture width;
- neighboring-fiber brightness contrast;
- expected local contamination;
- and agreement with twilight normalization assumptions.

## Profile Extraction QA

Evaluate:

- detector-space residuals;
- profile mismatch;
- conditioning;
- covariance;
- negative or unstable fitted amplitudes;
- sensitivity to profile source;
- and comparison with aperture spectra.

## Cross-Method QA

For representative data, compare:

```text
aperture spectrum
profile-extracted spectrum
forward-extracted spectrum
```

Differences should be evaluated as functions of:

- wavelength,
- fiber,
- brightness,
- illumination type,
- temperature,
- telescope position,
- and neighboring-fiber contrast.

---

# Important Assumptions

The baseline extraction assumes:

- the trace accurately follows the fiber center;
- the five-pixel aperture remains within detector bounds;
- profile-shape changes are small enough that aperture capture is stable;
- twilight and science aperture capture are sufficiently similar;
- the five-pixel mean is a consistent spectral estimator;
- local crosstalk is usually modest;
- long-range scatter has been removed or modeled separately;
- full covariance is unnecessary for ordinary science;
- and fiber normalization absorbs the repeatable wavelength- and fiber-dependent
  aperture loss.

These assumptions should be monitored through repository analytics.

---

# Initial Implementation Decisions

- Preserve the fractional five-pixel aperture as the default production
  extraction.
- Treat aperture extraction as a valid scientific estimator, not merely a
  temporary hack.
- Use identical extraction geometry for twilight and science.
- Preserve the current output scaling until downstream units are audited.
- Explicitly document the mean-versus-integrated convention.
- Monitor aperture capture fraction by fiber and wavelength.
- Do not automatically apply profile-based aperture corrections until their
  stability is demonstrated.
- Keep local core extraction separate from CCD-scale long-range scatter.
- Do not propagate full covariance in the baseline Product.
- Preserve compact contamination and covariance-risk diagnostics.
- Support constrained profile extraction as an optional mode.
- Support simultaneous forward extraction as a higher-fidelity mode.
- Use aperture spectra to initialize iterative scatter and profile solutions.
- Never allow failure of an advanced mode to erase or block the stable aperture
  Product.
- Treat configured dead fibers as unavailable direct spectra.
- Preserve explicit confidence and QA for weak fibers.

---

# Open Questions

- How stable is the aperture capture fraction across years?
- How different are LDLS, twilight, sky, and point-source profiles?
- Are illumination-source differences primarily width and centroid changes, or
  higher-order shape changes?
- What aperture width minimizes total systematic and statistical error?
- Would a shape-preserving weighted aperture improve precision without excessive
  model risk?
- Should local profile extraction solve all 112 fibers or overlapping groups?
- What compact covariance representation is useful downstream?
- Which science cases justify full forward extraction?
- Can exposure-level profile corrections be constrained reliably?
- How should source-specific profile mismatch be flagged for bright stars?
- How should local crosstalk and power-law scatter be partitioned near four to
  five pixels from the trace?

---

# Repository Goals

VIRUSFlow should:

- preserve the five-pixel fractional aperture as a stable extraction reference;
- quantify its enclosed-light fraction for every fiber and wavelength;
- test the assumption of approximately one-percent temporal stability;
- separate intrinsic fiber throughput from aperture capture where evidence
  permits;
- compare LDLS, twilight, sky, and point-source profile behavior;
- measure when neighboring-fiber contamination becomes scientifically important;
- identify bright-source regimes where covariance cannot be ignored;
- determine whether weighted apertures improve precision without increasing
  systematic error;
- develop and validate constrained local profile extraction;
- support full simultaneous forward extraction for demanding science cases;
- preserve detector-space residuals and conditioning evidence;
- model extraction and scattered light iteratively without conflating their
  physical scopes;
- and allow increasingly sophisticated extraction Products to coexist with,
  rather than replace or obscure, the robust aperture baseline.
