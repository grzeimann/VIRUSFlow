# VIRUSFlow Response Model Architecture

## Purpose

This document defines the architectural framework for a VIRUSFlow instrument-response system that supports three scientific applications:

1. **Scattered-light prediction and subtraction**
2. **Spectral forward modeling and inversion/extraction**
3. **Detector pixel-quality inference and masking**

The purpose is to establish the required scientific components, interfaces, dependencies, and validation path before detailed algorithm design. The architecture is intentionally **science-forward and implementation-enabling** while avoiding premature commitment to specific fitting forms, interpolation schemes, numerical solvers, or storage formats unless those choices follow directly from the physical and computational reasoning developed for VIRUS.

The central principle is to model the detected electrons as the sum of two physically distinct response scales:

$$
K_{\mathrm{total}} = K_{\mathrm{halo}} + K_{\mathrm{core}}.
$$

The **long-range halo** is modeled and subtracted as a scattered-light contribution. The remaining **compact core** is then used for pixel-quality inference and spectral extraction/inversion. This separation keeps the inverse problem local and computationally tractable while preserving a physically meaningful forward description of the detector image.

---

## Architectural Principles

### 1. Direct calibration products are first-class inputs

The response model is grounded in three empirical calibration families:

- **Master Arc**
- **Master LDLS**
- **Master Science**

These products provide complementary information and should remain explicit in the architecture rather than being hidden inside downstream algorithms.

### 2. The compact response is empirical by default

The compact monochromatic response should be represented in coordinates that are physically meaningful for spectroscopy:

$$
K_{\mathrm{core}}(\Delta\lambda, u),
$$

where

$$
u = y - T_f(\lambda)
$$

is the trace-referenced cross-dispersion coordinate in detector pixels, and $\Delta\lambda$ is the wavelength offset from a monochromatic input.

This coordinate system separates optical-response morphology from detector geometry. Trace and wavelength solutions determine how the empirical response maps back onto detector pixels.

Analytic descriptions such as Gaussian, Gauss-Hermite, or transport models may later be adopted as validated compressions of the empirical response, but they should not define the truth model a priori.

### 3. The long-range halo is treated separately from the compact core

The halo is a forward-only scattered-light contribution for normal reductions. It should be predicted from estimated total compact fiber flux and subtracted before either core inversion or pixel-quality evaluation.

This keeps the compact response operator sparse/local and avoids forcing extraction to carry an extended power-law response.

### 4. Existing robust methods remain the foundation

Trace recovery, wavelength calibration, basic extraction, detector masks, and scattered-light handling are not replaced by the response framework. They establish the reliable coordinate and calibration state required before more complex inference becomes trustworthy.

The response model may refine or use these products, but should not initially be responsible for discovering them simultaneously.

### 5. Scientific representation and runtime representation are separate

The scientifically meaningful response may be empirical and oversampled in $(\Delta\lambda,u)$. Runtime application should use a compiled, efficient detector operator derived from that response and the current trace/wavelength mapping.

The architecture therefore distinguishes:

- **calibration-space representation**: interpretable, empirical, inspectable
- **runtime operator representation**: compact, fast, pixel-integrated

### 6. Complexity is earned by residuals

The simplest scientifically adequate model is preferred. The architecture supports progressive complexity:

- fiber profile only
- fiber profile plus simple dispersion response
- compact empirical non-separable response
- more flexible field/state dependence if required

Each increase in complexity should be justified by held-out data and science-relevant residuals.

---

# Complete Feature Set

## Feature 1 — Master Calibration Products

### Purpose

Provide the direct empirical data products from which the instrument-response components are inferred and validated.

### 1.1 Master Arc

Primary roles:

- wavelength calibration support
- compact monochromatic response measurement
- isolated-line response characterization
- long-range halo/scattered-light calibration
- direct tests of spatial/wavelength response stability

