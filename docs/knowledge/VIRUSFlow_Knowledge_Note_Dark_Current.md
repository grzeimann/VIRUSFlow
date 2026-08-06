# VIRUSFlow Scientific Knowledge Specification

# Working Note: Dark Current and Hot-Pixel Structure

> Status: Initial implementation specification

This note captures the current operational understanding of dark current in VIRUS
and how it should be represented in VIRUSFlow.

The central modeling decision is that dark behavior contains at least two
physically and computationally distinct components:

1. a smooth, slowly varying dark-current field, and
2. a sparse population of bright or hot pixels.

These components should not be treated as one undifferentiated detector image.

---

## Relevant Architectural Entities

### Products

- Individual Dark Frame
- Overscan-Corrected Dark Frame
- Master Dark Product
- Smooth Dark Component
- Hot-Pixel Mask
- Pixel Mask Product
- Dark-Subtracted Science Product
- Dark-Subtracted Calibration Product

### Model Components

- Smooth Dark Current Component
- Hot-Pixel / Detector Defect Component
- Time-Dependent Dark Evolution Component (future)

---

## Scientific Interpretation

Dark current is detector signal accumulated in the absence of illumination.

After gain has been applied, it should be represented in:

```text
electrons per second
```

This allows the same dark model to be scaled to the exposure time of any science
or calibration frame.

The detector dark signal is not spatially uniform.

It contains:

- a smooth baseline component,
- and a strongly pixelated component associated with individual detector defects.

---

## Two-Component Dark Model

## 1. Smooth Dark-Current Component

The smooth component appears as a broad, gradual glow across the amplifier.

It is expected to depend primarily on detector temperature and other slowly
varying electronic or environmental conditions.

Characteristics include:

- low spatial frequency,
- broad gradients,
- smooth variation over large fractions of the detector,
- approximate linear scaling with exposure time.

This component should be modeled as a continuous image in electrons per second.

---

## 2. Hot-Pixel and Bright-Pixel Component

The pixelated component appears as isolated bright pixels or compact groups of
pixels.

These arise from localized detector imperfections and should be treated as
detector defects rather than as part of the smooth dark-current field.

Characteristics include:

- sparse spatial distribution,
- high contrast relative to neighboring pixels,
- potentially unstable amplitude,
- possible evolution over time,
- possible nonlinearity or intermittency.

The preferred initial treatment is to identify these pixels as outliers in
Master Dark products and add them to the detector Pixel Mask.

They should not be allowed to influence the smooth dark model.

---

## Dark-Frame Preprocessing

Every dark frame should undergo the normal base detector reduction before dark
modeling:

1. detect and reject zero readouts,
2. measure and subtract the row-wise overscan,
3. trim the overscan region,
4. orient the amplifier into canonical coordinates,
5. apply gain,
6. retain the exposure time and relevant metadata.

After this processing, the frame is in electrons and can be divided by exposure
time to produce an estimate in electrons per second.

---

## Exposure-Time Normalization

Dark frames are commonly obtained in groups of approximately:

- 1 frame,
- 3 frames,
- or 11 frames.

Within a group, they are almost always taken at the same exposure time.

For modeling purposes, every valid dark frame may be normalized to a one-second
rate:

```text
dark_rate = dark_electrons / exposure_time_seconds
```

Dark-rate images from different exposure times can then be combined, provided
that the detector response is sufficiently linear and the frames represent
compatible detector states.  This is safe when dark exposures are at the same initial order
of magnitude of exposure, for example: 300s - 600s exposures can likely be combined, but not 1s and 3000s exposures.

For application to another exposure:

```text
predicted_dark_electrons = dark_rate × science_exposure_time_seconds
```

This representation makes exposure-time scaling explicit and avoids treating
each exposure duration as a separate calibration family.

---

## Combining Dark Frames Across Exposure Times

All dark frames can technically contribute to a common dark-rate model after
normalization to electrons per second.

This is a reasonable initial approach under the current two-component
decomposition.

However, there are physical reasons to avoid assuming perfect equivalence
without validation, including:

- dark-current nonlinearity,
- warm-up or settling behavior,
- exposure-time-dependent electronics,
- temperature evolution during long integrations,
- persistence,
- unstable hot pixels,
- cosmic-ray contamination,
- short-timescale electronic interference.

VIRUSFlow should therefore preserve the original exposure time and allow
analytics to test whether normalized dark frames from different durations are
statistically compatible.

---

## Hot-Pixel Identification

Hot pixels should be identified from robust residuals relative to the local
smooth dark field or a robust Master Dark.

Candidate criteria may include:

- robust sigma thresholds,
- repeated elevation across multiple dark frames,
- temporal persistence,
- local contrast relative to neighboring pixels,
- minimum dark-current rate.

A hot pixel should preferably be masked rather than corrected by direct dark
subtraction alone.

This reflects the possibility that a defective pixel may be unstable,
nonlinear, or have unreliable variance.

---

## Smooth Dark Construction

The smooth dark component should be constructed only after hot pixels and other
outliers have been excluded.

Preferred initial sequence:

1. normalize each dark frame to electrons per second,
2. robustly combine compatible dark frames,
3. identify hot pixels and other high-frequency outliers,
4. mask those pixels,
5. apply a broad robust smoother to estimate the low-frequency dark field.

The smoothing scale should be a substantial fraction of the detector size.

An initial characteristic scale of approximately:

```text
100 pixels
```

is reasonable for a 1032 × 1032 amplifier image, corresponding to roughly one
tenth of the detector dimension.

The exact smoother and scale should be configurable and validated by residual
analysis.

---

## Robust Smoothing Requirements

