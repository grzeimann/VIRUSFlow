# VIRUS Flow Scientific Models and Scientific Operating System

> **Status:** Working synthesis for scientific and architectural review  
> **Audience:** Scientists and scientific-software developers  
> **Purpose:** Consolidate the scientific canon, the proposed model set, the role of evidence and model construction, and a practical path from the current artifact/task system to a model-aware scientific operating system.

---

## 1. Executive summary

VIRUS Flow should be understood as a **scientific operating system for observational spectroscopy**.

Its purpose is not merely to execute a reduction pipeline. Its purpose is to preserve and execute the reasoning by which detector evidence becomes a defensible inference about incident astrophysical radiation.

The central relationship is:

$$\mathrm{evidence} + \mathrm{scientific\ model} + \mathrm{model\ context} \longrightarrow \mathrm{scientific\ inference}$$

The evidence records what happened. The scientific model provides a physically motivated explanation of one transformation between detector signal and incident radiation. The model context states how that explanation is being applied. The inference is the resulting estimate, together with uncertainty, validity, evidence bindings, and provenance.

The present VIRUS Flow system already contains much of the required computational substrate:

- an artifact service with immutable revisions, provenance, serialization, validity, and lifecycle;
- a task graph with explicit dependencies;
- a shared executor;
- publication and registry services;
- a distinction between retained artifacts and scratch-only intermediates;
- an analysis-materialization service.

The main missing layer is not another orchestration framework. It is an explicit scientific-model layer connecting:

1. scientific models;
2. versioned model-construction policies;
3. evidence artifacts and their roles;
4. existing task-graph operations;
5. model-dependent inference artifacts;
6. analysis, visualization, and model evaluation.

The intended architecture is therefore:

```text
scientific request
        ↓
scientific model and target context
        ↓
model construction and evidence requests
        ↓
artifact discovery and binding
        ↓
existing task graph and executor
        ↓
scientific inference
        ↓
assessment, analysis, and visualization
```

The task graph remains valuable. The artifact service remains valuable. The scientific model layer makes their scientific meaning explicit.

---

# Part I — Scientific foundation

## 2. The scientific inverse problem

A detector image is not the scientific object.

It is evidence.

The scientific objective of VIRUS Flow is to infer the incident astrophysical radiation field immediately above Earth’s atmosphere that produced the recorded detector measurements.

Every scientific model explains one physical transformation between those two boundaries.

Detector effects, optical effects, atmospheric effects, instrument response, sky emission, and observational state are progressively identified, measured, modeled, and separated until the remaining quantity represents the best-supported estimate of the incident astrophysical radiation.

The calibrated fiber is therefore not merely a reduced spectrum.

It is the best-supported estimate of the incident astrophysical radiation sampled by one fiber during one exposure, together with the evidence that justifies that inference.

> **A scientific model is not primarily a computational algorithm. It is a physically motivated explanation of one transformation between the recorded detector signal and the incident astrophysical radiation field. Measurements provide evidence. Information constrains the explanation. Predictions test it. Residuals challenge it. Uncertainty defines the limits of what can be inferred.**

The calibrated fiber is best understood as the canonical scientific **inference**, not merely the canonical data product.

---

## 3. The canonical calibrated fiber

For one fiber $f$, exposure $e$, and wavelength sample $\lambda$, the desired scientific entity is approximately

$$
\left\{
F_{f,e}(\lambda),
\ \sigma^2_{F,f,e}(\lambda),
\ M_{f,e}(\lambda),
\ \alpha_f,\delta_f,
\ \mathbf{x}_{f,e},
\ \mathcal{P}_{f,e,\lambda}
\right\},
$$

where:

- $F_{f,e}(\lambda)$ is the flux-calibrated, sky-subtracted fiber spectrum;
- $\sigma^2_F$ is its practical marginal variance;
- $M$ is the mask and compact validity state;
- $(\alpha_f,\delta_f)$ is the sky position;
- $\mathbf{x}_{f,e}$ is the focal-plane or instrumental position;
- $\mathcal{P}$ is the provenance chain of measurements, models, constructions, calibrations, and configuration.

The forward detector-space measurement can be written schematically as

