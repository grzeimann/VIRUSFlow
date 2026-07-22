# VIRUSFlow Implementation Plan from the Knowledge Notes

> Status: Proposed full-width vertical-slice plan

This plan converts the knowledge-note collection and target architecture into an
implementation sequence.

The strategy is:

> **Implement the full breadth of the baseline reduction with the simplest
> scientifically accepted method for each domain, while preserving interfaces
> for advanced models.**

This is a full-width, thin-depth vertical slice.

It is preferable to completing one calibration domain perfectly while the rest
of the scientific graph remains hypothetical.

---

# 1. Definition of the First Complete Vertical Slice

The first complete vertical slice should process:

```text
one real VIRUS science Observation
```

including:

- every available IFUSLOT;
- all four amplifiers;
- paired physical CCD operations;
- all exposures in the observation;
- calibration selection;
- detector reduction;
- aperture extraction;
- astrometry;
- sky subtraction;
- relative response;
- uncertainty;
- and observation/dither relationships.

It should produce queryable Products for every stage.

The first slice does not require:

- full forward extraction;
- full forward scattered-light correction;
- global stellar self-calibration;
- flux-conserving cube reconstruction;
- or complete covariance.

Those interfaces should exist, but the baseline methods are sufficient.

---

# 2. Baseline Method Choices

| Domain | First implementation |
|---|---|
| Orientation | Canonical amplifier orientation |
| Overscan | Explicit row/column overscan correction |
| Bias | Robust Master Bias |
| Read noise | Bias residual MAD × 1.4826 |
| Gain | Versioned configured gain |
| Dark | Exposure-normalized smooth dark + defect evidence |
| Pixel mask | Persistent detector-defect mask |
| Trace | Current empirical peak + robust polynomial method |
| Wavelength | Current Hg/Cd line-identification and 2D interpolation |
| Fiber profile | Current empirical profile measurement |
| Spectral PSF | Measurement Product, not required for extraction |
| Scatter | Gap-constrained physical-CCD model |
| Extraction | Fractional five-pixel aperture |
| Variance | Read noise + Poisson + exact aperture weights |
| Fiber normalization | Twilight within-amp + exposure-wide amp normalization |
| Astrometry | Header TAN prior + catalog shift/rotation fit |
| Sky | Native-grid oversampled common sky |
| Sky residuals | Small constrained PCA basis if required |
| Relative response | Baseline response curve |
| Dither | Nominal pattern + astrometric refinement |
| Exposure time | EXPTIME primary; PEXPTIME - 8 parallel |
| Rectification | Linear, explicit non-flux-conserving policy |

---

# 3. Phase 0: Freeze the Vocabulary

## 3.1 Deliverables

```text
artifact_kind_registry.yaml
scope_registry.yaml
units_registry.yaml
coordinate_registry.yaml
relation_registry.yaml
qa_status_registry.yaml
legacy_vocabulary_map.yaml
```

## 3.2 Required decisions

- one canonical name per Product;
- one primary scope per Product;
- unit convention for every array;
- coordinate convention for every geometry;
- and legacy-to-canonical name mapping.

## 3.3 Acceptance criteria

- no task may persist an unregistered Artifact kind;
- no Product may omit units when units are applicable;
- and all legacy HDF5 fields map to canonical names.

---

# 4. Phase 1: Core Contracts and Artifact Service

## 4.1 Implement

```text
Target
AlgoResult
ArtifactRequest
ArtifactRecord
Provenance
Validity
QAFact
QAResult
TaskResult
```

## 4.2 ArtifactService capabilities

- register Artifact;
- persist array/table/metadata payload;
- load Artifact;
- find exact identity;
- select by validity and QA;
- list lineage;
- supersede without mutation;
- and materialize requested format.

## 4.3 Acceptance criteria

A synthetic algorithm can:

1. return AlgoResult;
2. be wrapped by a Task;
3. create an Artifact;
4. reload its payload;
5. query its provenance;
6. and run a QA rule.

---

# 5. Phase 2: Configuration and Hardware Timeline

## 5.1 Load versioned configuration

```text
F-plane
fiber positions
amplifier orientations
physical CCD transforms
controller history
gain values
dead fibers
arc line list
dither pattern
shutter policy
```

## 5.2 Resolve configuration at exposure time

```python
registry.resolve(kind, identity, at)
```

## 5.3 Physical CCD transform

The legacy scattered-light code provides the baseline transform:

```text
Left CCD:
    LL lower
    LU upper and y-reflected

Right CCD:
    RU lower
    RL upper and y-reflected
```

