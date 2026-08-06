# VIRUSFlow Stages 8–10: Storage, Materialization, Sky Modeling, and Parallel Execution Revision

## Purpose

Stages 8 through 10 are producing scientifically promising results, but the current artifact strategy is expanding the data far beyond the intended scale. The current run has produced approximately 77 GB of artifacts, dominated by dense detector-level images and evaluated model products:

```text
25 GB   ccd_scattered_light_model
21 GB   scatter_subtracted_image
12 GB   reduced_science_image
5.2 GB  aperture_extracted_spectrum
2.4 GB  master_ldls
2.4 GB  master_bias
2.4 GB  master_dark
1.2 GB  master_arc
1.2 GB  master_twilight
1.1 GB  fiber_normalization
1.0 GB  sky_subtracted_spectrum
529 MB  final_exposure_response
529 MB  fiber_sky_prediction
536 MB  extracted_variance
```

This should be corrected before stages 8 through 10 are considered complete and before the full workflow is rerun.

The issue is not primarily insufficient file compression. The pipeline is persisting both compact underlying information and multiple dense evaluations of that information.

The intended architecture is:

1. Preserve raw observations through the existing raw-data archive.
2. Persist compact calibration and physical models.
3. Apply those models quickly during science reduction.
4. Persist one final calibrated fiber-spectrum product for each complete observation.
5. Keep detector-level and pre-final spectral intermediates temporary.
6. Recreate intermediates deliberately during post-run analysis when they are needed to understand, validate, or improve a model.
7. Promote validated analysis results into new versioned models.
8. Run independent tasks in parallel by default with four workers.

The target for a complete three-dither VIRUS science observation is approximately:

```text
2–4 GB
```

This should include the final scientifically useful spectral planes, metadata, masks, astrometry, and provenance.

---

# 1. Governing information principle

A persistent artifact should contain one or more of the following:

1. Irreducible measured information.
2. A compact model sufficient to reconstruct a prediction.
3. A final scientific product.
4. Compact QA evidence.
5. A bounded analysis result.
6. Provenance describing exactly how a result was produced.

The production system should not preserve every intermediate representation between the raw CCD and the final calibrated fiber spectra.

The production reduction should conceptually be:

```text
raw CCD observation
+ master calibration models
+ trace model
+ wavelength model
+ scattered-light model
+ fiber-response models
+ astrometric model
+ sky model
        |
        v
final calibrated fiber-spectrum observation
```

Detector images and pre-final spectral arrays created along this path should normally exist only in memory or run-local scratch space.

The central architectural principle is:

> Production preserves raw information, accepted models, final scientific products, and compact evidence. Analysis deliberately materializes intermediate views to inspect, test, and improve those models.

---

# 2. Scope of this revision

This revision applies primarily to stages 8 through 10 and the science-reduction artifact lifecycle.

The immediate priorities are:

1. Remove permanent detector-sized scattered-light evaluations.
2. Remove permanent reduced and scatter-subtracted science images.
3. Remove permanent pre-final extracted spectra.
4. Replace per-fiber sky predictions with a compact generative sky model.
5. Create one final observation-level calibrated spectral product.
6. Convert large non-astrometric arrays to `float32`.
7. Adopt scaled flux units through `BUNIT`.
8. Add bounded analysis materialization.
9. Change the default task execution mode to four-worker parallel execution.

Master calibration products do not need a complete conceptual redesign during this pass. They should use appropriate dtypes and compression, but eliminating redundant science intermediates is the immediate priority.

---

# 3. Numerical storage conventions

## 3.1 Default floating-point type

Use `float32` for all large persistent numerical arrays except astrometric quantities that require `float64`.

This includes:

- Flux spectra
- Uncertainties
- Variances
- Wavelength arrays
- Trace models and evaluations
- Wavelength models and evaluations
- Scattered-light model parameters and evaluations
- Response models and evaluations
- Sky spectra
- Sky basis components
- Persistent calibration images
- Diagnostic numerical arrays

Algorithms may calculate internally in `float64` where useful. Persistent serializers should cast large non-astrometric outputs to `float32`.

Do not force all algorithm calculations to occur in `float32`. This is a persistence convention, not necessarily an internal numerical-computation restriction.

## 3.2 Float64 exceptions

Retain `float64` for:

- Right ascension
- Declination
- Astrometric transformation coefficients
- World-coordinate transformations
- Focal-plane-to-sky transformations
- Other quantities explicitly demonstrated to require double precision

Astrometry is presently the primary expected persistent use case for `float64`.

## 3.3 Flux scaling and BUNIT

Store flux values in units scaled by \(10^{-17}\), so typical numerical values are near unity.

Conceptually:

```text
stored_flux = physical_flux / 1e-17
```

The dataset should declare a unit equivalent to:

```text
BUNIT = 1e-17 erg s-1 cm-2 Angstrom-1
```

This does not reduce the number of bytes, but it:

- Makes interactive inspection easier.
- Avoids displaying nearly every value using scientific notation.
- Keeps variances and uncertainties in a more convenient numerical range.
- Makes debugging and QA tables easier to interpret.

Do not store a per-fiber or per-spectrum normalization array for this purpose. The factor is a scalar unit convention recorded in metadata.

If uncertainty is stored:

