# VIRUS Fiber Positions and IFU Geometry Corrections

## Purpose

This note documents the calibrated spatial positions of VIRUS fibers and the known IFU-specific corrections required to reproduce the established instrument geometry.

Within VIRUSFlow, these positions may currently be called `fiber_offsets`. In earlier VIRUS reduction software, the same concept was commonly called `ifupos` or `ifucen`.

These names all refer to the two-dimensional position of each fiber within a VIRUS IFU.

This document is intended to preserve the scientific and instrumental knowledge behind the implementation. It is not intended to prescribe a particular software architecture.

---

## Scientific Meaning

A VIRUS amplifier contains 112 extracted fiber spectra. Each spectrum must be associated with the correct spatial position of its fiber within the IFU.

The fiber-position table provides this mapping:

```text
extracted spectrum index → IFU-plane fiber position
```

This mapping is required when constructing spatially meaningful products, including:

- reconstructed images,
- data cubes,
- fiber-level spatial diagnostics,
- astrometric products,
- focal-plane visualizations,
- and comparisons between fibers or IFUs.

The position associated with a spectrum must follow the ordering of the extracted spectral arrays. A geometrically correct table with the wrong fiber ordering is still scientifically incorrect.

---

## Terminology

The following names have historically been used for the same underlying concept:

| Name | Meaning |
|---|---|
| `ifucen` | IFU-center or IFU fiber-position table |
| `ifupos` | IFU-plane position of each fiber |
| `fiber_offsets` | Current VIRUSFlow terminology for the fiber coordinates |

`fiber_offsets` should be understood as calibrated fiber-center coordinates, not as an offset measured dynamically during a reduction.

These positions are static instrument configuration.

---

## Authoritative Position Tables

The archival implementation used two text files:

```text
IFUcen_HETDEX.txt
IFUcen_HETDEX_reverse_R.txt
```

The files were read with:

```python
np.loadtxt(
    filename,
    usecols=[0, 1, 2, 4],
    skiprows=30,
)
```

After loading, the resulting table contains:

```text
448 rows
4 retained columns
```

The two spatial coordinates are columns `1:3` of the loaded array.

The original physical units should be preserved. VIRUSFlow should not apply a coordinate conversion unless the source-file definition explicitly requires one.

The authoritative files should live with the version-controlled VIRUS instrument configuration or package resources.

They should not be replaced with a synthetic fiber grid.

---

## Fiber Organization

A complete VIRUS IFU position table contains 448 fibers:

```text
4 amplifiers × 112 fibers per amplifier = 448 fibers
```

The archival mapping from the complete table to individual amplifiers is:

| Amplifier | Rows in the complete table |
|---|---:|
| `LU` | `0:112` |
| `LL` | `112:224` |
| `RL` | `224:336` |
| `RU` | `336:448` |

These are zero-based NumPy row ranges, with the upper bound excluded.

Each amplifier slice is reversed before it is associated with the extracted spectra:

```python
amplifier_coordinates = complete_table[start:stop, 1:3][::-1]
```

Therefore, the calibrated positions returned for an amplifier must have shape:

```text
112 × 2
```

and must be ordered consistently with the amplifier's extracted spectral arrays.

The output reversal is distinct from the IFU-specific geometry corrections described below.

---

## Standard Geometry

Most VIRUS IFUs use:

```text
IFUcen_HETDEX.txt
```

without any IFU-specific modification to the complete 448-fiber table.

For these IFUs, the only transformation is:

1. select the rows corresponding to the amplifier;
2. retain the two coordinate columns;
3. reverse the 112-row amplifier slice into extracted-spectrum order.

---

## Right-Side Orientation Corrections

A small number of IFUs require the right-side fibers to be reversed within the complete IFU table.

The affected IFUs are:

```text
003
004
005
008
```

For these IFUs, rows:

```python
224:448
```

are reversed before the amplifier-specific slice is selected.

This operation affects the `RL` and `RU` portions of the complete table.

