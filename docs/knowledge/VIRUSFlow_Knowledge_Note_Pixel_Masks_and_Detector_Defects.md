# VIRUSFlow Scientific Knowledge Specification

# Working Note: Pixel Masks and Detector Defects

> Status: Initial implementation specification

This note captures the current operational understanding of persistent detector
defects in VIRUS and how they should be represented, measured, combined, and
propagated within VIRUSFlow.

The central idea is that a pixel mask is not merely a nightly calibration output.
It is a cumulative model of detector health built from multiple kinds of
evidence over time.

---

## Relevant Architectural Entities

### Products

- Master Dark Product
- Master Flat Product
- Master Science Product
- Extracted Master Science Product
- Hot-Pixel Mask
- Flat-Response Defect Mask
- Science-Residual Defect Mask
- Pixel Mask Product
- Cosmic-Ray Mask
- Detector Health Summary

### Model Components

- Persistent Detector Defect Component
- Hot-Pixel Component
- Column Defect Component
- Charge-Transfer Defect Component
- Quantum-Efficiency Depression Component
- Transient Cosmic-Ray Component

---

## Scientific Interpretation

A pixel mask records locations where detector measurements should not be trusted
as ordinary samples.

The mask may represent defects in:

- dark-current behavior,
- charge collection,
- charge transfer,
- quantum efficiency,
- amplifier readout,
- or transient contamination.

These effects do not all have the same origin or lifetime.

VIRUSFlow should therefore preserve defect category and evidence source rather
than collapsing every masked sample into a single unexplained binary state.

---

## Common VIRUS Detector Defects

## Hot Pixels

Hot pixels have elevated dark current and are most naturally identified in
Master Dark products.

They may be:

- stable,
- intermittent,
- temperature dependent,
- or progressively worsening.

The preferred treatment is masking rather than attempting to recover the pixel
through dark subtraction alone.

---

## Charge Traps

Charge traps interfere with charge transport or collection.

Their appearance may depend on:

- signal level,
- charge-transfer direction,
- local detector history,
- and exposure conditions.

They may produce localized depressions, vertical trails, or irregular structures that
are difficult to identify using a single uniform threshold.

---

## Hot Columns

Hot columns contain many vertically aligned pixels with elevated response or
dark signal.

They may be identified when a large number of pixels in one detector column are
individually flagged.

The current flat-mask algorithm promotes a column to a fully masked column when
more than 300 pixels in that column have already been identified as outliers.

---

## Dead Columns

Dead columns have strongly reduced or absent response.

They are most apparent in smooth, illuminated frames such as Master Flats.

As with hot columns, a column-level representation is usually more scientifically
honest than attempting to treat each affected pixel as an independent defect.

---

## Charge-Transfer Issues

Charge-transfer problems may appear as column-aligned structure or abnormal
response associated with detector readout.

For VIRUS amplifiers, the central two columns are a known location where such
issues may occur.

The defect is not always present, but when it occurs it is often associated with
these central columns.

VIRUSFlow should treat this as an empirical prior and diagnostic target, not as
a rule that automatically masks the central columns for every amplifier.

---

## Pox

"Pox" are detector-surface defects that produce localized depressions in
quantum efficiency.

They often appear as irregular splatter-like structures, particularly toward
the corners of detector chips.

Their morphology is unlike isolated hot pixels or simple column defects.

They are most naturally detected in smooth illumination frames by comparing the
observed response to a locally smoothed synthetic response.

---

## Cosmic Rays

Cosmic rays are transient contamination, not persistent detector defects.

They require a distinct mask category and should generally not be promoted into
the persistent Pixel Mask unless repeated evidence demonstrates a stable
underlying defect at the same location.

Cosmic-ray masks are associated with individual exposures or combined products,
whereas persistent detector masks describe the detector state over a validity
interval.

---

## Evidence Sources

Persistent detector defects are identified using several complementary Products.

## Master Dark

Useful for detecting:

- hot pixels,
- hot columns,
- unstable dark-current structure,
- some electronic artifacts.

## Master Flat

Useful for detecting:

- pocks,
- dead pixels,
- dead columns,
- response depressions,
- charge traps,
- charge-transfer features,
- other pixel-to-pixel response defects.

The laser-driven light source provides a smooth continuum, and the fiber-profile
structure varies smoothly enough that a local synthetic model can expose
high-frequency detector defects.

## Processed Master Science After Extraction

A processed form of the Master Science product after extraction can expose
defects that become apparent through:

- extraction residuals,
- repeated fiber-profile failures,
- persistent spectral artifacts,
- charge-transfer effects,
- or detector features insufficiently visible in darks and flats alone.

This evidence should supplement, rather than replace, clean calibration
illumination.

---

