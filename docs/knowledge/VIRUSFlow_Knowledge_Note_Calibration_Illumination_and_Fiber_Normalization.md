# VIRUSFlow Scientific Knowledge Specification

# Working Note: Calibration Illumination, Master LDLS, Master Twilight, and Fiber Normalization


---

# Central Distinction

VIRUS uses multiple illumination sources, but they do not represent the same
physical quantity.

The calibration unit at the top of the focal plane approximately mimics
illumination from the primary mirror, but it does not reproduce the science
illumination pattern accurately.

It embeds its own large-scale illumination structure across the VIRUS field of
view.

Therefore:

> **Calibration-unit exposures are not direct measurements of the illumination
> pattern present during science observations.**

This applies to both:

- the laser-driven light source continuum exposures,
- and the Hg/Cd comparison-lamp exposures.

The lamps remain scientifically valuable because they constrain detector,
spectrograph, trace, profile, and wavelength behavior. Their field illumination
must not be mistaken for the illumination of the telescope during science
observations.

---

# Terminology

The Product historically called a:

```text
Master Flat
```

is more accurately named:

```text
Master LDLS
```

because it is constructed from the laser-driven light source.

Calling it a flat implies that it provides the multiplicative flat-field
correction for science data.

It does not.

The proposed terminology should distinguish:

```text
Master LDLS
Master Hg
Master Cd
Master Arc
Master Twilight
Fiber Normalization
Science Illumination Correction
```

Each represents a different physical or operational object.

---

# Master LDLS

## Physical Meaning

The Master LDLS is a high-signal detector image produced by the smooth continuum
laser-driven light source in the calibration unit.

It contains a combination of:

- detector response,
- fiber transmission,
- spectrograph throughput,
- calibration-unit illumination,
- scattered light,
- and the LDLS spectral energy distribution.

Because the calibration unit does not reproduce the primary-mirror science
illumination, the Master LDLS is not a direct science flat-field Product.

## Primary Uses

The smooth continuum and high signal make the Master LDLS especially valuable
for:

- trace recovery,
- empirical fiber-profile measurement,
- detector defect detection,
- pixel-response diagnostics,
- pock and column identification,
- scattered-light characterization,
- and other CCD-level measurements.

Its strength is that it illuminates the detector and fibers smoothly and
strongly.

Its limitation is that its large-scale illumination pattern is not the science
illumination pattern.

---

# Master Arc Lamps

Hg and Cd comparison exposures use the same calibration-unit optical path.

Their illumination therefore also contains the calibration-unit spatial
structure.

For wavelength calibration, this is acceptable because the primary information
is:

```text
detector position of known emission lines
```

rather than the absolute illumination level of each fiber.

However, the calibration-unit illumination can still affect:

- line signal-to-noise,
- extraction quality,
- scattered-light level,
- detectability of weak lines,
- and amplifier-to-amplifier weighting.

Hg and Cd exposures should continue to be averaged separately and then summed:

```text
Master Arc = Master Hg + Master Cd
```

The Master Arc is a wavelength-calibration Product, not a flat-field
Product.

---

# Master Twilight

## Physical Meaning

The Master Twilight provides the baseline illumination used to derive fiber
normalization.

Twilight illumination passes through the telescope and therefore samples the
combined response of:

- the telescope,
- focal-plane geometry,
- fibers,
- spectrographs,
- detectors,
- and the twilight sky spectrum.

It is not identical to an arbitrary science exposure, but it is a much more
appropriate reference for relative fiber illumination than the calibration
unit.

## Center-Track Reference State

Modern VIRUS twilight observations are taken at center track.

Since at least 2018, different exposure times have been used to obtain suitable
illumination while maintaining the same center-track geometric reference.

This establishes a common reference state:

```text
Fiber normalization is relative to center-track twilight illumination.
```

Historical twilight data from the earlier period may include observations away
from center track.

Those exposures should not be assumed equivalent without using their telescope
position and illumination metadata.

---

# Twilight Field-Uniformity Assumption

The fiber-normalization procedure assumes that the twilight sky has no
significant illumination gradient across the full VIRUS field of view during a
center-track exposure.

Under this assumption, any measured large-scale difference among fibers is
attributed to the combined response of the telescope, IFUs, fibers,
spectrographs, and detectors rather than to a true sky-brightness gradient.

The algorithm therefore renormalizes the complete twilight exposure toward a
uniform incident illumination and infers a full-system relative fiber
normalization.

Conceptually:

```text
Observed Twilight Pattern
    =
Uniform Incident Twilight
    ×
Full-System Relative Response
```

This assumption is not guaranteed to be exact.

A real twilight gradient may remain because of:

- solar geometry,
- atmospheric scattering,
- clouds or transparency structure,
- rapidly changing twilight brightness,
- and residual directional dependence across the sky.