```text
stored_uncertainty = physical_uncertainty / 1e-17
```

and it uses the same unit as flux.

If variance is stored:

```text
stored_variance = physical_variance / 1e-34
```

and its unit should be the square of the flux unit.

The serializer and loader must preserve this convention consistently.

## 3.4 Other dtypes

Use:

- `uint8` or `uint16` for masks and bit flags.
- Appropriately sized integer types for identifiers and indices.
- Boolean or packed-bit representations where appropriate.
- `float32` for a shared rectified output wavelength axis.
- `float64` for sky coordinates and astrometric transformations.

---

# 4. Artifact lifecycle classes

The artifact system should distinguish scientific identity from storage lifecycle.

Add or formalize a lifecycle vocabulary equivalent to:

```text
canonical
model
analysis
cache
scratch
```

## 4.1 Canonical

A final persistent scientific product.

Examples:

```text
calibrated_fiber_observation
master_bias
master_dark
master_arc
master_twilight
```

A canonical artifact is intended to remain available and reproducible.

## 4.2 Model

A persistent compact calibration or physical model.

Examples:

```text
trace_model
wavelength_model
scattered_light_model
sky_model
fiber_response_model
astrometric_solution
line_spread_function_model
```

A model stores the representation needed to generate a prediction, not necessarily the full evaluated prediction.

## 4.3 Analysis

An output belonging to a bounded scientific analysis or model-development study.

Examples:

```text
scattered_light_residual_study
sky_model_validation
lsf_sampling_convergence
candidate_sky_model
candidate_scattered_light_model
```

Analysis outputs may include deliberately retained intermediates when justified by the study.

## 4.4 Cache

A reconstructable payload retained only for performance.

A cache:

- May be deleted.
- Must not be required for scientific reproducibility.
- Must be associated with exact input artifact identities and algorithm versions.
- Should only be introduced after a performance need has been demonstrated.

## 4.5 Scratch

A run-local temporary value.

Scratch data:

- Is not a permanent artifact.
- Usually does not receive a persistent artifact record.
- Is removed after successful completion.
- May be retained temporarily following a failed task for debugging.
- Must use worker-specific paths during parallel execution.

Most detector-level science intermediates should be scratch, not cache and not canonical artifacts.

---

# 5. Permanent final science product

For each complete three-dither observation, produce one primary persistent artifact provisionally named:

```text
calibrated_fiber_observation
```

The final product should be observation-level rather than a collection of separately persisted intermediate artifacts.

## 5.1 Expected spectral payload

A reasonable payload includes:

```text
flux[fiber, wavelength]                    float32
uncertainty[fiber, wavelength]             float32
or variance[fiber, wavelength]             float32
mask[fiber, wavelength]                    uint8 or uint16
optional scientifically distinct planes   float32
wavelength[wavelength]                     float32
```

The product may contain four or five spectral planes when those planes provide independently useful scientific information.

Do not retain a plane merely because it was convenient during implementation.

Examples of potentially useful distinct planes might include:

- Final calibrated sky-subtracted flux
- Uncertainty or variance
- Mask
- A pre-flux-calibration measurement when scientifically justified
- A sky or continuum quantity when it has lasting scientific value

The final set of planes should be explicitly justified and documented.

## 5.2 Fiber and observation metadata

The product should include or reference:

```text
observation identity
exposure identity
dither identity
ifuslot
ifuid
specid
amplifier
controller
fiber number
focal-plane coordinates
RA
Dec
wavelength solution reference
trace-model reference
sky-model reference
scattered-light-model reference
response-model reference
astrometric-solution reference
LSF-model reference when applicable
algorithm versions
software version
creation timestamp
```

Avoid duplicating large metadata structures unnecessarily when they can be represented by normalized tables or shared references.

## 5.3 Size expectation

A complete VIRUS three-dither observation contains approximately 100,000 fibers and approximately 1,000 wavelength samples.

One `float32` plane therefore occupies approximately 0.4 GB before container overhead.

Approximate uncompressed spectral sizes are:

```text
1 float32 plane  ≈ 0.4 GB
2 float32 planes ≈ 0.8 GB
4 float32 planes ≈ 1.6 GB
5 float32 planes ≈ 2.0 GB
```

After masks, metadata, provenance, tables, and additional scientifically justified arrays, the intended range is:

```text
2–4 GB per complete three-dither observation
```

The design should meet this range without depending upon unusually strong compression.

---

# 6. Science intermediates must not be permanent production artifacts

The following products should not be persisted during normal science reduction:

```text
reduced_science_image
scatter_subtracted_image
aperture_extracted_spectrum
extracted_variance as a separate artifact
fiber_sky_prediction
standalone sky_subtracted_spectrum
full detector evaluation of scattered light
full detector evaluation of compact calibration models
```

These may remain internal concepts and algorithm values.

A typical reduction may still perform:

```python
reduced_image = calibrate_raw_image(
    raw_image,
    calibration_models,
)

scatter_prediction = scattered_light_model.evaluate(
    detector_coordinates,
)

scatter_subtracted_image = (
    reduced_image - scatter_prediction
)

extracted_flux, extracted_variance = extract_fibers(
    scatter_subtracted_image,
    trace_model,
)

sky_prediction = sky_model.evaluate(
    fiber_metadata=fiber_metadata,
    wavelength_bin_edges=wavelength_bin_edges,
    lsf_model=lsf_model,
)

sky_subtracted_flux = (
    extracted_flux - sky_prediction
)

final_flux = apply_flux_calibration(
    sky_subtracted_flux,
    response_model,
)
```

