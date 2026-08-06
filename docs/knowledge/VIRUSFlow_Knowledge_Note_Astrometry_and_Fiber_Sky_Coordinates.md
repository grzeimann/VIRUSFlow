# VIRUSFlow Scientific Knowledge Specification

# Working Note: Astrometry, F-Plane Geometry, and Fiber Sky Coordinates

> Status: Initial scientific and architectural specification based on
> `quick_reduction.py` and `astrometry.py`

This note defines how VIRUSFlow maps VIRUS focal-plane and fiber coordinates
onto the sky.

The central principle is:

> **The internal focal-plane and IFU fiber geometry are treated as stable
> instrument knowledge, while each science observation solves a small global
> correction to the header-based tangent point and rotation.**

The result is an exposure-level astrometric solution that assigns right
ascension and declination to every fiber.

---

# Scientific Object

For every science exposure, astrometry defines the transformation:

```text
Focal-Plane Position
    →
Sky Position
```

For fiber `f` in IFUSLOT `s`:

```text
(RA_f, Dec_f)
    =
AstrometricProjection[
    FPlaneOffset_s
    +
FiberOffset_f
]
```

The projection depends on:

- the sky position of the focal-plane origin;
- the field rotation;
- the F-plane geometry;
- the internal fiber coordinates;
- and any exposure-specific shift or rotation correction.

---

# The F-Plane Reference Frame

The F-plane file contains the focal-plane offsets of every IFUSLOT in
arcseconds.

The reference origin:

```text
(0, 0)
```

corresponds to the IHMP.

For each IFUSLOT, the F-plane provides:

```text
(x_IFUSLOT, y_IFUSLOT)
```

relative to the IHMP.

These coordinates are generally accurate and stable.

Accumulated astrometric evidence may nevertheless support occasional updates to
the F-plane model.

The F-plane should therefore be treated as:

```text
versioned configuration knowledge
```

rather than as hard-coded geometry.

---

# Internal IFU Fiber Geometry

Within each IFU, the fiber positions are known accurately.

The fibers are fixed in a headplate and are not mechanically free to move
relative to one another during ordinary operations.

For one fiber:

```text
x_focal = x_IFUSLOT + x_fiber
y_focal = y_IFUSLOT + y_fiber
```

The astrometric model therefore normally solves a global field transformation,
not independent positions for every fiber.

This strong geometric prior is one reason the VIRUS astrometric solution is
robust.

---

# Initial Header Solution

The fitting process begins with an approximate observation pointing from the
science FITS header.

The current function is named:

```text
get_ra_dec_from_header
```

It attempts to read:

```text
TRAJCRA
TRAJCDEC
```

and compares those values with:

```text
TRAJRA
TRAJDEC
```

Right ascension values are converted from hours to degrees.

If the two header positions differ by more than approximately:

```text
25 arcseconds
```

the code falls back to `TRAJRA` and `TRAJDEC`.

If the commanded-coordinate keywords cannot be read, the trajectory values are
used directly.

The parallactic angle is read from:

```text
PARANGLE
```

This header solution is normally accurate to roughly ten arcseconds or better
and provides the initial point around which catalog matching can proceed.

---

# Initial Tangent Projection

The header right ascension and declination define the tangent point:

```text
RA0, Dec0
```

of an astropy TAN projection.

The `Astrometry` helper constructs a WCS with:

```text
CTYPE = RA---TAN, DEC--TAN
scale = 1 arcsecond per focal-plane unit
```

The default scale signs are:

```text
x_scale = -1
y_scale = +1
```

The effective focal-plane rotation is derived from:

- parallactic angle;
- a fixed system rotation;
- and the F-plane orientation convention.

The current F-plane relation is:

```text
rot = 360° - (90° + PA + system_rotation)
```

with a default system rotation of:

```text
1.55°
```

The WCS rotation is implemented through a clockwise two-dimensional rotation
matrix.

---

# Coordinate Conventions

The F-plane and tangent-plane coordinate conventions require an axis swap.

The helper explicitly maps:

```text
F-plane x/y
    →
WCS y/x
```

