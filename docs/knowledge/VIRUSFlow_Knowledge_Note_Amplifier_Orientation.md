# VIRUSFlow Scientific Knowledge Specification

# Working Note: Amplifier Orientation and Canonical Detector Coordinates

> Status: Initial implementation specification

This note records the VIRUS-specific detector geometry conventions required to
transform raw amplifier reads into the common coordinate system assumed by all
downstream calibration and reduction algorithms.

The transformations already exist in the reduction code. The purpose of this
note is to preserve why they exist, define the canonical output orientation,
and identify metadata and validation requirements that VIRUSFlow must retain.

---

## Relevant Architectural Entities

### Raw Evidence and Products

- Raw Amplifier Frame
- Overscan Measurement
- Bias-Corrected Amplifier Image
- Oriented Amplifier Image
- Master Bias Product
- Master Dark Product
- Master Comparison Product
- Master Flat Product
- Master Twilight Product
- Master Science Product
- Trace Measurement Product
- Wavelength Measurement Product
- Extracted Spectrum Product

### Model Components

- Trace Geometry Component
- Wavelength Solution Component
- Fiber Profile / Extraction Geometry Component
- Pixel Mask / Detector Defect Component
- Detector Response Component

---

## VIRUS IFU and Readout Structure

Each VIRUS IFU contains 448 fibers.

The fiber head is divided between two physically separate spectrographs:

- 224 fibers feed the left spectrograph.
- 224 fibers feed the right spectrograph.

Each spectrograph has its own CCD detector. Each CCD is read through two
amplifiers corresponding to the lower and upper halves of the detector.

The complete IFU therefore produces four amplifier images:

- `LL`: left spectrograph, lower amplifier
- `LU`: left spectrograph, upper amplifier
- `RL`: right spectrograph, lower amplifier
- `RU`: right spectrograph, upper amplifier

Each amplifier contains 112 fibers after the detector is placed into the
canonical VIRUSFlow orientation.

---

## Raw Image Geometry

The raw image orientation is determined by the physical detector and readout
electronics. It is not guaranteed to match the scientific orientation required
for trace finding, wavelength calibration, extraction, or comparison between
amplifiers.

In every raw amplifier image, the overscan appears on the right side of the
array.

The overscan consists of the final 32 columns in the raw, fixed 2×1-binned
image.

The raw position of the overscan is a readout convention. It does not define the
final dispersion direction.

---

## Base-Reduction Ordering

The sequence of operations is scientifically important:

1. Measure the overscan in the raw readout orientation.
2. Subtract the row-dependent overscan estimate.
3. Trim the 32 overscan columns.
4. Transform the remaining data section into canonical orientation.
5. Apply gain and construct the propagated error image.

Overscan measurement and trimming must occur before orientation.

This preserves the invariant that the overscan is always selected from the final
32 raw columns and prevents amplifier-dependent flips from moving the overscan
to another edge before it is measured.

The current CCD utility implements this order explicitly: overscan subtraction,
overscan trimming, orientation, gain multiplication, and error propagation.

---

## Overscan Geometry

For each raw amplifier frame:

- the final 32 columns are designated as overscan,
- the first two columns within that overscan region are excluded from the
  row-wise estimator,
- the remaining 30 columns are combined independently for each row,
- the resulting one-dimensional profile is subtracted from every column in that
  row,
- and all 32 overscan columns are then trimmed.

The overscan pixels are therefore absent from the reduced image, but the
overscan measurement and its diagnostics should remain available as metadata or
a dedicated measurement payload.

---

## Canonical Detector Coordinate System

All oriented VIRUS amplifier images must share the same scientific coordinate
system.

### Spectral Axis

The x-axis is the dispersion axis:

- blue wavelengths are on the left,
- red wavelengths are on the right.

Therefore wavelength must increase with increasing x.

### Fiber Axis

The y-axis is the fiber-order axis:

- fiber 1 is at the bottom,
- fiber 112 is at the top.

Therefore fiber number must increase with increasing y.

In array-index language, where row zero is commonly displayed at the bottom for
scientific images, the intended displayed orientation is:

```text
fiber 112
    ↑
    │
    │
fiber 1     blue  ─────────────→  red
```

The repository should define this convention explicitly rather than relying on
plotting-library defaults.

---

## Amplifier-Dependent Transformations

The current orientation function applies amplifier-dependent flips.

Its documented rules are:

- `LU`: flip both axes
- `RL`: flip both axes
- `LL` and `RU`: no initial two-axis flip
- if legacy `AMPNAME` is `LR` or `UL`, additionally flip the columns

These operations normalize both dispersion direction and fiber order.

The transformation is driven by FITS metadata:

- `CCDPOS`
- `CCDHALF`
- `AMPNAME`

The implementation constructs the amplifier identity from `CCDPOS + CCDHALF`
and then applies an additional legacy correction from `AMPNAME`.

---

## Metadata Ambiguity and Legacy Naming

The primary VIRUS amplifier vocabulary is:

- `LL`
- `LU`
- `RL`
- `RU`

The orientation code also recognizes `AMPNAME` values:

- `LR`
- `UL`

