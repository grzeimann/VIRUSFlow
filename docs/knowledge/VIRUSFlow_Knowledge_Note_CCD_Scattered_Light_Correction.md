# VIRUSFlow Scientific Knowledge Specification

# Working Note: CCD-Scale Scattered-Light Correction

> Status: Initial scientific and architectural specification  
> The paired-amplifier orientation is now inferred from the legacy scatter
> implementation. The remaining validation concerns the exact pixel-center and
> seam convention used when materializing a 2064-row CCD image.

This note defines practical strategies for estimating and subtracting the
long-range scattered-light component from VIRUS detector images.

The physical origin and power-law response are described in the broader
crosstalk and scattered-light knowledge note.

The present note focuses on the correction problem.

The central principle is:

> **Scattered-light correction is a physical-CCD measurement, not an
> independent amplifier correction.**

The two amplifiers on each CCD must be considered together:

```text
Right CCD: RU + RL
Left CCD:  LU + LL
```

---

# Scientific Object

The observed physical-CCD image can be represented as:

```text
D_CCD(x, y)
    =
D_direct(x, y)
    +
S_scatter(x, y)
    +
B_detector(x, y)
    +
N(x, y)
```

where:

- `D_direct` contains the direct fiber-profile cores;
- `S_scatter` is the summed long-range scattered-light contribution;
- `B_detector` contains other additive detector or readout structure;
- and `N` is noise.

The correction seeks to estimate:

```text
S_scatter(x, y)
```

without absorbing:

- real fiber flux;
- spectral structure;
- detector bias or overscan structure;
- or other additive components that should be modeled separately.

---

# Physical Modeling Scope

An amplifier edge is a readout boundary, not a physical boundary to scattered
light.

The response from fibers on one amplifier extends into the paired amplifier.

Therefore, each correction requires:

- both amplifier images from the same exposure;
- both trace maps;
- both masks;
- the physical placement and orientation of both amplifiers;
- and the complete provenance of both amplifier lineages.

The computation should occur in one shared physical CCD coordinate system.

The output may later be split back into amplifier-scoped Products.

---

# Physical CCD Orientation

The legacy scattered-light implementation contains the following transform:

```python
if amp in ['LL', 'RU']:
    ntrace = np.vstack([trace, 2064 - trace])
else:
    ntrace = np.vstack([-trace, trace])
```

This strongly constrains the physical relationship between the paired
amplifiers.

For an amplifier in the canonical reduction orientation:

- `x` is unchanged between paired amplifiers;
- the paired amplifier is reflected in `y`;
- `LL` and `RU` occupy the lower side of their physical CCD;
- `LU` and `RL` occupy the upper side.

A common physical-CCD coordinate system with approximately 2064 rows can
therefore be written as:

```text
Left CCD:
    LL: x_CCD = x, y_CCD = y
    LU: x_CCD = x, y_CCD = 2063 - y

Right CCD:
    RU: x_CCD = x, y_CCD = y
    RL: x_CCD = x, y_CCD = 2063 - y
```

Thus the physical vertical ordering is:

```text
Left CCD:
    LU
    --
    LL

Right CCD:
    RL
    --
    RU
```

The alternative expression in the legacy code places the seam at zero when the
currently processed amplifier is one of the upper members:

```text
LU or RL current coordinates:
    paired lower amplifier = -y
    current upper amplifier = +y
```

These are equivalent relative-coordinate descriptions of the same reflection.

The legacy use of:

```text
2064 - y
```

rather than the canonical array-index transform:

```text
2063 - y
```

is pre-refactor characterization evidence from a one-indexed interpretation.
It is not an alternative coordinate convention.

When materializing a physical `2064 × 1032` CCD image, VIRUSFlow must verify:

- the pixel-center convention;
- whether the CCD seam contains a physical or coordinate gap;
- that the indexed image transform is exactly `2063 - y`.

This is a required implementation-validation test, not an open convention.

The transform should be stored as versioned instrument configuration and tested
by confirming continuity of:

- fiber-group geometry;
- scattered-light wings;
- detector defects;
- and bright-source contributions across the paired-amplifier seam.

---

# Two Primary Correction Strategies

The two principal approaches are:

```text
1. Gap-Constrained Empirical Scatter Model
2. Iterative Full Forward Scatter Model
```

They differ in computational cost, assumptions, and sensitivity to source
structure.

A hybrid approach is likely the most practical long-term solution.