The Master Arc is the cleanest source for measuring response to discrete monochromatic illumination. Where scientifically useful, the individual contributing arc exposures should remain accessible so that subpixel sampling and robust ensemble inference are not lost through premature combination.

### 1.2 Master LDLS

Primary roles:

- high-S/N empirical core fiber-profile calibration
- aperture-fraction calibration
- high-count pixel-quality evidence
- broadband validation of the halo model
- construction of a common LDLS spectral template

The LDLS operates at much higher counts than typical science observations and is therefore especially useful for high-S/N profile and detector-response characterization, while potentially being less sensitive to low-level charge-loss effects.

### 1.3 Master Science

Primary roles:

- pixel-quality evidence at normal observational count levels
- detection of low-level charge traps or count-dependent defects
- validation of core forward models under real spectral structure
- construction of appropriately grouped science spectral templates

“Master Science” should be understood as a robustly selected and grouped science ensemble appropriate to the intended inference, not necessarily one universal average of heterogeneous science observations.

### Scientific requirement

The system should preserve enough provenance to determine which direct calibration family supports each downstream inference.

---

## Feature 2 — Clean Empirical Core Fiber Profile

### Purpose

Measure the compact cross-dispersion fiber response independently of the long-range halo.

### Scientific representation

$$
P_f(u,\lambda,\mathrm{field}),
$$

with $u$ measured relative to the floating trace center.

The profile should be normalized to compact/core flux rather than to core-plus-halo flux.

### Required behavior

The profile model should:

- preserve floating-point trace phase
- use robust aggregation of many detector samples
- allow smooth profile evolution across detector position/wavelength
- remain resistant to individual detector defects
- be explicitly normalized
- support pixel-integrated aperture-fraction calculations

### N5 / N6 / N8 concept

The current architectural working geometry is:

- **N5** — nominal extraction width, with fractional contribution from two boundary detector pixels
- **N6** — trusted physical-pixel region containing all pixels that can contribute to the N5 extraction
- **N8** — wider profile reconstruction/modeling support

These are scientific working concepts, not immutable implementation constants. Their exact definitions should remain explicit and testable.

### Derived products

The profile calibration should provide enclosed compact-flux fractions such as:

$$
\eta_5(f,\lambda),\quad \eta_6(f,\lambda),\quad \eta_8(f,\lambda).
$$

These quantities are used by scattered-light prediction, extraction QA, and pixel-quality inference.

---

## Feature 3 — Total Compact-Fiber Spectrum Estimator

### Purpose

Convert the existing fast nominal extraction into an estimate of total compact fiber flux.

### Core relation

$$
F_{\mathrm{core}}(f,\lambda)
\approx
\frac{F_{5}(f,\lambda)}{\eta_5(f,\lambda)}.
$$

### Scientific motivation

A fixed five-pixel extraction does not contain the same fraction of total compact flux for every fiber or wavelength because the profile changes across the CCD and with trace phase.

Correcting the fast extraction by the empirically measured aperture fraction provides a much better source normalization for local scattered-light prediction and a useful initial spectrum for later inversion.

### Outputs

- total compact-fiber spectral estimate
- corresponding uncertainty propagation
- aperture-correction QA diagnostics

### Validation

Predicted ratios such as N5/N6 and N5/N8 should agree with directly measured aperture sums within the expected noise and model uncertainty.

---

## Feature 4 — Long-Range Halo / Scattered-Light Model

### Purpose

Predict the broad source-correlated redistribution of light and subtract it before compact-core inference.

### Initial physical hypothesis

A useful starting family is a softened power law, e.g.

$$
H(r) = \frac{A}{1 + (r/r_0)^\alpha},
$$

or an equivalent parameterization motivated by the existing VIRUS scattered-light model.

The specific functional form is an initial hypothesis, not an architectural requirement.

### Calibration strategy

The Master Arc provides the strongest direct constraint because isolated arc lines approximate monochromatic inputs. The high-S/N common five-pixel arc spectrum can constrain the projected long-range wing behavior, while the two-dimensional arc image constrains the actual detector-space halo geometry.

