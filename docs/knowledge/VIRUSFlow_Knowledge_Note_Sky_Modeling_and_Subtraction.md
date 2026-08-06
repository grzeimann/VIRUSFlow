# VIRUSFlow Scientific Knowledge Specification

# Working Note: Sky Modeling, Illumination Correction, and Sky Subtraction

> Status: Initial scientific and architectural specification

Sky subtraction is one of the most important scientific processes in VIRUS
reduction.

For a large fraction of VIRUS science programs, the sky is the dominant source
of noise and one of the principal limits on achievable depth.

The central principle is:

> **A sky model is not only an average spectrum. It is a prediction of what the
> sky should look like in each fiber after accounting for illumination,
> normalization, wavelength sampling, and the local spectral PSF.**

---

# Scientific Object

For fiber `f`, the observed extracted science spectrum may be represented as:

```text
S_f(λ)
    =
O_f(λ)
    +
K_f(λ)
    +
R_f(λ)
    +
N_f(λ)
```

where:

- `O_f(λ)` is astrophysical source flux;
- `K_f(λ)` is the sky contribution;
- `R_f(λ)` contains residual instrumental or calibration structure;
- and `N_f(λ)` is random noise.

The sky-subtraction problem is to estimate:

```text
K_f(λ)
```

for every fiber without removing real astrophysical signal.

---

# Why Sky Subtraction Is Critical

For most VIRUS observations, sky counts dominate the uncertainty over a large
fraction of the wavelength range.

As a result, even a small fractional sky-model error can limit:

- faint continuum recovery;
- weak emission-line detection;
- line-ratio measurements;
- surface-brightness sensitivity;
- stacked-observation depth;
- and large-area diffuse-emission experiments.

Improving sky subtraction can therefore provide greater scientific gain than
small improvements in many earlier calibration steps.

---

# Fundamental Sky Assumption

The baseline VIRUS sky model assumes that the incident sky spectrum is uniform
across the approximately 20-arcminute field of view.

This assumption is not always exactly true.

However, over the VIRUS wavelength range:

```text
approximately 3500–5500 Å
```

the sky is expected to be close to uniform across the field for most wavelengths
and most observations.

A small number of wavelength regions or observing conditions may violate this
assumption more strongly.

The model should therefore begin with field-wide uniformity while preserving the
ability to detect and model residual spatial structure.

---

# Illumination Correction

The physical sky may be approximately uniform, but the measured sky is not
uniform across the focal plane.

The observed pattern depends on:

- focal-plane illumination;
- telescope track position;
- IFUSLOT;
- IFUID;
- fiber normalization;
- spectrograph throughput;
- detector response;
- and the extraction aperture.

The center-track twilight fiber normalization provides a baseline full-system
relative response.

A science exposure then requires an additional illumination correction that maps
the center-track twilight reference state to the actual science-exposure state.

Conceptually:

```text
Measured Sky_f
    =
True Uniform Sky
    ×
Center-Track Fiber Normalization_f
    ×
Exposure Illumination Correction_f
```

The exposure illumination term is essential when the telescope is away from the
center-track reference position.

---

# Exposure-by-Exposure Illumination

The most conservative approach is to determine an illumination correction for
every science exposure.

The correction can be inferred from sky-dominated fibers when enough of the
field is blank.

This is difficult for extended targets that occupy much of the VIRUS field, such
as M33.

For those experiments, the exposure itself may not contain enough uncontaminated
fibers to determine the full illumination pattern.

A repository-scale solution should therefore support both:

```text
Empirical exposure correction
```

and:

```text
Predictive track-position illumination model
```

---

# Track-Position Illumination Model

Repeated observations can be used to learn the illumination pattern as a
function of telescope state.

Potential predictors include:

- track position;
- azimuth;
- focal-plane position;
- IFUSLOT;
- time;
- mirror illumination;
- guider throughput;
- and environmental state.

The model should predict the relative illumination of every fiber compared with
the center-track twilight reference.

Conceptually:

```text
I_f
    =
I(
    IFUSLOT,
    IFUID,
    track position,
    azimuth,
    instrument state
)
```

The predictive model is particularly important when the science target fills
most of the field and exposure-local blank fibers are unavailable.

---

# Full-System Normalization

Successful sky subtraction depends on separating:

```text
Fiber Normalization
Exposure Illumination
Sky Spectrum
Spectral PSF
```