## Current Flat-Response Detection Algorithm

The historical flat-based pixel-mask algorithm operates row by row.

For each detector row:

1. Apply a median filter with a kernel width of 17 pixels.
2. Treat the filtered row as a local smooth synthetic illumination model.
3. Divide or compare the real row with the smooth model.
4. Flag deviations greater than 10%.
5. Ignore locations where the local filtered signal is below 200 ADU.
6. Never mask the first or last eight detector columns in this stage.
7. Promote any column with more than 300 flagged pixels to a fully masked
   column.

Representative implementation:

```python
def detect_flat_response_outliers(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image, dtype=float)
    ny, nx = img.shape
    mask = np.zeros((ny, nx), dtype=bool)

    for i in np.arange(ny):
        row_medf = medfilt(img[i], 17)

        denom = np.where(row_medf == 0, np.inf, row_medf)
        dev = np.abs((img[i] - row_medf) / denom)

        bad = dev > 0.1
        bad[row_medf < 200] = False
        mask[i] = bad

    mask[:, :8] = False
    mask[:, -8:] = False

    col_bad = np.sum(mask, axis=0) > 300
    if np.any(col_bad):
        mask[:, col_bad] = True

    return mask.astype(np.uint8)
```

---

## Interpretation of the Flat Algorithm

The median-filtered row is not intended to model the detector physically.

It is a local synthetic representation of the expected smooth illumination and
fiber-profile behavior.

The residual ratio highlights structures that vary more sharply than the
illumination profile.

This makes the method effective for:

- pocks,
- isolated response depressions,
- narrow column defects,
- some charge traps,
- local flat-response anomalies.

However, the thresholds and kernel size are empirical and should remain
configurable.

---

## Edge Exclusion

The current algorithm excludes the first and last eight columns from masking.

This protects detector edges from false classifications caused by:

- incomplete filter support,
- strong edge gradients,
- reduced illumination,
- or boundary artifacts in the synthetic smooth model.

This exclusion should be documented explicitly because it also creates a blind
region in which defects will not be found by this algorithm.

Other evidence sources may still identify edge defects.

---

## Low-Signal Exclusion

Locations where the median-filtered flat signal is below 200 ADU are excluded.

These regions are likely to correspond to inter-fiber gaps or other
poorly illuminated detector locations where relative deviations become unstable
or scientifically irrelevant to extraction.

The threshold is operational rather than universal.

VIRUSFlow should preserve the threshold used and evaluate whether it remains
appropriate across amplifiers and illumination levels.

---

## Column Promotion

A detector column is fully masked when more than 300 pixels in that column are
already classified as outliers.

This encodes the idea that a highly populated vertical defect is better
represented as a column defect than as hundreds of independent pixel defects.

The threshold should remain configurable and should be interpreted relative to
the amplifier image height.

---

## Construction of the Pixel Mask

The initial Pixel Mask should combine at least:

- hot-pixel evidence from Master Darks,
- flat-response defects from Master Flats,
- persistent defects found in processed Master Science products.

Conceptually:

```text
Pixel Mask =
    Hot-Pixel Mask
    OR Flat-Response Defect Mask
    OR Persistent Science-Residual Mask
```

Cosmic rays should remain separate because they are exposure-specific.

The combined mask should preserve per-category bit flags or labels even if
downstream algorithms also consume a simplified boolean mask.

---

## External Pixel-Mask Library

A library of masks from another pipeline is available.

Those masks were created through broadly similar empirical procedures rather
than through a fundamentally independent laboratory characterization.

They may still be useful as:

- historical evidence,
- prior masks,
- validation data,
- detector-state comparisons,
- or recovery support when current calibration data are sparse.

They should not automatically be treated as more authoritative than masks
constructed within VIRUSFlow.

VIRUSFlow could compare the external library with its own measurements and
preserve provenance when external masks contribute.

---

## Temporal Stability

Most persistent detector defects are expected to be stable in existence over
long periods.

This makes repeated nightly or weekly measurements valuable.

Rather than rebuilding an unrelated mask from scratch each night, VIRUSFlow
should combine evidence into a coherent time-aware detector model.

The model should distinguish:

- persistent defects,
- newly appearing defects,
- intermittent defects,
- recovered or no-longer-detected defects,
- transient contamination.

A defect should carry an evidence history and validity interval.

---

## Evidence Accumulation

Repeated measurements can improve both completeness and precision.

For each detector location or defect region, VIRUSFlow should retain:

- first detection,
- most recent detection,
- number of supporting Products,
- evidence types,
- detection strength,
- persistence fraction,
- current status,
- category,
- and confidence.

A single strong detection may be sufficient for some severe defects.

Marginal detections should require repeated evidence.

---

## Mask Semantics

