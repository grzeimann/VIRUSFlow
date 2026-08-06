# VIRUSFlow Scientific Knowledge Specification

# Working Note: Detector Gain

> Status: Initial implementation specification

This note captures the operational understanding of detector gain within VIRUS
and how it should be represented in VIRUSFlow. The objective is to document how
gain enters the reduction process, what assumptions are currently made, and what
future repository analytics should investigate.

---

## Relevant Architectural Entities

### Products

- Raw Amplifier Frame
- Bias-Corrected Amplifier Image
- Master Bias Product
- Master Dark Product
- Master Flat Product
- Master Science Product
- Error Image

### Model Components

- Gain Component
- Read Noise Component
- Variance Model
- Fiber Normalization Component

---

## Scientific Interpretation

Gain is the conversion factor between detector analog-to-digital units (ADU) and
electrons.

Its primary purpose is to convert detector measurements into physically
meaningful electron counts so that detector statistics, read noise, Poisson
noise, and uncertainty propagation are expressed in consistent units.

Gain is a multiplicative calibration.

Unlike nightly calibrations, it is currently treated as static instrument
knowledge.

---

## Origin of Gain

Gain is not measured during routine observations.

Instead, it is determined during laboratory characterization of the detector.

The standard CCD gain experiment measures the relationship between signal level
and variance over a sequence of increasing illumination levels. The slope of
this relationship provides the detector gain.

After laboratory characterization:

- the gain values are placed into instrument configuration files,
- propagated into the FITS headers of raw observations,
- and treated as authoritative during science operations.

Operational reductions therefore inherit laboratory measurements rather than
producing independent gain estimates.

---

## Operational Assumption

Once an amplifier is deployed, VIRUSFlow assumes the configured gain remains
valid.

No routine operational measurement currently updates this value.

Consequently, gain should be viewed as static operational knowledge rather than
a nightly calibration product.

This assumption is likely adequate for normal reductions but should remain open
to future validation.

---

## Gain Retrieval

The gain used during reduction is taken from the FITS header.

Representative implementation:

```python
gain = float(hdr.get("GAIN", 0.85))

if not np.isfinite(gain) or gain <= 0:
    gain = 0.85
```

The fallback value of **0.85 electrons/ADU** represents an average VIRUS gain
and provides a safe operational default when:

- the keyword is absent,
- the value is non-finite,
- or the value is zero or negative.

The use of a default allows reduction to continue while preserving operational
robustness.

Such substitutions should always be recorded as provenance rather than occurring
silently.

---

## Relationship to Other Components

Gain enters nearly every downstream detector model.

It determines:

- detector units,
- read-noise units,
- Poisson variance,
- propagated uncertainty,
- signal-to-noise calculations.

Its uncertainty is not modeled independently.

Instead, any residual gain errors are naturally absorbed into later calibration
steps, particularly empirical calibrations such as fiber normalization and
throughput calibration.

This does not imply the gain is perfectly known—only that downstream empirical
calibrations compensate for modest gain errors.

---

## Time Stability

The long-term stability of detector gain remains largely unmeasured.

Potential sources of evolution include:

- electronics aging,
- amplifier replacement,
- controller changes,
- detector maintenance,
- hardware upgrades.

At present there is insufficient operational evidence to justify a time-variable
gain model.

---

## Required Metadata

Products should preserve:

- gain value used,
- gain source (header or default),
- configuration version,
- amplifier / ZipCode,
- controller identity,
- observation time,
- reduction algorithm version.

If the default value is substituted, that decision should be explicitly recorded.

---

## Required Analytics

VIRUSFlow should investigate:

- long-term stability of gain assumptions,
- consistency between configured gain and observed detector statistics,
- amplifier-to-amplifier gain distribution,
- gain changes following hardware interventions,
- whether read-noise evolution suggests gain drift,
- whether empirical calibration products show systematic gain-dependent trends.

---

## Open Questions

- Does detector gain remain constant over many years?
- Can gain be estimated from routine calibration data?
- Can photon-transfer behavior be partially recovered operationally?
- Are controller swaps associated with gain changes?
- Would a slowly evolving gain model improve uncertainty estimates?

---

## Initial Implementation Decisions

Until evidence suggests otherwise:

- Treat gain as static operational knowledge.
- Read gain from the FITS header.
- Use 0.85 electrons/ADU as the default fallback.
- Reject non-finite or non-positive gain values.
- Record whether the configured or fallback gain was used.
- Apply gain before detector uncertainty calculations.
- Keep gain separate from nightly calibration products.

---

## Repository Goals

VIRUSFlow should:

- preserve complete provenance of gain values,
- distinguish configured gain from fallback defaults,
- evaluate the long-term validity of laboratory gain measurements,
- detect hardware changes that may invalidate configured gain,
- determine whether gain drift can be measured from operational data,
- quantify the scientific impact of gain uncertainty,
- and eventually determine whether gain should remain static knowledge or evolve
into a time-dependent detector model.