The smooth-dark estimator should:

- ignore masked pixels,
- resist contamination by hot pixels and cosmic rays,
- preserve broad gradients,
- avoid fitting small-scale pixel structure,
- avoid edge artifacts,
- produce a finite estimate across the usable detector.

Possible implementations include:

- robust block medians followed by interpolation,
- large-kernel median filtering,
- sigma-clipped low-order surface fitting,
- robust spline surfaces,
- multi-scale smoothers.

The repository should specify the scientific contract rather than permanently
binding the model to one implementation.

---

## Application During Reduction

For an exposure with duration `t`, the reduction should conceptually apply:

```text
corrected_image =
    image_electrons
    - smooth_dark_rate × t
```

Pixels identified as hot or otherwise defective should remain masked.

The system should not rely on the smooth dark model to rehabilitate known bad
pixels.

---

## Relationship to Bias Structure

Dark frames contain more than dark current.

Before decomposition, they may contain:

- overscan structure,
- residual bias structure,
- read noise,
- electronic interference,
- dark current,
- hot pixels,
- cosmic rays.

A useful dark-current model therefore depends on adequate removal of the bias
state.

Dark frames can also serve as evidence about residual bias and electronic
stability, but those components should remain conceptually separate from the
dark-current rate.

---

## Temperature Dependence

The smooth dark-current baseline is expected to be temperature dependent.

The initial implementation does not require a predictive temperature model.

Instead:

- build empirical dark-rate products from measured frames,
- retain detector and ambient temperature metadata,
- analyze residual dark rate as a function of temperature,
- introduce interpolation or predictive models only after sufficient evidence.

Measured dark products should remain authoritative endpoints.

---

## Time Stability

The temporal behavior of both components should be studied separately.

### Smooth Component

Questions include:

- stability within a night,
- stability between nights,
- dependence on temperature,
- long-term detector evolution.

### Hot-Pixel Component

Questions include:

- persistence,
- intermittent behavior,
- growth in the bad-pixel population,
- recovery or disappearance,
- changes after hardware events.

A single temporal model may not be appropriate for both.

---

## Product Contract Requirements

A Master Dark Product should not be represented only as one array.

It should expose at least:

- smooth dark-rate image,
- hot-pixel mask,
- input-frame references,
- input exposure times,
- normalization convention,
- combination method,
- smoothing method and scale,
- detector temperature metadata,
- summary statistics,
- validity interval,
- algorithm version.

Optional diagnostic payloads may include:

- unsmoothed robust dark-rate image,
- per-pixel scatter image,
- hot-pixel rate image,
- model residuals.

---

## Required Metadata

Preserve:

- observation times,
- amplifier / ZipCode,
- controller identity,
- exposure time,
- gain value and source,
- detector and ambient temperature,
- number of input frames,
- original exposure-time distribution,
- overscan algorithm,
- combination estimator,
- hot-pixel detection rule,
- smoothing method,
- smoothing scale,
- dark-current summary statistics,
- mask statistics,
- algorithm and configuration versions.

---

## Required Analytics

VIRUSFlow should characterize:

- median smooth dark rate by amplifier,
- smooth dark morphology over time,
- dark rate versus detector or ambient temperature,
- compatibility of normalized frames from different exposure times,
- residuals after exposure-time scaling,
- hot-pixel count and growth rate,
- persistence and intermittency of hot pixels,
- amplifier-to-amplifier differences,
- controller-dependent behavior,
- impact of dark correction on science and calibration residuals,
- and whether one common temporal model is adequate.

---

## Open Questions

- Is dark current perfectly linear with exposure time over the operational range?
- Are one-second-normalized dark frames from different exposure durations
  interchangeable?
- What smoothing scale best separates broad dark structure from detector
  defects?
- Is approximately 100 pixels appropriate for every amplifier?
- Which robust smoother gives the most stable edge behavior?
- How stable is the smooth dark morphology within a night?
- How strongly does the smooth component depend on detector temperature?
- Are hot pixels stable enough for a persistent mask, or should they carry
  validity intervals?
- Do some bright pixels require subtraction rather than masking?
- Can dark frames help isolate short-timescale residual bias interference?
- How should cosmic rays be distinguished from transient hot pixels when only
  one dark frame is available?

---

## Initial Implementation Decisions

Until evidence requires a richer model:

- Express dark current in electrons per second.
- Normalize every valid dark frame by its exposure time.
- Permit compatible normalized dark frames to be combined across exposure
  durations.
- Preserve the original exposure times for validation and provenance.
- Decompose the Master Dark into a smooth component and a hot-pixel mask.
- Mask hot pixels rather than treating them as part of the smooth dark field.
- Estimate the smooth component with a robust large-scale smoother.
- Use an initial characteristic smoothing scale near 100 pixels.
- Scale the smooth dark-rate image to each target exposure time.
- Keep masked detector defects masked after dark subtraction.
- Retain temperature metadata without initially requiring a predictive model.

---

## Repository Goals

VIRUSFlow should:

- separate smooth dark current from sparse detector defects,
- maintain dark-current models in physical units of electrons per second,
- determine whether dark current scales linearly with exposure time,
- test whether frames of different exposure durations can be combined safely,
- optimize the robust smoothing scale and estimator,
- measure the temperature dependence of the smooth dark component,
- track the birth, persistence, intermittency, and recovery of hot pixels,
- maintain time-aware Pixel Mask products,
- distinguish dark current from residual bias and electronic interference,
- quantify the improvement produced by dark subtraction,
- and eventually determine whether dark behavior is best represented by nightly
  products, interpolated endpoint models, or a continuously evolving detector
  model.
