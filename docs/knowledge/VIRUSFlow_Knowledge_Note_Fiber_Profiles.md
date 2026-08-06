# VIRUSFlow Scientific Knowledge Specification

# Working Note: Fiber Profiles, Environmental Dependence, and Empirical Profile Recovery

> Status: Initial implementation specification based on the current fiber-profile algorithm

This note separates three related but distinct concepts:

1. the physical fiber-output profile,
2. the detector image of that profile after propagation through the spectrograph,
3. the empirical algorithm used to recover a representative profile from reduced data.

The measured profile is not assumed to have a fixed analytic form. It is an
empirical detector-space model whose shape may vary with environment,
illumination, telescope configuration, and instrument state.

---

# Scientific Object

For a given fiber and wavelength, the fiber profile describes the distribution
of detector counts in the cross-dispersion direction around the fiber trace.

Conceptually:

```text
P_f(Δy, x)
```

where:

- `f` is fiber identity,
- `x` is detector column or wavelength,
- `Δy = y - trace_f(x)` is cross-dispersion distance from the trace center.

The profile determines how flux from one fiber is distributed across detector
pixels.

It is therefore fundamental to:

- optimal extraction,
- deblending neighboring fibers,
- variance propagation,
- scattered-light discrimination,
- cross-talk estimation,
- and spectrograph PSF characterization.

---

# Geometry and Coordinate Convention

Fiber profiles are measured only after the amplifier image has been transformed
into canonical detector coordinates:

- blue light on the left,
- red light on the right,
- wavelength increasing with `x`,
- fiber number increasing from bottom to top,
- and the trace map providing the center `y_f(x)` of every fiber.

The profile coordinate is centered on the measured trace:

```text
fiber_offset = detector_y - trace_f(x)
```

The profile model therefore depends directly on the quality of the trace
solution.

A trace error appears as an apparent profile shift or broadening.

---

# Why Fiber Profiles Vary

Fiber profiles are not immutable detector templates.

They respond to multiple physical processes.

---

# Ambient Temperature Effects

Ambient temperature can influence both the placement and shape of fiber images.

## 1. Trace Shifts and Low-Order Image Distortion

Thermal expansion and contraction of optical mounts, together with changes in
the refractive index of air, can shift the spatial and spectral image on the
detector.

The dominant effect may resemble:

- translation,
- scaling,
- small rotation,
- or another low-order distortion.

Observed changes may be on the order of a few tenths of a pixel.

These motions are primarily trace and wavelength effects, but an imperfectly
tracked displacement also changes the apparent centered fiber profile.

## 2. Fiber Near-Field and Far-Field Output

Temperature changes can alter:

- fiber geometry,
- mechanical stress,
- IFU microlens alignment,
- modal distribution,
- and focal-ratio degradation.

This changes both:

- the near-field spatial illumination at the fiber output,
- and the far-field angular distribution entering the spectrograph.

The resulting detector profile can become broader, narrower, or less symmetric.

## 3. Spectral PSF and Line-Spread Function

Thermal gradients may alter:

- VPH grating behavior,
- Schmidt camera focus,
- optical alignment,
- and stress within optical elements.

These effects can change higher-order profile structure.

The image may:

- broaden,
- become asymmetric,
- develop extended wings,
- or vary with wavelength.

This is not adequately represented as a simple trace shift.

## 4. Pixel-Grid Interaction

As a profile shifts or broadens, its light is sampled differently by the finite
pixel grid.

This can expose or amplify:

- intra-pixel response variation,
- charge-transfer effects,
- undersampling,
- and subpixel-centering differences.

Thus, even a stable optical profile can produce slightly different discrete
pixel samples as its centroid moves.

---

# Telescope-Dependent Effects

## Tracking and Fiber Bending

As the telescope tracks, the fiber cable system bends, twists, and changes
orientation.

Mechanical stress can alter focal-ratio degradation through macro- and
micro-bending.

This changes the angular cone emerging from the fiber and therefore changes how
the spectrograph optics are illuminated.

The detector manifestation can include:

- broader profiles,
- changed wings,
- wavelength-dependent PSF changes,
- and altered cross-talk between adjacent fibers.

## Input Illumination and Guiding

Multimode fibers scramble light imperfectly.

For point-source illumination, guiding errors, seeing, wind shake, or source
miscentering can produce asymmetric illumination at the input fiber face.