$$D_{a,e}(x,y) = B_a(x,y) + D^{\mathrm{dark}}_{a,e}(x,y) + C_{a,e}(x,y) + S^{\mathrm{scat}}_{p,e}(x,y) + \sum_f P_{f,a}(x,y) \left[ G_{f,e}(\lambda) + K_{f,e}(\lambda) \right] + \epsilon_{a,e}(x,y).$$

After detector correction and extraction,

$$c_{f,e}(u) \approx R_{f,e}(\lambda(u)) \left[ G_{f,e}(\lambda(u)) + K_{f,e}(\lambda(u)) \right] + r^{\mathrm{inst}}_{f,e}(u).$$

The desired calibrated fiber is then schematically

$$ \widehat{F}_{f,e}(\lambda) = \frac{ c_{f,e}(\lambda)- \widehat{K}_{f,e}(\lambda)}{ \widehat{R}_{f,e}(\lambda)}.$$

The scientific model catalog and the implementation architecture should be organized around the physical transformations required to move from the detector equation to this inference.

---

## 4. The common scientific derivation

Every scientific model should be described through the same reasoning:

```text
Physical reality
        ↓
Physical quantity
        ↓
Reference boundary and convention
        ↓
Measurements
        ↓
Available information
        ↓
Scientific model
        ↓
Model construction
        ↓
Inference
        ↓
Prediction
        ↓
Scientifically defined comparison
        ↓
Residual or discrepancy
        ↓
Uncertainty and validity
        ↓
Propagation into downstream inferences
```

### Physical quantity, measurement, and information are not synonyms

- **Physical quantity:** what exists in nature or the instrument.
- **Measurement:** how that quantity interacts with a detector, calibration source, catalog, or observing apparatus.
- **Information:** relationships and constraints extracted from measurements and physical knowledge.
- **Scientific model:** a coherent representation of the physical quantity using that information.
- **Inference:** the model-dependent estimate obtained for a particular target and context.

### Evidence and inference have different histories

> **Evidence belongs to the history of the observation. Inference belongs to the history of the model.**

A measured arc centroid does not change when the wavelength model changes. The wavelength inference can change because the model, construction, or context changes.

### Evidence becomes information through model binding

> **Evidence becomes information only through an explicit, versioned binding to a scientific model and its context.**

The same artifact may play different roles in different constructions.

---

# Part II — Canon-aligned scientific model set

## 5. Defining the model set

The model set follows the physical inverse path rather than current task names or artifact boundaries.

Each model represents a stable physical quantity or transformation. Its implementation may evolve without redefining its scientific meaning.

The final calibrated fiber is a composition of these model inferences, not simply another peer model.

---

## 6. Detector and electronic models

### 6.1 Detector coordinate and electronic conversion model

```text
raw amplifier coordinates and ADU
        ↓
canonical detector coordinates and detector charge/count units
```

This model owns amplifier orientation, reflections, coordinate conventions, amplifier identity, gain or ADU-to-electron conversion, and stable detector-coordinate transformations.

### 6.2 Bias and overscan model

Represents the electronic baseline added during detector readout and predicts its contribution at each detector location.

### 6.3 Dark-current model

Represents thermally generated charge accumulated during an exposure. Its construction may depend on detector location, exposure duration, temperature, time, and long-term detector behavior.

### 6.4 Transient detector-contamination model

Represents transient charge not produced through the intended optical path, including cosmic rays. Its inference may be a contamination estimate, probability, mask, rejection state, or replacement decision.

### 6.5 Scattered-light and detector-background model

Represents incident or redistributed light on the physical CCD that is not correctly attributed to the nominal fiber profiles. It remains distinct from atmospheric sky.

---

## 7. Fiber-association and spectral-coordinate models

### 7.1 Trace geometry model

```text
physical fiber identity and detector dispersion coordinate
        ↓
detector cross-dispersion position
```

This model represents the trajectory of each physical fiber across the detector.

Construction-policy dimensions include admissible trace measurements, coordinate convention, functional representation, robust loss, weighting, neighboring-fiber constraints, interpolation and extrapolation, uncertainty, and validity.

Huber regression is one construction policy for this model, not the model itself.

### 7.2 Fiber extraction-profile model

```text
detector charge around a known trace
        ↓
signal attributed to one physical fiber
```