The center-track twilight Product supplies the baseline relative response.

The exposure illumination correction adjusts that baseline to the actual
telescope state.

Only after these corrections can spectra from different fibers be compared as
measurements of one common incident sky.

An error in either normalization term appears directly as a sky-subtraction
residual.

---

# Blank-Fiber Identification

The sky model is constructed from fibers believed to contain little or no source
flux.

Blank-fiber identification may use two complementary sources of evidence:

```text
External Source Catalogs
Data-Driven Spectral Selection
```

---

# External Catalog Masking

After astrometry is applied, external photometric catalogs can identify fibers
near known astronomical sources.

For each catalog source:

1. determine its sky position;
2. evaluate the source-to-fiber distance;
3. mask fibers within a configured radius;
4. optionally expand the radius based on source brightness, seeing, and source
   extent.

This method is valuable because it does not depend on the spectral appearance of
the source in the VIRUS data.

It can mask:

- stars;
- galaxies;
- known extended sources;
- and bright neighboring objects.

Its limitations include:

- catalog incompleteness;
- inaccurate source sizes;
- wavelength-dependent morphology;
- uncataloged emission-line sources;
- and diffuse emission.

---

# Data-Driven Blank-Fiber Selection

The spectra themselves can identify fibers inconsistent with the common sky.

A typical process is:

1. construct a preliminary sky estimate;
2. normalize each fiber relative to that estimate;
3. identify fibers close to unity over suitable wavelength ranges;
4. reject positive or negative outliers;
5. rebuild the sky;
6. iterate.

The selection may use:

- broadband ratio;
- robust residual width;
- emission-line excess;
- continuum excess;
- spectral-shape difference;
- and neighboring-fiber context.

A fiber should not be declared blank from one scalar threshold alone.

---

# Combined Blank-Fiber Policy

The strongest blank-fiber selection combines:

```text
Catalog Mask
    +
Data-Driven Mask
    +
Instrument Mask
```

where the instrument mask includes:

- dead fibers;
- weak fibers;
- detector defects;
- poor wavelength solutions;
- poor normalization;
- and high scattered-light contamination.

The final sky-fiber set should be preserved as an explicit Product or mask.

---

# Native-Wavelength Sky Construction

Each fiber samples the sky on a slightly different native wavelength grid.

This is normally treated as a complication.

For sky construction, it is also an advantage.

Combining many fibers on their native wavelength grids provides dense
sub-pixel sampling of the common sky spectrum.

Conceptually:

```text
Fiber 1 samples λ1
Fiber 2 samples λ2
Fiber 3 samples λ3
...
```

where the grids are offset slightly from one another.

Together they create a highly oversampled representation of the sky.

---

# Oversampled Sky Model

A suitable construction is:

1. retain each accepted sky fiber on its native wavelength grid;
2. divide by the full system normalization;
3. gather all valid wavelength-flux samples;
4. sort them by wavelength;
5. robustly combine or smooth them onto an oversampled sky representation;
6. evaluate the model back on each fiber's native wavelength grid.

This avoids rectifying every fiber before the sky model is built.

It also preserves the information provided by the slight wavelength offsets
among fibers.

The output should be a continuous or finely sampled sky model:

```text
K_mean(λ)
```

that can be interpolated to:

```text
K_mean[λ_f(x)]
```

for each fiber.

---

# Advantages of Native-Grid Combination

Building the sky in native wavelength coordinates:

- avoids early rectification covariance;
- provides natural oversampling;
- reduces pixel-phase artifacts;
- creates a smooth high-signal sky model;
- and uses the diversity of wavelength solutions constructively.

The method depends critically on accurate wavelength calibration.

Wavelength errors broaden or distort the combined sky features.

---

# Spectral PSF Mismatch

A common sky spectrum does not appear identically in every fiber.

Each fiber and amplifier has its own spectral PSF or LSF.

Therefore:

```text
Observed Sky_f
    =
Common Incident Sky
    convolved with
LSF_f
```

A sky model constructed by averaging many fibers has an effective resolution
that may differ from the local resolution of any one fiber.

Interpolating the common model onto a fiber's wavelength grid does not, by
itself, reproduce that fiber's exact line shape.

This is one of the principal causes of structured sky-subtraction residuals.

---

# Direct LSF Forward Modeling