The expected gradient should be relatively small because modern twilight
observations are taken:

- at center track,
- at center azimuth,
- and directly south rather than toward the eastern or western track limits.

Nevertheless, any real field gradient that remains will be absorbed into the
inferred fiber normalization.

The resulting Product should therefore be described as:

> **The relative full-system response inferred under the assumption of uniform
> center-track twilight illumination.**

This is both a scientific definition and an explicit model assumption.

The repository should eventually test this assumption by:

- comparing repeated center-track twilights,
- examining residual gradients in focal-plane coordinates,
- comparing evening and morning twilight,
- evaluating dependence on solar altitude and azimuth,
- and determining whether a low-order sky-gradient term should be separated
  from the instrumental normalization.

---

# Twilight Dynamic Range

Twilight illumination is not perfectly uniform across the full VIRUS field.

Different fibers and amplifiers receive different signal levels.

Saturation is uncommon, but the illumination range produces variation in:

- signal-to-noise,
- usable wavelength range,
- precision of the normalization,
- and sensitivity to detector or scattered-light residuals.

The dynamic range is scientifically useful because it provides information
about relative fiber throughput.

It also requires QA so that low-signal regions are not treated as equally
constrained.

---

# Twilight Spectral Structure

Unlike the LDLS continuum, twilight contains a solar-like spectrum with sharp
absorption features.

A simple ratio to one average twilight spectrum would imprint residual spectral
features when:

- the wavelength solutions differ slightly between fibers,
- the spectral resolution varies,
- the line-spread function varies,
- or the average spectrum is sampled differently.

Therefore, the fiber normalization should not track the pixel-to-pixel
structure of the twilight spectrum.

Instead:

> **The ratio of each fiber to the common twilight spectral model must be
> smoothed along wavelength or detector column.**

The normalization is intended to represent broad relative response, not solar
absorption lines.

---

# Fiber Normalization Scope

Fiber normalization is fundamentally an exposure-scope measurement.

The physical comparison is not complete within one amplifier because the final
normalization must place all fibers in the exposure onto a common relative
response scale.

A useful decomposition is:

```text
fiber_norm
    =
fiber_norm_within_amp
    ×
amp_to_amp_norm
```

where:

- `fiber_norm_within_amp` describes relative response among fibers within one
  amplifier;
- `amp_to_amp_norm` places the amplifier onto the common exposure-wide scale.

This decomposition permits amplifier-parallel measurement while preserving the
scientific requirement that the final Product is exposure-wide.

---

# Within-Amplifier Fiber Normalization

The supplied algorithm computes a smooth relative normalization for every fiber
within one amplifier.

Its main sequence is:

1. Validate that spectra and wavelength arrays have identical two-dimensional
   shapes.
2. Identify fibers with wavelength solutions that are finite over more than
   80 percent of the detector.
3. Build a common twilight spectral model from the valid fiber spectra.
4. Evaluate that model on the wavelength grid of every fiber.
5. Divide each extracted fiber spectrum by the common spectral model.
6. Robustly average the ratio in broad detector-column bins.
7. Interpolate the binned values into a smooth normalization curve for each
   fiber.

Conceptually:

```text
raw_ratio_f(x)
    =
twilight_spectrum_f(x)
    /
common_twilight_model[λ_f(x)]
```

followed by:

```text
fiber_norm_within_amp,f(x)
    =
smooth(raw_ratio_f(x))
```

---

# Common Twilight Spectral Model

The current implementation delegates construction of the common spectral model
to:

```text
build_model_spectra
```

The model is evaluated independently on every fiber's wavelength grid:

```text
M_f(x) = twilight_model[λ_f(x)]
```

This wavelength-aware evaluation is essential.

It prevents small differences in wavelength solution from turning sharp solar
features directly into apparent fiber-response features.

The exact construction of the common model remains important knowledge that must
be documented separately or incorporated when the relevant code is reviewed.

Questions that remain include:

- how spectra are normalized before combination;
- how fibers are weighted;
- how outliers and object contamination are rejected;
- whether line-spread-function differences are represented;
- and whether one common model is adequate across all amplifiers.

---

# Ratio Construction

For every fiber:

```text
R_f(x) = S_f(x) / M_f(x)
```

where:

- `S_f(x)` is the extracted twilight spectrum;
- `M_f(x)` is the common twilight model evaluated on that fiber's wavelength
  grid.

Invalid or zero model values are converted to missing values before division.

The raw ratio is retained as an important diagnostic Product.

It contains:

- broad fiber-response structure,
- residual solar spectral features,
- wavelength errors,
- extraction defects,
- scattered-light residuals,
- bad pixels,
- and noise.

The smooth normalization should capture only the broad response component.

---

# Robust Spectral Smoothing

