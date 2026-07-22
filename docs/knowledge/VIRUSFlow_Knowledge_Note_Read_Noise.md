# VIRUSFlow Scientific Knowledge Specification

# Working Note: Read Noise

> Status: Initial implementation specification

This note captures the current understanding of read noise in VIRUS and how it
should be represented within VIRUSFlow. The goal is not to explain detector
electronics, but to document the assumptions, measurements, and repository
behavior surrounding read noise.

---

## Relevant Architectural Entities

### Products

- Bias-Corrected Amplifier Image
- Master Bias Product
- Individual Bias Frames
- Master Dark Product

### Model Components

- Read Noise Component (optional future)
- Bias Structure Component

---

## Scientific Interpretation

Read noise represents the uncertainty introduced during measurement of the
detector voltage. After conversion by the detector gain, it is naturally
expressed in electrons.

For VIRUS, the typical read noise is approximately:

    3 electrons

It represents an irreducible measurement uncertainty that is independent of the
incident photon signal.

---

## Relationship to Gain

VIRUSFlow applies detector gain during the basic reduction process using the
gain recorded in the FITS header.

These gain values originate from static configuration files established during
instrument characterization. Once an amplifier is deployed there is generally no
routine operational measurement of gain, so the repository currently treats gain
as fixed knowledge.

Because gain is applied before read-noise estimation, the measured read noise is
reported in electrons.

This ordering is mathematically equivalent to measuring read noise before gain
application and then converting units afterward; the operations commute.

The principal uncertainty is therefore not the ordering, but the assumption that
the configured gain remains correct throughout the lifetime of the amplifier.

---

## Current Measurement Strategy

Read noise is estimated from a collection of bias frames.

The procedure is:

1. Construct a robust Master Bias using the biweight location.
2. Compare every bias frame with the Master Bias.
3. Compute the absolute residuals for every detector pixel.
4. Convert the Median Absolute Deviation (MAD) to an equivalent Gaussian
   standard deviation.

Representative implementation:

```python
stack = np.stack(frames, axis=0)

master = biweight_location(
    stack,
    axis=0,
    ignore_nan=True,
)

mad = np.median(
    np.abs(stack - master[None, :, :]),
    axis=0,
) * 1.4826
```

The resulting per-pixel scatter image characterizes detector read noise after
bias structure has been removed.

Repository QA may summarize this image using robust statistics such as the
median or biweight location.

---

## Expected Behavior

Typical values:

- ~3 electrons: nominal
- >4.5 electrons: warning
- >6 electrons: strong indication of amplifier problems

Persistent read noise above six electrons may justify declaring the amplifier
scientifically invalid until the condition is resolved.

Thresholds should remain configurable as operational experience evolves.

---

## Time Stability

The long-term stability of read noise should not be assumed.

Potential contributors include:

- electronics aging,
- controller changes,
- hardware maintenance,
- environmental conditions,
- amplifier degradation.

Current operational practice assumes the nightly estimate adequately represents
the detector state, but VIRUSFlow should continuously test this assumption.

---

## Required Metadata

Retain:

- observation time
- amplifier / ZipCode
- controller identity
- gain value used
- gain source/version
- read-noise estimate
- number of input bias frames
- robust estimator used
- algorithm version
- environmental metadata

---

## Required Analytics

VIRUSFlow should characterize:

- read-noise evolution versus time,
- amplifier-to-amplifier distribution,
- controller-dependent behavior,
- correlation with hardware interventions,
- relationship between gain configuration and measured read noise,
- read-noise stability over months and years,
- fraction of amplifiers exceeding warning thresholds,
- relationship between read noise and calibration quality.

---

## Open Questions

- Does configured gain drift over the instrument lifetime?
- Can gain be estimated empirically from operational data?
- Is read noise stable within a night?
- Are elevated read-noise states predictive of future amplifier failures?
- Are there identifiable environmental drivers?

---

## Initial Implementation Decisions

- Measure read noise from nightly bias products.
- Apply gain before estimating read noise.
- Treat configured gain as fixed operational knowledge.
- Publish read-noise measurements as Product metadata and QA.
- Flag amplifiers exceeding operational thresholds.
- Allow read noise to invalidate amplifier Products when limits are exceeded.

---

## Repository Goals

VIRUSFlow should:

- monitor read noise throughout the lifetime of every amplifier,
- detect gradual degradation before failure,
- identify persistent high-noise states,
- evaluate whether gain assumptions remain valid,
- correlate read noise with hardware history and controller changes,
- determine whether nightly measurements adequately characterize amplifier
  performance,
- and build a long-term statistical model of detector health.