The Master LDLS and Master Science provide broadband validation of whether the summed halo model predicts real scattered-light structure.

### Runtime role

Given estimated total compact spectra and the trace/wavelength geometry, predict

$$
H_{\mathrm{image}}(x,y).
$$

Then define the compact-response image as

$$
D_{\mathrm{core}} = D - H_{\mathrm{image}}.
$$

### Architectural constraint

Pixel-quality inference and spectral inversion should operate on $D_{\mathrm{core}}$, not on a response operator that still contains the extended halo.

### Iterative use

Where needed:

1. perform fast initial extraction
2. aperture-correct to total compact flux
3. predict halo
4. subtract halo
5. re-extract
6. iterate only if scientifically necessary

### Open scientific questions

- Is one global halo shape adequate?
- Does shape vary by amplifier, field position, wavelength, or temperature?
- Is an outer break/truncation required?
- What fraction of apparent “continuum” in arc frames is true lamp continuum versus summed monochromatic halo response?

These are validation questions rather than architecture decisions.

---

## Feature 5 — Empirical Compact Monochromatic Core Response

### Purpose

Describe how monochromatic compact flux is distributed over wavelength-relative and trace-relative detector coordinates after long-range scatter has been separated.

### Scientific coordinate system

$$
K_{\mathrm{core}}
\left(
\Delta\lambda,
 u
 \mid
 \lambda_0,
\mathrm{field},
\mathrm{state}
\right).
$$

This representation should be empirical by default.

### Why these coordinates

Using $(\Delta\lambda,u)$:

- allows different fibers to contribute to a common response model
- separates trace location from profile morphology
- separates wavelength-solution motion from LSF shape
- permits comparison of lines at different detector locations
- naturally supports later detector projection using the local wavelength map and trace

### Initial calibration source

Useful isolated arc lines with adequate S/N and detector coverage.

### Required statistical behavior

The calibration should support robust pooling across multiple lines/fibers/exposures so that:

- one detector defect has little leverage
- weak lines can still contribute partial information
- smooth field behavior helps constrain sparsely sampled regions
- held-out lines can be used for validation

### Model complexity

The empirical response is the calibration truth model. Possible production compressions may later include:

- fiber-profile-only response
- separable profile × dispersion kernel
- transport from a canonical response
- low-rank empirical representation
- other compact parameterizations

No compression should replace the empirical truth model unless it reproduces science-relevant held-out residuals adequately.

---

## Feature 6 — Core Evaluation / Detector Operator

### Purpose

Convert the empirical core response into a fast, pixel-integrated operator for forward modeling and inversion.

### Conceptual mapping

$$
K_{\mathrm{core}}(\Delta\lambda,u)
+
\lambda_f(x)
+
T_f(\lambda)
\longrightarrow
W.
$$

The compiled operator $W$ should account for:

- local wavelength sampling
- floating trace position
- subpixel phase
- detector pixel boundaries
- compact field/state response

### Forward application

$$
F \rightarrow M_{\mathrm{core}}.
$$

### Transpose application

$$
R \rightarrow W^T R.
$$

The transpose should be implemented alongside the forward operator even if full inversion is deferred, because it preserves a path toward efficient matrix-free extraction.

### Runtime philosophy

The runtime system should not repeatedly construct or warp oversampled 2-D response images if that can be avoided. Expensive empirical calibration can be compiled into a representation optimized for repeated application.

Candidate runtime forms may include compact stencils, cached operator states, or low-rank forms, but the architecture does not prescribe one before benchmarking and accuracy validation.

### Required properties

- flux conservation
- pixel integration
- deterministic forward/transpose consistency
- efficient batching across fibers/wavelength
- explicit validity/support region
- compatibility with variance and DQ handling

---

## Feature 7 — Spectral Template and Pixel-Quality Evidence Model