The most physical approach is:

1. infer a high-resolution incident sky spectrum;
2. evaluate the wavelength grid for each fiber;
3. convolve the sky with that fiber's measured LSF;
4. apply the full-system normalization;
5. compare with the observed spectrum.

Conceptually:

```text
K_f(λ)
    =
I_f(λ)
    ×
[
K_intrinsic(λ)
    ⊗
LSF_f(λ)
]
```

where `I_f` includes fiber and exposure illumination corrections.

The 2D spectral PSF and derived LSF Products provide the needed instrumental
response.

This method is physically interpretable but requires accurate LSF models and is
computationally more demanding.

---

# Principal-Component Residual Modeling

Historically, sky residuals have also been modeled empirically using principal
components.

Residuals can be accumulated across:

- fibers;
- amplifiers;
- exposures;
- fiber position;
- and repeated sky observations.

A relatively small number of components, often approximately:

```text
5 to 7
```

can capture much of the structured mismatch caused by:

- spectral PSF variation;
- wavelength offsets;
- normalization errors;
- and repeatable sky-model residuals.

The model can be written:

```text
Residual_f(λ)
    ≈
Σ_k a_fk PC_k(λ)
```

---

# Advantages of PCA

PCA is:

- effective;
- computationally efficient;
- flexible;
- data driven;
- and capable of describing residual structures that are difficult to model
  analytically.

It can significantly reduce line-shaped residuals without requiring a perfect
physical LSF model.

---

# Risks of PCA

PCA can absorb real astrophysical signal.

This is especially dangerous when:

- the source spectrum resembles a sky residual component;
- extended emission appears in many fibers;
- the target fills much of the field;
- the same astrophysical feature repeats across observations;
- or too many components are allowed.

PCA should therefore be treated as a constrained residual model, not an
unrestricted cleanup step.

The Product should preserve:

- component basis;
- fitted coefficients;
- wavelength masks;
- number of components;
- and the amount of flux removed.

---

# Physical and Empirical Hybrid

A strong long-term strategy is:

```text
Physical Mean Sky Model
    +
LSF-Based Prediction
    +
Small PCA Residual Correction
```

The physical model should explain most of the sky.

The PCA basis should capture only residual structure left by imperfect
instrument modeling.

As the LSF, illumination, and normalization models improve, the required PCA
amplitude and number of components should decrease.

This provides a measurable indication that the physical model is improving.

---

# Extended Targets

For targets such as M33, a large fraction of the VIRUS field may contain real
source emission.

In this regime:

- local blank-fiber selection can fail;
- data-driven clipping can classify diffuse source emission as sky;
- PCA can absorb real astrophysical structure;
- and empirical exposure illumination becomes difficult to measure.

The pipeline must support external or repository-derived priors for:

- illumination;
- sky spectrum;
- sky temporal behavior;
- and source masks.

Potential strategies include:

- dedicated offset-sky observations;
- track-position illumination models;
- sky models learned from neighboring exposures;
- external source geometry;
- and joint modeling of source plus sky.

---

# Spatial Sky Variation

The default model assumes one incident sky spectrum across the field.

The repository should test this assumption.

Possible spatial extensions include:

```text
K(λ, ξ, η)
    =
K0(λ)
    +
a(λ) ξ
    +
b(λ) η
```

or another low-order focal-plane model.

A spatial sky term should be introduced only when supported by evidence.

Otherwise, added degrees of freedom can absorb:

- diffuse source emission;
- illumination errors;
- and calibration residuals.

The distinction between true sky gradient and instrumental illumination error is
especially important.

---

# Temporal Sky Variation

Sky brightness and spectral structure vary with time.

For one science exposure, the sky model should use data sufficiently close in
time to represent:

- continuum brightness;
- airglow intensity;
- twilight contamination;
- and transient atmospheric conditions.

Combining sky fibers within one exposure is ideal when enough blank fibers are
available.

Combining multiple exposures can increase signal-to-noise but may blur temporal
variation.

The model should preserve its temporal support explicitly.

---

# Sky Subtraction and Stacking

Small coherent sky residuals can become the dominant limitation when many
observations are stacked.

Random noise decreases approximately with the square root of the number of
exposures.

Repeatable sky-model residuals do not.

Therefore, a reduction that appears adequate for one exposure may fail to
improve at the expected rate in a deep stack.