These arrays should disappear after:

1. The final observation product has been written successfully.
2. Required QA measurements have been recorded.
3. Small diagnostic summaries have been created.
4. Artifact and provenance records have been committed.

Do not create persistent artifact records for every internal algorithm state.

---

# 7. Scattered-light model representation

The current `ccd_scattered_light_model` must not contain a full CCD-sized model image.

Persist the compact representation used to generate the model.

Depending on the algorithm, this may include:

- Two-dimensional polynomial coefficients
- Tensor-product polynomial coefficients
- B-spline knots and coefficients
- Sparse detector samples and interpolation settings
- Control points
- Low-rank basis coefficients
- Another compact parameterization sufficient to reconstruct the model

A scattered-light model payload should include information such as:

```text
representation type
detector shape
coordinate domain
polynomial order or spline definition
coefficients
control-point coordinates
sample values
sample weights
fit-mask reference
algorithm name
algorithm version
training-data references
validity domain
residual RMS
residual percentiles
fit diagnostics
```

The evaluated detector model should be generated through an interface such as:

```python
scatter_prediction = scattered_light_model.evaluate(x, y)
```

A full-resolution evaluated image may be created temporarily for:

- Science reduction
- QA
- Model validation
- A bounded analysis study

It should not be the canonical model artifact.

---

# 8. Sparse mask representation

Pixel masks and other sparse masks should not automatically be stored as full two-dimensional arrays.

Support compact lossless representations containing information such as:

```text
shape
flat pixel indices
flag values
encoding
```

The serializer may select among:

- Sparse flat indices
- Coordinate pairs
- Run-length encoding
- Packed bit masks
- Dense masks when occupancy makes dense storage preferable

Mask values should use `uint8` or `uint16` bit fields.

The representation must be:

- Lossless
- Reconstructable
- Transparent to consuming algorithms
- Versioned
- Validated through round-trip tests

Algorithms should be able to request a normal dense mask without knowing how the mask was serialized.

---

# 9. Sky-model representation

The canonical sky artifact should be a compact generative model, not a complete predicted sky spectrum for every fiber.

The sky model should be designed with scientifically meaningful spectral sampling and future line-spread-function support.

## 9.1 Shared wavelength axis as a latent model coordinate

The sky model may use a shared wavelength coordinate, but that coordinate must be a:

```text
latent, supersampled model grid
```

It is not necessarily:

- The final rectified science wavelength grid.
- The native wavelength sampling of any one fiber.
- A grid onto which every measured fiber spectrum is directly interpolated.

Each fiber has its own wavelength solution and potentially its own line-spread function.

The sky prediction should be generated by evaluating a continuous or sufficiently supersampled latent model through the observation operator for that fiber.

Do not create a sky spectrum on one fiber’s sampled wavelength grid and interpolate that sampled array onto another fiber.

## 9.2 Motivation

Strong and narrow sky lines are sensitive to:

- Small wavelength offsets
- Pixel phase
- Inadequate sampling
- Fiber-to-fiber wavelength differences
- Fiber-to-fiber resolution differences
- Interpolation and rectification

Direct interpolation of sampled narrow lines can generate adjacent positive and negative residuals that resemble P-Cygni structure.

The sky model should therefore be forward modeled onto the true wavelength bins of each fiber.

## 9.3 Conceptual forward model

For fiber \(f\) and spectral pixel \(p\), the predicted sky should conceptually be:

\[
\widehat{D}_{f,p}
=
\int_{\lambda^-_{f,p}}^{\lambda^+_{f,p}}
\left[
S_0(\lambda)
+
\sum_k c_{f,k}B_k(\lambda)
\right]
\otimes L_f(\lambda)
\,d\lambda
\]

where:

- \(S_0(\lambda)\) is the baseline incident sky spectrum.
- \(B_k(\lambda)\) are additional spectral components.
- \(c_{f,k}\) are fiber-, amplifier-, exposure-, or spatially dependent coefficients.
- \(L_f\) is the applicable line-spread function.
- \(\lambda^-_{f,p}\) and \(\lambda^+_{f,p}\) are the native wavelength boundaries of pixel \(p\).

The first implementation may omit explicit LSF convolution if no accepted LSF model is available.

However, the sky-model artifact and evaluator interface must support adding LSF convolution without redefining the entire model architecture.

Even without an explicit LSF, evaluate or integrate the latent model onto each fiber’s true wavelength coordinates.

## 9.4 Latent spectral representation

The latent sky model may be represented by:

- A supersampled regular wavelength grid
- B-spline knots and coefficients
- Another continuous basis representation
- Smooth continuum components plus narrow-line components
- A hybrid physical and empirical basis

The representation must support evaluation at arbitrary wavelength coordinates.

When using a regular grid, the latent grid should be sufficiently fine to represent the narrowest effective LSF within the model’s validity range.

The final observation product does not need to retain the latent grid as its science wavelength axis.