### Purpose

Predict expected compact detector counts well enough to distinguish detector pathology from real spectral structure.

### Spectral templates

#### LDLS template

A common high-S/N LDLS spectral template should preserve real LDLS structure while preventing detector-coordinate defects from being absorbed into the spectral model.

#### Science template

Science prediction may require an exposure-, target-, configuration-, or ensemble-specific spectral template. It should not assume one universal science spectrum.

### Pixel-level comparison

After halo subtraction:

$$
M_{\mathrm{core}} = W F,
$$

and detector evidence may include quantities such as

$$
D_{\mathrm{core}}-M_{\mathrm{core}},
$$

$$
\frac{D_{\mathrm{core}}}{M_{\mathrm{core}}},
$$

and

$$
\frac{D_{\mathrm{core}}-M_{\mathrm{core}}}{\sigma}.
$$

No single one of these is assumed to be the definitive detector-quality statistic.

### Multi-regime evidence

The architecture explicitly supports complementary detector evidence from:

- **Arc** — monochromatic/localized illumination
- **LDLS** — high-count illumination
- **Science** — normal observational count regime

This allows detection of defects that may be count dependent, including low-level charge traps that are diluted in very bright LDLS exposures.

### Self-influence protection

A pixel being judged should have minimal leverage on the model used to predict that pixel. Robust pooling, regularization, leave-one-out-like strategies, or other techniques may be used during detailed design.

### Output philosophy

The primary product should preserve **pixel-quality evidence and confidence**, not only a binary mask. The final DQ mask can then be derived from the accumulated evidence with explicit thresholds and science requirements.

---

## Feature 8 — Scientific Validation Harness

### Purpose

Make scientific validation a formal component of the architecture rather than an end-stage activity.

Each response component should be independently testable and should earn additional complexity through measurable improvement.

### Required validation families

#### Fiber-profile validation

- N5/N6/N8 prediction versus direct measurements
- held-out fibers/columns
- center-to-corner behavior
- sensitivity to injected bad pixels

#### Halo validation

- fit on selected strong arc lines
- predict held-out lines
- predict inter-line/inter-fiber scattered-light regions
- test LDLS and science broadband residuals
- quantify any remaining continuum/additive component

#### Core-response validation

- hold out isolated arc lines
- compare empirical model to compressed/production representations
- quantify residuals by field position and wavelength
- verify robustness to detector defects and varying S/N

#### Operator validation

- compare compiled operator output with direct empirical response evaluation
- verify flux conservation
- verify forward/transpose consistency
- benchmark runtime and memory

#### Pixel-quality validation

- inject synthetic low/high response defects and charge-loss behavior
- verify detector-coordinate recovery
- ensure real spectral features are not flagged as pixel defects
- compare high-count LDLS evidence with normal-count science evidence
- assess temporal stability

#### Extraction/inversion validation

- recover known simulated spectra
- compare against existing extraction
- evaluate bias near sharp spectral features
- test missing/bad pixel behavior
- quantify uncertainty propagation

### Scientific acceptance principle

No feature should be promoted to production merely because it fits the calibration data. It should demonstrate improvement on held-out or independent data in the science quantity it is intended to support.

---

# Application Dependency Map

| Component | Scattered Light | Spectral Inversion | Pixel Quality |
|---|:---:|:---:|:---:|
| Master Arc | **Required for halo calibration** | **Required for compact response** | Supports monochromatic evidence |
| Master LDLS | **Required for profile / flux scale** | Supports profile calibration | **Required high-count evidence** |
| Master Science | Validation / optional | Validation | **Required normal-count evidence** |
| Trace geometry | **Required** | **Required** | **Required** |
| Wavelength map | **Required** | **Required** | **Required** |
| Core fiber profile | **Required** | **Required** | **Required** |
| N5→total compact correction | **Required** | Useful initialization | **Required** |
| Halo model | **Required** | Preprocessing | Preprocessing |
| Compact core response | Not required for broad halo | **Required** | Required where spectral structure matters |
| Core forward operator | No | **Required** | **Required** |
| Core transpose/inverse | No | **Required** | No |
| Spectral template | Simple source estimate | Useful | **Required** |
| Multi-exposure evidence | No | No | **Required** |
| Validation harness | **Required** | **Required** | **Required** |

