# VIRUSFlow Knowledge-System Coherence Map

> Status: First authoritative synthesis of the VIRUSFlow scientific knowledge notes

This document compresses the individual VIRUSFlow knowledge notes into a
coherent scientific vocabulary, scope model, dependency map, and set of
cross-cutting rules.

It does not replace the detailed notes.

The detailed notes remain the scientific source of truth for each domain. This
document defines how those domains fit together.

---

# 1. Purpose

The current knowledge-note collection describes:

- detector electronics;
- detector geometry;
- calibration illumination;
- fiber geometry and profiles;
- wavelength geometry;
- extraction;
- scattered light;
- uncertainty;
- astrometry;
- sky subtraction;
- spectrophotometric response;
- and observation grouping.

The implementation requires a compressed representation that answers:

1. What are the canonical entities?
2. What Products exist?
3. At what scope is each Product valid?
4. Which Products depend on which others?
5. Which assumptions are common across notes?
6. Which decisions are baseline production policy?
7. Which alternatives remain experimental or analytic?
8. Which unresolved questions block implementation?

---

# 2. Authoritative Knowledge Notes

The current collection is:

```text
VIRUSFlow_Knowledge_Note_Amplifier_Orientation.md
VIRUSFlow_Knowledge_Note_Astrometry_and_Fiber_Sky_Coordinates.md
VIRUSFlow_Knowledge_Note_Bias_Stability.md
VIRUSFlow_Knowledge_Note_Calibration_Illumination_and_Fiber_Normalization.md
VIRUSFlow_Knowledge_Note_CCD_Scattered_Light_Correction.md
VIRUSFlow_Knowledge_Note_Controller_Hardware_Identity.md
VIRUSFlow_Knowledge_Note_Crosstalk_and_Scattered_Light.md
VIRUSFlow_Knowledge_Note_Dark_Current.md
VIRUSFlow_Knowledge_Note_Exposure_Observation_Shot_and_Dither_Identity.md
VIRUSFlow_Knowledge_Note_Fiber_Profiles.md
VIRUSFlow_Knowledge_Note_Fiber_Trace_Geometry.md
VIRUSFlow_Knowledge_Note_Gain.md
VIRUSFlow_Knowledge_Note_Pixel_Masks_and_Detector_Defects.md
VIRUSFlow_Knowledge_Note_Read_Noise.md
VIRUSFlow_Knowledge_Note_Relative_Throughput_and_Spectrophotometric_Response.md
VIRUSFlow_Knowledge_Note_Sky_Modeling_and_Subtraction.md
VIRUSFlow_Knowledge_Note_Spectral_Extraction.md
VIRUSFlow_Knowledge_Note_Spectral_PSF_LSF_and_Resolution.md
VIRUSFlow_Knowledge_Note_Temperature_Drift.md
VIRUSFlow_Knowledge_Note_Variance_and_Uncertainty_Propagation.md
VIRUSFlow_Knowledge_Note_Wavelength_Calibration.md
VIRUSFlow_Knowledge_Note_Zero_Readouts.md
```

---

# 3. Foundational Knowledge-System Rules

## 3.1 Physical object before algorithm

Every Product must represent a named physical or inferential object.

The algorithm is one method for estimating that object.

```text
Physical Quantity
    ≠
Current Implementation
```

Examples:

```text
Fiber Trace Geometry
    ≠
Degree-4 polynomial fit

Relative System Response
    ≠
One historical response curve

Scattered-Light Field
    ≠
One power-law implementation
```

## 3.2 Configuration, measurement, and inference are distinct

VIRUSFlow should preserve three kinds of knowledge:

```text
Configuration Knowledge
Measured Knowledge
Inferred Knowledge
```

Examples:

| Knowledge | Type |
|---|---|
| F-plane IFUSLOT offsets | Configuration |
| Raw bias frame | Measurement |
| Master Bias | Inference |
| Dead-fiber list | Configuration or persistent inferred configuration |
| Exposure astrometric shift | Inference |
| Mirror-cleaning event | Measurement/configuration event |
| Track-dependent illumination model | Inference |