This model represents how light from a fiber is distributed across detector pixels.

Construction-policy dimensions include aperture versus profile extraction, profile family, cross-fiber overlap, bad-pixel treatment, covariance, extraction support, and wavelength dependence.

### 7.3 Wavelength geometry and state model

```text
detector dispersion coordinate
        ↓
physical wavelength
```

Admissible evidence includes measured line centroids in detector pixels, laboratory wavelengths, line identities, trace geometry, time, instrument ZIP code, environmental state, historical wavelength inferences, and science-frame validation features.

The model must distinguish:

1. wavelength geometry at calibration states;
2. wavelength state inferred for a science-exposure context.

The ordinary arc-fit residual is an included-line detector-pixel residual at the arc state. It does not by itself measure science-time wavelength uncertainty.

---

## 8. Additive and multiplicative signal models

### 8.1 Sky-contribution model

Represents radiation entering a fiber from atmospheric or other sky contribution rather than the target source.

### 8.2 Relative detector and fiber response model

Represents relative multiplicative differences within the instrument, including amplifier normalization, fiber-to-fiber response, within-amplifier response, and wavelength-dependent relative response.

### 8.3 Illumination model

Represents focal-plane, mirror, and telescope-state illumination. It explains why nominally identical incident radiation does not illuminate all fibers equally.

### 8.4 Throughput and spectrophotometric response model

```text
above-atmosphere astrophysical radiation
        ↓
instrument-level extracted signal
```

Evidence can include spectrophotometric standards, external catalog spectra, atmospheric context, instrument response history, source reconstruction, and aperture or total-flux conventions.

This model must remain distinguishable from source-reconstruction, DAR, PSF, centering, and aperture-correction errors.

---

## 9. Position and spatial models

### 9.1 Fiber focal-plane geometry model

```text
physical fiber identity
        ↓
focal-plane or instrumental position
```

It owns fiber identity and ordering, IFU geometry, amplifier/fiber mapping, focal-plane offsets, reversals, and coordinate corrections.

### 9.2 Astrometric and dither-registration model

```text
focal-plane position
        ↓
reference-wavelength sky position
```

It includes the exposure astrometric solution, dither registration, coordinate projection, system rotation, residual evidence, and positional uncertainty.

### 9.3 Differential atmospheric refraction model

```text
above-atmosphere source direction
+
atmospheric and observing state
        ↓
apparent source position as a function of wavelength
```

DAR is distinct from astrometry because it describes the wavelength-dependent displacement of a source during an exposure.

It can be constructed at exposure level and calibrated at population level using residuals from many exposures.

### 9.4 Spatial PSF model

Represents the wavelength-dependent spatial response of the observation.

It may include source centroid, width, shape, wavelength dependence, focal-plane dependence, exposure dependence, and fiber-footprint integration.

It is informed by calibrated-fiber spectra and consumed by source reconstruction.

### 9.5 Source reconstruction and spatial extraction model

```text
calibrated fiber inferences
+
fiber geometry
+
astrometry
+
DAR
+
spatial PSF
+
source assumptions
        ↓
source-level spectrum
```

Fiber extraction asks which detector signal belongs to a physical fiber.

Source reconstruction asks what source-level radiation field most plausibly produced a collection of calibrated-fiber measurements.

Its bounded construction space includes source morphology, aperture versus forward modeling, PSF and DAR treatment, fiber-footprint integration, aperture correction, dithers, repeated exposures, missing fibers, covariance, and background separation.

The result is a **Source Spectrum Inference**.

This model is essential for spectrophotometric calibration because external standard catalogs describe source-level spectra rather than individual fibers.

---

## 10. Horizontal inference systems

Uncertainty and validity are cross-cutting companions to every model inference.

### 10.1 Inference uncertainty system

The uncertainty system answers:

> How uncertain is this inferred quantity, what are the sources and structure of that uncertainty, and what ignorance remains unquantified?

It may include:

- measurement uncertainty;
- model-parameter uncertainty;
- context-transfer uncertainty;
- shared or correlated uncertainty;
- empirical uncertainty calibration;
- bounded but unquantified uncertainty.

Every model should state which uncertainty entered, what uncertainty it added, how it was propagated, which correlations remain, and what is not yet quantified.