---

# Recommended Implementation Sequence

The architecture should be implemented incrementally while keeping all interfaces visible from the beginning.

## Phase 1 — Formalize existing robust pieces

- define direct Master Arc / Master LDLS / Master Science interfaces
- formalize clean empirical fiber-profile calibration
- define N5/N6/N8 aperture-fraction products
- implement total compact-flux estimator from the existing fast extraction
- establish validation plots and metrics

### Scientific milestone

Demonstrate that the profile model predicts observed aperture fractions across fibers and field position without learning detector defects.

## Phase 2 — Long-range halo model

- use identified arc lines to fit the initial halo model
- compare one-dimensional projected fits with two-dimensional detector morphology
- normalize halo strength against total compact flux
- build forward scatter prediction
- validate on held-out lines and LDLS/science images

### Scientific milestone

Show that the halo model predicts broad source-correlated scattered-light structure better than the existing purely inter-fiber/background interpolation while leaving no scientifically important structured residual.

## Phase 3 — Empirical compact monochromatic response

- select usable isolated arc-line samples
- map them to $(\Delta\lambda,u)$
- build robust empirical local/field response measurements
- quantify center-to-edge/wavelength/state changes
- establish a held-out arc-line benchmark

### Scientific milestone

Determine what additional information beyond the existing fiber profile is actually required by the data.

## Phase 4 — Production core representation and operator

- test candidate compressions against the empirical benchmark
- compile the selected response into a fast detector operator
- implement forward and transpose paths
- benchmark runtime, memory, and accuracy

### Scientific milestone

Match the empirical core model to within the tolerance required by extraction and pixel-quality applications at acceptable runtime cost.

## Phase 5 — Pixel-quality inference

- construct LDLS spectral template
- define suitable master-science grouping/template strategy
- accumulate Arc + LDLS + Science detector evidence
- characterize count dependence and persistence
- derive scientifically motivated DQ criteria

### Scientific milestone

Recover known/injected detector defects while rejecting wavelength-coherent spectral structure and smooth optical-response changes.

## Phase 6 — Spectral inversion / advanced extraction

- use halo-subtracted detector images
- apply the compact forward/transpose operator
- compare against current extraction
- introduce inversion only to the level justified by science improvement

### Scientific milestone

Demonstrate reduced bias or improved recovery in cases where conventional extraction is limited, without degrading robust ordinary reductions.

---

# Data and Interface Contracts

The detailed storage format is intentionally deferred, but each product should expose scientifically explicit quantities.

## Master products

Must expose:

- detector data
- variance/error where available
- DQ/mask
- trace/wavelength association
- exposure provenance
- relevant instrument state metadata

## Fiber-profile product

Must expose:

- trace-relative profile representation
- normalization convention
- aperture fractions N5/N6/N8
- validity/coverage information
- uncertainty or quality diagnostics

## Halo product

Must expose:

- model family/version
- normalization convention relative to compact flux
- field/wavelength/state dependence if present
- validity range
- uncertainty/quality diagnostics

## Empirical core-response product

Must expose:

- $\Delta\lambda$ grid
- trace-relative $u$ grid
- normalization convention
- field/wavelength/state coordinates
- empirical response values or equivalent representation
- coverage and uncertainty

## Compiled operator

Must expose:

- forward application
- transpose application
- detector support/validity
- normalization/flux-conservation guarantees
- state/provenance linking it to the calibration products

## Pixel-quality evidence product

Should preserve, where practical:

- expected counts
- measured counts
- residual/significance metrics
- count regime
- number of independent observations
- temporal information
- final DQ classification and confidence