---

# Method 1: Gap-Constrained Empirical Modeling

## Fiber-Group Geometry

Fibers on an amplifier are normally separated by approximately:

```text
8 to 8.5 detector pixels
```

The fiber traces are also arranged into approximately three groups, each
containing roughly 34 fibers.

Larger gaps occur between these groups.

Within those gaps:

- the bright direct profile cores have largely declined;
- neighboring-fiber core overlap is much weaker;
- and the remaining detector signal is dominated more strongly by long-range
  scattered light and other additive backgrounds.

These gaps provide direct empirical samples of the smooth scattered-light
surface.

---

# Gap Identification

The trace Product defines the fiber positions as a function of detector column.

For every detector column or column chunk, the algorithm can:

1. evaluate all fiber traces;
2. sort them in physical detector order;
3. identify the large separations between fiber groups;
4. define detector regions sufficiently far from the neighboring core profiles;
5. reject masked or contaminated pixels;
6. retain the remaining gap pixels as scatter measurements.

The gap locations vary with trace curvature.

They must therefore be derived from the trace rather than treated as fixed
detector rows.

---

# Core-Exclusion Boundary

The direct fiber core dominates close to the trace.

The long-range scattered component becomes dominant approximately:

```text
4 to 5 pixels from a trace center
```

Gap pixels used for scattered-light measurement should lie beyond a configured
core-exclusion distance from every trace.

The exact distance should be validated using the empirical fiber-profile and
power-law measurements.

Too small an exclusion region allows direct fiber light into the scatter model.

Too large an exclusion region discards useful information and may leave too few
constraints.

---

# Building a Two-Dimensional Gap Model

A practical gap-based algorithm may proceed as follows:

1. assemble the paired amplifiers in physical CCD coordinates;
2. divide the detector into broad column chunks;
3. identify valid gap samples within each chunk;
4. robustly summarize the gap signal as a function of physical `x` and `y`;
5. reject cosmic rays, defects, and direct-profile contamination;
6. fit or interpolate a smooth two-dimensional surface;
7. evaluate the model over the full CCD;
8. split the result back into amplifier coordinates.

Possible surface representations include:

- two-dimensional polynomial;
- tensor-product spline;
- robust grid interpolation;
- low-rank basis;
- smoothed empirical mesh;
- or Gaussian-process-like smooth regression if computationally practical.

The representation should be selected by residual behavior rather than visual
smoothness alone.

---

# Column Chunking

The scattered-light surface is expected to vary smoothly in detector space.

Therefore, it is not necessary to estimate a completely independent
cross-dispersion profile at every detector column.

Column chunking can improve robustness by:

- increasing signal-to-noise;
- suppressing bad pixels and cosmic rays;
- reducing computational cost;
- and smoothing over narrow spectral structure.

However, excessively broad chunks can erase real wavelength-dependent scatter
features.

The appropriate chunk scale depends on:

- spectral diversity among fibers;
- brightness of arc or sky features;
- source structure;
- and the desired correction accuracy.

---

# Smoothness Assumption

The gap method relies on a central assumption:

> **The summed scattered-light field varies smoothly across the CCD.**

This is usually plausible because the observed background is the sum of
long-range contributions from many fibers and wavelengths.

The assumption is strongest when:

- illumination is broadly similar among fibers;
- spectra vary smoothly;
- no small number of fibers dominates;
- and the detector contains no strong localized additive artifact.

The assumption weakens for:

- bright stars;
- sparse bright emission lines;
- large fiber-to-fiber spectral diversity;
- saturation;
- highly structured science fields;
- and strong cross-amplifier illumination differences.

---

# Spectral-Similarity Assumption

The gap method does not explicitly attribute scattered light to individual
fibers or wavelengths.

It instead estimates the aggregate smooth detector background.

This works best when the fiber spectra are not extremely diverse.

If one or a few fibers contain exceptionally bright localized spectral
features, the resulting scattered-light pattern may contain structure not
adequately constrained by the gaps or by a low-order smooth surface.

Thus:

```text
Calibration frames and diffuse illumination
    →
often favorable for gap modeling

High-contrast science frames
    →
potentially require forward modeling or hybrid refinement
```

---

# Advantages of Gap Modeling

The gap-constrained method is:

- relatively fast;
- empirically anchored to the actual exposure;
- independent of a precise power-law coefficient model;
- applicable once a trace is available;
- robust for smooth illumination;
- and useful as an initial scatter estimate.