with:

```text
x_CCD = x

lower amplifier:
    y_CCD = y

upper amplifier:
    y_CCD = 2063 - y
```

Implementation must characterize the canonical indexed-array convention at the
seam. Materialized image and trace indices use exactly:

```text
2063 - y
```

The historical `2064 - y` expression is retained only as pre-refactor
characterization evidence and is not an alternative convention. This is no
longer a blocker to architecture or task implementation.

## 5.4 Acceptance criteria

Given any raw frame, the system can determine:

- complete ZipCode;
- hardware epoch;
- canonical orientation;
- physical CCD membership;
- and all applicable configuration versions.

---

# 6. Phase 3: Raw Inventory and Observation Identity

## 6.1 Implement scan

The scanner records:

- raw files and tar members;
- headers;
- date;
- observation;
- exposure;
- IFUSLOT;
- IFUID;
- SPECID;
- AMP;
- CONTROLLER;
- frame kind;
- and checksums.

## 6.2 Classify observing mode

```text
OBJECT == parallel
    → parallel

otherwise
    → primary
```

## 6.3 Effective exposure time

```text
primary:
EXPTIME

parallel:
PEXPTIME - 8
```

Preserve policy version and both raw values.

## 6.4 Build grouping entities

```text
Observation
Exposure
Shot
DitherSet
```

## 6.5 Acceptance criteria

One observation can be listed with:

- all expected raw frames;
- missing/zero-readout status;
- exposure grouping;
- observing mode;
- and effective exposure-time evidence.

---

# 7. Phase 4: Detector Baseline

## 7.1 Orientation and overscan

Implement:

```text
Raw Frame
→ canonical orientation
→ overscan model
→ overscan-corrected image
```

The overscan correction must be completed before scattered-light estimation.

## 7.2 Gain and variance

Convert to electrons where practical.

Create:

```text
detector_variance
=
read_noise_variance
+
nonnegative_poisson_expectation
```

## 7.3 Calibration Products

Implement:

```text
Master Bias
Read Noise
Master Dark
Pixel Mask
Zero Readout Status
```

## 7.4 Acceptance criteria

For every amplifier in a calibration interval:

- calibrated detector Products exist;
- QA facts are stored;
- and raw-to-calibration provenance is queryable.

---

# 8. Phase 5: Calibration Illumination and Geometry

## 8.1 Master illumination Products

Implement:

```text
Master LDLS
Master Hg
Master Cd
Master Arc
Master Twilight
```

## 8.2 Trace

Wrap the current trace method as a pure algorithm.

Publish:

- trace map;
- discrete samples;
- sample columns;
- per-fiber residual scatter;
- and failed/interpolated fiber flags.

## 8.3 Wavelength

Wrap the current wavelength method.

Publish:

- arc identifications;
- native wavelength map;
- per-fiber residuals;
- and surface-model diagnostics.

## 8.4 Fiber profile

Publish the empirical profile grid and capture diagnostics.

## 8.5 Spectral PSF

Implement a measurement-only first version:

- arc cutouts;
- normalized 2D stacks;
- width;
- tilt;
- and residual diagnostics.

It need not feed extraction yet.

## 8.6 Acceptance criteria

One calibration interval supplies all geometry needed to extract a science
exposure.

---

# 9. Phase 6: Fiber Normalization

## 9.1 Within-amplifier component

Implement the current smoothed twilight ratio method.

## 9.2 Amp-to-amp component

Place all amplifiers in one exposure-wide twilight reference frame.

## 9.3 Final Product

```text
fiber_normalization
=
within_amp
×
amp_to_amp
```

## 9.4 Preserve

- raw ratios;
- smooth model;
- masks;
- extrapolated regions;
- and reference illumination state.

## 9.5 Acceptance criteria

All usable fibers in the exposure have one normalization Product with complete
lineage to Master Twilight, trace, wavelength, extraction method, and scatter
policy.

---

# 10. Phase 7: Science Detector Reduction

For every science amplifier:

```text
orientation
→ overscan
→ gain
→ bias
→ dark
→ mask
→ variance
```

Create an immutable:

```text
reduced_science_image
```

Do not subtract independent amplifier scattered-light models at this stage.

## Acceptance criteria

All science amplifiers are reduced or explicitly marked unavailable/degraded.

---

# 11. Phase 8: Physical CCD Scattered Light

## 11.1 Assemble CCD

```text
RU + RL
LU + LL
```

