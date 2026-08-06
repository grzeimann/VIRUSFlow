# VIRUSFlow Scientific Knowledge Specification

# Working Note: Bias Stability, Overscan, and Electronic Structure

> Status: Initial implementation specification

This note captures the current operational understanding of VIRUS detector bias behavior. The objective is not to explain CCD electronics generally, but to preserve the VIRUS-specific knowledge needed to implement, validate, and improve VIRUSFlow.

---

## Relevant Architectural Entities

### Products
- Overscan Measurement
- Bias-Corrected Raw Frame
- Master Bias Product
- Master Dark Product

### Model Components
- Bias Structure Component
- Electronic Offset Component (future)
- Time-Dependent Bias Drift Component (future)

---

## Two Distinct Components of Bias

The detector bias should be treated as two related but distinct phenomena.

### Overscan (Per-Exposure Electronic Offset)

The overscan measures the electronic bias present during every detector read.

Historically this was represented by a single scalar (typically a robust biweight or median), reducing digitization effects and outliers. Experience with VIRUS has shown this approximation is insufficient for many amplifiers.

## Overscan Geometry

Each VIRUS amplifier contains an overscan region consisting of the final 32 detector columns (following the fixed 2×1 detector binning).

The overscan is not considered part of the science image.

Its purposes are:

- Measure the electronic bias for every detector read.
- Capture row-dependent electronic offsets.
- Monitor short-timescale electronic behavior.
- Provide quality diagnostics for detector electronics.

During base reduction:

1. The overscan region is measured.
2. A row-by-row overscan profile is constructed from the final 30 usable columns (excluding the first two overscan columns).
3. The profile is subtracted from every detector row.
4. The overscan columns are trimmed from the image.

The trimmed science image therefore contains only the illuminated detector region, while the overscan measurements remain available as metadata and diagnostics.

### Residual Bias Structure

After overscan subtraction, coherent two-dimensional electronic structure remains. This residual pattern is captured by the Master Bias.

---

## Improved Overscan Algorithm

Many amplifiers exhibit significant overscan structure rather than a constant offset.

The common morphology resembles a broad valley:

- high near the amplifier readout,
- decreasing across the detector,
- increasing again toward the opposite side.

Current preferred algorithm:

- exclude the first two overscan columns
- average the remaining ~30 overscan columns independently for every detector row,
- construct a one-dimensional overscan profile,
- subtract that profile from each detector column.

This changes overscan correction from a scalar estimate to a row-by-row measurement.

---

## Residual Bias Structure

After overscan correction, images retain coherent electronic patterns.

These vary between amplifiers and appear related to electronics, cabling, and network configuration.

One of the dominant remaining structures is often a net electronic offset remaining after overscan subtraction, commonly several tenths of an ADU (roughly ±0.6 ADU in current experience).

The Master Bias primarily estimates and removes this remaining structure.

---

## Time Stability

The temporal behavior of residual electronic structure remains poorly understood.

Important unknowns include:

- stability within a night,
- stability between nights,
- dependence on environmental conditions,
- dependence on hardware configuration,
- repeatability after maintenance or power cycles.

The current nightly calibration strategy should be viewed as a working hypothesis rather than established truth.

---

## Current Operational Model

Typical observing nights obtain approximately eleven bias frames.

The initial implementation should construct a nightly Master Bias from all available overscan-corrected bias frames.

Whether this adequately captures read-to-read electronic variation remains unknown.

---

## Relationship to Dark Frames

Ignoring dark current, a dark frame behaves like a science exposure containing only:

- overscan,
- residual bias structure,
- electronic interference,
- read noise.

Dark frames therefore provide an independent probe of short-timescale electronic behavior while simultaneously measuring dark current.

Future analyses should combine bias and dark products.

---

## Initial Modeling Strategy

Initial assumptions:

- Overscan is measured independently for every exposure.
- Master Bias models the remaining electronic structure.
- Nightly Master Bias represents the baseline detector state.
- Longer-term models should only be introduced after sufficient evidence.

Future models may separate:

- stable detector structure,
- slowly varying offsets,
- transient electronic interference.

---

## Required Metadata

Preserve:

- observation time,
- ZipCode,
- controller identity,
- overscan algorithm/version,
- overscan column selection,
- number of bias frames,
- read-noise estimate,
- environmental metadata,
- hardware configuration.

---

## Required Analytics

VIRUSFlow should characterize:

- nightly Master Bias evolution,
- read-to-read residual structure,
- variance after overscan subtraction,
- principal components of bias structure,
- net electronic offsets,
- amplifier behavior,
- controller behavior,
- temporal evolution,
- comparison between bias and dark residuals.

---

## Open Questions

- How stable is the residual bias pattern?
- Is a nightly Master Bias sufficient?
- What fraction of residual structure is deterministic?
- Can transient interference be isolated?
- Is a hierarchical model preferable?
- Which environmental variables explain the observed changes?

---

## Initial Implementation Decisions

Until further evidence is available:

- Perform row-wise overscan subtraction.
- Construct nightly Master Bias products.
- Preserve overscan measurements and metadata.
- Treat nightly stability as a working assumption.
- Continuously evaluate the assumption through analytics.

The architecture should preserve enough information to replace the nightly Master Bias with richer temporal or hierarchical models without changing downstream interfaces.