For an IFUSLOT:

```text
RA_IFU, Dec_IFU
    =
TAN(
    fplane_y,
    fplane_x
)
```

For a fiber or local IFU image position:

```text
RA, Dec
    =
TAN(
    fplane_y + local_x,
    fplane_x + local_y
)
```

These conventions are scientifically important and should be represented in
configuration and tests.

An unnoticed axis reversal or sign change would produce a coherent but
incorrect field solution.

---

# Catalog Reference

The current quick reduction uses Pan-STARRS as the external astrometric
reference.

A cone search is performed around the header-based tangent point over a radius
of approximately:

```text
9 arcminutes
```

The catalog provides:

- right ascension;
- declination;
- and aperture magnitudes used for source-quality selection and photometric
  analysis.

The astrometric architecture should not be permanently coupled to one catalog.

The reference catalog should be a versioned input Product.

---

# Reconstructed IFU Images

The astrometric fit begins by creating one broadband reconstructed image for
each IFUSLOT.

The current implementation builds a g-band-like image from the sky-subtracted
fiber spectra.

It uses:

```text
image scale = 0.75 arcsec per pixel
image range ≈ -23 to +25 arcsec in each direction
```

The spectra are divided into wavelength chunks, corrected for the adopted
atmospheric-refraction offsets, spatially interpolated, and convolved to form
the image.

For three-exposure observations, the reconstruction uses a nominal seeing value
near:

```text
1.76 arcseconds
```

and otherwise uses approximately:

```text
3 arcseconds
```

These values belong to the present implementation and should become explicit
parameters rather than permanent physical constants.

---

# Source Detection

Sources are identified independently in each reconstructed IFU image.

The current implementation:

1. computes sigma-clipped image statistics;
2. uses `DAOStarFinder`;
3. assumes a detection FWHM of four image pixels;
4. applies a threshold of seven times the image standard deviation;
5. excludes detections at the image border.

The source detector returns image centroids.

A circular aperture with radius:

```text
3.5 image pixels
```

is used for approximate source photometry.

The photometry supports catalog filtering and later throughput analysis, but the
astrometric position is supplied by the detected centroid.

---

# Mapping Detections into the F-Plane

For each detected source, the local reconstructed-image coordinates are
converted into IFU-plane offsets using the image scale and range.

The F-plane offset of the IFUSLOT is then added:

```text
fx = local_x + IFUSLOT_fplane_x
fy = local_y + IFUSLOT_fplane_y
```

The result is one detected source position in the global focal-plane coordinate
system.

The current tangent projection converts that focal-plane position into a
provisional sky coordinate.

---

# Catalog Matching

Each detected source is initially associated with the nearest catalog source in
sky coordinates.

The match table retains:

- detection photometry;
- nearest-neighbor separation;
- catalog magnitude;
- catalog RA and Dec;
- detected focal-plane `fx` and `fy`;
- provisional RA and Dec offsets;
- and IFUSLOT.

The catalog coordinate is also projected back into the IFU image for
visualization.

This allows reconstructed-image detections and expected catalog positions to be
overplotted.

---

# Why Nearest-Neighbor Matching Is Not Enough

The initial header position may be offset by several arcseconds.

A simple nearest-neighbor match can therefore associate detections with the
wrong catalog objects.

The implementation addresses this by searching for a coherent cluster in
two-dimensional offset space.

For each provisional match:

```text
ΔRA = cos(Dec) × (RA_detected - RA_catalog)
ΔDec = Dec_detected - Dec_catalog
```

The algorithm computes the pairwise separation of all candidate offsets.

It identifies the offset having the largest number of neighbors within:

```text
1.5 arcseconds
```

Only candidates in that dense offset cluster are retained.

This makes the solution depend on a common field translation rather than on
isolated nearest-neighbor assignments.

---

# Initial Candidate Selection

Before the offset-density selection, candidate matches must satisfy:

```text
catalog separation < 25 arcseconds
15 < catalog g magnitude < 22
```

After identifying the densest offset cluster, at least:

```text
4 matched sources
```

