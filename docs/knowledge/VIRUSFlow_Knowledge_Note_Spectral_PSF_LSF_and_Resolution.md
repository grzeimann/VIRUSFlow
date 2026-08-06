# VIRUSFlow Scientific Knowledge Specification

# Working Note: Two-Dimensional Spectral PSF, Line-Spread Function, and Resolution

> Status: Initial scientific and architectural specification

This note defines how VIRUSFlow should measure and represent the monochromatic
response of the spectrograph.

The central principle is:

> **The primary physical Product should be a two-dimensional detector-space
> spectral PSF measured from Master Arc exposures.**

The one-dimensional line-spread function and spectral resolution should be
derived from that two-dimensional model under a stated extraction method.

---

# Scientific Objects

Three related quantities must remain distinct:

```text
2D Spectral PSF
Line-Spread Function
Spectral Resolution
```

## Two-Dimensional Spectral PSF

The two-dimensional spectral point-spread function describes how monochromatic
light from one fiber is distributed on the detector around an arc-line
location.

Conceptually:

```text
P_f(Δx, Δy | λ, instrument state)
```

where:

- `Δx` is displacement in the dispersion direction;
- `Δy` is displacement in the cross-dispersion direction;
- `f` is fiber identity;
- `λ` is physical wavelength.

The 2D model includes:

- spectral width;
- cross-dispersion width;
- profile tilt;
- asymmetry;
- flat-topped structure;
- wings;
- detector sampling;
- and coupling between the dispersion and cross-dispersion axes.

## Line-Spread Function

The line-spread function is the one-dimensional spectral response obtained after
applying a specified extraction operator to the 2D spectral PSF.

Conceptually:

```text
LSF_f(Δx)
    =
ExtractionOperator[
    P_f(Δx, Δy)
]
```

The LSF therefore depends on both:

- the physical 2D detector response;
- and the extraction method.

An aperture-extracted LSF is not necessarily identical to a profile-extracted
LSF.

## Spectral Resolution

Spectral resolution is a summary derived from the LSF.

Common measures include:

```text
FWHM_λ
σ_λ
R = λ / FWHM_λ
```

Because the VIRUS PSF can be flat topped, asymmetric, or non-Gaussian, one
single width may not describe all scientifically relevant structure.

Resolution metrics should therefore accompany, not replace, the empirical PSF
and LSF models.

---

# Why Master Arc Exposures Are the Best Measurement

Master Arc exposures provide:

- narrow emission features;
- known physical wavelengths;
- exact line identifications;
- strong signal;
- broad wavelength coverage;
- repeatable illumination;
- and many fibers sampling the same spectrograph.

The wavelength-calibration algorithm already identifies the detector location of
the nine Hg and Cd lines.

Those line positions provide the natural centers for 2D PSF measurements.

The Master Arc is therefore the most direct calibration Product for measuring:

- monochromatic detector response;
- LSF shape;
- spectral resolution;
- line tilt;
- and wavelength-dependent PSF behavior.

---

# Relationship to the Wavelength Solution

For each identified line and fiber, the wavelength model provides the detector
column:

```text
x_line,f
```

and the trace provides the corresponding row:

```text
y_line,f = trace_f(x_line,f)
```

Together they define the expected line center:

```text
(x_line,f, y_line,f)
```

The PSF algorithm should use the same line-identification evidence produced by
the wavelength solution.

It should not independently search the full detector for unidentified bright
features unless operating in an explicit validation mode.

---

# Why a Two-Dimensional Model Is Preferred

A one-dimensional fiber profile convolved with an independent one-dimensional
spectral profile assumes separability:

```text
P(Δx, Δy)
    ≈
L(Δx) × F(Δy)
```

That approximation may fail when the detector image contains:

- line tilt;
- rotation;
- wavelength-dependent centroid shifts across the fiber profile;
- asymmetric coupling between axes;
- optical aberration;
- or detector sampling effects.

A tilted line cannot be represented exactly by multiplying two independent
one-dimensional functions.

The two-dimensional empirical PSF preserves these effects directly.

---

# Flat-Topped VIRUS PSF

VIRUS fiber images are relatively flat topped rather than purely Gaussian.

This affects both dimensions of the spectral PSF.

Consequences include:

- FWHM may be less informative than for a Gaussian;
- the core can contain a plateau;
- small centroid shifts may change pixel sampling without strongly changing the
  peak;
- profile weighting may offer less improvement than expected;
- and residual structure from analytic Gaussian models can remain coherent.

The preferred model should therefore be empirical rather than restricted to a
single analytic function.

---

# Fiber Overlap