The current implementation divides the 1032 detector columns into:

```text
51 bins
```

or a user-specified number with a minimum of five.

For each fiber and bin, it computes a robust location of the finite ratio values:

```text
biweight location
```

with a median fallback.

This suppresses:

- narrow solar absorption features,
- bad pixels,
- cosmic rays,
- extraction outliers,
- and isolated wavelength-calibration residuals.

The resulting binned values describe broad response as a function of detector
column.

---

# Interpolation

The preferred interpolation is cubic when at least four valid bins are
available.

Fallback behavior is:

- linear interpolation for at least two valid bins;
- a constant median response when fewer than two valid bins are available.

The interpolation extrapolates beyond the outer valid bin centers.

This creates a complete per-fiber normalization array, but the confidence in
the edges may be lower than in the interior.

A future Product should distinguish:

- directly constrained wavelength regions,
- interpolated regions,
- and extrapolated edge regions.

---

# What the Current Algorithm Measures

The resulting within-amplifier normalization contains whatever broad
multiplicative differences remain after division by the common twilight model.

This can include:

- physical fiber transmission,
- spectrograph throughput differences among fibers,
- detector response,
- center-track illumination structure,
- and residual calibration effects.

It should not be interpreted as pure intrinsic fiber throughput.

A more accurate description is:

> **Relative full-system fiber response inferred under the assumption of
> uniform center-track twilight illumination.**

---

# Additive Contamination

The computation assumes that additive terms have already been removed or are
negligible.

This is important because:

```text
Observed Twilight
    =
Multiplicative Response × Twilight Signal
    +
Scattered Light
    +
Other Additive Background
```

If scattered light remains, dividing by the common twilight model can absorb
part of the additive term into the inferred multiplicative fiber normalization.

This can bias:

- faint fibers,
- fibers near bright detector neighbors,
- amplifier-to-amplifier scaling,
- and wavelength-dependent normalization.

The relationship between scatter subtraction and fiber-normalization
construction must therefore be explicit.

---

# Exposure-Scope Amp-to-Amp Normalization

The within-amplifier algorithm does not determine the final relative scaling
between amplifiers.

A second measurement is required:

```text
amp_to_amp_norm
```

This stage should compare the amplifier-level response on a common twilight
illumination scale.

It may need to account for:

- center-track focal-plane illumination,
- different amplifier signal levels,
- different numbers of valid fibers,
- spectrograph throughput,
- detector response,
- scattered light,
- and wavelength overlap.

The amp-to-amp factor should be determined from the full exposure or an
equivalent complete center-track twilight set.

It should not be inferred independently from unrelated amplifier subsets.

The exact algorithm remains to be specified.

---

# Fiber Normalization Product Identity

The final fiber-normalization Product should be tied to:

- the complete exposure;
- center-track twilight illumination;
- the full hardware configuration;
- the trace and wavelength Products;
- the extraction Product;
- and the scattered-light treatment.

Because IFUSLOT influences illumination and IFUID identifies the physical
fibers, both are essential to normalization identity.

SPECID and AMP also matter through spectrograph and detector response.

CONTROLLER should remain in measured Product provenance even if it is not
expected to determine optical throughput directly.

The complete five-part ZipCode is therefore the safe amplifier-level provenance
identity, while the final normalization Product is grouped at exposure scope.

---

# Suggested Product Decomposition

A useful set of Products is:

```text
within_amp_fiber_normalization
amp_to_amp_normalization
exposure_fiber_normalization
```

with:

```text
exposure_fiber_normalization
    =
within_amp_fiber_normalization
    ×
amp_to_amp_normalization
```

The repository should retain both factors rather than only the multiplied final
array.

This allows analytics to distinguish:

- within-amplifier fiber behavior;
- amplifier-scale throughput changes;
- and exposure-wide illumination effects.

---

# Product Contract

## Master LDLS

Recommended metadata:

```text
illumination_kind = LDLS
calibration_unit = true
science_flat_applicable = false
input_exposures
combination_method
exposure_times
saturation_fraction
signal_statistics
hardware_identity
```

## Master Twilight

Recommended metadata:

```text
illumination_kind = twilight
track_position
center_track_reference
input_exposures
exposure_times
airmass
sky_brightness
saturation_fraction
signal_statistics
historical_observing_mode
```

## Within-Amplifier Fiber Normalization

Recommended arrays:

```text
within_amp_fiber_normalization
raw_fiber_to_twilight_model_ratio
valid_wavelength_mask
interpolated_region_mask
extrapolated_region_mask
```

Recommended metadata:

```text
twilight_product
trace_product
wavelength_product
extraction_product
scattered_light_product
common_twilight_model
number_of_bins
robust_statistic
interpolation_method
center_track_reference
algorithm_version
```