Sky-model analytics should measure:

- residual coherence;
- repeatability;
- wavelength dependence;
- and depth scaling with exposure count.

This is essential for experiments designed to reach very low surface brightness
or line-flux limits.

---

# Iterative Sky Modeling

A practical sky-subtraction sequence is:

```text
Preliminary Reduced Spectra
    ↓
Initial Blank-Fiber Selection
    ↓
Initial Oversampled Mean Sky
    ↓
Initial Sky Subtraction
    ↓
Refined Source and Outlier Mask
    ↓
Exposure Illumination Correction
    ↓
LSF or PCA Residual Modeling
    ↓
Final Sky Prediction per Fiber
```

The number of iterations should be limited and convergence monitored.

---

# Model Scope

Different parts of sky modeling have different natural scopes.

## Exposure Scope

Owns:

- incident sky spectrum;
- blank-fiber set;
- exposure illumination correction;
- temporal sky state.

## Fiber Scope

Owns:

- wavelength sampling;
- LSF;
- fiber normalization;
- local mask;
- predicted sky spectrum.

## Amplifier or Spectrograph Scope

Owns:

- PCA basis;
- LSF family;
- residual behavior;
- wavelength-calibration structure.

## Observation-Set Scope

Owns:

- repeated-exposure consistency;
- deep-stack residuals;
- shared source masks;
- and long-term illumination constraints.

---

# Product Decomposition

Useful Products include:

```text
sky_fiber_mask
incident_sky_spectrum
exposure_illumination_correction
fiber_sky_prediction
sky_subtracted_spectrum
sky_residual_components
```

These should remain separately traceable.

A single final sky-subtracted array is not enough to understand or reproduce the
correction.

---

# Sky-Fiber Mask Product

Recommended arrays:

```text
sky_fiber_mask
catalog_source_mask
data_outlier_mask
instrument_quality_mask
```

Recommended metadata:

```text
catalogs_used
source_mask_radius_policy
seeing
astrometry_product
selection_iterations
relative_residual_thresholds
valid_fiber_count
```

---

# Incident Sky Product

Recommended arrays:

```text
incident_sky_wavelength
incident_sky_flux
incident_sky_variance
```

Recommended metadata:

```text
exposure_id
native_grid_input_count
oversampling_method
robust_combination_method
wavelength_range
temporal_support
field_uniformity_assumption
```

---

# Exposure Illumination Product

Recommended arrays:

```text
exposure_illumination_correction
```

Recommended metadata:

```text
center_track_twilight_reference
track_position
azimuth
empirical_or_predictive
model_version
valid_blank_fibers
normalization_convention
```

---

# Fiber Sky Prediction Product

Recommended arrays:

```text
fiber_sky_prediction
fiber_sky_variance
```

Optional arrays:

```text
lsf_convolved_sky
pca_residual_correction
sky_model_residual
```

Recommended metadata:

```text
incident_sky_product
fiber_normalization_product
illumination_product
wavelength_product
lsf_product
pca_basis_product
number_of_components
```

---

# Required QA

## Blank-Fiber QA

Evaluate:

- number and distribution of selected fibers;
- catalog-mask completeness;
- residual source contamination;
- focal-plane coverage;
- and stability across iterations.

## Mean-Sky QA

Evaluate:

- residual scatter among blank fibers;
- line-profile sharpness;
- wavelength alignment;
- temporal consistency;
- and dependence on the selected fiber subset.

## Illumination QA

Evaluate:

- smoothness across focal-plane position;
- dependence on track position;
- consistency with twilight reference;
- and residual spatial trends after correction.

## Fiber-Prediction QA

Evaluate:

- normalized residual width;
- line-shaped residuals;
- amplifier dependence;
- fiber-position dependence;
- LSF mismatch;
- and PCA coefficient distributions.

## Deep-Stack QA

Evaluate:

- noise scaling with exposure count;
- coherent residual floor;
- repeated wavelength features;
- and source-flux preservation.

---

# Variance Propagation

The sky prediction has uncertainty.

For:

```text
S_sub = S_obs - K_pred
```

the formal variance is:

```text
Var(S_sub)
    =
Var(S_obs)
    +
Var(K_pred)
```

when covariance is neglected.

The sky-model variance should include contributions from:

- finite blank-fiber sampling;
- illumination-correction uncertainty;
- LSF-model uncertainty;
- interpolation;
- PCA coefficient uncertainty;
- and temporal mismatch where relevant.

In practice, empirical residual calibration from blank fibers remains essential.

---

# Failure Modes

Important failure modes include:

- using source-contaminated fibers as sky;
- treating illumination gradients as true sky variation;
- treating true sky variation as instrumental illumination;
- rectifying too early and losing native-grid oversampling;
- ignoring fiber-dependent LSF differences;
- using too many PCA components;
- allowing PCA to remove extended astrophysical emission;
- estimating exposure illumination from a target-filled field;
- using one sky spectrum across exposures with changing airglow;
- and judging success only from visually smooth residuals.

---

# Separation of Responsibilities

## Astrometry and Catalog Matching

Owns:

- fiber sky positions;
- external source masks;
- source-to-fiber distances.

## Fiber Normalization

Owns:

- center-track relative system response.

## Exposure Illumination

Owns:

- correction from twilight reference to actual science-exposure illumination.

## Sky Selection

Owns:

- blank-fiber identification;
- catalog and data masks;
- iterative rejection.

## Mean-Sky Algorithm

Owns:

- native-grid sample gathering;
- oversampled incident sky construction;
- robust uncertainty.

## LSF Sky Prediction

Owns:

- fiber-specific spectral-resolution transformation.

## PCA Residual Model

Owns:

- constrained empirical residual components;
- coefficients;
- and preservation diagnostics.

## QA and Analytics

Owns:

- field-uniformity tests;
- source-preservation tests;
- deep-stack behavior;
- and comparison among physical and empirical methods.

---

# Initial Implementation Decisions

- Treat sky modeling as an exposure-scope process.
- Assume one incident sky spectrum across the field initially.
- Test rather than hide the field-uniformity assumption.
- Apply full fiber normalization and exposure illumination correction before
  combining sky fibers.
- Use catalog masking after astrometry where available.
- Refine masks with data-driven iterative rejection.
- Build the common sky from native wavelength samples.
- Preserve the natural oversampling produced by fiber-to-fiber wavelength
  offsets.
- Interpolate the incident sky back to each fiber's native wavelength grid.
- Begin with a single common sky model plus constrained empirical residual
  correction.
- Support fiber-specific LSF forward modeling as the preferred physical
  improvement.
- Limit PCA to a small, validated number of components.
- Preserve the PCA correction separately from the physical sky model.
- Require special handling for fields dominated by extended targets.
- Preserve all intermediate Products and masks.
- Evaluate performance in repeated-exposure stacks, not only single exposures.

---

# Open Questions

- How uniform is the true sky across the VIRUS field as a function of
  wavelength?
- Which wavelengths require an explicit spatial sky model?
- What is the best exposure illumination model as a function of track position?
- How many blank fibers are required for a stable sky?
- What source-mask radii are appropriate as functions of seeing and magnitude?
- How should diffuse sources be protected from data-driven masking?
- What oversampled wavelength representation best preserves sky features?
- How should the incident high-resolution sky be regularized?
- How accurately can the measured LSF predict fiber-specific sky lines?
- How many PCA components are needed after improved LSF modeling?
- How can PCA source-flux loss be measured and bounded?
- What is the best strategy for M33-like target-filled fields?
- Can neighboring exposures provide reliable sky priors?
- How should sky-model variance be propagated?
- Which residual metrics best predict the depth floor in stacked data?

---

# Repository Goals

VIRUSFlow should:

- make sky modeling a first-class exposure-level scientific Product;
- identify blank fibers using catalogs, data, and instrument quality together;
- exploit native-grid wavelength diversity to build an oversampled sky;
- separate incident sky from fiber normalization and exposure illumination;
- learn focal-plane illumination as a function of track position;
- support target-filled fields where local empirical illumination is impossible;
- predict the sky at each fiber's wavelength sampling and spectral resolution;
- replace empirical PCA corrections progressively with physical LSF modeling;
- constrain PCA so that it does not erase real astrophysical signal;
- quantify true spatial sky variation across the field;
- propagate sky-model uncertainty into science spectra;
- measure coherent residual floors in deep stacks;
- determine why sky subtraction fails for particular fibers, wavelengths, or
  observing states;
- and turn accumulated sky residuals into improved models of illumination,
  spectral resolution, normalization, and instrument behavior.