Neighboring fiber profiles overlap in the cross-dispersion direction.

At an arc-line wavelength, the detector image is not one isolated 2D PSF.

It is the sum of many nearby fiber-line images:

```text
D_line(x, y)
    =
Σ_f A_f P_f(
    x - x_line,f,
    y - y_line,f
)
    +
Background
```

where `A_f` is the line amplitude in fiber `f`.

The PSF measurement therefore requires either:

- sufficiently isolated detector regions;
- simultaneous decomposition of overlapping fibers;
- or robust stacking that suppresses neighboring contributions.

A simple cutout around one fiber can contain light from adjacent fibers.

---

# Continuum and Background Removal

Although arc frames are dominated by emission lines, they can contain:

- lamp continuum;
- scattered light;
- detector background;
- long-range power-law wings;
- and residual additive structure.

A useful first step is to construct a smooth continuum/background model with a
long median filter or related robust method.

Known arc-line regions should be masked during background estimation.

The sequence should be:

```text
Identify expected line regions
    ↓
Mask line cores and wings
    ↓
Estimate smooth continuum/background
    ↓
Subtract background
    ↓
Measure line PSFs
```

The background model should not absorb real line wings.

The line masks must therefore extend beyond the bright core.

---

# Empirical Grid Measurement

A natural implementation follows the same philosophy as the empirical fiber
profile, extended into two dimensions.

For each accepted line and fiber:

1. extract a detector cutout centered near the predicted line position;
2. subtract the smooth local background;
3. apply pixel masks;
4. estimate the line amplitude;
5. convert detector coordinates to local offsets;
6. normalize the cutout by line amplitude;
7. place valid samples into an oversampled 2D coordinate grid;
8. robustly combine many measurements;
9. interpolate the robust grid into a continuous empirical PSF model.

The local coordinates may be:

```text
Δx = x - x_line,f
Δy = y - trace_f(x)
```

or a rotated coordinate system aligned with the local PSF axes.

The coordinate choice should preserve measurable tilt rather than remove it by
assumption.

---

# PSF Tilt and Orientation

The 2D line image may be tilted relative to detector rows and columns.

The tilt can arise from:

- optical geometry;
- trace curvature;
- wavelength-surface geometry;
- camera aberration;
- detector placement;
- and fiber-dependent spectrograph response.

Useful measurements include:

- centroid as a function of `Δx`;
- principal-axis angle;
- covariance between `Δx` and `Δy`;
- and wavelength dependence of the angle.

The empirical grid should preserve this tilt.

A derived low-dimensional model may summarize it, but the original empirical
shape should remain available.

---

# Spatial and Wavelength Dependence

The PSF is unlikely to be identical for every fiber and every wavelength.

It may depend on:

- SPECID;
- AMP;
- fiber position;
- wavelength;
- temperature;
- focus;
- illumination;
- and time.

A practical model should begin with a low-dimensional hierarchy:

```text
Spectrograph-Level Baseline
    +
Wavelength Dependence
    +
Fiber-Position Perturbation
    +
Environmental Perturbation
```

The nine arc lines provide discrete wavelength anchors.

The full model can interpolate between them if the PSF changes smoothly.

---

# Model Scope

The physical PSF is primarily a spectrograph and detector property.

A conservative measured Product should preserve the complete ZipCode and Master
Arc provenance.

A reusable PSF model may ultimately be keyed by:

```text
SPECID
AMP
```

with additional dependence on:

- fiber coordinate;
- wavelength;
- temperature;
- and instrument epoch.

IFUSLOT and CONTROLLER should remain available in provenance even if they do not
strongly determine the optical PSF.

---

# Proposed Fiber Grouping

The full detector may not require one independent PSF for every fiber.

A practical first model could use broad fiber groups, analogous to the existing
fiber-profile algorithm.

For example:

- divide the amplifier into several fiber-index regions;
- combine neighboring fibers within each region;
- measure one empirical 2D PSF per region and arc line;
- interpolate the model across fiber position.

This reduces noise and model dimensionality.

The repository should later test whether the residuals require:

- more fiber groups;
- per-fiber perturbations;
- or a continuous fiber-position model.

---

# Decomposing Overlap

Several approaches are possible.

## Iterative Neighbor Subtraction

1. estimate a common PSF;
2. fit amplitudes for neighboring fibers;
3. subtract neighboring models;
4. rebuild the target-fiber PSF;
5. iterate.

This is conceptually simple but may inherit bias from the initial PSF.

## Simultaneous Local Fit

Fit a group of neighboring fiber lines together using one shared or smoothly
varying PSF family.

This respects overlap directly and produces local covariance.