The final variance array is a practical marginal interface, not the complete uncertainty representation.

### 10.2 Inference validity system

The validity system answers:

> Is this inference scientifically applicable and usable in this context, and if not, why not?

Validity includes:

- evidence sufficiency;
- domain validity;
- identity and compatibility;
- measurement validity;
- construction validity;
- scientific-use validity.

Useful states include:

```text
supported
limited
degraded
outside_supported_domain
unavailable
```

The final mask is a compact interface to a richer validity record.

---

## 11. Canonical inference levels

### 11.1 Calibrated Fiber Inference

The best-supported estimate of above-atmosphere radiation sampled by one fiber in one exposure, together with uncertainty, validity, position, and evidence/model provenance.

### 11.2 Source Spectrum Inference

The best-supported estimate of above-atmosphere radiation from one source, reconstructed from calibrated-fiber inferences under explicit spatial, astrometric, PSF, and DAR models.

The source-level inference preserves the calibrated fibers as atomic evidence.

---

# Part III — Model construction, policy, algorithms, and tasks

## 12. Why construction and algorithm currently appear blended

Many current tasks simultaneously:

1. gather artifacts;
2. assign scientific roles;
3. select measurements;
4. choose a representation;
5. apply modeling policies;
6. execute numerical algorithms;
7. determine validity and fallback behavior;
8. publish outputs.

The task therefore acts as an implicit model constructor.

### Scientific model

The stable physical quantity and transformation.

### Model construction

The complete scientific policy for turning admissible evidence into a realization of the model.

### Scientific operator

A reusable operation such as robust regression, interpolation, alignment, grouping, projection, or uncertainty propagation.

### Algorithm implementation

The numerical code executing an operator.

### Task

The execution unit that resolves inputs, runs construction operations, and publishes outputs.

```text
ScientificModel
        ↓ realized by
ModelConstruction
        ↓ composed from
ScientificOperators
        ↓ executed by
Algorithms within Tasks
        ↓ producing
Model-dependent artifacts and inferences
```

---

## 13. Huber regression as a trace-construction policy

Huber regression illustrates the distinction.

The numerical algorithm minimizes a Huber objective.

The scientific policy assumes that most trace-position measurements represent the physical trajectory, while some deviations are more plausibly contaminated measurements than genuine fiber excursions, so their influence should be bounded.

The construction includes more than the solver:

- admissible measurements;
- representation;
- complexity;
- weighting;
- Huber transition scale;
- neighboring-fiber constraints;
- gap treatment;
- interpolation and extrapolation;
- validity;
- uncertainty estimation.

---

## 14. Versioned policy bundles

The existing configuration system already provides a versioned policy substrate through:

```text
kind
version
value
evidence state
source
```

Current examples include orientation, CCD transforms, gain and read-noise fallbacks, fiber geometry, astrometric projection, response baseline, wavelength masking, extraction width, and spectral-mask behavior.

Scientifically, these are not all the same. They include:

- instrument invariants;
- geometric knowledge;
- construction policies;
- fallback assumptions;
- operational policies.

A model construction should assemble a coherent versioned bundle:

```text
TraceConstructionDefinition

measurement policy
coordinate policy
association policy
representation policy
robust-fit policy
weighting policy
domain policy
uncertainty policy
validity policy
```

The task may initially remain computationally unchanged. It receives and records the explicit construction bundle that it already implements implicitly.

---

## 15. Bounded construction phase space

Each scientific model defines a limited phase space through:

- physical quantity;
- input and output boundaries;
- admissible direct measurements;
- admissible supporting information;
- upstream model relationships;
- natural time and ZIP support;
- prediction interfaces;
- unit and coordinate rules;
- uncertainty and validity meaning;
- downstream propagation.

This creates a rich but bounded scientific palette.

Recurring construction forms may include direct construction, transfer from related contexts, population construction, upstream composition, environment-conditioned construction, and hierarchical construction across related ZIP scopes.

These forms should be reusable without becoming a rigid menu.

---

# Part IV — Evidence, artifacts, and inference

## 16. Artifact classes

The artifact service can retain:

### Observation-evidence artifacts

Raw detector images, exposure metadata, telemetry, and hardware state.