## 3.3 Exposure is atomic

An Exposure is the smallest complete observing-state measurement.

Observation, Shot, DitherSet, and ObservationSet are grouping entities.

The system must not erase per-exposure:

- sky;
- seeing;
- transparency;
- illumination;
- astrometry;
- exposure time;
- and detector state.

## 3.4 The complete ZipCode is conservative provenance

The five-part ZipCode is:

```text
IFUSLOT
IFUID
SPECID
AMP
CONTROLLER
```

The complete ZipCode is the conservative lineage identity for measured
amplifier Products.

Narrower reusable model identities are permitted when physically justified.

Examples:

```text
Trace configuration:
IFUSLOT + IFUID + SPECID + AMP

Physical CCD scatter computation:
Exposure + SPECID + CCD_ID

Exposure sky:
Exposure

Astrometry:
Exposure
```

## 3.5 Products are immutable evidence

A Product should not be overwritten because a better model becomes available.

A newer Product:

- supersedes;
- refines;
- or derives from

an older Product.

Both remain traceable.

## 3.6 Assumptions must be named and testable

Examples:

```text
Twilight illumination is uniform across the focal plane.
A five-pixel aperture capture fraction is stable to about 1%.
One incident sky spectrum describes the exposure.
The gap-constrained scatter surface is smooth.
The baseline relative-response curve is broadly stable.
```

Each assumption should have:

- an identifier;
- a test;
- evidence;
- and a current confidence state.

## 3.7 Empirical cleanup terms remain visible

The following must not be hidden inside unrelated arrays:

- empirical variance scale factors;
- PCA sky-residual corrections;
- aperture-capture corrections;
- track-response perturbations;
- forward scatter refinements;
- and manual configuration corrections.

They are separate Products or explicit model terms.

---

# 4. Canonical Entity Vocabulary

## 4.1 Hardware and configuration

```text
Instrument
FocalPlane
IHMP
IFUSLOT
IFUID
SPECID
PhysicalCCD
AMP
CONTROLLER
Fiber
DetectorPixel
MirrorSegment
Shutter
Guider
```

## 4.2 Configuration versions

```text
FPlaneVersion
FiberMapVersion
AmplifierOrientationVersion
CCDTransformVersion
ControllerAssignmentVersion
GainConfigurationVersion
DeadFiberConfigurationVersion
ArcLineListVersion
DitherPatternVersion
ShutterPolicyVersion
```

## 4.3 Observing entities

```text
Program
Target
Observation
Shot
DitherSet
Exposure
ObservationSet
TrackState
EnvironmentalState
MirrorState
```

## 4.4 Evidence entities

```text
RawFrame
HeaderSnapshot
GuiderMeasurement
MirrorReflectivityMeasurement
MirrorCleaningEvent
ExternalCatalogSnapshot
StandardStarReference
```

## 4.5 Processing entities

```text
Target
Task
TaskGraph
Algorithm
AlgoResult
ArtifactRequest
Artifact
QAResult
Study
Model
```

---

# 5. Canonical Scope Vocabulary

Every Product must declare one primary scope.

```text
PIXEL
FIBER
AMPLIFIER
PHYSICAL_CCD
SPECTROGRAPH
IFU
EXPOSURE
DITHER_SET
OBSERVATION
OBSERVATION_SET
INSTRUMENT_EPOCH
INSTRUMENT_LIFETIME
```

Scope means:

> The smallest physical or scientific domain over which the Product is one
> coherent estimate.

Examples:

| Product | Scope |
|---|---|
| Pixel defect evidence | Pixel |
| Extracted spectrum | Fiber + Exposure |
| Master Bias | Amplifier lineage + validity interval |
| Read noise | Amplifier lineage + interval |
| Trace map | Amplifier configuration + interval |
| CCD scattered-light image | Physical CCD + Exposure |
| Incident sky spectrum | Exposure |
| Fiber sky prediction | Fiber + Exposure |
| Astrometric solution | Exposure |
| Dither coverage map | DitherSet |
| Baseline response curve | Instrument epoch |
| Track-response model | Instrument model domain |