## Robust Stack

Normalize and stack many line cutouts.

Neighbor contamination may average down if its local offsets vary sufficiently,
but repeated fiber geometry can preserve structured residuals.

A robust stack alone should therefore be validated against simultaneous local
fits.

---

# Relation to Long-Range Scatter

The 2D spectral PSF should represent the local monochromatic response.

The long-range power-law scattered-light model remains a separate CCD-scale
Product.

A practical boundary is near:

```text
4 to 5 pixels from the trace in cross-dispersion
```

though the exact 2D boundary should be measured rather than imposed blindly.

The local PSF model and long-range scatter model must define complementary
domains.

Otherwise, line wings can be:

- omitted;
- or counted twice.

---

# Derived LSF

Once the empirical 2D PSF is measured, the LSF should be derived under one or
more extraction operators.

## Aperture LSF

Apply the standard fractional five-pixel aperture.

This produces the LSF relevant to routine VIRUS spectra.

## Profile-Weighted LSF

Apply the profile-extraction weights.

This produces the LSF relevant to profile-extracted spectra.

## Total-Flux LSF

Integrate the complete local 2D PSF over cross-dispersion.

This describes the intrinsic local spectral response independent of one
particular aperture.

All three can differ.

The Product metadata must state which definition is being used.

---

# Resolution Metrics

Recommended per-line and per-fiber-region metrics include:

```text
FWHM in pixels
FWHM in Å
σ-equivalent width
R = λ / FWHM_λ
centroid
asymmetry
skewness
kurtosis
wing fraction
tilt angle
encircled-energy dimensions
```

Because the PSF is not Gaussian, robust alternatives should also be considered:

- width containing 68 percent of the line flux;
- width containing 80 or 90 percent;
- second-moment width;
- matched-filter effective width.

The full empirical model should remain the authoritative Product.

---

# Scientific Uses

The spectral PSF is not currently required for the baseline reduction sequence.

It is nevertheless scientifically valuable.

## Emission-Line Verification

A real unresolved emission line should resemble the instrument PSF in:

- detector position;
- dispersion width;
- cross-dispersion width;
- tilt;
- and fiber-centered morphology.

A cosmic ray may be:

- too narrow;
- off-center;
- misaligned;
- or inconsistent with the local 2D PSF.

A detector defect may persist at one pixel location and fail to follow the
expected fiber and wavelength geometry.

Thus, PSF consistency is strong evidence for or against a real astronomical
line.

## HETDEX and Lyman-Alpha Emitters

For HETDEX, candidate Lyman-alpha emission should resemble the instrumental
spectral PSF, modified by possible astrophysical structure such as:

- velocity offset;
- asymmetry;
- broadening;
- multiple components;
- or extended spatial emission.

The PSF provides the null model for an unresolved line.

Differences from it can then be interpreted as either:

- astrophysical structure;
- or evidence of an artifact.

## Matched Filtering

The empirical 2D PSF can be used as a matched-filter kernel for faint-line
detection.

This improves sensitivity when the model is accurate and allows a direct
likelihood comparison between:

- real fiber-centered line;
- cosmic ray;
- pixel defect;
- and noise.

## Resolution Mapping

The PSF provides a wavelength- and fiber-dependent map of instrumental
resolution.

This is useful for:

- line-width measurements;
- velocity-dispersion inference;
- deconvolution;
- source classification;
- and comparison among spectrographs.

---

# Artifact Rejection

A candidate feature can be compared against several hypotheses:

```text
Instrumental Line PSF
Cosmic Ray
Persistent Pixel Defect
Scattered-Light Residual
Noise
```

Useful discriminants include:

- centroid distance from the trace;
- 2D shape residual;
- tilt consistency;
- wavelength-direction width;
- cross-dispersion width;
- persistence across exposures;
- and neighboring-fiber behavior.

The PSF should therefore be available to detection and classification systems,
not only calibration analytics.

---

# Model Residuals

For each line cutout, the algorithm should preserve:

```text
observed_cutout
modeled_cutout
residual_cutout
valid_pixel_mask
neighbor_model
background_model
```

Residual structure may reveal:

- incorrect tilt;
- asymmetric wings;
- profile variation;
- line blending;
- detector defects;
- or an inadequate model basis.

A scalar FWHM or RMS cannot reveal these patterns.

---

# Required Inputs

The PSF algorithm should consume:

```text
Master Arc image
Master Arc extracted spectra
Trace Product
Wavelength Product
Arc-line identification table
Pixel Mask Product
Variance Product
Scattered-Light Product or background model
```

Environmental and provenance inputs should include:

```text
ambient temperature
observation time
SPECID
AMP
complete ZipCode
Master Hg and Master Cd provenance
```

---

# Product Contract

## Primary Arrays

```text
spectral_psf_2d_grid
spectral_psf_x_offsets
spectral_psf_y_offsets
```

Recommended model dimensions include:

```text
arc line
fiber group
Δx
Δy
```

or an equivalent compact basis.

## Derived Arrays

```text
aperture_lsf
profile_weighted_lsf
total_flux_lsf
resolution_map
tilt_map
```

## Diagnostic Arrays

```text
line_cutout_stack
line_model_residuals
accepted_line_mask
accepted_fiber_mask
neighbor_subtraction_model
background_model
```

## Metadata

```text
master_arc_product
trace_product
wavelength_product
pixel_mask_product
variance_product
line_list
fiber_groups
cutout_size
continuum_filter
line_mask_width
normalization_method
overlap_method
interpolation_method
local_scatter_boundary
temperature
algorithm_version
```

---

# Required QA

QA should evaluate:

- number of accepted fibers per line;
- number of accepted lines per fiber group;
- signal-to-noise;
- centroid agreement with trace and wavelength predictions;
- residual RMS;
- coherent residual structure;
- symmetry;
- tilt stability;
- width stability;
- neighboring-fiber contamination;
- wavelength interpolation quality;
- and consistency among repeated Master Arcs.

A PSF model should fail or warn when too few line/fiber measurements constrain a
region.

---

# Important Assumptions

The proposed model assumes:

- Master Arc lines are sufficiently narrow relative to instrumental resolution;
- the line list is correct;
- trace and wavelength positions are accurate;
- continuum and scattered background can be separated from local line wings;
- neighboring-fiber overlap can be modeled or robustly suppressed;
- PSF variation is smooth with wavelength and fiber position;
- arc illumination provides a useful instrumental PSF baseline;
- and science-source illumination differences do not completely invalidate the
  arc-derived shape.

The final assumption should be tested with bright unresolved science lines where
possible.

---

# Initial Implementation Decisions

- Use Master Arc exposures as the primary PSF calibration source.
- Measure an empirical 2D detector-space PSF.
- Do not assume separability into one-dimensional spectral and fiber profiles.
- Preserve line tilt and axis coupling.
- Use the existing identified arc-line positions as measurement centers.
- Remove continuum/background with robust filtering that masks known lines.
- Build oversampled 2D grids from normalized line cutouts.
- Begin with fiber-group and line-specific models.
- Model neighboring-fiber overlap explicitly where necessary.
- Keep local spectral PSF and long-range scattered light as separate Products.
- Derive aperture and profile-weighted LSFs from the same 2D model.
- Treat resolving power as a derived summary.
- Preserve residual cutouts for QA.
- Make the PSF available to line-detection and artifact-classification systems.
- Do not require PSF modeling for the baseline aperture-reduction path.

---

# Open Questions

- How narrow are the intrinsic Hg and Cd lamp lines relative to VIRUS
  resolution?
- What cutout size best contains the local PSF without entering the long-range
  scatter regime?
- What filter scale removes continuum without subtracting line wings?
- How many fiber groups are required per amplifier?
- Can one shared model describe both amplifiers of a spectrograph?
- How strongly does the PSF vary among the nine arc wavelengths?
- Does temperature mainly change width, tilt, centroid, or higher-order shape?
- How different are arc, LDLS, twilight, sky, and point-source PSFs?
- Is a rotated empirical grid sufficient, or is a more flexible basis needed?
- Should neighboring fibers be fit locally or all 112 together?
- Which resolution metric is most useful for flat-topped profiles?
- How much of HETDEX line-classification performance improves when the full 2D
  PSF is used?
- Can science emission lines refine the arc-derived PSF without introducing
  astrophysical bias?

---

# Repository Goals

VIRUSFlow should:

- build a two-dimensional empirical spectral PSF for every spectrograph;
- map PSF shape across wavelength and fiber position;
- measure line tilt and non-separable structure;
- derive extraction-specific LSFs;
- map resolving power across VIRUS;
- track PSF evolution with temperature and time;
- determine how calibration-unit and science illumination affect PSF shape;
- quantify overlap among neighboring fiber-line images;
- distinguish local PSF wings from CCD-scale scattered light;
- provide matched-filter kernels for faint-line detection;
- distinguish real astronomical emission from cosmic rays and pixel defects;
- support HETDEX emission-line classification and resolution correction;
- preserve residual evidence rather than only scalar width summaries;
- and turn Master Arc exposures into a long-term spectrograph-health and
  scientific-validation resource.
