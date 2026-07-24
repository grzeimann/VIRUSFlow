# Separate Master-Science Construction, Extraction, and Spectral Masking

## Context

The current VIRUSFlow master-science algorithm produces:

- a robustly combined detector image, `master_sci`;
- and an array called `fiber_wavelength_mask_support`.

The support array is calculated from the median absolute deviation among the input detector images. It is a detector-coordinate measurement of frame-to-frame variation, not the fiber-by-wavelength mask produced by the archival reduction.

The archival spectral mask was created only after the master-science image had been extracted into fiber spectra using the trace solution. Those spectra were then interpreted using the wavelength solution:

```python
good_solutions = (
    np.isfinite(curr_wave).sum(axis=1)
    > 0.8 * curr_wave.shape[1]
)

sci_interp, sci_model, sci_image, _ = (
    mask_utils.build_model_spectra(
        _spec,
        curr_wave,
        good_solutions,
    )
)

msci_model_spec = sci_interp(curr_wave)

_maskspec = mask_utils.make_spectral_mask(
    _spec,
    msci_model_spec,
).astype("float32")
```

In the older code, `base_spectra` was simply assigned from `_spec`. There is no separate reference-spectra product or additional scientific distinction to preserve there.

## Architectural Decision

The master-science image, its extracted spectra, and the fiber-by-wavelength mask should be separate products with explicit dependencies.

Conceptually:

```text
eligible science detector frames
        ↓
master-science construction
        ↓
master_sci detector image
        │
        ├── trace solution
        ↓
master-science extraction
        ↓
extracted master-science spectra
        │
        ├── wavelength solution
        ↓
spectral-model and mask construction
        ↓
fiber-by-wavelength spectral mask
```

### Master-science image

The master-science product should be independently constructible and inspectable.

Its responsibility is to robustly combine the eligible, base-reduced science detector images. It should not require trace extraction, a wavelength solution, or spectral-mask construction.

Remove `fiber_wavelength_mask_support` from the master-science result, along with metadata that describes that array as downstream mask evidence.

There is no current need to retain that detector-stack MAD array under another name.

### Extracted master-science spectra

The extracted master-science spectra should be a separate, persisted product.

They are independently useful beyond spectral-mask construction, including lower-count fiber-normalization checks and other fiber-level diagnostics.

This product is derived from:

- the master-science detector image;
- the trace solution;
- and the canonical VIRUS extraction behavior and configuration.

Its coordinate system is fiber by detector spectral sample.

### Fiber-by-wavelength spectral mask

The spectral mask should be a separate downstream product.

It is derived from:

- the extracted master-science spectra;
- the wavelength solution;
- and the spectral-model and masking logic represented by the archival `build_model_spectra` and `make_spectral_mask` behavior.

Its coordinate system is fiber by wavelength or spectral sample, matching the extracted spectra.

The mask should not be approximated by detector-coordinate scatter among the original science frames.

## Product and Mask Vocabulary

Keep the following concepts distinct:

```text
master_sci
    A combined detector-coordinate science image.

extracted master-science spectra
    The master-science image sampled along the fiber traces.

fiber-by-wavelength spectral mask
    A mask derived by comparing the extracted spectra with a
    wavelength-space spectral model.

detector pixel mask
    A detector-coordinate mask identifying bad detector pixels.
```

A detector pixel mask and a fiber-by-wavelength spectral mask are different products, even when both ultimately help reject unreliable data.

Names should make their coordinate systems and meanings clear.

## Implementation Scope

Inspect the repository and determine the best implementation using the existing VIRUSFlow architecture, task graph, artifact contracts, extraction code, configuration system, and provenance model.

Codex should decide:

- the appropriate task and artifact names;
- whether existing extraction components can be reused directly;
- whether the relevant `mask_utils` functions should be retained, adapted, moved, or rewritten;
- the most appropriate algorithm boundaries;
- how the dependencies should appear in planning and execution graphs;
- and the appropriate tests and documentation.

The implementation should follow the established VIRUSFlow separation in which algorithms perform numerical work and tasks coordinate dependencies and artifact production.

Avoid unrelated architectural refactoring.

## Expected Outcome

After the change, VIRUSFlow should support three independently meaningful stages:

1. construction and inspection of the master-science detector image;
2. extraction and inspection of the master-science fiber spectra;
3. construction of the fiber-by-wavelength spectral mask once wavelength information is available.

A complete calibration workflow may schedule all three stages, but the master-science image must remain usable even when its downstream extraction or mask dependencies are unavailable.

## Completion Report

After implementing the change, report:

1. the previous behavior;
2. the architecture selected;
3. the tasks, algorithms, and artifacts added or changed;
4. how the current support array was removed;
5. how extracted master-science spectra are represented and persisted;
6. how the spectral mask is constructed and connected to its dependencies;
7. any changes made to `mask_utils`;
8. the tests and documentation added or updated;
9. the test results;
10. and any compatibility or migration implications.