The output may retain partial memory of this illumination through its near-field
distribution.

This can skew or broaden the measured detector profile.

A calibration lamp, twilight sky, blank sky, and point source do not necessarily
produce identical profiles.

---

# Fiber Profile vs. Spectral LSF

The cross-dispersion fiber profile and the dispersion-direction line-spread
function are related through the full spectrograph PSF but are not identical.

This note concerns the cross-dispersion profile around the trace.

A future two-dimensional PSF model may need to represent:

- cross-dispersion width and asymmetry,
- spectral LSF width and asymmetry,
- covariance between the two axes,
- wavelength dependence,
- fiber dependence,
- environmental dependence.

The current empirical profile is a one-dimensional cross-dispersion model.

---

# Why an Empirical Model Is Preferred

Simple functional forms such as:

- Gaussian,
- Moffat,
- Voigt,
- or sums of symmetric components

do not adequately capture the full residual structure of VIRUS fiber profiles.

Functional fits tend to leave coherent residuals caused by:

- asymmetry,
- shoulders,
- extended wings,
- neighboring-fiber influence,
- detector sampling,
- and changing illumination.

The current approach therefore measures the profile empirically.

The model is built directly from normalized detector samples and represented by
interpolation rather than by a fixed analytic function.

This allows the data to define the profile shape.

---

# Required Inputs

The current algorithm requires:

- a reduced two-dimensional amplifier image,
- extracted fiber spectra from an aperture method,
- a trace map,
- a wavelength map,
- a detector-column range.

The image is expected to have:

- detector bias removed,
- dark correction applied where appropriate,
- gain applied,
- pixel defects masked,
- scattered light or background counts removed.

The aperture-extracted spectrum provides the per-column normalization needed to
convert detector counts into a normalized cross-dispersion profile.

---

# Current Algorithm Overview

The current implementation performs the following sequence:

1. Select a detector-column interval.
2. For each fiber, define a cross-dispersion window around its trace.
3. Compute detector-pixel offsets relative to the trace.
4. Divide image counts by the aperture-extracted fiber spectrum.
5. Retain samples within six pixels of the trace.
6. Group neighboring fibers into broad fiber-index regions.
7. Pool all normalized detector samples within each region.
8. Sort samples by cross-dispersion offset.
9. Bin the pooled samples robustly into 25 equal-population groups.
10. Identify the central peak.
11. Identify the two surrounding valleys.
12. center the profile on the measured peak.
13. add valley and zero-valued wing constraints,
14. construct a quadratic interpolation model,
15. evaluate all regional profile models on a common grid,
16. median-combine them into one representative empirical profile.

The returned result includes:

- one empirical interpolation model,
- and the measured shifts of the accepted regional peaks.

---

# Column Selection

The default detector interval is:

```text
xmin = 400
xmax = 600
```

This selects a central wavelength region.

The choice likely reflects a compromise:

- high signal,
- stable illumination,
- limited edge effects,
- representative fiber separation,
- and manageable wavelength variation.

The current model is therefore not a full wavelength-dependent profile.

It is a representative profile measured over a restricted detector interval.

A future implementation should preserve the wavelength interval explicitly and
test whether one profile is adequate across the full detector.

---

# Per-Fiber Sample Construction

For each fiber, the algorithm computes:

```text
fiber_offset =
    detector_y - trace_f(x)
```

It then computes a normalized profile value:

```text
normalized_value =
    image(y, x) / aperture_spectrum_f(x)
```

Only samples satisfying:

```text
|fiber_offset| <= 6 pixels
```

are retained.

This creates a cloud of empirical samples:

```text
(offset, normalized intensity)
```

for every fiber.

The normalization assumes that the aperture-extracted spectrum adequately
represents the total or reference flux at each detector column.

Errors in the aperture spectrum propagate directly into profile shape.

---

# Fiber Grouping

The algorithm does not fit one independent profile for every fiber.

Instead, it constructs regional profiles centered near fiber indices:

```text
8, 24, 40, 56, 72, 88, 104
```

using:

```text
|fiber_index - region_center| <= 8
```

Thus, each region pools approximately 16 or 17 neighboring fibers.

This improves robustness by combining many detector samples.

It also assumes that nearby fibers have sufficiently similar profiles.

The grouping creates a coarse description of profile variation along the
cross-dispersion axis.

