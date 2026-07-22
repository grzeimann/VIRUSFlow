# VIRUSFlow Scientific Knowledge Specification

# Working Note: Zero Readouts and Missing Amplifier Data

> Status: Initial implementation specification

This note records a VIRUS-specific acquisition failure mode in which an amplifier
produces a raw image filled entirely with zeros.

The purpose is to ensure that VIRUSFlow treats these frames as missing scientific
evidence rather than as valid detector measurements.

---

## Relevant Architectural Entities

### Raw Evidence

- Raw Amplifier Frame
- Raw Observation Record
- RawDB Availability State

### Products

- Overscan Measurement
- Bias-Corrected Amplifier Image
- Master Bias Product
- Master Dark Product
- Master Comparison Product
- Master Flat Product
- Master Twilight Product
- Master Science Product
- Extracted Spectrum Product

### Model and Status Entities

- Amplifier Availability State
- Instrument Health / Operational State
- Calibration Completeness State
- Observation Completeness State

---

## Observed VIRUS Behavior

When an amplifier is unavailable or nonfunctional, the upstream acquisition
infrastructure may still produce a raw amplifier frame.

The entire array is filled with zeros:

- the illuminated data section is zero,
- the overscan region is zero,
- all rows and columns are zero.

The existence of a raw file therefore does not guarantee that the amplifier
produced a detector measurement.

---

## Temporal Behavior

A zero readout is generally not an isolated single-exposure event.

The condition often persists for:

- an entire observing night,
- several consecutive days,
- or a longer interval until the amplifier or associated electronics can be
  repaired or restored.

This persistence makes zero readouts both a frame-level validity problem and an
instrument-state problem.

---

## Scientific Interpretation

A zero-filled raw amplifier frame has no scientific utility.

It is not:

- a valid bias frame,
- a measurement of zero bias,
- a valid dark frame,
- a zero-flux science observation,
- evidence of zero sky,
- or a calibration product with unusually low signal.

Scientifically, the amplifier did not observe.

The correct interpretation is:

```text
raw record exists
        ≠
valid detector evidence exists
```

For that amplifier and exposure, the scientific state is equivalent to missing
data.

---

## Detection Rule

The initial detection rule should be explicit and conservative.

A raw amplifier frame should be classified as a zero readout when:

- every pixel in the raw array is exactly zero, or
- all finite pixels are zero and no nonzero detector or overscan samples exist.

The test should be performed before:

- overscan subtraction,
- gain application,
- error propagation,
- calibration stacking,
- trace finding,
- extraction,
- or any other downstream algorithm.

A zero-readout detector should not rely on the overscan alone, because a valid
frame may have a small or unusual overscan while still containing detector
signal. The whole raw array is the decisive condition.

---

## Validation State

Zero readout should be represented as an explicit validity or availability
state, not merely as a QA warning.

Suggested states include:

```text
VALID
ZERO_READOUT
MISSING
CORRUPT
UNREADABLE
```

The exact vocabulary may be normalized later, but `ZERO_READOUT` should remain
distinguishable from both absent files and malformed files.

This distinction preserves the fact that:

- the acquisition system created a nominal frame,
- the detector channel produced no usable measurement,
- and the condition may carry information about instrument health.

---

## RawDB Representation

RawDB should record both file existence and scientific availability.

A zero frame should remain indexed for provenance and operational history, but
should be marked as scientifically unavailable.

Relevant fields may include:

- raw record exists
- file readable
- array shape valid
- zero-readout flag
- scientific usability
- amplifier operational state
- first detected time
- last detected time
- associated night or observing interval
- recovery time, when known

This prevents downstream systems from interpreting "file found" as "input
available."

---

## Product Construction Policy

Zero readouts must be excluded from all calibration and science products.

They must not contribute to:

- master bias stacks,
- master dark stacks,
- comparison stacks,
- twilight stacks,
- flat stacks,
- science stacks,
- trace measurements,
- wavelength solutions,
- fiber normalization,
- sky measurements,
- extraction,
- or QA statistics describing valid detector data.

A zero readout should count as an unavailable input, not as a rejected numerical
outlier within an otherwise valid stack.

---

## Calibration Consequences

If an amplifier produces zero readouts for all candidate calibration frames,
VIRUSFlow cannot construct a new calibration product for that amplifier and
time interval.

The system should report the actual reason:

```text
calibration unavailable because amplifier produced no valid detector evidence
```

It should not report only a generic failure such as:

- insufficient frames,
- stack failed,
- trace not found,
- wavelength solution failed,
- or invalid statistics.