Equivalent archival behavior:

```python
ifucen[224:, :] = ifucen[-1:223:-1, :]
```

This is an IFU-level orientation correction. It is separate from the final reversal applied to every amplifier when aligning positions with extracted-spectrum order.

### Special source file for IFU 004

IFU `004` differs from the other orientation-corrected IFUs because its base positions come from:

```text
IFUcen_HETDEX_reverse_R.txt
```

After loading that alternate table, the same reversal of rows `224:448` is still applied.

Therefore, IFU `004` requires both:

1. use of `IFUcen_HETDEX_reverse_R.txt`;
2. reversal of the right-side rows.

Using the alternate file does not replace the row reversal.

---

## Individual Fiber Coordinate Corrections

Several IFUs contain known pairs of fibers whose spatial coordinates must be exchanged.

Only the two coordinate columns are exchanged. The correction does not require swapping the complete source rows or unrelated metadata columns.

The known corrections are:

| IFU | Source-table rows whose coordinates are exchanged |
|---|---|
| `007` | `37` and `38` |
| `025` | `208` and `213` |
| `030` | `445` and `446` |
| `038` | `302` and `303` |
| `041` | `251` and `252` |

All indices are zero-based indices into the complete 448-row position table.

The archival operation was equivalent to:

```python
a = ifucen[first, 1:3].copy()
b = ifucen[second, 1:3].copy()

ifucen[first, 1:3] = b
ifucen[second, 1:3] = a
```

The correction must be applied before selecting and reversing the amplifier slice.

### Amplifiers affected by the swaps

The source-table ranges imply the following affected amplifiers:

| IFU | Rows | Affected amplifier |
|---|---:|---|
| `007` | `37`, `38` | `LU` |
| `025` | `208`, `213` | `LL` |
| `030` | `445`, `446` | `RU` |
| `038` | `302`, `303` | `RL` |
| `041` | `251`, `252` | `RL` |

The final positions of these fibers within an amplifier-local array will differ from their complete-table indices because every amplifier slice is reversed before being returned.

---

## Complete Archival Behavior

The historical functional implementation was:

```python
def get_ifucenfile(folder, ifuid, amp):
    if ifuid == "004":
        ifucen = np.loadtxt(
            op.join(
                folder,
                "IFUcen_files",
                "IFUcen_HETDEX_reverse_R.txt",
            ),
            usecols=[0, 1, 2, 4],
            skiprows=30,
        )
        ifucen[224:, :] = ifucen[-1:223:-1, :]
    else:
        ifucen = np.loadtxt(
            op.join(
                folder,
                "IFUcen_files",
                "IFUcen_HETDEX.txt",
            ),
            usecols=[0, 1, 2, 4],
            skiprows=30,
        )

        if ifuid in ["003", "005", "008"]:
            ifucen[224:, :] = ifucen[-1:223:-1, :]

        if ifuid == "007":
            a = ifucen[37, 1:3] * 1.0
            b = ifucen[38, 1:3] * 1.0
            ifucen[37, 1:3] = b
            ifucen[38, 1:3] = a

        if ifuid == "025":
            a = ifucen[208, 1:3] * 1.0
            b = ifucen[213, 1:3] * 1.0
            ifucen[208, 1:3] = b
            ifucen[213, 1:3] = a

        if ifuid == "030":
            a = ifucen[445, 1:3] * 1.0
            b = ifucen[446, 1:3] * 1.0
            ifucen[445, 1:3] = b
            ifucen[446, 1:3] = a

        if ifuid == "038":
            a = ifucen[302, 1:3] * 1.0
            b = ifucen[303, 1:3] * 1.0
            ifucen[302, 1:3] = b
            ifucen[303, 1:3] = a

        if ifuid == "041":
            a = ifucen[251, 1:3] * 1.0
            b = ifucen[252, 1:3] * 1.0
            ifucen[251, 1:3] = b
            ifucen[252, 1:3] = a

    if amp == "LL":
        return ifucen[112:224, 1:3][::-1, :]

    if amp == "LU":
        return ifucen[:112, 1:3][::-1, :]

    if amp == "RL":
        return ifucen[224:336, 1:3][::-1, :]

    if amp == "RU":
        return ifucen[336:, 1:3][::-1, :]
```

