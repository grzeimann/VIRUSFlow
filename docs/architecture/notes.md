# What we have

Three things underpin VIRUSFlow: a raw database fast enough to treat "opening a
FITS file" as a non-event, an artifact warehouse that lets us select the right
scientific evidence for an exposure without ever touching a payload, and a
canonical forward-model equation that ties every calibration measurement and
every science exposure together into one consistent inverse problem. Together
they form a scientific knowledge system: observations become durable
measurement evidence; flexible, versioned model sets describe how that evidence
constrains the terms of the equation; and those models are evaluated to produce
the calibration state needed for a particular exposure. This note records what
each part is, why it matters to a working scientist, and how we go from raw
electrons on the detector to the calibrated source and sky spectra we actually
care about. It is deliberately focused rather than exhaustive.

## The raw database: making 300-file-per-exposure I/O disappear

VIRUS delivers roughly 300 individual amplifier FITS files per exposure,
physically packed into nested tar archives: HET/Corral ships a single
`YYYYMMDD.tar` ("date-tar") containing one tar per observation, and each of
those contains the ~300 per-amplifier FITS frames. Naively reading any one
frame out of that structure means asking Python's `tarfile` module to walk its
member list looking for a name match — an O(n) scan per file, so scanning or
re-reading an exposure is effectively quadratic in the number of amplifiers.
At VIRUS data volumes that quadratic cost was the single largest source of
pipeline latency.

We removed it by treating "where is this FITS frame in the tar" as something
you compute once and cache forever, rather than something you ask `tarfile`
every time. A dedicated SQLite catalog (`virusflow_raw.sqlite3`, physically
separate from the mutable artifact/provenance database) records, for every
raw file we've ever seen:

- which exposure it belongs to and its header-derived scientific metadata
  (object, program, exposure time, pointing, temperature, humidity, ...),
  captured once at scan time so later queries never need to reopen the file;
- which amplifier it came from, keyed by a stable "ZipCode" (ifuslot / ifuid /
  specid / amp / controller);
- which storage backend holds it (plain filesystem, a flat tar, or a nested
  date-tar), and, for the two tar cases, the exact byte `(offset, size)` of
  that member inside the archive.

That offset index is the whole trick. We scan each tar or nested tar exactly
once, record every member's offset, and validate the cached index against the
archive's `mtime`/`size` so a changed file on disk automatically invalidates
and rebuilds its index rather than silently going stale. After that first
scan, reading any frame — even one buried two tar levels deep — is a direct
`seek()` to a known byte position followed by a read of exactly the right
number of bytes. Header-only reads (used heavily during cataloging) go
further still: we `seek` to the offset and parse only the FITS header in
place, never touching the pixel data at all.

On top of the offset index sits a runtime loader that keeps one open file
handle per archive (instead of repeatedly opening and closing tars), adds an
in-process, byte-bounded LRU cache so a frame requested twice within a run is
only physically read once, and uses single-flight locking so concurrent
requests for the same frame wait on one read rather than duplicating it. The
net effect, measured on real pipeline runs, was roughly a 99.996% reduction in
raw member-lookup time (17.9 task-seconds down to well under a millisecond)
and a ~98% reduction in raw-read p95 latency. Practically: cataloging or
re-touching a night's worth of data went from "wait for coffee" to
"instantaneous," which is what makes it feasible to re-scan and reprocess
aggressively rather than trying to avoid it.

**Why this matters scientifically:** every calibration step below depends on
being able to cheaply pull an arbitrary raw frame — a bias here, an arc there,
a twilight flat from three nights ago — without paying a tar-scan tax each
time. Fast raw I/O is what makes it affordable to build calibration products
from *many* raw exposures instead of settling for whatever's most convenient
to open.

## The artifact warehouse: selecting without loading

Everything the pipeline produces — master calibration frames, derived maps,
QA diagnostics, and final reduced science products — is an "artifact." An
artifact carries a `kind` (`master_bias`, `master_arc`, `trace_map`,
`wavelength_map`, `fiber_response_model`, `calibrated_fiber_observation`,
`extracted_master_sci_spectrum`, `source_detection_catalog`, and so on), a
scope (which amplifier, exposure, observation, or dither set it applies to),
scientific metadata (ambient conditions, program, pointing), full provenance
(what algorithm and parameters produced it, and from which parent artifacts),
a validity window, a lifecycle state, and one or more named components, each
of which points at an actual file on disk.