---

# 6. Canonical Product Families

## 6.1 Raw and configuration Products

```text
raw_frame
header_snapshot
hardware_assignment
amplifier_orientation
physical_ccd_transform
fplane_geometry
fiber_map
dead_fiber_configuration
dither_pattern
shutter_exposure_policy
arc_line_list
```

## 6.2 Detector Products

```text
oriented_detector_image
overscan_model
overscan_corrected_image
master_bias
read_noise
gain
master_dark
dark_rate
pixel_mask
detector_variance
zero_readout_status
```

## 6.3 Calibration-illumination Products

```text
master_ldls
master_hg
master_cd
master_arc
master_twilight
```

## 6.4 Geometry and response Products

```text
trace_map
trace_samples
wavelength_map
arc_identification
fiber_profile
spectral_psf_2d
line_spread_function
resolution_map
```

## 6.5 Normalization and throughput Products

```text
within_amp_fiber_normalization
amp_to_amp_normalization
fiber_normalization
exposure_illumination_correction
baseline_relative_response
temporal_response_perturbation
track_response_perturbation
exposure_transparency
absolute_response_scale
final_exposure_response
```

## 6.6 Science detector and extraction Products

```text
reduced_science_image
gap_scatter_model
forward_scatter_refinement
ccd_scattered_light_model
scatter_subtracted_image
aperture_extracted_spectrum
profile_extracted_spectrum
forward_extracted_spectrum
extracted_variance
aperture_capture_fraction
```

## 6.7 Astrometry Products

```text
initial_astrometry
source_detection_catalog
catalog_match_table
final_astrometry
fiber_sky_coordinates
```

## 6.8 Sky Products

```text
sky_fiber_mask
incident_sky_spectrum
fiber_sky_prediction
pca_sky_residual_correction
sky_subtracted_spectrum
sky_model_variance
```

## 6.9 Grouping and coverage Products

```text
exposure_mode_classification
effective_exposure_time
dither_assignment
dither_registration
dither_coverage_map
observation_membership
observation_set_membership
```

## 6.10 QA and empirical-calibration Products

```text
qa_result
empirical_noise_scale
residual_distribution
model_comparison
human_review
```

---

# 7. Shared Product Contract

Every Artifact should carry the following contract.

## 7.1 Identity

```text
artifact_id
kind
role
scope
target_identity
```

## 7.2 Lineage

```text
complete_zipcodes
hardware_epoch
configuration_versions
source_artifact_ids
raw_frame_ids
```

## 7.3 Validity

```text
valid_from
valid_to
selection_domain
inheritance_policy
extrapolation_policy
```

## 7.4 Method

```text
algorithm
algorithm_version
parameters
software_version
execution_id
```

## 7.5 Scientific meaning

```text
units
coordinate_system
normalization_convention
reference_state
assumptions
```

## 7.6 Quality

```text
qa_status
qa_result_ids
confidence
messages
known_limitations
```

## 7.7 Payload

```text
payload_type
storage_uri
shape
dtype
summary_statistics
```

---

# 8. Dependency Graph

The baseline dependency structure is:

```text
Raw Frames
    ↓
Orientation + Overscan + Gain
    ↓
Detector Images + Detector Variance
```

Calibration branches:

```text
Bias Frames
    → Master Bias
    → Read Noise
```

```text
Dark Frames
    + Master Bias
    → Dark Rate
    → Hot-Pixel Evidence
```

```text
LDLS Frames
    + Detector Corrections
    → Master LDLS
    → Trace
    → Fiber Profile
    → Detector Defect Evidence
```

```text
Hg Frames + Cd Frames
    + Trace
    → Master Hg + Master Cd
    → Master Arc
    → Wavelength
    → 2D Spectral PSF
    → LSF + Resolution
```

```text
Twilight Frames
    + Trace
    + Wavelength
    + Extraction
    + Scatter Treatment
    → Master Twilight
    → Within-Amp Normalization
    → Exposure-Wide Amp Normalization
    → Fiber Normalization
```