A modern VIRUSFlow implementation does not need to reproduce this exact code structure, but it should reproduce its scientifically relevant numerical behavior.

---

## Configuration Expectations

The known exceptions should be represented as explicit instrument configuration rather than hidden inside unrelated reduction code.

The configuration must be capable of expressing:

- the default source file;
- an alternate source file for a particular IFU;
- reversal of a range of rows in the complete table;
- exchange of coordinate values between two rows;
- the complete-table slice associated with each amplifier;
- and the reversal required to match extracted-spectrum order.

The choice of YAML, typed Python data, or another repository-standard configuration format is an implementation decision.

The important requirement is that the exceptional IFU geometry remains visible, centralized, and testable.

---

## Ownership Within VIRUSFlow

Fiber positions belong to the VIRUS instrument geometry or instrument configuration layer.

They are not:

- reduction products,
- dynamically measured calibrations,
- algorithm results,
- artifacts,
- QA metrics,
- or analytics products.

Reduction algorithms should obtain fiber positions from a single authoritative instrument-geometry interface.

Algorithms and tasks should not independently read the source text files or reproduce the IFU-specific exceptions.

---

## Validation Expectations

The implementation should verify at least the following invariants:

```text
Complete table shape:       448 × 4
Fibers per amplifier:       112
Returned coordinate shape:  112 × 2
Recognized amplifiers:      LU, LL, RL, RU
```

The implementation should fail clearly if:

- a source file is missing;
- the source file has an unexpected shape;
- an amplifier name is invalid;
- a configured row range is outside the table;
- or a configured coordinate-swap index is invalid.

It should not silently fall back to an approximate or synthetic geometry.

---

## Mutation and Caching

The two source tables are static and may reasonably be cached.

However, IFU-specific corrections must never mutate a cached base table.

For example, applying the right-side reversal for IFU `003` must not change the positions later returned for a standard IFU.

Likewise, the array returned to a caller should not expose mutable cached state.

A corrected working copy or equivalent immutable transformation is required.

---

## Regression Standard

The archival function defines the expected behavior for the existing calibrated files.

Regression tests should compare the modern implementation against the archival behavior for:

```text
001
003
004
005
007
008
025
030
038
041
```

across all four amplifiers:

```text
LU
LL
RL
RU
```

The test set should establish:

- standard IFU behavior;
- right-side orientation corrections;
- the special source file for IFU `004`;
- all five coordinate swaps;
- correct amplifier selection;
- final extracted-spectrum ordering;
- correct output shape;
- and isolation between repeated calls for different IFUs.

---

## Scope

This geometry configuration does not include:

- trace positions on the detector;
- wavelength solutions;
- focal-plane positions of separate IFUs;
- telescope astrometry;
- sky-coordinate transformations;
- differential atmospheric refraction;
- fiber throughput;
- or time-dependent calibration.

This note describes only the mapping between extracted VIRUS fiber spectra and their calibrated positions within an individual IFU.

---

## Summary

The calibrated VIRUS fiber-position behavior consists of four layers:

1. Load the authoritative 448-fiber IFU position table.
2. Apply any known IFU-specific orientation or coordinate correction.
3. Select the 112 rows associated with the requested amplifier.
4. Reverse the amplifier slice to match extracted-spectrum order.

The exceptional IFUs are:

```text
Right-side orientation:
003, 004, 005, 008

Alternate source file:
004

Coordinate exchanges:
007: 37 ↔ 38
025: 208 ↔ 213
030: 445 ↔ 446
038: 302 ↔ 303
041: 251 ↔ 252
```

These rules are part of the established VIRUS instrument geometry and should be preserved as explicit, version-controlled configuration.