## Exposure Fiber Normalization

Recommended arrays:

```text
exposure_fiber_normalization
amp_to_amp_normalization
```

Recommended metadata:

```text
participating_amplifiers
exposure_id
reference_scale
amp_comparison_method
valid_fiber_counts
illumination_reference
```

---

# Required QA

## Master LDLS QA

Evaluate:

- signal level;
- saturation;
- lamp stability;
- detector coverage;
- amplifier consistency;
- scattered-light structure;
- and suitability for trace, profile, and detector-defect measurements.

Do not evaluate it as though it were a valid science illumination flat.

## Master Twilight QA

Evaluate:

- center-track position;
- signal and dynamic range;
- saturation;
- valid wavelength coverage;
- cloud or rapid-brightness variation;
- exposure-to-exposure consistency;
- and field-wide illumination continuity.

## Fiber Normalization QA

Evaluate:

- raw ratio residual structure;
- number of valid bins per fiber;
- deviation from the amplifier median;
- edge extrapolation;
- residual solar absorption structure;
- discontinuities between amplifiers;
- wavelength smoothness;
- dead or weak fibers;
- and dependence on scattered-light treatment.

A smooth interpolation alone is not sufficient evidence of a valid
normalization.

---

# Important Current-Code Assumptions

The present within-amplifier algorithm assumes:

- the incident twilight illumination is effectively uniform across the VIRUS
  field of view;
- wavelength solutions are valid for most fibers;
- the common twilight model is scientifically representative;
- extracted spectra are already corrected for additive backgrounds;
- broad response varies smoothly with detector column;
- 51 bins provide enough spectral resolution for the normalization;
- cubic interpolation does not introduce unphysical oscillation;
- edge extrapolation is acceptable;
- and a constant fallback is preferable to returning no Product.

These assumptions should be preserved as explicit configuration or tested by
QA.

---

# Initial Implementation Decisions

Until the remaining illumination model is specified:

- Rename `master_flat` to `master_ldls`.
- Do not use Master LDLS as the science fiber-normalization Product.
- Preserve Master LDLS for trace, profile, detector, and scattered-light
  measurements.
- Preserve Hg and Cd lamp masters separately before producing Master Arc.
- Use center-track Master Twilight as the fiber-normalization reference.
- State explicitly that the inferred normalization assumes uniform twilight
  illumination across the exposure.
- Mark historical non-center-track twilight exposures explicitly.
- Evaluate the common twilight spectrum on each fiber's wavelength grid.
- Smooth the fiber-to-model ratio so solar features are not interpreted as
  throughput structure.
- Compute within-amplifier normalization in parallel.
- Compute amp-to-amp normalization at exposure scope.
- Retain both factors and the final multiplied Product.
- Treat scattered-light subtraction as a prerequisite or explicit dependency.
- Preserve raw ratios and masks for QA.
- Do not describe the resulting Product as pure intrinsic fiber throughput.

---

# Open Questions

- How exactly is the common twilight spectral model constructed?
- Should the model be exposure-wide, spectrograph-wide, or amplifier-wide?
- How should line-spread-function differences be handled?
- What is the best amp-to-amp normalization statistic?
- Is the twilight sky sufficiently uniform across the VIRUS field for the
  current inference?
- Should a low-order twilight sky-gradient model be separated from the
  instrumental response?
- How should the known center-track illumination structure be represented?
- How much of the center-track twilight pattern should remain in the final
  normalization?
- How should science illumination variations be corrected relative to the
  twilight baseline?
- Should cubic interpolation be replaced by a shape-preserving interpolator?
- How should uncertainty in the normalization be propagated?
- How stable are within-amplifier and amp-to-amp factors through time?
- Can LDLS and twilight measurements be combined to separate detector response
  from telescope illumination more cleanly?

---

# Repository Goals

VIRUSFlow should:

- distinguish calibration-unit illumination from telescope illumination;
- eliminate the misleading interpretation of Master LDLS as a science flat;
- preserve LDLS, Hg, Cd, comparison, and twilight Products as separate physical
  measurements;
- establish center-track twilight as the explicit illumination reference state;
- test and quantify the assumption of uniform twilight illumination across the
  VIRUS field;
- separate any measurable twilight sky gradient from instrumental response;
- identify and quarantine historical twilight observations obtained under
  different track conditions;
- separate within-amplifier fiber response from amp-to-amp scaling;
- derive final fiber normalization at exposure scope;
- measure how additive scatter biases multiplicative normalization;
- quantify wavelength-dependent normalization uncertainty;
- learn the stability of fiber response through time;
- determine which changes belong to fibers, spectrographs, detectors, or
  illumination;
- and ultimately separate intrinsic instrument throughput from telescope and
  science-exposure illumination using multiple complementary calibration
  Products.