## 9.5 Scientifically grounded sampling rule

Do not hard-code an unexplained universal oversampling multiplier of two or three.

The appropriate model-grid spacing should be derived from:

1. The narrowest relevant LSF FWHM.
2. The native wavelength width per detector pixel.
3. The accuracy required when shifting narrow lines.
4. The accuracy required during LSF convolution.
5. The accuracy required during pixel integration.
6. Instrument-specific convergence tests.

Two detector samples per FWHM should be treated as a minimum detector-sampling scale, not as a sufficient latent-model criterion.

Realistic LSFs are not perfectly band limited, and sampling behavior depends upon:

- LSF shape
- LSF asymmetry
- Pixel phase
- Wavelength-solution offsets
- Downsampling method

For the initial implementation, use a configurable target of approximately:

```text
6 latent samples across the narrowest relevant LSF FWHM
```

This is a grounded implementation starting point rather than a universal physical constant.

Define:

```text
native_samples_per_fwhm =
    minimum_lsf_fwhm /
    representative_native_pixel_width
```

Choose the initial integer oversampling factor as:

```text
oversampling_factor =
    ceil(6 / native_samples_per_fwhm)
```

This gives typical behavior such as:

```text
native sampling near 2 pixels/FWHM
→ latent oversampling factor near 3

native sampling near 3 pixels/FWHM
→ latent oversampling factor near 2
```

This provides a defensible explanation for the expected two-to-three-times oversampling.

The sampling target and calculated factor must be recorded in model metadata.

## 9.6 Convergence determines the final grid

The final latent sampling density should be established through an instrument-specific convergence study.

Evaluate representative sky models using candidate densities such as:

```text
4 samples per narrowest LSF FWHM
6 samples per narrowest LSF FWHM
8 samples per narrowest LSF FWHM
```

For each candidate:

1. Construct or evaluate the same latent sky model.
2. Apply representative wavelength offsets.
3. Apply representative fiber LSFs when available.
4. Integrate onto actual native fiber wavelength bins.
5. Compare predicted native pixel fluxes.
6. Compare sky-subtraction residuals around strong narrow lines.
7. Include fibers spanning the range of wavelength solutions.
8. Include fibers spanning the range of LSF widths and shapes.
9. Examine positive-negative residual structure.
10. Measure computational cost.

Select the coarsest grid for which further refinement produces no scientifically meaningful change in:

```text
pixel-integrated sky prediction
sky-line residual RMS
positive-negative residual structure
integrated line flux
line centroid
line width
derived sky-subtracted spectrum
```

The acceptance threshold should be tied to expected observational uncertainty and required sky-subtraction accuracy.

Do not choose the grid solely by comparing latent arrays.

Record the convergence study and final sampling rule as part of the sky-model implementation.

## 9.7 LSF-aware model evolution

The long-term model should distinguish:

```text
latent incident sky spectrum
instrumental line-spread function
detector pixel integration
```

The preferred forward process is:

```text
latent incident sky
    ↓
convolution with fiber-specific LSF
    ↓
integration over native wavelength bins
    ↓
predicted sky for that fiber
```

This allows one incident sky model to predict observations from fibers with different:

- Wavelength solutions
- Spectral resolutions
- LSF widths
- LSF asymmetries

The sky-model API should accept an LSF model or LSF artifact reference even if the first implementation treats it as optional.

## 9.8 Empirical sky components and reference resolution

An empirical sky basis learned from observed spectra already includes instrumental broadening.

Its reference resolution must therefore be explicit.

Valid approaches include:

1. Define the empirical basis at a reference LSF and apply only the additional broadening needed for wider fibers.
2. Build a latent model from continuum terms and intrinsically narrow emission-line components, then convolve with each fiber’s LSF.
3. Jointly fit a latent sky representation and the LSF-dependent observation operator.
4. Use groups of similar LSFs and maintain a reference basis for each group.

Do not implicitly treat an empirically broadened basis as an unconvolved incident spectrum.

Do not introduce unstable deconvolution merely to satisfy the abstraction.

The representation must state clearly whether its components are:

```text
intrinsic
reference-LSF broadened
fiber-specific
```

## 9.9 Flux-conserving downsampling

Downsampling from the latent model to a fiber must preserve integrated flux.

Prefer integration over each destination wavelength bin rather than evaluating only at the pixel center:

```python
predicted_sky[pixel] = integrate(
    convolved_latent_sky,
    wavelength_lower_edge[pixel],
    wavelength_upper_edge[pixel],
)
```

When useful for performance, represent the forward operation as a sparse projection operator:

```text
fiber_prediction =
    projection_operator @ latent_sky
```

The projection operator is reconstructable from:

```text
latent wavelength definition
native wavelength-bin edges
LSF model
integration convention
algorithm version
```

It should not normally be stored separately for every reduction unless caching provides a demonstrated performance improvement.

## 9.10 Compact sky-model payload

A sky-model payload may include:

```text
latent wavelength grid, knots, or basis definition
wavelength validity range
sampling target in samples per LSF FWHM
actual latent-grid spacing
oversampling factor
baseline sky spectrum or coefficients
spectral basis components
component coefficient model
per-fiber coefficients when required
spatial coefficient model
line-specific corrections
training-fiber selection
sky-fiber mask reference
reference LSF definition
fiber-LSF model reference
pixel-integration convention
fit statistics
sampling-convergence metrics
algorithm name
algorithm version
training-data provenance
```