Science branch:

```text
Science Raw Frames
    + Detector Products
    → Reduced Science Images
```

```text
Paired Reduced Amplifiers
    + Trace
    + Physical CCD Transform
    → Gap Scatter Model
    → Preliminary Scatter-Subtracted Images
```

```text
Scatter-Subtracted Images
    + Trace
    + Variance
    → Aperture Spectra
    → Preliminary Science Spectra
```

```text
Preliminary Spectra
    + Astrometry
    + Catalog
    → Source Masks
```

```text
Preliminary Spectra
    + Fiber Normalization
    + Exposure Illumination
    + Source Masks
    + Wavelength
    → Incident Sky
    → Fiber Sky Predictions
    → Sky-Subtracted Spectra
```

```text
Primary/Secondary Standards
    + Sky-Subtracted Spectra
    + Astrometry
    + Track/Mirror State
    → Relative Response Model
```

Optional refinement:

```text
Aperture Spectra
    → Forward Scatter Refinement
    → Improved Images
    → Profile/Forward Extraction
```

Grouping branch:

```text
Exposure Products
    + Observation Metadata
    + Astrometry
    → DitherSet
    → Measured Dither Registration
    → Coverage Map
```

---

# 9. Baseline vs. Advanced Methods

## 9.1 Baseline production methods

```text
Fractional five-pixel aperture extraction
Gap-constrained CCD scatter model
Linear wavelength rectification
Native-grid oversampled sky
Small constrained PCA sky residual model
Historical baseline relative-response curve
Header + catalog astrometry with shift and rotation
Nominal dither pattern with astrometric refinement
```

## 9.2 Supported advanced methods

```text
Iterative forward scattered-light model
Constrained profile extraction
Full simultaneous forward extraction
Flux-conserving spectral resampling
Fiber-specific LSF sky forward model
Track- and time-dependent response model
Global stellar self-calibration
Spatially varying sky model
```

Advanced methods must produce separate Products and be compared against the
baseline rather than silently replacing it.

---

# 10. Cross-Note Coherence Decisions

## 10.1 Master Flat terminology

Use:

```text
Master LDLS
```

not `Master Flat` for the calibration-unit continuum Product.

It is not a direct science flat.

## 10.2 Arc terminology

Use:

```text
Master Hg
Master Cd
Master Arc
```

The legacy `mastercmp` field is migration vocabulary only.

## 10.3 Fiber normalization

Fiber normalization is:

```text
within_amp_component
×
amp_to_amp_component
```

and is finalized at Exposure scope.

## 10.4 Scattered light

The physical correction scope is:

```text
Physical CCD
```

not independent amplifier.

## 10.5 Extraction

The baseline Product is an aperture-defined spectrum, not total fiber flux.

Its repeatable capture fraction is calibrated through the same twilight
extraction operator.

## 10.6 Wavelength and barycentric correction

The detector wavelength map remains separate from observation-frame velocity or
barycentric corrections.

## 10.7 Sky

The incident sky is Exposure scoped.

The prediction for one fiber depends on:

- normalization;
- illumination;
- wavelength;
- LSF;
- and mask state.

## 10.8 Astrometry

F-plane and fiber geometry are configuration.

Shift and rotation are Exposure-level inference.

## 10.9 Observation grouping

Exposure is atomic.

Observation, Shot, DitherSet, and ObservationSet are explicit relationships.

---

# 10.10 Physical CCD transform

The legacy scatter code resolves the orientation relationship:

```text
Left CCD:
    LL lower, unchanged y
    LU upper, reflected y

Right CCD:
    RU lower, unchanged y
    RL upper, reflected y
```

with `x` unchanged and the upper member represented exactly by:

```text
y_CCD = 2063 - y
```

The indexed seam convention is resolved. Endpoint, round-trip, row-coverage,
and physical ordering remain required validation tests.

---

# 11. Coherence Problems Requiring Resolution

## 11.1 Duplicate controller note