It does not require accurate preliminary spectra for all fibers.

This makes it attractive early in the reduction sequence.

---

# Limitations of Gap Modeling

The gap method can fail or bias the correction when:

- direct profile wings contaminate the selected gaps;
- gap locations are incorrect because of trace errors;
- the scattered-light field contains localized source-driven structure;
- there are too few clean gap pixels;
- detector background terms are mixed with optical scatter;
- amplifier orientation is incorrect;
- or polynomial/spline models extrapolate poorly outside the gaps.

The gaps constrain the scattered-light surface only at selected detector
locations.

The rest of the CCD is inferred through smoothness.

---

# Method 2: Iterative Full Forward Modeling

The full forward model predicts the scattered-light contribution from the
estimated spectrum of every fiber.

For physical CCD position `(x, y)`:

```text
S_scatter(x, y)
    =
Σ over both amplifiers
Σ over fibers f
Σ over sampled x'
F_f(x') K[
    distance_CCD(
        x, y,
        x', trace_f(x')
    )
]
```

where:

- `F_f(x')` is the fiber spectrum;
- `K(r)` is the extended power-law response;
- and distances are evaluated in physical CCD coordinates.

This model directly accounts for the actual spectral diversity of the exposure.

---

# Iterative Circularity

The forward scatter model requires fiber spectra.

Accurate fiber spectra require scattered-light subtraction.

Therefore, the calculation is naturally iterative:

```text
Initial Reduced CCD
    ↓
Preliminary Aperture Extraction
    ↓
Initial Forward Scatter Model
    ↓
Scatter-Subtracted CCD
    ↓
Updated Extraction
    ↓
Updated Scatter Model
    ↓
Convergence
```

The stable five-pixel aperture extraction is the natural initializer.

---

# Need for a Good Starting Point

A forward model can be computationally expensive and unstable when the initial
spectra are poor.

Potential problems include:

- attributing detector background to fiber spectra;
- reinforcing extraction errors;
- overpredicting scatter from contaminated fibers;
- instability around bright sources;
- and sensitivity to inaccurate power-law parameters.

The method is most useful when the initial solution is already reasonably close
to the true direct fiber signal.

This strongly favors a hybrid strategy in which the gap model supplies the
first scatter estimate.

---

# Computational Cost

The naive forward calculation couples:

- every destination detector location;
- every fiber;
- and many source wavelengths.

This is expensive.

Practical acceleration may use:

- sparse wavelength sampling;
- precomputed kernels;
- separable or approximate convolution;
- FFT methods where geometry permits;
- low-rank kernel representations;
- fiber grouping;
- thresholding negligible contributions;
- and parallel CCD computation.

The algorithm should preserve approximation metadata so speed improvements do
not become hidden changes to the physical model.

---

# Method 3: Hybrid Correction

The most promising operational strategy is:

```text
Gap-Constrained Smooth Model
    →
Initial Scatter Subtraction
    →
Aperture Extraction
    →
Forward Power-Law Refinement
    →
Optional Iteration
```

The gap model provides:

- a stable exposure-specific baseline;
- correction of broad additive structure;
- and a better starting point for spectra.

The forward model then adds source-dependent structure not captured by the
smooth gap interpolation.

A hybrid decomposition could be:

```text
S_total
    =
S_gap_smooth
    +
δS_forward
```

where `δS_forward` is constrained to describe only the residual structure
predicted from the fiber spectra.

This may be more stable than allowing the full forward model to determine the
entire background from the start.

---

# Overscan and Detector Background Prerequisites

Previous scattered-light experiments were affected by reduction terms that had
not yet been modeled separately, including overscan-row behavior.

This is a critical architectural lesson:

> **A scattered-light model should not be asked to absorb uncorrected detector
> or readout structure.**

Before fitting optical scatter, the reduction should account for:

- overscan rows or columns;
- residual bias structure;
- amplifier offsets;
- dark structure;
- detector defects;
- and other known electronic backgrounds.

Otherwise, the empirical scatter surface may fit a mixture of:

```text
optical scattered light
+
detector/readout residuals
```

and become physically uninterpretable.

---

# Overscan as a Separate Product

Overscan measurements should produce their own detector-correction Product or
metadata.

The scattered-light algorithm should receive an image in which overscan-derived
corrections have already been applied.

The repository should preserve:

- overscan geometry;
- measurement method;
- fitted structure;
- correction applied;
- and residual diagnostics.

If overscan treatment is unavailable, the scatter Product should be marked as
potentially contaminated by readout structure.

---

# Distinguishing Additive Components

The total additive detector background may contain:

```text
Overscan/Bias Residual
Dark Structure
Long-Range Fiber Scatter
Other Optical Background
Sky or Source Continuum
```

These terms have different physical identities and scopes.

VIRUSFlow should model them separately where possible.

A smooth two-dimensional image is not automatically scattered light.

Component identity must come from:

- calibration context;
- detector geometry;
- trace-relative behavior;
- cross-amplifier continuity;
- and response to changing fiber illumination.

---

# Calibration vs. Science Frames

## Calibration Frames

Master LDLS, Master Twilight, and Master Arc frames often provide:

- high signal;
- relatively broad illumination;
- predictable structure;
- and many constraints in the fiber gaps.

Gap-based smooth modeling may perform well.

## Science Frames

Science images may contain:

- sparse bright sources;
- strong emission lines;
- large dynamic range;
- uneven illumination;
- and highly distinct spectra among fibers.

The gap model remains useful, but a forward-model refinement may be required.

The correction method should therefore be selectable by exposure kind and QA,
not permanently fixed for all images.

---

# Required Inputs

## Gap-Constrained Method

```text
paired reduced amplifier images
paired trace maps
paired pixel masks
physical CCD transforms
core-exclusion distance
column-chunk definition
overscan/bias correction provenance
```

Recommended:

```text
variance maps
fiber status
profile model
```

## Forward Method

All gap-method inputs plus:

```text
preliminary fiber spectra
extended power-law model
spectrograph/CCD coefficients
wavelength maps
iteration parameters
```

---

# Product Scope and Identity

A suitable target is:

```text
ScatteredLightTarget(
    exposure_id,
    SPECID,
    CCD_ID
)
```

with:

```text
CCD_ID = right | left
```

and paired amplifier membership:

```text
right → RU + RL
left  → LU + LL
```

The algorithm-level Product is CCD scoped.

Amplifier-scoped projections may be materialized for downstream tasks.

---

# Product Contract

## Primary Arrays

```text
ccd_scattered_light_model
```

Optional decomposed arrays:

```text
gap_smooth_scatter_model
forward_scatter_refinement
total_scattered_light_model
scatter_subtracted_ccd
```

Amplifier projections:

```text
ru_scatter_model
rl_scatter_model
lu_scatter_model
ll_scatter_model
```

as appropriate for the CCD.

## Diagnostic Arrays

```text
gap_sample_mask
gap_sample_values
core_exclusion_mask
surface_fit_residual
forward_model_residual
iteration_difference
cross_amplifier_scatter_fraction
```

## Metadata

```text
correction_method
participating_amplifiers
physical_ccd_transform
trace_products
overscan_product
bias_product
pixel_mask_products
column_chunks
core_exclusion_distance
surface_model_type
surface_model_order
power_law_model
iteration_count
convergence_metric
algorithm_version
```

---

# Required QA

## Gap Model QA

Evaluate:

- number of valid gap samples;
- direct-profile contamination;
- residual structure in the gaps;
- interpolation behavior away from gaps;
- cross-amplifier continuity;
- sensitivity to model order;
- and dependence on chunk size.

## Forward Model QA

Evaluate:

- convergence;
- residual correlation with bright fibers;
- residual power-law structure;
- sensitivity to initial spectra;
- model amplitude stability;
- and conservation of direct source flux.

## Hybrid QA

Evaluate:

- size of the forward correction relative to the gap baseline;
- whether the refinement improves detector residuals;
- whether it removes real source structure;
- and whether additional iterations materially change the result.

---

# Validation Tests

## Gap Holdout Test

Withhold some gap regions from the fit and predict their values.

This tests whether the smooth surface generalizes.

## Bright-Fiber Test

Measure residual scatter around unusually bright fibers.

## Cross-Amplifier Test

Verify that bright illumination on one amplifier produces the expected modeled
contribution on the paired amplifier.

## Uniform-Illumination Test

Compare gap and forward models for LDLS or twilight exposures.

## Science-Structure Test

Inject or identify bright localized spectra and determine whether the gap model
misses their scattered-light pattern.

## Overscan-Separation Test