Those may be downstream symptoms, but the zero readout is the primary cause.

---

## Observation Consequences

For science observations, a zero-readout amplifier should be treated as absent
coverage.

This means:

- its 112 fibers did not provide spectra,
- the corresponding spectrograph region has no valid measurement,
- the observation is incomplete for that amplifier,
- and any combined observation or cube should retain an explicit coverage gap.

The raw observation may still be valid for the remaining amplifiers.

Therefore the condition should propagate at amplifier scope rather than
invalidating the entire exposure or IFU automatically.

---

## Relationship to Amplifier and IFU Structure

Each VIRUS IFU has four amplifier channels:

- `LL`
- `LU`
- `RL`
- `RU`

A zero readout in one amplifier removes the contribution of 112 fibers while the
other three amplifiers may remain scientifically usable.

The repository should distinguish:

- complete IFU observation,
- partially available IFU observation,
- unavailable spectrograph half,
- unavailable amplifier,
- and fully unavailable IFU.

This distinction matters for calibration completeness, extraction, focal-plane
coverage, and downstream science products.

---

## Error and Variance Policy

A zero raw image must not be converted into a normal reduced image with nominal
read noise.

Doing so would create a misleading product that appears to contain valid
zero-valued data with finite uncertainty.

Instead:

- no reduced detector Product should be published as scientifically valid,
- no normal variance model should be assigned,
- and downstream masks should identify the entire amplifier as unavailable.

If a placeholder array is required for shape compatibility, every pixel should
be marked invalid and the Product must carry an explicit non-scientific status.

---

## Provenance and Persistence

Zero readouts should not be discarded from the repository.

Preserve:

- the original raw-frame reference,
- observation and exposure identity,
- amplifier / ZipCode,
- controller identity,
- detection algorithm and version,
- array shape and data type,
- detection time,
- operational-state classification,
- and any recovery or maintenance context.

The raw record is operational evidence even though it is not scientific detector
evidence.

---

## Required Metadata

At minimum, retain:

- observation time
- exposure identity
- observation identity
- IFUSLOT
- IFUID
- SPECID
- amplifier
- controller
- frame type
- raw filename or archive member
- zero-readout status
- detection method and version
- first and last time associated with the state
- usable / unusable scientific status
- reason for exclusion

---

## Required Analytics

VIRUSFlow should analyze:

- zero-readout frequency by amplifier,
- first and last occurrence of each outage,
- duration of continuous zero-readout intervals,
- correlation with controller identity,
- correlation with hardware interventions,
- recovery events,
- affected calibration families,
- affected science observations,
- number of lost fibers and exposure-hours,
- repeated transitions between valid and zero-readout states,
- and whether any nonzero precursor behavior appears before an amplifier fails.

---

## Open Questions

- Does every amplifier outage produce an exactly zero array?
- Are there partial failures that produce zero rows, columns, or detector
  regions?
- Can stale or repeated arrays occur instead of zeros?
- Is the failure state associated with the amplifier, controller, acquisition
  channel, or another upstream component?
- Can a zero-readout interval begin or end during a night?
- What operational metadata reliably identifies repair or recovery?
- Should a persistent zero state automatically create an instrument-state
  interval in the repository?
- How should calibration resolution behave when the nearest valid calibration
  predates a long amplifier outage?

---

## Initial Implementation Decisions

Until evidence requires a richer classifier:

- Test every raw amplifier frame for an all-zero array before base reduction.
- Classify such frames explicitly as `ZERO_READOUT`.
- Retain the raw record for provenance and instrument-health analysis.
- Exclude the frame from all scientific and calibration Products.
- Treat the amplifier as scientifically unobserved for that exposure.
- Propagate missing coverage at amplifier scope.
- Do not generate nominal reduced data or variance from the zero array.
- Report zero readout as the root cause of downstream calibration
  unavailability.
- Track persistent intervals of zero-readout behavior.

---

## Repository Goals

VIRUSFlow should:

- distinguish raw-file existence from valid detector evidence,
- represent zero readout as an explicit amplifier availability state,
- prevent zero frames from contaminating calibrations and QA statistics,
- propagate amplifier-level missing coverage into all downstream Products,
- identify persistent outage intervals automatically,
- quantify the scientific exposure and fiber coverage lost to zero readouts,
- correlate outages with controller and hardware history,
- detect partial or nonzero failure modes that may accompany amplifier outages,
- provide clear root-cause diagnostics when calibrations cannot be built,
- and preserve enough operational history to study amplifier reliability over
  the full lifetime of VIRUS.