### Measurement artifacts

Arc-line centroids, trace peaks, extracted counts, standard-star measurements, and sky-line positions.

### Model-representation artifacts

Wavelength coefficients, trace parameterizations, temporal or temperature-response parameters, sky bases, response curves, PSF representations, and DAR population representations.

### Target-context inference artifacts

Science-time wavelength state, predicted sky contribution, exposure DAR, calibrated fibers, and source spectra.

### Assessment or study artifacts

Arc-fit comparisons, inter-arc evolution, repeat consistency, uncertainty calibration, and construction comparisons.

---

## 17. Artifact roles are relational

An artifact has an intrinsic kind and a contextual role.

A trace map can be:

- the inference of the trace model;
- an upstream coordinate constraint for extraction;
- supporting information for wavelength calibration.

Preserve:

```text
intrinsic artifact kind
+
role in this model construction
```

Possible roles include:

```text
direct measurement
laboratory reference
preceding temporal boundary
following temporal boundary
temperature constraint
upstream trace inference
historical comparison
assessment evidence
```

---

## 18. Evidence requests and bindings

A construction should not call an unexplained generic “best artifact” lookup.

```text
Model construction asks for evidence
        ↓
Artifact service returns deterministic candidates
        ↓
Construction assigns roles and selects evidence
        ↓
Task graph executes the construction
```

### Evidence request

States the artifact or measurement vocabulary, time relation, ZIP relationship, compatibility, quantity, cardinality, and scientific purpose.

### Candidate set

Records what the artifact service found.

### Evidence binding

Records exact selected revisions, roles, selection reasons, time and ZIP relationships, compatibility, rejected candidates, missing evidence, and limitations.

The artifact service discovers and retrieves.

The model construction interprets and creates.

---

## 19. Inference manifest

Every retained model-dependent result should carry or reference:

```text
scientific model
model revision
construction revision
target context
evidence requests
candidate artifacts
selected artifacts and roles
upstream inferences
policy revisions
task-graph execution
implementation revision
assumptions
uncertainty status
validity state and reasons
limitations
output artifact references
```

This converts a task output into an inspectable scientific inference.

---

# Part V — The scientific operating system

## 20. Definition

> **VIRUS Flow is a scientific operating system for observational spectroscopy. It maintains the identities and histories of evidence, scientific models, constructions, and inferences; resolves model-dependent evidence through time and instrument ZIP; executes scientific derivations through a task graph; retains model representations and selected inferences as immutable artifacts; and provides a common substrate for production, evaluation, analysis, and visualization.**

$$\mathrm{evidence} + \mathrm{scientific\ model} + \mathrm{construction} + \mathrm{context} \longrightarrow \mathrm{inference}$$

---

## 21. Operating-system analogy

### Scientific kernel

Scientific identity, artifact revision, model and construction identity, evidence roles, time and ZIP context, dependency resolution, validity, uncertainty, provenance, and lifecycle.

### Scientific memory

The artifact service retains observation, measurement, model-representation, inference, and reasoning memory.

### Scientific scheduler

The task graph and executor handle dependencies, parallelism, retries, failures, timing, publication, and caching.

### Scientific processes

Operations such as infer wavelength state, construct trace geometry, predict sky contribution, reconstruct a source spectrum, and study an inference.

### Scientific system calls

Stable requests such as:

```text
infer
predict
gather evidence
explain
materialize
study
```

### Scientific user space

Notebooks, visualization, model comparison, residual studies, and population calibration operate above retained guarantees of identity and reproducibility.

---

## 22. Mechanism versus scientific policy

### Shared mechanisms

Artifact lookup, immutable revisions, component loading, serialization, caching, scheduling, publication, lifecycle, and provenance mechanics.

### Scientific policy

What evidence is meaningful, how it is bound, which construction is used, whether transfer is physically justified, how uncertainty is treated, what validity follows, and what inference is produced.

> **The artifact service and task graph provide mechanism. Scientific models and constructions provide policy.**

---

## 23. Demand-driven inference

A stable logical inference need not always exist as a dense persisted artifact.

```text
exact retained inference
        ↓ if absent
cheap evaluation from retained representation
        ↓
composition from existing evidence
        ↓
task-graph construction of missing dependencies
```