Large latent spectral arrays should use `float32` unless a numerical test demonstrates a need for persistent `float64`.

Per-fiber coefficients are acceptable when needed because they remain far smaller than complete predicted spectra for every fiber.

## 9.11 Production behavior

Do not persist `fiber_sky_prediction` as a complete array during normal production.

Evaluate it through the sky-model interface:

```python
sky_prediction = sky_model.evaluate(
    fiber_metadata=fiber_metadata,
    wavelength_bin_edges=wavelength_bin_edges,
    lsf_model=lsf_model,
)
```

The final sky-subtracted calibrated flux belongs in the final observation product.

It should not also be duplicated as a separate persistent intermediate artifact.

Every final observation must reference:

```text
exact sky-model artifact
exact wavelength solution
exact LSF model when applied
sky-model evaluator version
pixel-integration method
```

---

# 10. Response and normalization composition

Review whether the following are being persisted as redundant dense evaluations:

```text
final_exposure_response
baseline_relative_response
within_amp_fiber_normalization
fiber_normalization
amp_to_amp_normalization
exposure_illumination_correction
```

Where scientifically and computationally appropriate, represent the final response as a deterministic composition of compact components:

```text
final response =
    baseline relative response
    × within-amplifier fiber normalization
    × amplifier normalization
    × exposure illumination correction
```

Persist independent model components and provenance.

Do not automatically persist both:

- Every compact component
- A redundant full dense final evaluation

A dense final response may be an evictable cache if repeatedly evaluating it is demonstrated to be expensive.

This review is secondary to eliminating the large detector and science-intermediate products but belongs in the same architectural revision.

---

# 11. Production reduction versus scientific analysis

Production and analysis have different persistence requirements.

## 11.1 Production reduction

Production reduction should:

- Apply accepted calibration and physical models.
- Create temporary intermediates.
- Produce the final calibrated observation.
- Calculate compact QA measurements.
- Remove temporary arrays.

Production should not retain an intermediate merely because that intermediate might eventually be useful for investigating a model.

## 11.2 Analysis and model-development studies

A post-run analysis must be able to recreate any useful intermediate over a selected range of data.

For example, a scattered-light study may request:

```text
selected raw exposures
+ selected calibration models
+ current scattered-light model
        |
        v
temporary reduced images
temporary scattered-light evaluations
temporary residual images
aggregate residual analysis
candidate scattered-light model
```

This allows analysis of:

- Residual RMS within selected detector regions
- Inter-trace residuals
- Residuals near traces
- Detector-position dependence
- Amplifier dependence
- Illumination dependence
- Exposure-type dependence
- Time dependence
- Representative residual images
- Outlier residual images
- Candidate-model performance

Similarly, a sky-model analysis may materialize:

- Fiber-extracted spectra before sky subtraction
- Native wavelength solutions
- LSF estimates
- Predicted sky spectra
- Sky-subtraction residuals
- Line-centered residual windows
- Residual statistics over selected fibers
- Candidate latent sky models

These arrays may be retained for the bounded analysis when scientifically justified.

They should not become routine production artifacts.

## 11.3 Reuse production algorithms

Analysis must use the same calibration, model-evaluation, extraction, and sky-subtraction functions used by production.

Do not build a separate analysis implementation.

A production-compatible API may expose staged reduction state:

```python
state = reduce_to_detector_state(
    raw_exposure=raw_exposure,
    calibration_bundle=calibration_bundle,
    scattered_light_model=scattered_light_model,
)
```

Production may continue immediately:

```python
final_product = reduce_to_final_fiber_product(
    detector_state=state,
    trace_model=trace_model,
    wavelength_model=wavelength_model,
    sky_model=sky_model,
    response_model=response_model,
)
```

Analysis may inspect selected values:

```python
study.consume(
    state.reduced_image,
    state.scatter_prediction,
    state.scatter_subtracted_image,
)
```

The scientific calculation must remain identical. Only the lifecycle and retention policy should differ.

---

# 12. Analysis-study records

Introduce a first-class analysis or study record containing:

```text
study identity
scientific question
selection query
selected observations
model versions used
calibration versions used
software version
algorithm versions
intermediate kinds materialized
summary tables
plots
candidate models
validation results
retention policy
```

A study should make it possible to answer:

- Which observations were analyzed?
- Which accepted model was tested?
- Which calibration versions were used?
- How were residuals defined?
- Which detector or wavelength regions were included?
- Which samples were excluded?
- Which candidate model was produced?
- How did it compare with the current model?
- Why was it accepted or rejected?

The study record is more important than preserving an uncontrolled directory of intermediate arrays.

---

# 13. Analysis retention policies

Support analysis retention policies equivalent to:

```text
none
selected
outliers
all
until_study_completion
permanent
```

The default should favor compact analysis outputs.

For a large study, prefer retaining:

```text
per-exposure scalar metrics
per-fiber scalar metrics
binned residual statistics
sampled residual points
low-resolution residual maps
representative full-resolution examples
outlier full-resolution examples
candidate-model coefficients
validation summaries
plots
```