in validated physical coordinates.

## 11.2 Gap-constrained baseline

- derive fiber-group gaps from trace;
- exclude pixels within the core boundary;
- chunk in dispersion;
- robustly estimate gap values;
- fit/interpolate a smooth 2D surface;
- and split the model back to amplifier coordinates.

## 11.3 Preserve diagnostics

```text
gap mask
gap values
fit residual
cross-amplifier continuity
```

## 11.4 Acceptance criteria

Each complete physical CCD has:

- a scatter Product;
- a scatter-subtracted image;
- and QA demonstrating behavior in held-out gap regions.

---

# 12. Phase 9: Aperture Extraction and Uncertainty

## 12.1 Flux

Use the exact fractional five-pixel aperture.

Preserve the current mean-aperture convention initially.

## 12.2 Variance

Use the same fractional weights:

```text
Var(S)
=
Σ w_i² Var(D_i) / W²
```

## 12.3 Preserve

```text
effective aperture weight
valid pixel fraction
aperture capture estimate
```

## 12.4 Acceptance criteria

Every valid fiber has:

- native-grid spectrum;
- native-grid variance;
- wavelength;
- extraction metadata;
- and QA.

---

# 13. Phase 10: Astrometry

## 13.1 Initial model

- read header RA, Dec, PA;
- apply fallback logic;
- load F-plane;
- build TAN projection.

## 13.2 Fit

- reconstruct broadband IFU images;
- detect sources;
- match catalog;
- identify coherent offset cluster;
- solve IHMP shift;
- search rotation;
- rematch and repeat.

## 13.3 Improvement over legacy

Return:

- parameter uncertainties;
- match table;
- residuals;
- and convergence state.

## 13.4 Acceptance criteria

Every exposure has either:

- fitted astrometry;
- or explicit degraded header astrometry.

All fibers receive RA and Dec.

---

# 14. Phase 11: Exposure Illumination and Sky

## 14.1 Build sky-fiber mask

Combine:

- catalog source mask;
- data-driven outlier mask;
- detector/fiber quality mask.

## 14.2 Exposure illumination

For the first slice:

- infer a smooth empirical correction when enough blank fibers exist;
- otherwise use unity or a named inherited prior with DEGRADED status.

The track-position predictive model is a later analytic Product.

## 14.3 Incident sky

Combine normalized native-wavelength samples into an oversampled common sky.

## 14.4 Fiber sky prediction

Interpolate to each native wavelength grid.

Optionally apply a small PCA residual model.

## 14.5 Variance

Publish sky-model variance and an empirical residual scale.

## 14.6 Acceptance criteria

Every valid science fiber has:

- sky prediction;
- sky-subtracted spectrum;
- sky variance;
- and residual QA.

---

# 15. Phase 12: Relative Response

## 15.1 Baseline

Apply the historical baseline relative-response curve under a versioned reference
state.

## 15.2 Preserve separation

Do not combine into one opaque curve:

```text
baseline response
temporal perturbation
track perturbation
transparency
absolute scale
```

For the first slice, unsupported terms may be identity Products.

## 15.3 Acceptance criteria

The output spectrum has:

- declared physical or relative units;
- response lineage;
- and no hidden response terms.

---

# 16. Phase 13: Observation and Dither Products

## 16.1 Dither assignment

Use:

- observation membership;
- exposure sequence;
- primary/parallel mode;
- and nominal dither pattern.

## 16.2 Astrometric refinement

Store measured relative exposure offsets.

## 16.3 Coverage

Build a footprint/coverage Product without forcing spectral coaddition.

## 16.4 Acceptance criteria

A standard observation exposes:

- three independent Exposure Products;
- one DitherSet relationship;
- measured offsets;
- and a coverage map.

Parallel exposures remain sparse and undithered.

---

# 17. Phase 14: Query and Observation Sets

Implement query-defined collections.

Examples:

```text
all spectra for M33
all exposures with seeing < 2 arcsec
all dither sets with PASS astrometry
all fibers covering one sky polygon
```

Saved collection rules become ObservationSet Products.

No native coaddition is required.

---

# 18. Phase 15: Analytics and Model Learning

Implement studies after the vertical slice is producing evidence.

Priority studies:

1. aperture-capture stability;
2. dither repeatability;
3. PEXPTIME minus-eight validation;
4. astrometric residuals by IFUSLOT;
5. sky residual floor;
6. variance scale factors;
7. temperature trace/wavelength drift;
8. scattered-light kernel comparison;
9. LDLS versus twilight profile;
10. relative-response evolution.