The model defines what scientifically satisfies the request. The operating system chooses the cheapest valid execution path.

---

## 24. Compression and speed

### Semantic compression

Request a physical inference rather than manually assembling tasks and files.

### Storage compression

Use immutable references, structural sharing, content identity, copy-on-write inference histories, lazy evaluation, and selective materialization.

### Execution compression

Use exact inference caching, model-aware memoization, precise invalidation, incremental recomputation, plan-informed prefetching, time-and-ZIP working sets, and scheduling for shared data locality.

---

# Part VI — Mapping onto the current system

## 25. Current computational spine

```text
CLI/configuration
        ↓
Target and registry selection
        ↓
ReductionGraph.plan
        ↓
planning.schedule
        ↓
PlanningExecutor
        ↓
Task
        ↓
storage-neutral algorithm
        ↓
ArtifactRequest
        ↓
DefaultPublicationService
        ↓
ArtifactService.persist_request
        ↓
serializer and registry
```

The model layer should be inserted into this path rather than replace it.

---

## 26. Proposed refined path

```text
Scientific request
        ↓
ScientificModel + target context
        ↓
ModelConstructionDefinition
        ↓
Evidence requests
        ↓
ArtifactService candidate resolution
        ↓
Evidence bindings and role assignments
        ↓
ReductionGraph.plan / planning.schedule
        ↓
PlanningExecutor
        ↓
Tasks implementing construction operations
        ↓
ArtifactRequest + InferenceManifest
        ↓
Publication and registry
        ↓
Inference, explanation, and study access
```

Initially, the existing task graph can remain static. It should first be annotated and validated against explicit model and construction contracts.

---

## 27. Task-to-model registration

Every scientific task should declare:

```text
scientific model
model operation
construction identity or policy bundle
input artifact roles
output artifact kind
output epistemic role
```

The task remains the execution unit.

The model construction becomes the explicit scientific meaning of the task or task subgraph.

---

## 28. Analysis and visualization

The system should provide a scientifically constrained palette rather than a fixed menu of residuals.

### Model-defined primitives

Predictions, measurement adapters, units and coordinates, evidence vocabularies, uncertainty structures, and valid comparison spaces.

### Shared study operators

Gather, align, compare, calculate signed differences, group, project, estimate robust location and scale, analyze coherence, analyze trends, calibrate uncertainty, compare constructions, and visualize.

### Scientific studies

A study is a versioned, composable program that can:

```text
gather evidence
request or construct inferences
make predictions
form comparisons
analyze
visualize
```

The model defines the physics. The study asks the question. The operating system preserves and executes the reasoning.

---

# Part VII — Minimum implementation

## 29. Requirements for an 80–90% operating-system implementation

The goal is not scientific completeness. It is model-aware, reproducible, demand-driven, and inspectable behavior.

### 29.1 Explicit scientific model definitions

For each model, define physical quantity, boundaries, admissible evidence, time and ZIP support, predictions, relationships, uncertainty, validity, and construction-policy dimensions.

### 29.2 Versioned construction definitions

Assemble the policies already ingested by tasks and identify evidence vocabulary, scientific operators, dependency structure, and output inference.

### 29.3 Model context

At minimum: target time, target ZIP, purpose, model revision, construction revision, upstream revisions, assumptions, and materialization policy.

### 29.4 Evidence request and role-aware binding

Replace or wrap unexplained “best artifact” calls with explicit requests and inspectable bindings.

### 29.5 Task registration

Every scientific task identifies the model operation and construction it executes.

### 29.6 Inference manifests

Every model-dependent artifact explains how it was produced.

### 29.7 Stable model requests

Downstream work requests a scientific inference by context rather than a particular task or filename.

### 29.8 Demand-driven resolution

Use exact inference, cheap evaluation, composition from retained evidence, then task-graph construction.

### 29.9 Scientific cache identity

Include model revision, construction revision, target context, evidence bindings, upstream revisions, policy values, and implementation revision.

### 29.10 Explanation access

Every inference reports model, construction, evidence roles, assumptions, execution, uncertainty, validity, and limitations.

### 29.11 Composable studies

At least one study path can gather evidence and inferences through model interfaces, form a comparison, analyze it, and materialize or visualize the result.