are required.

If fewer than four coherent matches exist, the initial astrometric solution is
returned unchanged.

Operationally, solutions with ten or more good reference sources are much more
reliable.

The minimum of four is therefore a fail-safe threshold, not a preferred
scientific standard.

---

# Solving the Tangent Point

The selected matched sources provide pairs:

```text
(focal-plane fx, fy)
    ↔
(catalog RA, Dec)
```

The current algorithm independently fits first-order two-dimensional
polynomials:

```text
RA = P_RA(fx, fy)
Dec = P_Dec(fx, fy)
```

using a Levenberg-Marquardt fitter with sigma-clipped outlier rejection.

The fitted values at:

```text
fx = 0
fy = 0
```

provide an initial estimate of:

```text
RA_IHMP
Dec_IHMP
```

Thus, the fit uses all selected sources to infer the sky position of the F-plane
origin.

The polynomial fit is primarily a convenient way of measuring the tangent-point
translation.

The final coordinate transformation remains the TAN projection tied to the
F-plane geometry.

---

# Rotation Search

After estimating the initial field center, the algorithm searches for a
correction to the effective field rotation.

The current implementation evaluates rotation offsets over:

```text
-1° to +1°
```

in:

```text
0.01° steps
```

For each trial rotation:

1. rebuild the tangent projection;
2. predict sky positions for the selected focal-plane detections;
3. calculate RA and Dec residuals;
4. compute a robust two-axis spread.

The objective currently used is:

```text
sqrt[
    MAD_sigma(ΔRA)
    ×
    MAD_sigma(ΔDec)
]
```

The trial rotation minimizing this quantity is retained.

The implementation uses degrees throughout the WCS and rotation search.


---

# Final Translation

With the best rotation fixed, the catalog-minus-model residuals are recomputed.

The median RA and Dec residuals provide the final tangent-point correction:

```text
RA0_new
    =
RA0_old
    +
median(ΔRA) / cos(Dec) / 3600
```

```text
Dec0_new
    =
Dec0_old
    +
median(ΔDec) / 3600
```

A new `Astrometry` object is then created with:

- corrected IHMP RA;
- corrected IHMP Dec;
- corrected position angle;
- the F-plane file;
- and a rebuilt TAN projection.

---

# Iterative Rematching

The full matching and fitting procedure is repeated.

`advanced_analysis` performs:

1. an initial catalog match using the header solution;
2. an astrometric fit;
3. rematching with the updated projection;
4. a second astrometric fit;
5. another rematching pass.

The current loop runs twice.

This improves the source associations because the catalog matching becomes more
accurate after each correction.

The iteration is especially important when the initial offset is several
arcseconds.

---

# Final Fiber Coordinates

Once the exposure-level solution is complete, each fiber receives its sky
coordinate.

For every IFUSLOT and fiber position:

```text
RA_f, Dec_f
    =
A.get_ifupos_ra_dec(
        IFUSLOT,
        fiber_x,
        fiber_y
    )
```

The resulting arrays are attached to the extracted fiber spectra and used for:

- source masking;
- sky selection;
- source extraction;
- reconstructed images;
- catalog association;
- and data-cube construction.

---

# Astrometric QA

The final model predicts sky coordinates for each matched focal-plane detection.

Residuals are calculated as:

```text
ΔRA
    =
cos(Dec) × (RA_catalog - RA_model)
```

```text
ΔDec
    =
Dec_catalog - Dec_model
```

The current QA plot reports:

- median ΔRA;
- median ΔDec;
- standard deviation in ΔRA;
- standard deviation in ΔDec;
- and number of matched sources.

A scatter plot of ΔRA against ΔDec provides a direct visual assessment of:

- residual translation;
- anisotropic scatter;
- outliers;
- rotation errors;
- and possible focal-plane distortions.

---

# Current Lack of Formal Uncertainty

The current fit does not return parameter uncertainties.

It reports residual offsets and scatter, but not:

- uncertainty in the IHMP RA;
- uncertainty in the IHMP Dec;
- uncertainty in rotation;
- covariance among fitted parameters;
- uncertainty in individual fiber coordinates.