A full intermediate image or spectral array should be retained when it carries information not adequately represented by compact summaries.

Analysis persistence should be bounded by:

- An explicit study identity
- A selection query
- A retention policy
- An expected storage budget
- A cleanup or completion state

Analysis must not become a second uncontrolled artifact namespace.

---

# 14. Model promotion workflow

A candidate model produced by an analysis study should not immediately replace an accepted production model.

Use an explicit sequence:

```text
accepted model
    ↓
analysis study
    ↓
candidate model
    ↓
validation
    ↓
comparison with accepted model
    ↓
promotion decision
    ↓
new accepted versioned model
```

A promoted model should include:

```text
compact model representation
validity domain
training-data selection
analysis-study reference
algorithm version
performance metrics
comparison against previous model
acceptance decision
provenance
```

The scientific feedback loop should be:

```text
accepted models
      ↓
fast production reduction
      ↓
final observations and compact QA
      ↓
post-run analysis over selected data
      ↓
deliberately materialized intermediates
      ↓
candidate model
      ↓
validation and promotion
      ↓
new accepted model version
```

---

# 15. QA behavior

Routine QA should not require persistent full-resolution intermediates.

During production, calculate compact measurements such as:

```text
residual RMS
residual percentiles
masked fraction
number of valid fibers
number of failed fibers
sky-fit statistics
sky-line residual statistics
extraction statistics
response statistics
astrometric residuals
model validity checks
warning flags
failure flags
```

Small diagnostics may include:

- Downsampled images
- Thumbnails
- Selected wavelength slices
- Representative residual profiles
- Compact tables
- Histograms
- Summary plots

A QA or analysis request to inspect a reduced science image should recreate it from:

```text
raw observation
+ exact calibration references
+ exact model references
+ exact algorithm versions
```

The absence of a permanently stored reduced image must not prevent later inspection.

---

# 16. Default parallel execution

The system currently runs serially when no worker setting is supplied.

Change the default so independent tasks execute in parallel.

## 16.1 Required defaults

```text
parallel execution: enabled
nworkers: 4
```

A normal invocation with no worker-related arguments should behave as though:

```text
--nworkers 4
```

## 16.2 Configuration precedence

Use:

```text
explicit CLI option
    overrides configuration file
        overrides default value of 4
```

Support explicit serial execution through:

```text
--nworkers 1
```

and preferably also:

```text
--serial
```

`--serial` should be equivalent to `--nworkers 1`.

## 16.3 Task-graph behavior

Parallel execution must:

- Respect graph dependencies.
- Execute only ready nodes concurrently.
- Avoid executing the same task or target more than once.
- Preserve deterministic artifact identity.
- Use atomic artifact writes.
- Use worker-specific scratch paths.
- Prevent concurrent writers from corrupting shared payloads.
- Propagate failures clearly.
- Prevent dependent tasks from running after prerequisite failure.
- Include task identity in logging.
- Include worker identity in logging when useful.
- Produce the same scientific results as serial execution.

Use the repository’s existing executor if one exists.

Do not add a second unrelated parallel-execution framework solely to change the default.

## 16.4 Avoid nested oversubscription

The configured worker count should represent the primary task-level concurrency budget.

A task running inside a worker should not automatically create its own process pool and multiply total concurrency.

Either:

- Reuse a shared execution budget.
- Disable internal multiprocessing inside task workers.
- Explicitly allocate sub-workers from a global resource manager.

This matters both on local computers and shared computing systems.

## 16.5 Resource awareness

Four workers is the default, not an unlimited guarantee.

The executor should leave room for future resource-aware scheduling using task metadata such as:

```text
estimated memory
estimated CPU
estimated scratch storage
exclusive resource requirements
```

This revision does not need a complete scheduler redesign, but it should not make future resource-aware execution impossible.

---

# 17. Storage safeguards

Add automatic storage checks so uncontrolled artifact expansion cannot recur silently.

At minimum:

1. Record payload bytes for every persisted artifact.
2. Summarize artifact count and total bytes by kind.
3. Report the largest individual artifacts.
4. Report the largest artifact categories.
5. Warn when a model payload has the same dimensions as the detector it predicts.
6. Warn or fail when a scratch-only kind is written to permanent storage.
7. Verify expected dtype by artifact kind.
8. Support configurable expected maximum size by artifact kind.
9. Verify temporary files are removed after successful completion.
10. Verify caches can be deleted without losing reproducibility.
11. Preserve failed-task scratch only through an explicit debug policy.
12. Provide a cleanup mechanism for failed or abandoned runs.
13. Estimate projected artifact size before a large run when possible.
14. Warn before a run is likely to exceed available disk space.

The completion summary should include a compact table similar to:

```text
artifact kind                  count      total bytes
-----------------------------------------------------
calibrated_fiber_observation       1        2.6 GB
sky_model                          1        8.3 MB
scattered_light_model             4        1.2 MB
trace_model                      312      146.0 MB
...
```

---

# 18. Immediate artifact changes

Apply the following changes to the current stage 8 through 10 implementation:

| Current artifact | Required treatment |
|---|---|
| `ccd_scattered_light_model` | Replace dense CCD payload with compact model representation |
| `scatter_subtracted_image` | Scratch only during normal production |
| `reduced_science_image` | Scratch only during normal production |
| `aperture_extracted_spectrum` | Scratch only during normal production |
| `extracted_variance` | Keep with temporary extraction state or final product; do not persist separately |
| `fiber_sky_prediction` | Evaluate from compact sky model; do not persist |
| `sky_subtracted_spectrum` | Store only as an appropriate plane in final observation product |
| `sky_fiber_mask` | Use compact sparse or packed representation |
| `final_exposure_response` | Prefer composition from compact response components |
| Large non-astrometric arrays | Serialize as `float32` |
| Flux products | Apply the \(10^{-17}\) `BUNIT` convention |
| Astrometric quantities | Preserve as `float64` |
| Task execution | Parallel by default with `nworkers=4` |

---

# 19. Implementation sequence

Implement this revision in the following order.

## Phase 1: Inventory and contracts

1. Identify every stage 8 through 10 artifact kind.
2. Record its current:
   - Payload type
   - Shape
   - Dtype
   - Size
   - Producer
   - Consumers
3. Classify each as:
   - Canonical
   - Model
   - Analysis
   - Cache
   - Scratch
4. Identify artifacts that are only dense evaluations of another model.
5. Define the final observation-product schema.
6. Define serialization dtype and BUNIT rules.

## Phase 2: Lifecycle support

1. Add or formalize lifecycle classes.
2. Add scratch-path management.
3. Add worker-specific temporary directories.
4. Add cleanup behavior.
5. Add atomic write behavior.
6. Add payload-size accounting.

## Phase 3: Scattered-light conversion

1. Replace the detector-sized model payload with compact parameters.
2. Add a model evaluator.
3. Update science reduction to evaluate the model temporarily.
4. Update QA to consume compact residual metrics.
5. Remove permanent `ccd_scattered_light_model` images.
6. Remove permanent `scatter_subtracted_image` artifacts.

## Phase 4: Science-product boundary

1. Keep reduced detector images in scratch.
2. Keep extracted pre-sky spectra in scratch.
3. Keep extracted variance in scratch.
4. Build one final observation-level spectral product.
5. Include scientifically justified planes.
6. Attach exact model and algorithm provenance.
7. Remove obsolete intermediate artifact writes.

## Phase 5: Sky-model conversion

1. Define the latent sky-model interface.
2. Add a supersampled or continuous model representation.
3. Add evaluation at native fiber wavelength-bin boundaries.
4. Add flux-conserving pixel integration.
5. Add optional LSF-model input.
6. Add metadata describing the reference LSF.
7. Add the initial six-samples-per-narrowest-FWHM rule.
8. Add a sampling-convergence analysis.
9. Remove persistent per-fiber sky predictions.
10. Store only final sky-subtracted spectra in the observation product.

## Phase 6: Sparse masks

1. Add sparse-mask serialization.
2. Add packed-bit support where appropriate.
3. Add dense reconstruction.
4. Add round-trip tests.
5. Convert applicable existing masks.

## Phase 7: Response composition

1. Determine whether response products duplicate model components.
2. Convert redundant dense products into virtual composition or cache.
3. Preserve exact component provenance.
4. Confirm performance remains acceptable.

## Phase 8: Analysis materialization

1. Add a first-class analysis-study record.
2. Allow selected intermediate materialization.
3. Reuse production reduction functions.
4. Add bounded retention policies.
5. Add cleanup and study-completion behavior.
6. Add candidate-model creation and validation support.

## Phase 9: Parallel-default revision

1. Set the default worker count to four.
2. Enable task-level parallel execution by default.
3. Preserve serial mode.
4. Confirm configuration precedence.
5. Add worker-safe scratch paths.
6. Confirm atomic artifact writes.
7. Add parallel-equivalence tests.
8. Prevent nested oversubscription.

## Phase 10: Migration and rerun

1. Invalidate obsolete dense artifacts.
2. Preserve unaffected upstream calibrations.
3. Rerun from the earliest affected stage.
4. Compare scientific outputs against the previous implementation.
5. Confirm final observation size.
6. Confirm total storage remains bounded.
7. Delete obsolete dense files only after validation.

---

# 20. Migration strategy

Existing dense artifacts should normally be invalidated and regenerated rather than transformed in place.

The migration should:

1. Identify artifact records for removed or redefined kinds.
2. Mark those records obsolete, invalid, or superseded.
3. Remove their use as dependencies for new products.
4. Preserve upstream raw-data references.
5. Preserve valid calibration artifacts.
6. Preserve provenance needed to compare old and new runs.
7. Implement new serializers and evaluators.
8. Rerun from the first affected scattered-light or science-reduction stage.
9. Avoid regenerating unaffected upstream calibrations.
10. Compare outputs before deleting old payloads.

Do not delete the existing products until the new implementation has demonstrated:

- Scientific equivalence
- Complete provenance
- Correct final product construction
- Correct model reconstruction
- Successful bounded storage

---

# 21. Required tests

## 21.1 Dtype and units

Test that:

1. Large non-astrometric arrays serialize as `float32`.
2. Astrometric quantities remain `float64`.
3. Flux values use the \(10^{-17}\) scale.
4. Uncertainties use the same scale as flux.
5. Variances use the squared scale.
6. Serialization and deserialization preserve physical values.
7. Unit metadata remains available through artifact loading.

