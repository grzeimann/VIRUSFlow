# VIRUSFlow Scientific Knowledge Specification

# Working Note: Variance, Covariance, and Empirical Uncertainty Calibration

> Status: Initial scientific and architectural specification

This note defines how VIRUSFlow should represent and propagate statistical
uncertainty from detector pixels to reduced fiber spectra.

The central principle is:

> **A propagated variance model is a scientific prediction that must be tested
> against the observed noise of the reduced data.**

Formal propagation is necessary, but it is not sufficient. The repository
should preserve both:

1. the variance predicted from detector and calibration models, and
2. the empirical correction required for that prediction to match observed
   blank-spectrum behavior.

---

# Statistical Objects

VIRUSFlow should distinguish:

```text
Variance
Covariance
Systematic Uncertainty
Empirical Noise Correction
```

These are related but not interchangeable.

## Variance

Variance describes the expected squared uncertainty of one pixel or spectral
sample.

```text
Var(x) = σ²(x)
```

## Covariance

Covariance describes correlated uncertainty between two measurements.

```text
Cov(x_i, x_j)
```

Covariance can arise when:

- neighboring detector pixels contribute to one extracted value;
- one detector pixel contributes to multiple rectified samples;
- fiber profiles overlap;
- sky models are shared among many fibers;
- or interpolation mixes neighboring spectral samples.

## Systematic Uncertainty

Systematic uncertainty represents effects not adequately described as
independent random noise, including:

- imperfect bias or dark correction;
- profile mismatch;
- scattered-light residuals;
- wavelength-calibration error;
- fiber-normalization error;
- sky-subtraction error;
- and flux-calibration error.

## Empirical Noise Correction

An empirical correction rescales the propagated variance when observed blank
spectra show that the formal model is systematically too small or too large.

---

# Detector-Level Noise Model

The initial detector model includes:

- read noise,
- Poisson counting noise.

In electron units:

```text
σ²_pixel
    =
σ²_read
    +
σ²_Poisson
```

For a measured signal `N_e` in electrons:

```text
σ²_Poisson ≈ N_e
```

so:

```text
σ²_pixel ≈ RN² + N_e
```

where `RN` is the read noise in electrons.

This is the standard high-count approximation.

---

# Low-Count Regime

At sufficiently low count levels, representing Poisson noise only as a symmetric
Gaussian variance is incomplete.

The underlying count distribution is discrete and asymmetric.

Additional complications can include:

- bias subtraction producing negative measured values;
- dark subtraction;
- clipping or masking;
- uncertain background estimates;
- and conversion between ADU and electrons.

For routine VIRUS reductions, the net effect of these approximations may be
small, but it is unlikely to be exactly zero.

The repository should therefore distinguish:

```text
physical Poisson count expectation
```

from:

```text
observed bias-subtracted pixel value
```

A negative reduced pixel does not imply negative Poisson variance.

The Poisson term should be based on an appropriate non-negative expectation or
forward model, not blindly on the final background-subtracted value.

---

# Gain and Units

Noise propagation should occur in a clearly defined unit system.

The preferred detector-space unit is:

```text
electrons
```

because:

- Poisson variance is naturally expressed in electrons;
- read noise is normally represented in electrons;
- and gain conversion can be performed once at the detector-reduction stage.


under the adopted gain convention.

The Product must preserve:

- data units;
- variance units;
- gain source;
- gain value;
- and whether gain uncertainty is propagated.

---

# Additive Corrections

For independent additive operations:

```text
Z = A ± B
```

the variance is:

```text
Var(Z)
    =
Var(A)
    +
Var(B)
```

when covariance is neglected.

Relevant additive steps include:

- bias subtraction;
- dark subtraction;
- scattered-light subtraction;
- background subtraction;
- and sky subtraction.

The uncertainty of the subtracted model must be included when it is known.

Subtracting a deterministic array without propagating its uncertainty produces
an underestimated variance.

---

# Master Calibration Uncertainty

A master calibration is itself an estimate constructed from finite data.

Examples include:

- Master Bias;
- Master Dark;
- Master LDLS;
- Master Twilight;
- Master Arc;
- scattered-light models;
- sky models.

Their uncertainties may be much smaller than the science-frame counting noise,
but they are not necessarily zero.

For a science image corrected by a Master Bias:

```text
I_corrected = I_raw - B_master
```

the variance is conceptually:

```text
Var(I_corrected)
    =
Var(I_raw)
    +
Var(B_master)
```

when the two are independent.

The repository should record whether calibration-product variance was:

- directly propagated;
- approximated;
- considered negligible;
- or unavailable.

---

# Multiplicative Operations

For:

```text
Z = A × B
```

and neglected covariance, the approximate variance is:

```text
Var(Z)
    ≈
B² Var(A)
    +
A² Var(B)
```

For division:

```text
Z = A / B
```

the approximate variance is:

```text
Var(Z)
    ≈
Var(A) / B²
    +
A² Var(B) / B⁴
```

These relations apply to operations such as:

- gain conversion;
- fiber normalization;
- aperture-capture correction;
- throughput correction;
- flux calibration;
- and atmospheric correction.

If the uncertainty in the multiplicative calibration is not available, the
statistical variance Product should not silently imply that the calibration is
exact.

Instead, VIRUSFlow should preserve a separate calibration-uncertainty term or
metadata indicating that the uncertainty is omitted.

---

# Aperture Extraction Variance

The standard fractional aperture extraction is particularly straightforward.

For detector values `D_i` with aperture weights `w_i`:

```text
S
    =
(1 / W)
Σ_i w_i D_i
```

where:

```text
W = Σ_i w_i = 5
```

for the standard five-pixel aperture.

If detector pixels are treated as independent:

```text
Var(S)
    =
(1 / W²)
Σ_i w_i² Var(D_i)
```

This naturally includes the fractional outer-pixel weights.

The algorithm should use exactly the same geometric weights for the flux and
variance extraction.

---

# Masked Pixels in Aperture Extraction

If one or more aperture pixels are invalid, there are several possible
conventions:

1. reject the spectral sample;
2. retain the original normalization and treat missing pixels as zero;
3. renormalize by the remaining effective aperture weight;
4. estimate the missing contribution from a profile model.

These choices have different statistical and systematic behavior.

A reimplementation must state explicitly:

- whether the effective aperture width changes;
- how the variance is rescaled;
- and whether the output remains compatible with the twilight fiber
  normalization.

Silent renormalization can change the aperture-capture fraction.

Silent zero filling can bias the flux low.

The baseline Product should preserve:

```text
effective_aperture_weight
valid_aperture_fraction
```

for every spectral sample.

---

# Why Aperture Extraction Simplifies Covariance

The aperture estimator is a direct weighted sum of detector pixels.

If neighboring apertures do not share detector pixels, the extraction itself
does not create direct mathematical covariance between fibers.

Physical overlap and crosstalk still create contamination, but that is not
identical to covariance generated by a coupled inversion.

Therefore, for ordinary fibers and moderate brightness contrasts, treating
aperture-extracted spectra as approximately independent is a defensible
operational choice.

This approximation becomes weaker for:

- bright stars;
- strong emission lines;
- saturated profiles;
- broad profiles;
- and unusually strong local crosstalk.

---

# Profile and Forward Extraction Variance

A simultaneous profile extraction can be written:

```text
D = P F + N
```

where:

- `D` is the detector-pixel vector;
- `P` is the profile design matrix;
- `F` is the vector of fiber fluxes.

For a weighted linear solution:

```text
F_hat
    =
(Pᵀ C_D⁻¹ P)⁻¹ Pᵀ C_D⁻¹ D
```

the formal covariance is:

```text
C_F
    =
(Pᵀ C_D⁻¹ P)⁻¹
```

when the profile model is exact.

This covariance can be banded or dense depending on profile overlap and the
scope of the fit.

The major challenge is that the formal covariance does not include profile-model
error unless that uncertainty is represented explicitly.

A precise inversion of an imperfect model can therefore underestimate the true
uncertainty.

---

# Covariance as a Deliberate Approximation

VIRUSFlow should not describe omitted covariance as an accidental implementation
failure.

It is often a deliberate approximation made because carrying complete
covariance is computationally and operationally expensive.

The repository should record:

```text
covariance_propagation = none
```

or:

```text
covariance_propagation = local_banded
```

or another explicit policy.

Possible compact alternatives include:

- nearest-neighbor covariance;
- banded covariance;
- correlation length;
- variance-inflation factors;
- covariance-risk flags;
- and resolution-element noise estimates.

The correct representation depends on the downstream science use.

---

# Rectification to a Common Wavelength Grid

Rectification maps spectra from their native detector wavelength sampling onto a
shared output grid.

This operation mixes neighboring spectral samples and therefore modifies both
variance and covariance.

For linear interpolation:

```text
F_out
    =
(1 - α) F_i
    +
α F_(i+1)
```

the propagated variance, neglecting input covariance, is:

```text
Var(F_out)
    =
(1 - α)² Var(F_i)
    +
α² Var(F_(i+1))
```

If input covariance exists:

```text
Var(F_out)
    =
(1 - α)² Var(F_i)
    +
α² Var(F_(i+1))
    +
2 α(1 - α) Cov(F_i, F_(i+1))
```

Even if the native samples are independent, adjacent output samples generally
share input samples and become correlated.

---

# Linear Rectification

Linear rectification is attractive because it is:

- fast;
- deterministic;
- local;
- comparatively robust;
- and easy to propagate formally.

Its limitation is that ordinary point interpolation does not conserve integrated
flux.

It estimates the value of a sampled function at a new coordinate rather than
integrating flux over the boundaries of the new output bin.

The resulting error may be acceptable for many applications, but it should be
recognized as an approximation.

---

# Flux-Conserving Resampling

A flux-conserving method treats both input and output samples as finite spectral
bins.

Each output bin receives contributions according to wavelength-bin overlap.

Conceptually:

```text
F_out,j
    =
Σ_i a_ji F_in,i
```

where `a_ji` is the fractional overlap or flux-transfer coefficient.

The propagated covariance is:

```text
C_out
    =
A C_in Aᵀ
```

and the diagonal variance is:

```text
Var_out,j
    =
Σ_i a_ji² Var_in,i
```

when input covariance is ignored.

This is more physically faithful for integrated flux but still introduces
correlations among neighboring output bins.

The choice between linear point interpolation and flux-conserving bin overlap
should be explicit in the Product metadata.

---

# Higher-Order Interpolation

Cubic splines and other higher-order interpolators can produce smoother spectra,
but they:

- mix a wider range of input samples;
- create longer-range covariance;
- can overshoot;
- can behave poorly near gaps;
- and may be more sensitive to outliers.

Their formal smoothness does not guarantee better scientific fidelity.

The historical preference for linear interpolation reflects a reasonable choice
to favor robustness and locality over nominal smoothness.

---

# Native-Grid and Rectified Products

VIRUSFlow should preserve a distinction between:

```text
native extracted spectrum
```

and:

```text
rectified spectrum
```

The native spectrum retains the direct detector sampling and usually has the
simplest noise structure.

The rectified spectrum is easier to compare and combine but has interpolation
covariance.

Whenever possible, scientifically sensitive fitting should be able to use the
native-grid Product.

---

# Why Formal Propagation Often Fails to Match Observed Noise

In final blank spectra, the measured sample variance often differs from the
formally propagated variance.

An empirical multiplicative correction is then required.

Typical correction factors may be approximately:

```text
0.8 to 1.2
```

with many cases modestly above one, and occasional values reaching roughly:

```text
1.0 to 1.6
```

The exact convention must be explicit:

- correction to standard deviation;
- or correction to variance.

If a factor `k` multiplies the standard deviation:

```text
σ_corrected = k σ_formal
```

then:

```text
Var_corrected = k² Var_formal
```

The repository must never store an ambiguous “error scale factor.”

---

# Possible Sources of Variance Mismatch

A post-facto correction can absorb many effects, including:

- omitted calibration-product uncertainty;
- interpolation covariance;
- residual scattered light;
- residual sky structure;
- incorrect Poisson expectation at low counts;
- wavelength-registration error;
- profile or aperture-capture variation;
- imperfect masking;
- detector correlations;
- background-model uncertainty;
- normalization uncertainty;
- extraction covariance;
- and genuine astrophysical signal in fibers assumed to be blank.

The empirical factor is therefore diagnostically valuable, but it does not by
itself identify the missing physical term.

---

# Blank-Fiber Noise Measurement

Blank or sky-dominated fibers provide an empirical test of the complete
reduction model.

A comparison should evaluate normalized residuals:

```text
r
    =
(data - expected_model)
/
σ_formal
```

If the error model is correct and the residual model is adequate, the robust
width of `r` should be near one.

The empirical correction can be estimated as:

```text
k = robust_width(r)
```

using an appropriate robust statistic.

This can be measured as a function of:

- wavelength;
- amplifier;
- spectrograph;
- exposure;
- sky brightness;
- signal level;
- temperature;
- and reduction stage.

A single global factor may conceal important structure.

---

# Defining “Blank”

A blank fiber is not automatically free of signal.

Potential contamination includes:

- unresolved sources;
- diffuse astrophysical emission;
- scattered light from bright sources;
- sky-subtraction residuals;
- and detector defects.

Blank-fiber selection should use:

- astrometric information;
- source masks;
- brightness thresholds;
- neighboring-fiber context;
- and iterative outlier rejection.

The selection policy must be preserved as provenance for the empirical noise
factor.

---

# Empirical Variance Calibration Product

The empirical correction should be a separate Product rather than silently
multiplying the variance array inside an unrelated algorithm.

Possible scopes include:

```text
exposure-wide
amplifier
spectrograph
wavelength interval
signal-level interval
```

A useful initial factorization may be:

```text
Var_calibrated(λ)
    =
k²(λ) Var_formal(λ)
```

with the simplest implementation using one factor per amplifier or exposure.

The Product should preserve:

- factor convention;
- wavelength range;
- blank-fiber selection;
- number of samples;
- robust width estimator;
- clipping policy;
- and uncertainty on the factor.

---

# Statistical vs. Calibration Uncertainty

The corrected variance should not be expected to represent every systematic
uncertainty.

A useful decomposition is:

```text
Total Uncertainty
    =
Statistical Variance
    +
Calibration Uncertainty
    +
Model Systematics
```

where practical.

For example:

- read noise and Poisson noise belong in statistical variance;
- fiber-normalization uncertainty may be a calibration term;
- absolute flux-scale uncertainty may remain a separate systematic;
- wavelength uncertainty may be stored as a coordinate uncertainty rather than
  flux variance.

Collapsing all effects into one diagonal variance array can make downstream
interpretation ambiguous.

---

# Product Contract

## Detector Variance Product

Recommended arrays:

```text
pixel_variance
poisson_variance
read_noise_variance
calibration_variance
```

Recommended metadata:

```text
data_units
variance_units
gain
read_noise
poisson_model
low_count_policy
calibration_terms_included
covariance_policy
algorithm_version
```

## Extracted Variance Product

Recommended arrays:

```text
extracted_variance
effective_aperture_weight
valid_aperture_fraction
```

Recommended metadata:

```text
extraction_method
aperture_width
fractional_weights
output_scale_convention
covariance_policy
```

## Rectified Variance Product

Recommended arrays:

```text
rectified_variance
```

Optional supporting arrays:

```text
resampling_weights
local_covariance
correlation_length
```

Recommended metadata:

```text
native_spectrum_product
output_wavelength_grid
resampling_method
flux_conserving
covariance_retained
```

## Empirical Noise Calibration Product

Recommended arrays or scalars:

```text
noise_scale_factor
noise_scale_uncertainty
```

Recommended metadata:

```text
factor_applies_to = standard_deviation | variance
scope
wavelength_range
blank_fiber_selection
sample_count
robust_estimator
clipping_policy
formal_variance_product
reduction_stage
```

---

# Required QA

## Detector-Variance QA

Evaluate:

- read-noise consistency;
- variance behavior versus signal level;
- negative reduced pixels;
- saturation;
- low-count behavior;
- and agreement between repeated exposures.

## Extraction-Variance QA

Evaluate:

- exact use of aperture weights;
- masked-pixel handling;
- edge rejection;
- effective aperture width;
- and comparison with repeat extractions.

## Rectification QA

Evaluate:

- variance propagation through interpolation weights;
- flux conservation;
- correlated residual structure;
- and dependence on output-grid spacing.

## Empirical Noise QA

Evaluate:

- robust width of normalized blank residuals;
- wavelength dependence;
- amplifier and spectrograph dependence;
- signal-level dependence;
- sky-brightness dependence;
- and stability through time.

---

# Failure Modes

Important failure modes include:

- using negative reduced counts as negative Poisson variance;
- neglecting calibration-product uncertainty without recording the choice;
- applying flux interpolation but propagating errors as if samples were copied;
- rescaling standard deviation while documenting a variance factor;
- estimating blank noise from contaminated fibers;
- absorbing coherent systematics into a single scalar factor;
- double counting covariance through both explicit terms and empirical
  inflation;
- and treating a diagonal corrected variance as a complete uncertainty model.

---

# Separation of Responsibilities

## Detector Reduction

Owns:

- gain conversion;
- read-noise contribution;
- Poisson expectation;
- bias and dark variance;
- detector mask;
- pixel-level variance.

## Calibration Algorithms

Own:

- uncertainties of their measured Products;
- validity masks;
- confidence or residual diagnostics.

## Spectral Extraction

Owns:

- propagation through aperture or profile weights;
- masked-pixel treatment;
- extraction covariance where supported;
- effective aperture information.

## Rectification

Owns:

- resampling weights;
- propagated variance;
- flux-conservation policy;
- covariance or correlation metadata.

## QA

Owns:

- comparison of formal and observed noise;
- pass/warn/fail thresholds;
- empirical scale-factor estimation;
- contamination rejection.

## Analytics

Owns:

- locating the physical sources of variance mismatch;
- trends with hardware, wavelength, signal, and environment;
- and determining when empirical corrections can be replaced by improved
  physical terms.

---

# Initial Implementation Decisions

- Build pixel variance from read noise and a non-negative Poisson expectation.
- Perform detector noise calculations in electrons where possible.
- Propagate known calibration-product uncertainty.
- Record explicitly when calibration uncertainty is omitted.
- Use exact fractional aperture weights for extracted variance.
- Preserve effective aperture weight and valid-pixel fraction.
- Keep covariance omission as an explicit policy.
- Retain native-grid spectra and variance.
- Use linear rectification as the initial robust default.
- Propagate variance using the exact linear interpolation weights.
- Record that linear point interpolation is not flux conserving.
- Support a flux-conserving resampler as a separate method.
- Do not use higher-order interpolation without explicit covariance and QA.
- Measure empirical noise scale factors from carefully selected blank fibers.
- Store whether the factor applies to standard deviation or variance.
- Preserve the formal variance separately from the empirically calibrated
  variance.
- Begin with simple exposure- or amplifier-level scale factors, then test
  wavelength and signal dependence.
- Never treat the corrected diagonal variance as a complete systematic-error
  model.

---

# Open Questions

- What non-negative Poisson expectation should be used after background
  subtraction?
- Is a Gaussian approximation adequate over the full VIRUS count range?
- Which master-calibration uncertainties are scientifically significant?
- Should calibration uncertainties be folded into the variance or stored
  separately?
- How should masked aperture pixels be renormalized?
- What is the most useful compact covariance representation?
- Should routine rectification become flux conserving?
- How much of the empirical variance inflation comes from resampling
  covariance?
- Is the correction factor primarily amplifier-, spectrograph-, exposure-, or
  wavelength-dependent?
- How should blank fibers be selected in crowded or diffuse fields?
- Can repeated exposures separate random noise from coherent systematics?
- How should sky-model uncertainty be propagated?
- How should profile-model uncertainty enter forward-extraction covariance?
- At what stage should empirical variance calibration be applied?
- Can the observed correction be decomposed into physical missing terms rather
  than retained as one scale factor?

---

# Repository Goals

VIRUSFlow should:

- preserve a transparent detector-to-spectrum variance lineage;
- distinguish variance, covariance, calibration uncertainty, and systematic
  uncertainty;
- quantify low-count departures from the simple Gaussian approximation;
- propagate exact aperture weights;
- measure the impact of masks and variable effective aperture width;
- retain native-grid uncertainty before rectification;
- compare linear and flux-conserving resampling;
- quantify interpolation-induced covariance;
- determine when full covariance can be replaced by compact approximations;
- measure empirical noise from blank fibers at every relevant scope;
- explain why formal and observed noise differ;
- replace empirical inflation factors with physical model terms where possible;
- preserve both formal and empirically calibrated variance Products;
- identify science regimes where diagonal uncertainty is inadequate;
- and ensure that every final uncertainty estimate is testable against observed
  residual behavior.