---

## 30. What can wait

The first implementation does not require:

- automatic generation of the complete task graph;
- a universal model base class;
- complete covariance propagation;
- every scientifically meaningful residual;
- a universal query language;
- one global coordinate system;
- conversion of every task;
- distributed execution;
- automatic model promotion;
- a universal plotting framework.

---

# Part VIII — Recommended sequence

## 31. Stage 1: inventory and annotation

Without changing behavior:

1. map each scientific task to a model;
2. identify embedded construction policies;
3. classify current artifacts;
4. assign contextual artifact roles;
5. identify hidden “best artifact” assumptions.

## 32. Stage 2: trace vertical slice

Define the trace model, explicit trace construction bundle, evidence binding, task registration, inference manifest, and explanation output.

Do not initially change the Huber implementation.

## 33. Stage 3: wavelength vertical slice

Implement time and ZIP access, calibration-state and science-time inference, environmental evidence, target-context inference, and detector-pixel evaluation.

## 34. Stage 4: calibrated-fiber composition

Make the final artifact reference exact upstream model inferences, construction revisions, evidence bindings, uncertainty contributions, validity states, and task execution.

## 35. Stage 5: source reconstruction, PSF, DAR, and response loop

Make the loop explicit:

```text
preliminary response
        ↓
calibrated fibers
        ↓
PSF + DAR + source reconstruction
        ↓
comparison with standard catalog
        ↓
improved spectrophotometric response
```

Retain iteration identity and avoid hidden circular reasoning.

## 36. Stage 6: composable scientific study

Implement one end-to-end study that gathers model evidence and inferences, constructs a science-facing comparison, analyzes it by time and ZIP, visualizes coherent structure, and retains the study definition and result.

---

# Part IX — Review questions

## 37. Scientific boundaries

1. Should detector orientation and gain remain one family or split?
2. Should relative response, illumination, and absolute throughput remain separate?
3. Which response components vary by exposure, night, ZIP, or long-term epoch?
4. What is the exact reference boundary of each intermediate inference?

## 38. Construction policy

1. Which defaults are instrument invariants?
2. Which are scientific construction policies?
3. Which are missing-evidence fallbacks?
4. Which policy changes require a new construction revision?
5. Which numerical changes require only an implementation revision?

## 39. Evidence and artifacts

1. Which artifacts are direct evidence, measurements, model representations, or target-context inferences?
2. Which task inputs arrive through implicit selection?
3. Which roles must be preserved?
4. Which dense inferences should remain virtual?

## 40. Uncertainty and validity

1. Which models already provide formal uncertainty?
2. Which provide only residual evidence?
3. Which uncertainties are shared across fibers or wavelength?
4. Which validity states and reasons are necessary?
5. Which inferences are acceptable for one use but not another?

## 41. Analysis and visualization

1. What are the minimum prediction interfaces?
2. Which measurement-space comparisons are legitimate?
3. What time and ZIP dimensions support fast gathering?
4. Which recurring studies should be promoted?
5. Which views become generic once the scientific comparison is explicit?

---

# 42. Closing principles

> **The calibrated fiber is the canonical scientific inference.**

> **Evidence belongs to the history of the observation. Inference belongs to the history of the model.**

> **Evidence becomes information only through an explicit, versioned binding to a scientific model and its context.**

> **The model defines the physics. The construction defines how admissible evidence is used. Tasks execute the construction. The artifact service preserves evidence, representations, and inferences. Scientific studies interrogate and improve them.**

> **The task graph should become the executable derivation of scientific inference rather than the hidden location of scientific assumptions.**

VIRUS Flow already contains much of the machinery required for this scientific operating system.

The next phase is not to replace that machinery. It is to give it explicit scientific identity, model ownership, construction policy, evidence roles, and inference manifests so that production, evaluation, analysis, and visualization become different views of the same preserved scientific reasoning.

---

## Source documents used in this synthesis

- *VIRUS Flow Scientific Canon: The Calibrated Fiber*
- *VIRUS Flow Scientific Model Catalog (Version 1.0 Philosophical Draft)*
- *Current implementation flow*
- Current versioned policy and configuration defaults
- The scientific and architectural discussion leading to this synthesis