However, the final implementation median-combines the accepted regional models
into a single amplifier-wide profile, so the current returned model does not
preserve spatial profile variation between fiber groups.

---

# Robust Equal-Population Binning

Within each regional group:

- all offsets are concatenated,
- all normalized intensities are concatenated,
- samples are sorted by offset,
- and the sorted arrays are split into 25 chunks.

The median offset and median intensity are computed in each chunk.

This is equal-population binning rather than fixed-width binning.

Its advantages include:

- robustness to outliers,
- stable sample counts per bin,
- less sensitivity to uneven subpixel sampling,
- and suppression of cosmic rays and detector defects.

The resulting 25-point profile is a robust summary of a much larger empirical
sample cloud.

---

# Peak and Valley Validation

The algorithm requires a recognizable profile topology.

It searches for:

- exactly one central peak,
- exactly two surrounding valleys.

The peak threshold is approximately:

```text
0.4
```

The valley search is performed on:

```text
1 - profile
```

with a threshold near:

```text
0.3
```

A regional model is rejected unless it has:

- one accepted peak,
- and two accepted valleys.

This is a strong morphological quality criterion.

It prevents interpolation through profiles that are:

- double-peaked,
- strongly contaminated,
- incompletely sampled,
- or otherwise inconsistent with the expected fiber shape.

---

# Profile Centering

The accepted profile is shifted so that its measured peak occurs at:

```text
offset = 0
```

The measured peak shift is retained separately.

This separates:

- profile shape,
- from trace-centering offset.

The returned list of shifts therefore provides evidence of systematic
miscentering between the input trace and the empirical profile peak.

These shifts can be interpreted as:

- residual trace offsets,
- profile asymmetry,
- or regional centering differences.

---

# Valley and Wing Constraints

After centering, the algorithm retains measured samples within:

```text
|offset| < 4 pixels
```

It then augments them with:

- the two measured valley positions,
- modified valley amplitudes,
- and explicit zero-valued points at `-5` and `+5` pixels.

These added constraints force the interpolation model toward zero in the wings.

This stabilizes the model outside the well-measured core.

However, it also imposes a compact-support assumption.

Extended profile wings beyond approximately five pixels are not represented by
the returned model.

---

# Empirical Interpolation

Each accepted regional profile is represented using:

```text
quadratic interpolation
```

with:

```text
fill_value = 0
bounds_error = False
```

The model is evaluated on a common grid from:

```text
-6 to +6 pixels
```

The accepted regional models are then median-combined point by point.

A final quadratic interpolation model is built from this median profile.

The final profile is therefore:

- non-parametric in physical form,
- but still regularized by interpolation,
- finite in support,
- robustly combined across detector regions.

---

# Important Assumptions

Any replacement implementation must understand the assumptions embedded in the
current algorithm.

## Trace Accuracy

The trace is assumed to locate each fiber closely enough that the profile lies
within the ±6-pixel extraction window.

Large trace errors can:

- broaden the pooled profile,
- shift the peak,
- or cause rejection.

## Aperture Spectrum Accuracy

The aperture spectrum is assumed to provide a stable per-column normalization.

Contamination by neighboring fibers, background, or bad pixels can distort the
normalized profile.

## Background Removal

The image is assumed to be free of scattered light and additive background.

Residual additive structure becomes artificially large after division by low
fiber flux.

## Neighboring-Fiber Similarity

Fibers within approximately eight indices of a regional center are assumed to
share a common profile.

## Amplifier-Wide Similarity

The final median combination assumes that one common profile adequately
represents the accepted fiber regions.

## Restricted Wavelength Validity

A profile measured between columns 400 and 600 is assumed to be representative
of the wavelength region in which it is applied.

## Compact Wings

The profile is forced to zero at approximately ±5 pixels.

## Single-Peak Morphology

A valid profile is assumed to have one central peak and two surrounding minima.

## Stable Fiber Separation

The method assumes neighboring profiles do not dominate the selected central
profile after normalization and pooling.

---

# Physical Variability vs. Measurement Variability

The repository must distinguish changes in the empirical profile caused by:

## Physical Effects

- ambient temperature,
- telescope tracking,
- fiber bending,
- focal-ratio degradation,
- guiding and source centering,
- optical focus,
- grating behavior,
- detector sampling.

## Calibration or Algorithm Effects

- trace error,
- wavelength error,
- aperture-spectrum normalization,
- background subtraction,
- masked pixels,
- column-range selection,
- grouping strategy,
- peak-finding thresholds.