These studies convert assumptions into measured models.

---

# 19. Advanced Follow-On Implementations

After the baseline slice:

```text
Forward scatter refinement
Profile extraction
Full forward extraction
Flux-conserving resampling
Fiber-specific LSF sky prediction
Global track illumination model
Temporal response model
Global secondary-star calibration
Absolute flux calibration
Cube reconstruction
```

Each advanced implementation should be evaluated against the baseline Product.

---

# 20. Tests

## 20.1 Contract tests

- Artifact-kind registration;
- units;
- Target identity;
- AlgoResult shape;
- provenance completeness;
- and selection determinism.

## 20.2 Characterization tests

Capture current outputs of legacy functions before refactor.

## 20.3 Unit tests

Test every algorithm with synthetic arrays.

## 20.4 Integration tests

Process:

- one amplifier;
- one physical CCD;
- one IFU;
- one exposure;
- one three-dither observation.

## 20.5 Regression tests

Compare against trusted Remedy/quick-reduction Products.

## 20.6 Science acceptance tests

Evaluate:

- blank-sky residuals;
- standard-star shape;
- astrometric residuals;
- repeated source flux;
- and dither coverage.

---

# 21. Implementation Workstreams

## Workstream A: Knowledge and contracts

- vocabulary;
- scopes;
- Product registry;
- assumptions;
- QA policy.

## Workstream B: Data and persistence

- raw index;
- ArtifactService;
- serializers;
- database;
- storage.

## Workstream C: Detector and calibration

- orientation;
- overscan;
- bias;
- dark;
- masks;
- trace;
- wavelength;
- normalization.

## Workstream D: Science exposure

- CCD scatter;
- extraction;
- astrometry;
- sky;
- response.

## Workstream E: Grouping and query

- exposure identity;
- dither;
- ObservationSet;
- query.

## Workstream F: Analytics

- studies;
- models;
- reports;
- validation.

These workstreams can proceed in parallel after Phase 1 contracts are stable.

---

# 22. Blocking Missing Information

The following must be supplied or confirmed before the corresponding production
step can be considered authoritative.

## 22.1 Physical CCD seam convention

The amplifier ordering and reflection are now inferred from legacy code:

```text
LL below LU
RU below RL
x unchanged
upper amplifier reflected in y
```

The remaining confirmation is the exact pixel-center and array-index convention
at the seam.

## 22.2 Overscan specification

- overscan rows/columns;
- orientation behavior;
- fitting method;
- and handling of overscan-related additive structure.

## 22.3 Canonical unit conventions

Especially:

- aperture output;
- per-pixel vs. per-Å spectra;
- variance units;
- and response-normalization reference.

## 22.4 Product validity policy

Rules for:

- time windows;
- interpolation;
- hardware changes;
- inherited priors;
- and fallback.

## 22.5 Artifact-kind names

One final registry must replace legacy aliases.

---

# 23. Missing but Inferable or Non-Blocking Information

The first implementation can proceed with conservative defaults for:

```text
profile extraction
forward scattered light
full covariance
LSF sky forward modeling
track illumination
temporal response
absolute response
cube reconstruction
```

Interfaces and Product kinds should be defined now.

The numerical models can be learned later from repository evidence.

---

# 24. Additional Knowledge Notes Recommended

Before or during implementation, add concise notes for:

```text
Calibration Validity, Selection, and Interpolation
QA Evidence, Status, and Product Usability
Absolute Flux Calibration and Atmospheric Extinction
Spatial Reconstruction, DAR, and Cube Products
Source Detection, Association, and Multi-Fiber Extraction
Artifact Provenance, Confidence, and Uncertainty Ontology
```

These are cross-cutting or downstream additions, not evidence that the existing
instrument specification is incomplete.

---

# 25. Definition of Vertical-Slice Success

The vertical slice succeeds when one real observation can be processed from raw
frames into queryable calibrated spectra with:

- immutable Products;
- complete provenance;
- explicit configuration versions;
- QA at every stage;
- per-exposure physical state;
- physical-CCD scatter correction;
- aperture spectra and variance;
- astrometry;
- sky subtraction;
- relative response;
- and dither relationships.

The system must also answer:

```text
Why was this calibration selected?
Which raw frames contributed?
Which assumptions were active?
What QA evidence exists?
What would need to be recomputed if one Product changes?
```

At that point VIRUSFlow is no longer merely an architectural proposal. It is a
working knowledge-producing reduction system.