The warehouse keeps all of that metadata in its own indexed SQLite database,
deliberately decoupled from the payload files themselves. The critical design
choice is that *selecting* an artifact never requires reading its payload.
Queries like "give me every trace map for this amplifier taken within an
hour of this exposure" or "what's the current best master arc for this
scope" run entirely as SQL against indexed metadata tables — no FITS file is
opened, no array is deserialized, no checksum is computed. Only when a task
actually needs the array data does it call into the loading path, which looks
up the component's storage path and format, hands it to the appropriate
serializer, and reads the bytes off disk — optionally verifying a SHA-256
checksum at that point.

This split (`find_artifacts` / `select_best` for free metadata queries, versus
`load_component` / `load_payload` for the one place real I/O happens) is what
"lazy loading for selection" means in practice: a scheduler can walk
provenance chains, resolve the correct calibration Product for thousands of
exposures, and check QA status across a whole run, all without the cost
scaling with payload size. Instrumentation on real runs shows this path is
cheap relative to raw I/O — on the order of a task-second or two total for
serialization, hashing, and publication combined across a representative
benchmark run.

**Why this matters scientifically:** calibration selection is a search
problem — "which master bias, illumination measurement, trace evidence, or
validated response state applies to *this* exposure, given its time, amplifier,
and conditions?" — and that search has to run fast and correctly at the scale
of a full survey. Separating "what evidence or evaluated state applies here"
(metadata) from "read the arrays" (payload) is what lets us ask that question
cheaply and often, including as new calibration data arrives and provenance
chains grow.

## Evidence, models, and evaluated calibration states

The artifact warehouse is primarily the durable evidence base. It preserves
what was actually measured or reproducibly summarized from observations:
master-bias images, extracted calibration spectra, averaged IFU illumination,
fiber-binned trace positions, measured arc centroids, inter-fiber
scattered-light samples, detector-state summaries, and validation residuals.
Each of these artifacts states what was measured, from which observations, by
which procedure, and with what uncertainty or QA. It does not need to claim to
be the final or unique description of the physical component that produced the
measurement.

Models sit above that evidence as flexible, versioned sets describing how one
or more terms in the canonical equation should be inferred, interpolated,
predicted, or coupled. A model set may contain a static reference, a spline or
basis expansion, a time-dependent relation, fitted coefficients, linked
component models, selection and combination rules, physical constraints,
applicability logic, and references to the evidence that supports it. It is not
necessarily one rectangular payload or one artifact kind. The important
requirements are that the model set is identifiable, versioned, reproducible,
and explicit about its evidence, assumptions, parameters, and prediction
method.

When a calibration build or science exposure is processed, the relevant model
set is evaluated using the applicable evidence and observing state. That
evaluation produces a concrete calibration state: for example, a bias image,
trace map, wavelength map, scattered-light prediction, fiber-response array,
throughput curve, atmospheric-transmission curve, or PSF/DAR description. Such
evaluated states may be retained as artifacts when reuse, exact provenance, or
reproducibility makes that valuable. They are predictions made by a model from
evidence, however, rather than the entirety of the model itself.

This distinction allows the evidence to remain durable while the models remain
scientifically revisable. A later trace model can reinterpret the same
fiber-binned trace positions; a later illumination model can reuse the same
averaged IFU measurements; and a later bias model can combine the same master
biases with overscan, temperature, or time dependence without rewriting the
observations from which those measurements were derived.

## The canonical equation

Everything the pipeline measures and every artifact it computes exists to
either provide evidence about a term, define how that evidence is modeled, produce an evaluated calibration state, or ultimately invert this equation:

$$\begin{align} D_{e,p} &= B_{e,p} + t_e d_p + L_{e,p} + t_e \sum_f P_{e,f,p}(\lambda_p)\, R^{\mathrm{pixel}}_{p}\, R^{\mathrm{fiber}}_{e,f}(\lambda_p)\, R^{\mathrm{amp}}_{e,a}\, T^{\mathrm{instrument}}_e(\lambda_p) \left[ \int_{\Omega} C_{e,f}(\theta,\lambda_p)\, T^{\mathrm{atmosphere}}_e(\theta,\lambda_p)\, S^{\mathrm{source}}_e(\theta,\lambda_p)\, d{\theta} + F^{\mathrm{sky}}_{e,f}(\lambda_p) \right] + N_{e,p}, \\ C_{e,f}(\theta,\lambda) &= \int_{\mathcal{A}_f} \mathrm{PSF}_e\!\left[ \mathbf{x} - \mathcal{M}_e\!\left( \theta + \Delta{\theta}^{\mathrm{DAR}}_e(\lambda) \right), \lambda \right] \,d\mathbf{x}. \end{align}$$

