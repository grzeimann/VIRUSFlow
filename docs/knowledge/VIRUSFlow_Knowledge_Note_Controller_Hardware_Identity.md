# VIRUSFlow Scientific Knowledge Specification

# Working Note: Controller Swaps, Hardware Identity, and ZipCode Lineages

> Status: Initial implementation specification

This note defines how VIRUSFlow represents hardware identity and how controller
swaps should influence calibration products, provenance, and long-term
analytics.

The central design principle is:

> **The complete ZipCode defines the smallest operationally independent
> calibration unit.**

A change to **any** component of the ZipCode begins a new calibration lineage.

---

# The VIRUS ZipCode

Every amplifier is identified by five components:

- IFUSLOT
- IFUID
- SPECID
- AMP
- CONTROLLER

These are not arbitrary identifiers. Each represents a distinct physical
subsystem with different scientific responsibilities.

| Component | Physical Meaning | Primarily Influences |
|-----------|------------------|----------------------|
| IFUSLOT | Position in the VIRUS array | Sky location, illumination pattern, vignetting, fiber normalization |
| IFUID | Physical fiber bundle | Fiber throughput, broken fibers, fiber characteristics |
| SPECID | Spectrograph | Trace, wavelength solution, optical throughput, PSF |
| AMP | CCD amplifier | Detector geometry, bias, gain, read noise, dark current, pixel defects |
| CONTROLLER | Readout electronics | Electronic behavior, bias structure, amplifier artifacts, read noise |

Together these define the complete observational and calibration identity of an
amplifier.

---

# Operational Identity

Although individual detector properties may depend primarily on one subsystem,
VIRUSFlow adopts a conservative operational rule:

> **If any ZipCode component changes, the calibration identity changes.**

A calibration Product generated for one ZipCode is never assumed to apply to a
different ZipCode.

This avoids silently transferring assumptions between different hardware
configurations.

---

# Calibration Lineages

Every unique ZipCode defines its own calibration lineage.

A lineage contains:

- calibration Products,
- QA measurements,
- detector-health measurements,
- analytics summaries,
- provenance,
- validity intervals.

When any ZipCode component changes, a new lineage begins.

This does **not** imply that all detector knowledge has changed—only that the
repository should require evidence before reusing it.

---

# Controller Swaps

Controllers are replaceable electronics.

Controller swaps occur because of:

- planned hardware upgrades,
- modernization campaigns,
- electrical failures,
- programming failures,
- dropped bias voltage,
- localized electrical damage,
- strategic optimization of the VIRUS array.

Controllers may also be moved deliberately from lower-priority locations into
higher-value regions of the focal plane to maximize scientific productivity.

---

# Scientific Consequences of Controller Swaps

Changing the controller can affect:

- bias structure,
- read noise,
- electronic artifacts,
- zero-readout behavior,
- calibration stability,
- amplifier reliability.

However, controller swaps generally do **not** change:

- fiber locations,
- spectrograph optics,
- detector geometry,
- wavelength mapping,
- trace geometry.

Nevertheless, VIRUSFlow does **not** assume continuity.

Instead, a controller swap begins a new calibration lineage while allowing
analytics to determine which properties remained unchanged.

---

# Carrying Knowledge Forward

Many detector characteristics are expected to survive controller changes.

Examples include:

- hot pixels,
- pocks,
- dead columns,
- detector geometry,
- amplifier orientation.

These may be copied forward as prior knowledge or initial guesses.

However, inherited information should remain provisional until confirmed by new
observations.

Knowledge inheritance is therefore an analytics and provenance decision—not an
assumption of the reduction pipeline.

---

# Products vs. Analytics

VIRUSFlow intentionally separates reduction from scientific discovery.

## Products

Reduction is conservative.

Products are always associated with one complete ZipCode.

No implicit relaxation of hardware identity occurs.

## Analytics

Analytics may intentionally relax identity.

Examples include:

- same amplifier, different controllers,
- same spectrograph over time,
- same IFU in different slots,
- same controller driving different amplifiers,
- identical hardware except for IFUSLOT.

These comparisons are scientific investigations and should never silently alter
calibration Products.

---

# Provenance Requirements

Every Product should preserve:

- complete ZipCode,
- controller identifier,
- configuration version,
- observation time,
- first/last validity,
- maintenance or swap references (when available),
- inherited calibration references,
- algorithm version.

---

# Required Analytics

VIRUSFlow should investigate:

- detector behavior before and after controller swaps,
- read-noise evolution by controller,
- bias evolution by controller,
- calibration success before and after maintenance,
- electronics reliability,
- recovery after hardware intervention,
- continuity of detector defects across controller changes,
- optimal strategies for reusing historical detector knowledge.

---

# Initial Implementation Decisions

- The complete ZipCode defines the operational calibration identity.
- Every ZipCode change begins a new calibration lineage.
- Controller identity is first-class provenance.
- Historical knowledge may be inherited only as prior information.
- Reduction never assumes continuity across hardware changes.
- Analytics may explicitly compare related lineages by relaxing selected
  ZipCode components.

---

# Repository Goals

VIRUSFlow should:

- preserve the complete history of every ZipCode,
- distinguish detector evolution from electronics evolution,
- identify controller swaps as explicit provenance events,
- determine which detector properties survive hardware changes,
- quantify the scientific impact of controller replacements,
- reconstruct historical hardware configurations,
- support controlled comparison between related calibration lineages,
- and build a repository that learns across hardware generations while keeping
  production reductions conservative, reproducible, and fully traceable.