---

# Explicit Non-Goals at the Architectural Stage

This document intentionally does **not** prescribe:

- the final analytic form of the compact LSF
- the final analytic form of the halo
- a specific spatial interpolation basis
- a specific temperature/time interpolation law
- a fixed number of arc lines or field bins
- a fixed runtime stencil shape
- a specific inversion solver
- a specific file format or class hierarchy
- a universal master-science construction rule

These decisions belong to detailed design after the empirical calibration data establish what is scientifically required.

---

# Open Scientific Questions to Resolve During Detailed Design

1. **Arc-line sufficiency**  
   Are there enough isolated, unsaturated, high-S/N Hg/Cd lines across the detector to constrain the compact empirical response and long-range halo everywhere needed?

2. **Field dependence of the core**  
   How rapidly does the compact response change from center to edge, and what coordinates best predict that change?

3. **State dependence**  
   Which response changes are explained by trace/wavelength motion versus true profile/LSF evolution with temperature or time?

4. **Halo dimensionality**  
   Is the long-range scatter well described by one global shape with local normalization, or does its shape vary materially across field/wavelength/state?

5. **True lamp continuum**  
   After summing the modeled monochromatic halo contributions, is a separate continuum component required by the arc data?

6. **Core model complexity**  
   Is the existing empirical fiber-profile representation sufficient for smooth spectra, and what residuals specifically require dispersion-direction or non-separable 2-D structure?

7. **Master-science grouping**  
   What scientific grouping provides sufficiently coherent spectra for detector-quality inference without washing out real object-dependent features?

8. **PQ count dependence**  
   Which detector defects are multiplicative, additive, count dependent, or temporally variable?

9. **Runtime representation**  
   What compiled operator gives the best accuracy/runtime trade after the empirical response family is measured?

---

# Architectural Success Criteria

The framework is successful if it enables VIRUSFlow to answer the following questions with independently validated products:

### Scattered light

Given a measured/estimated fiber spectrum, can VIRUSFlow predict and subtract the long-range source-correlated scattered-light contribution with residuals below the level relevant for subsequent science extraction and detector-quality inference?

### Spectral inversion

Given a halo-subtracted detector image and a validated compact response operator, can VIRUSFlow predict detector counts and, where scientifically beneficial, invert that response without introducing significant bias or unacceptable computational cost?

### Pixel quality

Given Arc, LDLS, and Science evidence, can VIRUSFlow distinguish persistent detector-coordinate defects from wavelength-coherent spectral structure and smoothly varying optical response, including defects whose significance depends on illumination level?

---

# Compact Architecture Summary

```text
                  DIRECT CALIBRATION PRODUCTS

          Master Arc      Master LDLS      Master Science
              |               |                 |
        +-----+-----+     +---+--------+        |
        |           |     |            |        |
        v           v     v            v        v
   Halo Model   Core Response  Fiber   LDLS     Science
                         Profile    Template   Template
                            |
                            v
                       N5 / N6 / N8
                            |
                            v
                    Total Core Spectrum
                            |
          +-----------------+-----------------+
          |                 |                 |
          v                 v                 v
   Scatter Prediction   Core Forward      PQ Evidence
          |              Operator          Accumulation
          |                 |                 |
          v                 v                 v
   Scatter-subtracted   Spectral          Pixel Quality
         image          Inversion             Mask
```

The key scientific abstraction is:

$$
\boxed{
\text{empirical compact response in wavelength / trace-relative coordinates}
}
$$

and the key numerical abstraction is:

$$
\boxed{
\text{fast compiled detector operator derived from that response}
}.
$$

The key architectural simplification is:

$$
\boxed{
\text{predict and subtract the long-range halo first; solve the compact core second}
}.
$$

This structure allows each scientific component to be implemented and validated independently while preserving a coherent path toward a more complete physical model of the electrons recorded by VIRUS.