- $D_{e,p}$: measured value, in electrons, at detector pixel $p$ in exposure $e$.
- $B_{e,p}$: additive electronic bias structure at pixel $p$.
- $t_e$: exposure time.
- $d_p$: dark-current rate at pixel $p$, so $t_e d_p$ is the accumulated dark signal.
- $L_{e,p}$: additive scattered-light background at detector pixel $p$.
- $f$: fiber index; the sum includes all fibers whose detector profiles contribute to pixel $p$.
- $P_{e,f,p}(\lambda)$: detector-plane extraction profile mapping light from fiber $f$ onto pixel $p$.
- $\lambda_p$: wavelength associated with pixel $p$ for the relevant fiber.
- $R^{\mathrm{pixel}}_{p}$: pixel-level sensitivity or gain variation.
- $R^{\mathrm{fiber}}_{e,f}(\lambda)$: wavelength-dependent fiber-to-fiber relative response.
- $R^{\mathrm{amp}}_{e,a}$: relative response of amplifier $a$ containing pixel $p$.
- $T^{\mathrm{instrument}}_e(\lambda)$: total wavelength-dependent instrumental throughput, including optics, spectrograph, and detector efficiency.
- ${\theta}$: angular position on the sky.
- $\Omega$: sky region contributing source light to the fiber system.
- $S^{\mathrm{source}}_e(\theta,\lambda)$: intrinsic spatially and spectrally resolved astronomical or calibration-source surface brightness.
- $T^{\mathrm{atmosphere}}_e(\theta,\lambda)$: atmospheric transmission, including extinction, clouds, and telluric absorption.
- $F^{\mathrm{sky}}_{e,f}(\lambda)$: sky-background spectrum entering fiber $f$, including airglow, scattered moonlight, zodiacal light, and unresolved background.
- $C_{e,f}(\theta,\lambda)$: wavelength-dependent coupling of light from sky position $\theta$ into fiber $f$.
- $\mathcal{A}_f$: focal-plane entrance aperture of fiber $f$.
- $\mathbf{x}$: coordinate in the telescope focal plane.
- $\mathcal{M}_e({\theta})$: astrometric and focal-plane mapping from sky coordinates to focal-plane coordinates, including pointing, rotation, scale, distortion, and fiber locations.
- $\Delta{\theta}^{\mathrm{DAR}}_e(\lambda)$: differential atmospheric-refraction displacement relative to a reference wavelength.
- $\mathrm{PSF}_e(\mathbf{x},\lambda)$: wavelength-dependent spatial point-spread function, including seeing, telescope focus, guiding, optical aberrations, and image motion.
- $N_{e,p}$: stochastic and transient contributions, including read noise, photon noise, cosmic rays, and unmodeled detector effects.

Read left to right, this is nothing more than "what a detector pixel records
is bias, plus dark current, plus scattered light, plus the source and sky
light that actually made it through the atmosphere, the optics, the fibers,
and the detector, plus noise." Calibration exposures do not usually isolate
one term perfectly. Instead, each observing configuration suppresses, fixes,
or externally constrains enough of the equation that a particular combination
of terms can be estimated. In a science exposure,
$S^{\mathrm{source}}$ and $F^{\mathrm{sky}}$ are the principal quantities we
want to infer, while the remaining terms enter as calibration models with
uncertainties, assumptions, and finite validity.

## Building the evidence base: calibration exposures constrain the terms

The reason we bother with a fast raw database and a queryable artifact
warehouse is that solving for $S^{\mathrm{source}}$ is only possible if the
other terms in the equation have already been constrained well enough for the
science problem at hand. The warehouse preserves the measurements that provide
those constraints, together with the scope, provenance, assumptions, and QA
needed to decide whether they apply to a given exposure. A calibration exposure
therefore does not simply reveal one hidden quantity. It changes which terms
dominate, which are negligible, and which can be fixed by external knowledge,
producing evidence from which model sets can infer particular combinations of
the equation's components:

- **Bias frames** (zero-second exposures, shutter closed) primarily constrain
  the electronic offset and stable bias structure $B_{e,p}$. Stacking many
  gives a `master_bias` evidence artifact, scoped per amplifier, together
  with information about read noise and temporal bias variation. A bias model
  may use that image directly or combine it with overscan, detector state, or
  temporal structure to predict $B_{e,p}$ for a particular exposure.