A single binary mask is useful operationally but insufficient scientifically.

A richer Pixel Mask Product should distinguish categories such as:

```text
HOT_PIXEL
HOT_COLUMN
DEAD_PIXEL
DEAD_COLUMN
CHARGE_TRAP
CHARGE_TRANSFER
POCK
COSMIC_RAY
UNKNOWN_DEFECT
```

The implementation may use bit values so multiple categories can overlap.

The Product should also distinguish:

- persistent mask,
- exposure-specific mask,
- advisory / suspect mask,
- invalid / mandatory mask.

---

## Downstream Handling

Masked pixels should not contribute normally to:

- master calibration combination,
- trace fitting,
- wavelength fitting,
- extraction,
- sky estimation,
- fiber normalization,
- QA statistics.

Some algorithms may interpolate across masked pixels for continuity or fitting,
but interpolation must not erase the mask provenance.

The original masked state should remain available in downstream Products.

---

## Algorithmic Challenges

Detector defects are visually obvious in combined frames but can be difficult to
identify completely and precisely.

Challenges include:

- smooth illumination gradients,
- fiber-profile structure,
- low-signal inter-fiber regions,
- edge effects,
- blended defect morphologies,
- unstable pixels,
- varying flat brightness,
- cosmic rays in calibration stacks,
- incomplete calibration sets,
- and ambiguous boundaries of pocks.

VIRUSFlow should support iterative refinement rather than assuming one pass
produces a perfect mask.

---

## Required Metadata

Pixel Mask Products should preserve:

- amplifier / ZipCode,
- controller identity,
- detector identity,
- observation dates,
- source Product references,
- source frame types,
- gain and base-reduction versions,
- flat smoothing method,
- median-filter width,
- deviation threshold,
- low-signal threshold,
- edge exclusion width,
- column-promotion threshold,
- dark hot-pixel criteria,
- science-residual criteria,
- external-library contribution,
- algorithm and configuration versions,
- validity interval,
- category counts,
- total masked fraction.

---

## Required Analytics

VIRUSFlow should characterize:

- masked fraction by amplifier,
- defect counts by category,
- new defect appearance rate,
- defect persistence and intermittency,
- growth of hot-pixel and bad-column populations,
- pock morphology and stability,
- frequency of central-column charge-transfer issues,
- overlap between dark-, flat-, and science-derived masks,
- disagreement with external mask libraries,
- false-positive and false-negative rates,
- impact of masks on extraction and calibration residuals,
- and detector-health changes following hardware interventions.

---

## Open Questions

- Which defect categories can be identified reliably from each Product type?
- What is the optimal median-filter scale for flat response?
- Should the 10% deviation threshold vary with signal level?
- Is 200 ADU the correct low-signal threshold for every amplifier?
- Should edge exclusion remain fixed at eight columns?
- Should column promotion depend on image height or fractional occupancy?
- How should irregular pock boundaries be represented?
- How many repeat detections are required before a defect becomes persistent?
- Can apparent recovery be trusted, or should some defect categories remain
  permanently masked?
- How should intermittent pixels affect variance models?
- Can extracted science residuals identify defects without imprinting
  astrophysical features into the mask?
- How should external library masks be reconciled with current VIRUSFlow
  evidence?

---

## Initial Implementation Decisions

Until evidence supports a richer model:

- Construct hot-pixel masks from Master Dark Products.
- Construct flat-response masks from smooth-illumination residuals.
- Use a row-wise median filter with an initial width of 17 pixels.
- Flag relative deviations greater than 10%.
- Ignore locations with filtered signal below 200 ADU.
- Exclude the first and last eight columns in the flat detector.
- Promote columns with more than 300 flagged pixels to full-column defects.
- Combine dark, flat, and persistent science-derived evidence.
- Keep cosmic-ray masks separate from persistent detector masks.
- Preserve defect categories through bit flags or equivalent labels.
- Combine repeated nightly or weekly measurements into a time-aware detector
  model.
- Treat external mask libraries as supporting evidence, not unquestioned truth.

---

## Repository Goals

VIRUSFlow should:

- build a coherent lifetime model of every detector's defects,
- distinguish persistent detector damage from transient contamination,
- classify defects by physical or phenomenological category,
- accumulate evidence across darks, flats, and processed science Products,
- optimize flat-based detection thresholds and smoothing scales,
- identify central-column charge-transfer behavior systematically,
- map and track pocks as stable quantum-efficiency depressions,
- measure defect birth, persistence, intermittency, and recovery,
- reconcile VIRUSFlow masks with external historical mask libraries,
- quantify the impact of each defect category on extraction and calibration,
- preserve mask provenance through every downstream Product,
- and ensure that pixel masks improve with accumulated evidence rather than
  being independently rediscovered each night.