Verify that changing overscan treatment does not substantially alter the
inferred optical scatter after proper correction.

---

# Failure Modes

Important failure modes include:

- modeling paired amplifiers in the wrong physical orientation;
- treating an amplifier boundary as a scatter boundary;
- allowing direct fiber cores into the gap sample;
- fitting overscan or bias residuals as optical scatter;
- using too-flexible a polynomial that removes real fiber flux;
- using too-rigid a surface that misses source-driven structure;
- initializing a forward model with contaminated spectra;
- double counting the smooth gap model and full forward prediction;
- and interpreting a visually smooth residual as proof of correct subtraction.

---

# Separation of Responsibilities

## Detector Reduction

Owns:

- overscan correction;
- bias correction;
- dark correction;
- detector masks;
- amplifier-level electronic backgrounds.

## Trace Geometry

Owns:

- fiber locations;
- group-gap locations;
- core-exclusion geometry.

## CCD Assembly

Owns:

- paired-amplifier identity;
- physical coordinate transforms;
- image and mask placement.

## Gap Scatter Algorithm

Owns:

- gap sampling;
- robust two-dimensional surface measurement;
- smooth empirical scatter model.

## Forward Scatter Algorithm

Owns:

- power-law kernel application;
- use of preliminary spectra;
- iterative refinement;
- source-dependent scatter prediction.

## Spectral Extraction

Owns:

- preliminary and updated spectra;
- propagation of scatter correction into extracted Products.

## QA and Analytics

Owns:

- method selection;
- residual evaluation;
- model comparison;
- long-term spectrograph/CCD behavior;
- and determination of when the forward refinement is scientifically justified.

---

# Initial Implementation Decisions

- Treat the physical CCD as the correction scope.
- Model RU with RL and LU with LL.
- Use the inferred LL/LU and RU/RL reflection transforms, with a validation test for the exact seam coordinate.
- Implement overscan correction before optical scatter measurement.
- Begin with the gap-constrained empirical method.
- Derive gap locations from the trace at each column or chunk.
- Exclude pixels within approximately four to five pixels of every trace.
- Build a robust two-dimensional smooth surface from gap samples.
- Preserve gap samples and fit residuals.
- Use the gap result as the initial correction for aperture extraction.
- Support iterative forward modeling as an optional refinement.
- Do not require the forward model for the basic pipeline.
- Prefer a hybrid gap-plus-forward strategy for difficult science frames.
- Keep smooth baseline and forward residual components separately.
- Never allow scattered-light correction to absorb unidentified overscan or bias
  structure silently.
- Fail or mark degraded mode when one paired amplifier is unavailable.

---

# Open Questions

- What exact pixel-center and seam convention should materialize the inferred RU/RL and LU/LL transforms?
- What are the precise fiber-group memberships and gap widths?
- What core-exclusion distance best avoids direct-profile contamination?
- What column-chunk scale preserves spectral structure while remaining robust?
- Is polynomial, spline, grid interpolation, or low-rank modeling most stable?
- How much of the scatter can the gaps constrain near detector edges?
- How should gap measurements be weighted by variance?
- How different are the best models for LDLS, twilight, arc, and science
  exposures?
- When does fiber-to-fiber spectral diversity invalidate the smoothness
  assumption?
- Can the forward refinement be represented as a small perturbation to the gap
  model?
- How many iterations are scientifically useful?
- Which overscan effects previously contaminated scatter measurements?
- How should model uncertainty be propagated into extracted variance?
- Can one global kernel with small spectrograph/CCD perturbations describe the
  forward component?

---

# Repository Goals

VIRUSFlow should:

- reconstruct each physical CCD from its paired amplifiers;
- encode and validate all amplifier-to-CCD orientation transforms;
- use fiber-group gaps as direct exposure-specific scatter measurements;
- quantify the limits of the smooth-scatter assumption;
- separate optical scatter from overscan, bias, and detector backgrounds;
- determine the optimal two-dimensional gap-surface representation;
- measure cross-amplifier scattered-light contributions;
- use stable aperture spectra to initialize forward scatter models;
- determine when full forward modeling provides meaningful scientific benefit;
- develop a hybrid correction that is both robust and source aware;
- track scatter behavior by spectrograph, CCD, wavelength, and time;
- propagate scatter-model uncertainty into later Products;
- and ensure that every scattered-light correction remains physically
  interpretable rather than merely producing a cosmetically smooth detector
  image.