- **Dark frames** (shutter closed, non-zero exposure time) constrain
  $t_e d_p$ after bias subtraction, giving an estimate of the dark-current
  structure and hot-pixel state. Those measurements remain conditional on
  bias removal, exposure-time scaling, detector temperature, and temporal
  stability; a dark model specifies how they are converted into the predicted
  $t_e d_p$ for a particular exposure.
- **Arc-lamp exposures** (calibration lamps with known emission features)
  constrain the pixel-to-wavelength mapping $\lambda_p$. In combination with
  trace measurements and profile assumptions, they support the
  measured arc centroids, fiber-binned trace positions, and profile evidence.
  A trace or wavelength model describes how those measurements are fitted and
  interpolated into evaluated `trace_map` and `wavelength_map` states. Line
  blending, flexure, profile shape, and imperfect trace placement remain part
  of the model uncertainty.
- **Twilight-flat exposures** provide broad, high-signal illumination that
  strongly constrains relative fiber and amplifier response. They do not
  determine those terms uniquely: the solar spectrum, atmospheric variation,
  field illumination, scattered light, pixel response, and true fiber response
  can remain partially degenerate. Twilight therefore contributes to the
  averaged IFU illumination and extracted twilight measurements. A response
  model then specifies how those measurements are combined with other evidence
  and with explicit assumptions about the common reference spectrum.
- **LDLS exposures** provide high-count calibration spectra that are useful for
  constraining fine-scale fiber and wavelength-dependent response structure.
  Their extracted spectra and illumination summaries are evidence; the
  response model specifies how the lamp spectrum, illumination geometry, and
  scattered-light background are separated from the response being inferred.
- **Master-science measurements** provide an independent transfer test at
  science-like count levels. They are most valuable as QA for the response
  inferred from twilight and LDLS. The residuals are validation evidence and
  should not silently change the fitted response.
- **Standard-star exposures**, where the source spectrum is constrained by an
  external reference, measure the combined effect of instrumental throughput,
  atmospheric transmission, source coupling, PSF, DAR, and aperture losses.
  Additional atmospheric and spatial information is needed to factor that
  combined measurement into separate instrumental, atmospheric, and spatial
  coupling components. A throughput and atmosphere model records that
  factorization and predicts
  $T^{\mathrm{instrument}}_e(\lambda)$ and
  $T^{\mathrm{atmosphere}}_e(\theta,\lambda)$ for the exposure.
- **Guiding and focus telemetry, plus repeated point-source measurements**,
  constrain the astrometric mapping $\mathcal{M}_e$, the PSF, and the DAR
  offset $\Delta\theta^{\mathrm{DAR}}_e(\lambda)$. These measurements help
  provide evidence for the coupling function $C_{e,f}(\theta,\lambda)$, but
  telemetry is not itself a complete measurement of focal-plane illumination or
  delivered image quality. The spatial model specifies how telemetry, repeated
  source measurements, focal-plane geometry, and atmospheric state are combined.
- **Scattered-light modeling** on science and calibration frames estimates
  $L_{e,p}$ from the inter-fiber gaps and the physical-CCD background
  structure. This is scientifically preferable to leaving the additive light
  in the image, but errors in the scatter model can be absorbed into later
  response estimates. The model and its residual diagnostics therefore remain
  part of the evidence, not an invisible preprocessing step. A scattered-light
  model defines how those samples and constraints are converted into the
  evaluated $L_{e,p}$ subtracted from a particular image.

The measurement artifacts are written to the warehouse with full provenance:
which raw exposures went in, which algorithm and parameters summarized them,
which references and assumptions were adopted, what QA was measured, and over
what validity window the evidence applies. The scientific system then contains
four distinct layers:

- **measurement evidence**, such as master biases, extracted spectra,
  inter-fiber background samples, averaged IFU illumination, fiber-binned trace
  positions, measured arc centroids, and validation residuals;
- **model specifications and fitted model state**, which are flexible,
  versioned sets describing how evidence is selected, combined, interpolated,
  constrained, and converted into predictions of one or more equation terms;
- **evaluated calibration states**, such as a trace map, wavelength map,
  scattered-light surface, fiber-response array, or atmospheric-transmission
  curve generated for a particular scope, time, or calibration build; and
- **references and assumptions**, such as a stable twilight spectrum, a
  source catalog, a functional form, a physical constraint, or an
  applicability rule.

