# VIRUSFlow Scientific Knowledge Specification

# Working Note: Temperature-Dependent Trace and Wavelength Drift

> Status: Initial implementation specification

This document captures current understanding of a VIRUS-specific behavior that
should directly influence implementation of Products, Model Components,
Snapshots, and future analytics.

---

## Relevant Architectural Entities

### Products

- Trace Measurement Product
- Individual Wavelength Solution Product
- Master Comparison Product
- Master Flat / Twilight Products

### Model Components

- Trace Geometry Component
- Wavelength Solution Component
- Time-Dependent Drift Component (future)

---

## Observed VIRUS Behavior

Small fluctuations in ambient temperature produce measurable shifts in both
fiber trace geometry and wavelength solution.

Although the physical mechanism belongs to the spectrograph (two amplifiers),
the initial implementation should model the behavior independently for each
amplifier (ZipCode scope). Spectrograph identity should be preserved only as
metadata for later investigation.

---

## Typical Scale

Across the normal operating temperature range, total shifts are approximately

    0.2–0.6 detector pixels

The amplitude may differ between amplifiers and may evolve with configuration
or long-term instrument changes.

---

## Predictor

Ambient temperature is strongly correlated with both trace and wavelength
shifts.

Temperature should therefore be retained as required metadata on all trace and
wavelength measurements and model evaluations.

---

## Scientific Interpretation

Temperature is a useful interpolation coordinate, but should not initially be
treated as a sufficiently accurate predictor of the absolute instrument state.

Measured calibrations remain the authoritative description of the instrument.

Temperature provides only the differential correction between measured states.

---

## Initial Modeling Strategy

Preferred implementation:

Measured Solution A

↓

Temperature-conditioned Δ shift

↓

Evaluated State

↓

Measured Solution B

The model estimates differential motion relative to measured calibration
products rather than predicting an absolute solution directly from temperature.

---

## Endpoint Policy

Interpolation should normally be bounded by measured calibration products.

Measured endpoints provide:

- Absolute instrument state
- Protection against secular drift
- Protection against unmodeled effects
- Empirical correction beyond temperature alone

---

## Extrapolation Policy

Large extrapolations beyond measured endpoints should not occur silently.

Possible policies:

- prohibit extrapolation
- explicitly flag degraded quality
- allow only configurable limited extrapolation

---

## Initial Mathematical Representation

Conceptually

Solution(context)

=

Measured Solution

+

Temperature-dependent differential correction

The first implementation may use linear interpolation, while allowing future
replacement by regression, splines, hierarchical models, or learned models.

---

## Important Quantities

Keep distinct:

- Absolute trace solution
- Trace shift (pixels)
- Absolute wavelength solution
- Wavelength shift (pixels)
- Wavelength shift (physical units)
- Measurement residual
- Model interpolation residual
- Endpoint separation (time)
- Endpoint separation (temperature)

---

## Required Analytics

VIRUSFlow should eventually analyze:

- trace shift versus temperature
- wavelength shift versus temperature
- correlation strength by amplifier
- residuals after temperature correction
- long-term slope evolution
- amplifier pair comparisons
- interpolation error using held-out measurements
- conditions where temperature fails as the dominant predictor

---

## Open Questions

- Is the relationship linear?
- Does every amplifier require its own slope?
- Are slopes stable over months or years?
- Is there measurable hysteresis?
- Are additional environmental variables required?
- Can amplifier pairs share parameters?
- What endpoint spacing preserves acceptable interpolation accuracy?

---

## Initial Implementation Decisions

Until measurements demonstrate otherwise:

- Model at amplifier (ZipCode) scope.
- Retain spectrograph identity as metadata.
- Anchor interpolation to measured calibration products.
- Use temperature only as a differential interpolation coordinate.
- Avoid unconstrained prediction.
- Preserve residuals and uncertainty.
- Continuously evaluate assumptions through analytics.

---

## Template for Future Knowledge Notes

Each note should contain:

- Purpose
- Relevant Products
- Relevant Model Components
- Scope
- Known Instrument Behavior
- Initial Modeling Strategy
- Required Metadata
- Validity / Failure Policy
- Required Analytics
- Open Questions
- Initial Implementation Decisions

The goal is not to explain detector physics, but to preserve the
instrument-specific knowledge required to build, validate, and continuously
improve VIRUSFlow.