These names do not belong to the same obvious four-value vocabulary and appear
to encode an additional or historical readout convention.

VIRUSFlow should not silently discard this distinction.

The repository should preserve both:

- canonical amplifier identity (`LL`, `LU`, `RL`, `RU`)
- original readout/orientation metadata (`CCDPOS`, `CCDHALF`, `AMPNAME`)

A later normalization layer may translate legacy metadata into a single explicit
orientation transform, but the original values should remain in provenance.

---

## Scientific Purpose of Canonical Orientation

Canonical orientation allows every downstream algorithm to use one geometry
without amplifier-specific branches.

After orientation:

- trace finding can assume wavelength increases with x,
- wavelength solutions can use one coefficient convention,
- fiber traces can be ordered from 1 through 112 with increasing y,
- extraction can use common profile and neighboring-fiber logic,
- masks and detector-coordinate products can be compared consistently,
- amplifier mosaics and QA images can use the same display convention,
- and analytics can compare behavior across all 312 VIRUS amplifiers directly.

Without canonical orientation, amplifier identity would leak into nearly every
scientific algorithm.

---

## Coordinate-System Distinctions

VIRUSFlow should keep the following coordinate systems distinct:

### Raw Readout Coordinates

The pixel coordinates exactly as stored in the FITS file.

Properties include:

- overscan on the right,
- amplifier-dependent dispersion direction,
- amplifier-dependent fiber order.

### Trimmed Raw Coordinates

The raw data section after overscan subtraction and trimming, but before
orientation.

This is useful mainly for debugging and provenance.

### Canonical Amplifier Coordinates

The standard VIRUSFlow detector coordinates:

- blue to red with increasing x,
- fiber 1 to fiber 112 with increasing y.

All normal calibration Products and Model Components should use these
coordinates unless explicitly labeled otherwise.

### Physical Detector Coordinates

Coordinates tied to the physical CCD, amplifier, or spectrograph layout.

These may be needed for hardware diagnostics and cross-amplifier studies, but
should not replace the canonical coordinates used by reduction algorithms.

### Focal-Plane / IFU Fiber Coordinates

Fiber identity and location at the IFU head or telescope focal plane.

These are not equivalent to detector row order and require an explicit mapping.

---

## Product Contract Requirements

Every oriented detector Product should record:

- canonical amplifier identity
- IFUSLOT
- IFUID
- SPECID
- controller identity
- original `CCDPOS`
- original `CCDHALF`
- original `AMPNAME`
- raw array shape
- overscan width
- trimmed array shape
- applied row transformation
- applied column transformation
- orientation algorithm name and version
- coordinate-system identifier

A minimal explicit transform representation could include:

```text
flip_x: true | false
flip_y: true | false
transpose: true | false
```

Even though the current implementation uses only flips, recording the transform
explicitly is safer than requiring future users to reconstruct it from
historical header vocabulary.

---

## Validation Requirements

Orientation should be validated independently of the implementation branches.

Useful checks include:

- arc wavelengths increase from left to right,
- known blue and red arc lines appear on the expected sides,
- trace numbering increases from bottom to top,
- fiber 1 and fiber 112 occupy the expected detector edges,
- neighboring fiber identities remain ordered,
- all four amplifier types produce the same canonical geometry,
- orientation is unchanged by archive versus loose-file access,
- and legacy `AMPNAME` values produce the expected additional correction.

These checks should use physical or calibration invariants, not only expected
array transformations.


## Open Questions

- What exact historical or hardware condition is represented by `AMPNAME=LR` or
  `AMPNAME=UL`?  Pre-2018 case.  Not a problem for any of our usage, but good to keep instead of throwing away.
- Are all current VIRUS headers guaranteed to contain consistent `CCDPOS`,
  `CCDHALF`, and `AMPNAME` values?  Current, yes.  Pre-2018, less so.
- Should the overscan width always be exactly 32 columns for VIRUS, rather than
  inferred from array width? Inferred is fine, but identical in this case.
- Should trimmed pre-orientation images ever be persisted, or only exposed for
  debugging? No.

---

## Initial Implementation Decisions

Until repository evidence requires otherwise:

- Treat `LL`, `LU`, `RL`, and `RU` as the canonical amplifier identities.
- Measure and trim overscan in raw coordinates before any orientation.
- Define canonical x as blue-to-red.
- Define canonical y as fiber 1-to-112.
- Preserve all original orientation-related header metadata.
- Record the resolved transform explicitly in every relevant Product.
- Require all downstream calibration algorithms to consume canonical
  coordinates.
- Validate orientation using wavelength and fiber-order invariants.
- Keep spectrograph, CCD, amplifier, detector, and IFU-head identities distinct
  even when the initial algorithms operate at amplifier scope.

---

## Repository Goals

VIRUSFlow should:

- establish one authoritative canonical detector-coordinate convention,
- remove amplifier-specific orientation logic from downstream algorithms,
- preserve enough metadata to reconstruct every raw-to-canonical transform,
- maintain explicit mappings between detector row, extracted fiber number,
  spectrograph fiber, and IFU-head fiber,
- and ensure that future algorithms cannot accidentally mix raw and canonical
  detector coordinates.