The artifact warehouse is therefore the durable measurement and prediction
store, not a declaration of final truth and not necessarily the sole container
for a scientific model. Model sets can evolve while continuing to cite the same
immutable evidence. When a model is evaluated, the resulting calibration state
can be retained as an artifact with exact provenance back to both the model
version and the evidence it used.

That queryable collection is the evidence base for the knowledge system. It is
a versioned set of observations, summaries, predictions, and validation claims
about the detector, instrument, atmosphere, and sky. Evidence is accumulated as
often as observations permit; model sets are revised when scientific
understanding improves; and evaluated states are reused across science
exposures when their applicability permits. The warehouse remains optimized for
queries such as `select_best(kind=..., scope=..., at_time=...)`, with no payload
I/O until an evidence artifact or evaluated state is actually needed.

## Inverting the equation: solving for the science

Once the evidence base, model sets, and applicable evaluated calibration states
are available for a science exposure, reduction becomes an approximate
inversion of the canonical equation. The principal quantities of interest are
$S^{\mathrm{source}}_e(\theta,\lambda)$ and
$F^{\mathrm{sky}}_{e,f}(\lambda)$. The other terms enter as predictions from
models grounded in measurement evidence, carrying their own uncertainties,
assumptions, applicability, and QA.

For a given science exposure $e$, the pipeline:

1. **Resolves the applicable evidence, model versions, and evaluated states**
   for the exposure's scope, timestamp, and observing conditions. Existing
   reusable predictions may be selected directly through `select_best`; other
   terms may be evaluated from the selected model set and its evidence for this
   exposure. Exposure-specific quantities such as scattered light may be
   computed from the current frame. Selection and planning remain metadata
   operations until arrays are actually needed.
2. **Removes the purely additive, non-astrophysical terms** from the raw
   detector data: subtract $B_{e,p}$, subtract $t_e d_p$, subtract the
   scattered-light model $L_{e,p}$. What remains is the extracted signal due
   to light that actually entered the fibers.
3. **Extracts per-fiber spectra** using the trace map and wavelength
   solution — this uses $P_{e,f,p}(\lambda)$ to go from detector pixels back
   to a flux value per fiber per wavelength, which is the sum term in the
   equation collapsed down to a single fiber's contribution.
4. **Divides out the multiplicative response and throughput terms** —
   $R^{\mathrm{pixel}}_{p}$, $R^{\mathrm{fiber}}_{e,f}(\lambda)$,
   $R^{\mathrm{amp}}_{e,a}$, $T^{\mathrm{instrument}}_e(\lambda)$, and
   $T^{\mathrm{atmosphere}}_e(\theta,\lambda)$ — converting the
   instrument-native, atmosphere-attenuated electron counts into calibrated
   flux, published as a `calibrated_fiber_observation` artifact per fiber.
5. **Separates sky from source** using the coupling function $C_{e,f}$:
   fibers, or parts of the field of view, dominated by blank sky constrain
   $F^{\mathrm{sky}}_{e,f}(\lambda)$. After subtracting that estimate, the
   astrometric mapping $\mathcal{M}_e$, DAR offset
   $\Delta\theta^{\mathrm{DAR}}_e$, and PSF are used in the spatial model or
   extraction appropriate to the observation. The result is an estimate of
   $S^{\mathrm{source}}_e(\theta,\lambda)$ and the corresponding
   source-detection and extracted-spectrum products. This description does not
   assume that every reduction performs a general mathematical deconvolution.
6. **Propagates $N_{e,p}$** (read noise, photon noise, cosmic-ray flags,
   residual systematics) through every one of the above steps so the final
   science artifacts carry honest uncertainties, not just point estimates.

Every quantity we solve for, remove, or condition on depends on evidence that
was produced through the same warehouse and raw-database infrastructure. A
`master_bias`, an averaged IFU illumination measurement, a set of fiber-binned
trace positions, and a master-science residual distribution are all durable
pieces of evidence. Different model sets can use those same measurements to
produce different, explicitly versioned calibration predictions.

The raw database and artifact warehouse are therefore not merely
infrastructure plumbing. They make scientific inference revisable at survey
scale: cheap raw access lets us accumulate and recompute as much measurement
evidence as the science demands; cheap, correct artifact selection lets each
model find the evidence and evaluated states appropriate to an exposure; and
explicit model versions preserve how that evidence was interpreted. When the
measurements, assumptions, parameterizations, or algorithms improve, the system
can produce a new calibrated interpretation without losing either the evidence
or the lineage of the interpretation it supersedes.