Not every profile change is evidence of changing fiber physics.

---

# Product Identity

A measured Fiber Profile Product should preserve the complete five-part ZipCode.

Unlike trace-reference configuration, the profile can plausibly depend on the
complete acquisition and illumination state.

At minimum, the Product should also be associated with:

- observation time,
- illumination type,
- exposure or calibration Product,
- ambient temperature,
- telescope position or track state when relevant,
- trace Product,
- wavelength Product,
- extraction Product,
- detector-column and wavelength interval.

---

# Product Contract

## Model Payload

The current conceptual output is:

```text
empirical_profile(offset)
```

A storage-neutral Product should not rely only on a live `interp1d` object.

It should preserve the sampled model explicitly:

```text
profile_offset_grid
profile_values
```

Interpolation behavior can then be reconstructed reproducibly.

## Diagnostic Arrays and Scalars

Recommended outputs include:

```text
regional_profile_models
regional_peak_shifts
accepted_region_mask
regional_peak_count
regional_valley_count
profile_sample_count
profile_width_metrics
profile_asymmetry_metrics
wing_fraction
```

## Metadata

Recommended metadata include:

```text
column_range
wavelength_range
fiber_group_centers
fiber_group_half_width
offset_limit
number_of_bins
peak_threshold
valley_threshold
interpolation_kind
wing_constraints
trace_product_id
spectrum_product_id
illumination_kind
ambient_temperature
algorithm_version
```

---

# Required QA

A profile measurement should not be judged only by successful interpolation.

QA should evaluate:

- number of accepted fiber regions,
- dispersion among regional models,
- regional peak-shift consistency,
- profile normalization,
- width,
- asymmetry,
- wing strength,
- number of peak and valley failures,
- sensitivity to the selected wavelength interval,
- and residuals when the profile is projected back onto the detector image.

A profile may be mathematically valid but scientifically inappropriate if
different fiber regions disagree strongly.

---

# Separation of Responsibilities

## Instrument and Environmental Metadata

Owns:

- ambient temperature,
- telescope position,
- illumination type,
- hardware identity,
- observation timing.

## Trace Algorithm

Owns:

- fiber centers as a function of detector column.

## Aperture Extraction

Owns:

- preliminary spectra used for normalization.

## Fiber-Profile Algorithm

Owns:

- normalized empirical sample construction,
- robust profile aggregation,
- centering,
- interpolation,
- and profile diagnostics.

## QA

Owns:

- width and asymmetry thresholds,
- regional-consistency tests,
- accepted-profile decisions.

## Analytics

Owns:

- temperature trends,
- telescope-track dependence,
- long-term profile evolution,
- comparison among illumination types,
- and testing whether a single amplifier-wide profile remains adequate.

---

# Initial Implementation Decisions

Until a richer model is validated:

- Use empirical profiles rather than fixed functional forms.
- Measure profiles in detector coordinates centered on the trace.
- Normalize detector counts using aperture-extracted spectra.
- Use a high-signal central wavelength interval initially.
- Retain samples within six pixels of the trace.
- Pool neighboring fibers to increase robustness.
- Use median equal-population binning.
- Require one central peak and two surrounding valleys.
- retain the measured peak shifts as diagnostics,
- force the empirical model toward zero near ±5 pixels,
- construct a quadratic interpolation model,
- median-combine accepted regional models,
- and preserve sampled profile arrays rather than only a Python interpolation
  object.

---

# Repository Goals

VIRUSFlow should:

- preserve empirical fiber profiles as measured scientific Products,
- separate profile shape from trace-centering errors,
- quantify profile dependence on ambient temperature,
- measure changes with telescope tracking and fiber bending,
- compare lamp, twilight, sky, and point-source illumination,
- characterize wavelength dependence across the amplifier,
- determine whether one profile per amplifier is sufficient,
- measure variation among fiber groups,
- quantify profile width, asymmetry, shoulders, and wings,
- determine whether compact support at ±5 pixels is justified,
- compare empirical profiles with functional alternatives using detector-space
  residuals,
- identify when profile changes arise from calibration errors rather than
  physical instrument changes,
- model how profile changes affect extraction and cross-talk,
- and eventually construct a time-, wavelength-, fiber-, illumination-, and
  environment-aware profile model supported by repository evidence.