## 21.2 Scattered-light model

Test that:

1. The compact payload reconstructs the expected model.
2. The reconstructed detector model agrees with the previous implementation within tolerance.
3. The persisted model is substantially smaller than a CCD-sized image.
4. Production does not write a permanent evaluated model image.
5. Analysis can deliberately materialize the evaluated image.

## 21.3 Sparse masks

Test that:

1. Sparse masks round-trip exactly.
2. Bit flags are preserved.
3. Dense reconstruction is correct.
4. The serializer selects a valid encoding.
5. Algorithms remain independent of storage encoding.

## 21.4 Sky model

Test that:

1. The latent model can evaluate at arbitrary wavelengths.
2. It can integrate over native wavelength-bin boundaries.
3. Downsampling preserves integrated flux.
4. Predictions respect fiber-specific wavelength solutions.
5. A sampled spectrum from one fiber is not interpolated directly to another.
6. The sampling factor is derived from LSF and native-pixel information.
7. The sampling target is configurable.
8. Convergence tests can compare multiple latent-grid densities.
9. The interface accepts an LSF model.
10. Empirical components declare their reference resolution.
11. Strong narrow lines do not develop material positive-negative residual structure due to interpolation.
12. Production does not persist full per-fiber sky predictions.

## 21.5 Final observation product

Test that:

1. One complete observation produces one primary final artifact.
2. The artifact contains expected spectral planes.
3. The artifact contains wavelength information.
4. The artifact contains masks.
5. The artifact contains fiber metadata.
6. The artifact references exact calibration and model versions.
7. The artifact is within the expected size range.
8. Temporary intermediates are removed after success.

## 21.6 Analysis materialization

Test that:

1. Analysis can request selected detector-level intermediates.
2. Analysis uses production algorithms.
3. Retention policies are respected.
4. Study metadata records selection and model versions.
5. Candidate models retain provenance to the study.
6. Study cleanup does not affect canonical artifacts.

## 21.7 Parallel execution

Test that:

1. No explicit worker option results in four workers.
2. `--nworkers 1` produces serial execution.
3. `--serial` produces serial execution.
4. Configuration overrides the default.
5. CLI input overrides configuration.
6. Independent graph nodes execute concurrently.
7. Dependent nodes do not begin prematurely.
8. The same target is not executed twice.
9. Concurrent artifact writes are atomic.
10. Worker scratch paths do not collide.
11. Failed prerequisites block dependents.
12. Parallel and serial runs produce equivalent artifact identities.
13. Parallel and serial runs produce scientifically equivalent results.
14. Nested executors do not cause uncontrolled oversubscription.

---

# 22. Acceptance criteria

The revision is complete when all of the following are true:

```text
Large non-astrometric arrays are persisted as float32.

Astrometric quantities retain float64 where required.

Flux arrays use the 1e-17 BUNIT scaling convention.

Uncertainty and variance units are represented consistently.

No permanent reduced science CCD images are produced.

No permanent scatter-subtracted CCD images are produced.

No permanent full-CCD scattered-light evaluations are produced.

No permanent per-fiber sky-prediction arrays are produced.

No permanent pre-final extracted science spectra are produced.

The scattered-light artifact contains a compact reconstructable model.

Sparse masks use compact lossless storage.

The sky artifact contains a compact generative model.

The sky model uses a latent supersampled or continuous wavelength
representation.

The latent sky model is evaluated on the true wavelength bins of each fiber.

Downsampling from the latent sky model is flux conserving.

The initial sky-grid sampling target is approximately six latent samples
per narrowest relevant LSF FWHM.

The final sampling rule is validated through an instrument-specific
convergence study.

The sky-model API supports a fiber-dependent LSF.

Empirical sky components explicitly declare their reference resolution.

Strong narrow sky lines do not acquire material positive-negative residual
structure because of interpolation or rectification.

A complete observation produces one final calibrated fiber-spectrum product.

The complete three-dither product is approximately 2–4 GB.

Selected intermediates can be recreated in post-run analysis.

Analysis studies can retain bounded intermediate products when scientifically
justified.

Analysis uses the same reduction algorithms as production.

Candidate models can be validated and promoted with complete provenance.

Artifact size is recorded and summarized automatically.

Scratch data is cleaned after successful execution.

Caches can be removed without losing reproducibility.

The task graph runs in parallel by default.

The default worker count is four.

Explicit serial execution remains available.

Parallel and serial execution produce scientifically equivalent outputs.

The affected stages can be rerun without exhausting local disk space.
```

---

# 23. Expected implementation response

Before modifying code, inspect the current repository and return:

1. The current producers and consumers of every affected artifact.
2. The current executor and worker-configuration path.
3. The current science-product payload format.
4. The current scattered-light model representation.
5. The current sky-model representation.
6. The current mask serializers.
7. The proposed files and modules to change.
8. Any architectural mismatch between this specification and the repository.
9. A staged implementation checklist.
10. The tests that will be added or updated.

Then implement the changes in small, reviewable commits or logical patches.

Do not preserve obsolete artifact writes solely for backward compatibility unless there is a demonstrated active consumer. Remove superseded code paths once their replacements are working and tested.