Resolve:

```text
Controller_Hardware_Identity
Controller_Swaps
```

One should become authoritative.

## 11.2 Product naming vocabulary

Some notes use conceptual names while legacy code uses:

```text
masterflt
mastercmp
ftf
plaw
```

A migration vocabulary table is required.

## 11.3 Units and scale conventions

The following require one authoritative registry:

- ADU vs. electrons;
- counts vs. counts per pixel;
- counts vs. counts per Å;
- integrated aperture flux vs. mean aperture value;
- variance vs. standard deviation factors;
- topocentric vs. corrected wavelength;
- RA angle vs. projected RA offset.

## 11.4 Validity and selection

The notes identify temporal behavior, but a single selection policy is still
needed for:

- closest Product;
- bracketing interpolation;
- strict hardware match;
- inherited prior;
- degraded fallback;
- and extrapolation refusal.

## 11.5 QA semantics

Notes define metrics and warnings, but the common meanings of:

```text
PASS
WARN
FAIL
INVALID
DEGRADED
EXPERIMENTAL
```

must be centralized.

## 11.6 Human evidence

Visual review is scientifically important for:

- astrometry;
- fiber maps;
- reconstructed images;
- PSF residuals;
- and scattered-light residuals.

The architecture needs a `human_review` evidence Product.

---

# 12. Missing Knowledge Areas

The present notes support a nearly complete single-exposure reduction.

The following areas remain missing or only partially covered.

## 12.1 Absolute flux calibration

The relative-response note discusses absolute scale but does not yet fully
specify:

- source-capture reconstruction;
- atmospheric extinction;
- transparency;
- standard-star fitting;
- final physical units;
- and absolute calibration uncertainty.

## 12.2 Spatial reconstruction and cube building

A dedicated note is needed for:

- fiber footprint;
- DAR;
- dither combination;
- flux conservation;
- spatial covariance;
- cube pixel scale;
- coverage;
- and reconstructed PSF.

## 12.3 Calibration validity and selection

A cross-cutting note should define:

- validity intervals;
- strict identities;
- interpolation;
- extrapolation;
- inheritance after hardware changes;
- and fallback policy.

## 12.4 QA policy

A note should define the difference between:

- metric;
- evidence;
- rule;
- status;
- usability;
- and manual override.

## 12.5 Provenance and uncertainty ontology

The variance note covers statistical behavior, but the architecture still needs
a unified representation of:

- statistical uncertainty;
- calibration uncertainty;
- model uncertainty;
- systematic uncertainty;
- covariance approximation;
- and confidence.

## 12.6 Source detection and science-object extraction

Astrometry and sky notes imply source Products, but no authoritative note yet
defines:

- detections;
- source hypotheses;
- extraction apertures;
- multi-fiber source spectra;
- and association across exposures.

---

# 13. Blocking vs. Non-Blocking Gaps

## Blocking for a correct baseline implementation

```text
Canonical Product-kind registry
Target/scope identity registry
Units and coordinate conventions
Physical CCD seam/pixel-center convention
Overscan geometry and correction contract
Calibration validity/selection policy
QA status semantics
Raw header/observation classification contract
```

## Important but non-blocking for the first vertical slice

```text
Absolute flux calibration
Cube reconstruction
Full covariance
Forward extraction
Forward scattered-light refinement
Physical LSF sky convolution
Global stellar self-calibration
Track-position illumination model
Automated fiber-map anomaly discovery
```

The baseline can proceed if these are represented as planned interfaces and
optional Products.

---

# 14. Definition of Coherence Complete

The note collection is coherent enough for implementation when:

- every Product kind has one canonical name;
- every Product has one primary scope;
- every dependency is explicit;
- every configuration identity is versioned;
- every baseline algorithm is selected;
- every advanced algorithm is optional and separately identified;
- every fallback is named;
- every unit and coordinate convention is registered;
- every QA status has one meaning;
- and every unresolved scientific assumption has a planned test.

The knowledge notes already provide most of the scientific content needed to
reach this state.