This should be improved in VIRUSFlow.

Potential approaches include:

- bootstrap resampling of matched sources;
- covariance from the local fit;
- robust jackknife analysis;
- catalog positional uncertainties;
- and propagation through the tangent projection.

The residual scatter is a QA statistic, not a complete astrometric uncertainty
model.

---

# Natural Product Scope

The astrometric fit is naturally:

```text
exposure scoped
```

because it describes one telescope pointing and rotation state.

The F-plane and internal IFU fiber geometry are:

```text
configuration scoped
```

The final fiber-coordinate table links the exposure-level solution to every
fiber Product.

A suitable target is:

```text
AstrometryTarget(
    observation_id,
    exposure_id
)
```

with all available IFUSLOT images contributing to the fit.

---

# F-Plane Evolution

The F-plane geometry is expected to be accurate.

Accumulated astrometric fits can nevertheless test for persistent residuals as a
function of IFUSLOT.

For example, the repository can determine whether one IFUSLOT consistently
requires:

- an x offset;
- a y offset;
- a rotation-like correction;
- or a local distortion.

Such evidence could justify a new version of the F-plane file.

The production reduction should not silently modify F-plane coordinates from
one exposure.

F-plane updates should be versioned, reviewed, and supported by many
observations.

---

# Rare Fiber-Mapping Problems

Historically, a small number of IFUs have shown problems such as:

- mislabeled fibers;
- flipped fiber ordering;
- or one or two incorrectly mapped fibers.

These are not ordinary exposure-level astrometric errors.

They are configuration or mapping defects within an IFU.

They often appear as:

- reconstructed stars distorted near one or two fibers;
- persistent local disagreement with external images;
- or repeated centering anomalies at the same fiber positions.

The current system need not attempt to discover these automatically during
routine reduction.

They should be represented eventually as versioned fiber-mapping configuration
corrections.

---

# Human Visual Validation

Visual comparison remains exceptionally valuable.

A reconstructed VIRUS image can be overlaid with:

- catalog source positions;
- external survey imagery;
- and detected VIRUS centroids.

A human observer can identify coherent geometric problems that may be difficult
to encode initially, especially:

- isolated fiber swaps;
- local flips;
- source morphology mismatch;
- and reconstructed-image artifacts.

This is not a rejection of automation.

It is an acknowledgment that high-dimensional visual residuals can contain
information not represented by the current scalar QA metrics.

Human-reviewed diagnostics should be preserved as evidence.

---

# Input Contract

The astrometric process requires:

```text
header pointing and parallactic angle
versioned F-plane file
fiber positions within each IFU
sky-subtracted fiber spectra
broadband reconstruction definition
external astrometric catalog
```

Recommended additional inputs include:

```text
variance spectra
fiber-quality mask
seeing estimate
DAR model
catalog position uncertainties
```

---

# Product Contract

## Initial Astrometry Product

Recommended metadata:

```text
header_RA
header_Dec
header_PA
header_keywords_used
fallback_triggered
F-plane_version
system_rotation
initial_tangent_projection
```

## Source Detection Product

Recommended table:

```text
IFUSLOT
detected_image_x
detected_image_y
detected_focal_x
detected_focal_y
aperture_flux
detection_SNR
source_quality
```

## Catalog Match Product

Recommended table:

```text
detection_id
catalog_source_id
catalog_RA
catalog_Dec
catalog_magnitude
initial_separation
delta_RA
delta_Dec
offset_cluster_membership
match_iteration
```

## Final Astrometry Product

Recommended metadata and scalars:

```text
IHMP_RA
IHMP_Dec
position_angle
effective_rotation
delta_RA_from_header
delta_Dec_from_header
delta_rotation
number_of_matches
median_RA_residual
median_Dec_residual
RA_residual_scatter
Dec_residual_scatter
fit_iteration_count
catalog
F-plane_version
algorithm_version
```

Recommended uncertainty fields:

```text
IHMP_RA_uncertainty
IHMP_Dec_uncertainty
rotation_uncertainty
fit_covariance
```

## Fiber Sky Coordinate Product

Recommended table or arrays:

```text
fiber_identity
IFUSLOT
fiber_x
fiber_y
RA
Dec
coordinate_uncertainty
astrometry_product
```

---

# Required QA

QA should evaluate:

- initial header discrepancy;
- number of detected sources;
- number of coherent catalog matches;
- focal-plane distribution of matches;
- magnitude range;
- maximum and median initial separation;
- residual median offsets;
- robust residual scatter;
- rotation correction;
- rematching stability;
- leave-one-out sensitivity;
- and visual reconstruction agreement.

The fit should warn when:

- fewer than approximately ten good matches are available;
- fewer than four matches exist;
- matches occupy only one small region of the focal plane;
- residuals are strongly anisotropic;
- rotation lies near the search boundary;
- or successive iterations do not converge.

---

# Important Current Assumptions

The current process assumes:

- the header position is close enough for nearest-neighbor catalog matching;
- the dominant mismatch is one global translation and rotation;
- the F-plane geometry is accurate;
- internal IFU fiber positions are fixed;
- enough point-like sources are detectable;
- the catalog is sufficiently complete and accurate;
- the g-band reconstruction has reliable centroids;
- DAR treatment is adequate for the broadband image;
- and a two-pass rematching loop reaches a stable solution.

These assumptions should be preserved as explicit tests.

---

# Initial Implementation Decisions

- Use the header trajectory solution as the initial tangent point.
- Retain the header fallback logic and record which keywords were used.
- Use a versioned F-plane file with the IHMP as the origin.
- Build a TAN projection with explicit scale, signs, and rotation convention.
- Reconstruct broadband images separately for each IFUSLOT.
- Detect sources in those images and map detections into global F-plane
  coordinates.
- Match to an external catalog.
- Select a coherent density peak in RA/Dec offset space before fitting.
- Require at least four coherent matches, while warning below approximately ten.
- Solve the IHMP tangent point from all selected focal-plane detections.
- Search a bounded rotation interval using robust residual scatter.
- Rematch and refit iteratively.
- Assign final coordinates to every fiber from the exposure solution and fixed
  geometry.
- Return formal fit uncertainties in the new implementation.
- Preserve residual tables and diagnostic images.
- Keep rare fiber swaps and flips as configuration issues rather than ordinary
  exposure-fit parameters.
- Retain human visual review as a supported QA evidence type.

---

# Open Questions

- Is the current ±1° rotation search range optimal?
- Should translation and rotation be solved simultaneously?
- Should the fit operate directly in tangent-plane coordinates instead of
  fitting RA and Dec independently?
- How should catalog positional uncertainties be weighted?
- What is the best robust likelihood for crowded fields?
- How should extended or blended sources be rejected?
- How should the astrometric uncertainty be propagated to every fiber?
- When is a higher-order focal-plane distortion justified?
- How stable is the system-rotation constant?
- How should accumulated residuals trigger an F-plane revision?
- Can automated image-comparison diagnostics identify rare fiber mapping
  problems?
- Which external catalog should be authoritative for each epoch and field?
- How should proper motion be applied to stellar catalogs?
- How should astrometry be handled when fewer than four sources are detected?

---

# Repository Goals

VIRUSFlow should:

- preserve the F-plane and internal fiber geometry as versioned instrument
  knowledge;
- produce an exposure-level astrometric solution for every science exposure;
- refine the header pointing using robust multi-IFU catalog matches;
- separate tangent-point translation from field rotation;
- return parameter and fiber-coordinate uncertainties;
- track astrometric residuals by IFUSLOT, fiber, time, and observing state;
- identify when the F-plane model should be updated;
- preserve catalog matching and residual evidence;
- support human validation with reconstructed-image overlays;
- detect persistent local mapping anomalies without allowing one exposure to
  rewrite configuration;
- provide accurate fiber sky positions for source masking, extraction, sky
  modeling, and cube reconstruction;
- and turn accumulated astrometric solutions into a continuously validated model
  of the VIRUS focal plane.
