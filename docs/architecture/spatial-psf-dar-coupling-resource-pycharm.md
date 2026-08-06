# Spatial PSF, DAR, Coupling, and Source Extraction

> Status: scientific implementation resource that guided the now-completed
> production wiring of PSF, DAR, source-to-fiber coupling, and source
> extraction (see the "Concrete decisions made in the production
> implementation" section below for what was actually built). This document
> does not add or redefine an Artifact kind or Product contract; the
> registered vocabulary and production-wiring status live in
> [`artifact-kinds-and-equation-inversion.md`](artifact-kinds-and-equation-inversion.md).
> It records the measurements, mathematical structure, algorithmic results,
> and validation requirements that guided that implementation and that remain
> relevant to future extensions (photometric source detection, direct
> baseline remeasurement).

## Purpose

The current VIRUSFlow inverse ends with positioned, extinction-corrected,
sky-subtracted, relatively normalized fiber measurements. It does not yet infer
how a source illuminates the fibers as a function of wavelength. That missing
operation is represented by the coupling term in the canonical model:

$$C_{e,f}(\theta,\lambda) = \int_{\mathcal{A}_f} \mathrm{PSF}_e\!\left[ \mathbf{x} - \mathcal{M}_e\!\left( \theta+\Delta\theta^{\mathrm{DAR}}_e(\lambda) \right), \lambda \right] \,d\mathbf{x}.$$

Here, the astrometric map, chromatic source displacement, spatial PSF, and
finite fiber aperture jointly determine the fraction of source light entering
fiber $f$ in exposure $e$.

Two legacy utilities contain useful procedures for this problem:

- the Remedy extraction utility constructs spatial profiles, evaluates
  wavelength-dependent fiber weights, and extracts a source spectrum;
- the Antigen PSF utility fits centroid and PSF width in wavelength intervals,
  compares measured chromatic motion with a header-derived DAR model, and
  evaluates fiber covering fractions.

They should be treated as evidence of successful procedures, not as classes to
port. Their useful physical content should be separated into retained
measurements, model construction, evaluated coupling, and downstream inference.

## Scientific decomposition

The useful flow is:

```text
calibrated fiber measurements
  -> wavelength-local spatial measurements
  -> smooth exposure PSF and chromatic-centroid model
  -> physical source-to-fiber coupling C(e,f,lambda)
  -> source, companion, background, or spectrophotometric inference
```

The first two stages determine the spatial response from the VIRUS data. The
coupling evaluator turns that response into a physical fraction of source light
falling in each fiber. Downstream algorithms then use the coupling without
having to refit or reinterpret the PSF.

Guider seeing and header-derived atmospheric information remain important, but
they are supporting measurements:

```text
guider and header measurements
    -> initialization, prior information, comparison, and QA

VIRUS fiber measurements
    -> science-field PSF and chromatic displacement used by the inverse
```

The guider probes a different location in the telescope field. It should not
replace the VIRUS-defined spatial response when the science fibers provide
enough information to measure it.

## Retained measurement evidence

The durable evidence should be the compact quantities measured directly from
the calibrated fiber data. A practical measurement unit is one exposure and
one wavelength interval.

For each fitted wavelength interval, retain:

| Measurement | Meaning |
|---|---|
| wavelength interval and reference wavelength | Spectral samples contributing to the measurement |
| source centroid $x_j,y_j$ | Measured source location in the selected coordinate frame |
| PSF width | Usually FWHM for the initial Moffat model |
| PSF shape state | Fixed or measured Moffat index, ellipticity, or orientation when applicable |
| source amplitude | Local fitted source normalization |
| parameter covariance or uncertainties | Statistical constraint from the local fit |
| fit statistic and residual scale | Agreement between measured and predicted fiber fluxes |
| fibers used | Exact fiber identities and coordinates entering the fit |
| wavelength samples used | Continuum windows and exclusions entering the local collapse |
| masks and rejection state | Bad fibers, spectral masks, rejected samples, and failed intervals |
| coverage | Sum of physical fiber coupling within the available field |
| algorithm identity | Local fitting method, robust loss, bounds, and integration convention |

The residual evidence need not be a large reconstructed image. It should be
sufficient to determine whether the fitted spatial model was constrained,
biased by individual fibers, contaminated by extended emission, or extrapolated
beyond the observed source footprint.

Also retain the conditioning measurements used to interpret the fit:

- guider seeing and its time interval;
- header or physically predicted DAR state;
- airmass and parallactic or instrumental orientation;
- exposure astrometry and dither state;
- source coordinate or initialization;
- selected fiber aperture geometry;
- atmospheric and response state already applied to the fiber measurements.

These quantities are evidence and context. They are not automatically the
spatial model used by source extraction.

## Local spatial measurement

For a wavelength interval $j$, collapse the usable spectral samples in each
fiber into a robust flux measurement $y_{f,j}$ with uncertainty
$\sigma_{f,j}$. The spatial fit is then

$$y_{f,j} = a_j\,C_f(x_j,y_j,\phi_j) + b_{f,j} + \epsilon_{f,j},$$

where:

- $a_j$ is the source amplitude;
- $C_f$ is the PSF integrated over the physical fiber aperture;
- $(x_j,y_j)$ is the source centroid;
- $\phi_j$ contains width and shape parameters;
- $b_{f,j}$ is an optional local-background component;
- $\epsilon_{f,j}$ is the measurement error.

The first implementation can use a circular Moffat PSF with a fixed shape
parameter and fitted centroid, FWHM, and amplitude. The fit should be bounded
and robust:

```text
centroid inside a declared search region
minimum FWHM < fitted FWHM < maximum FWHM
source amplitude >= 0
fiber coupling evaluated only within its supported domain
robust loss available for bad fibers and localized contamination
```

A failed wavelength interval is retained as failed evidence. Its parameters
should not be copied from a neighboring interval and then treated as a measured
point.

## Algorithmic result: chromatic spatial model

The interval measurements are then used to construct a smooth exposure model:

$$x_e(\lambda), \qquad y_e(\lambda), \qquad \mathrm{FWHM}_e(\lambda), \qquad \phi_e(\lambda).$$

The model may initially be polynomial, but the retained measurements must not
depend on that choice. A polynomial, spline, atmospheric DAR model, Gaussian
process, or joint physical fit should be able to consume the same interval
measurements.

The evaluated model state should include:

- reference wavelength and reference centroid;
- centroid displacement versus wavelength;
- FWHM and any other PSF-shape parameters versus wavelength;
- valid wavelength range;
- model covariance or parameter covariance;
- interval measurements and their validity state;
- fitting and smoothing algorithm versions;
- guider and header-DAR comparison statistics;
- explicit coordinate and sign conventions.

The measured centroid motion can be compared with a physically predicted DAR
curve. It should not be labeled purely as DAR unless the fit has separated
atmospheric refraction from wavelength-dependent astrometric, centering, or
instrumental effects. For downstream coupling, the essential quantity is the
measured chromatic source position.

## Physical fiber coupling

Given the evaluated spatial model and the actual fiber geometry, compute

$$C_{e,f}(\lambda) = \int_{\mathcal{A}_{e,f}} \mathrm{PSF}_e\!\left[ \mathbf{x}-\mathbf{x}_e(\lambda), \lambda \right]d\mathbf{x}.$$

This value is the predicted fraction of total source light entering the fiber.
It must remain **unnormalized**.

The wavelength-dependent captured fraction is

$$\eta_e(\lambda) = \sum_f C_{e,f}(\lambda).$$

This quantity contains real information about source centering, PSF width,
dither geometry, field edges, missing fibers, and aperture loss. Normalizing
the couplings so that they sum to one destroys that information and prevents
later spectrophotometric aperture correction.

The dense fiber-by-wavelength coupling matrix is normally recomputable from:

```text
fiber geometry
+ evaluated chromatic centroid
+ evaluated PSF state
+ fiber-aperture integration convention
```

It therefore does not need permanent retention unless a specific study or
validation requires materializing it. The compact spatial measurements and
evaluated model state are the durable boundary.

## Source extraction as a small linear inverse

For one wavelength sample, let $\mathbf{y}_\lambda$ contain the calibrated
fiber measurements and let $\mathbf{C}_\lambda$ contain the physical source
couplings. For an isolated source,

$$\mathbf{y}_\lambda = \mathbf{C}_\lambda s_\lambda + \boldsymbol{\epsilon}_\lambda.$$

With inverse-variance matrix $\mathbf{W}_\lambda$, the weighted
least-squares estimate is

$$\hat{s}_\lambda = \frac{ \mathbf{C}_\lambda^\mathsf{T} \mathbf{W}_\lambda \mathbf{y}_\lambda }{ \mathbf{C}_\lambda^\mathsf{T} \mathbf{W}_\lambda \mathbf{C}_\lambda },$$

with conditional variance

$$\operatorname{Var}(\hat{s}_\lambda) = \left( \mathbf{C}_\lambda^\mathsf{T} \mathbf{W}_\lambda \mathbf{C}_\lambda \right)^{-1}.$$

The more general form is

$$\hat{\beta}_\lambda = \left( \mathbf{X}_\lambda^\mathsf{T} \mathbf{W}_\lambda \mathbf{X}_\lambda \right)^{-1} \mathbf{X}_\lambda^\mathsf{T} \mathbf{W}_\lambda \mathbf{y}_\lambda.$$

The columns of $\mathbf{X}_\lambda$ may represent:

- one point source;
- a point source plus local background;
- two overlapping point sources;
- a source plus a compact nebular component;
- another explicitly defined spatial basis.

This small matrix formulation is the common downstream interface. It avoids a
separate extraction formula for every scientific case and makes degeneracy,
conditioning, and covariance explicit.

## Source-extraction result

A retained extracted-source result should state its estimand directly:

> A source amplitude spectrum under a specified source position, spatial PSF,
> chromatic-centroid model, fiber-aperture geometry, and background model.

Its compact evidence should include:

- wavelength, extracted amplitude, variance, and mask;
- target coordinate and source-model identity;
- contributing exposure and fiber identities;
- selected spatial-model identity;
- captured fraction $\eta_e(\lambda)$;
- usable-fiber count and effective information;
- fit statistic, residual scale, and conditioning flags;
- background or companion components included in the solve;
- response, extinction, and units of the input fiber measurements;
- algorithm and model versions.

The source spectrum should not silently absorb aperture loss. Whether the
reported amplitude is the modeled total source flux or only the flux captured
by the observed fibers must be explicit and testable.

## Lessons from the Remedy extraction utility

The Remedy code preserves several useful procedures:

1. **Finite fiber apertures matter.**  
   PSF values at fiber centers are not sufficient. The PSF must be integrated
   over each circular aperture.

2. **Spatial response changes with wavelength.**  
   Source positions must be evaluated on the wavelength grid or from a smooth
   chromatic model.

3. **A top-hat, Gaussian, or Moffat profile can share one coupling interface.**  
   The profile family should be replaceable without changing the downstream
   extraction solve.

4. **Curve-of-growth and captured-fraction diagnostics are valuable.**  
   They should be derived from physical couplings rather than from normalized
   weights.

5. **Nearby-fiber selection is an optimization, not part of the scientific
   definition.**  
   The selection radius and omitted-coupling tolerance should be explicit.

The following legacy details should not become VIRUSFlow contracts:

- the fixed 3470--5540 Angstrom wavelength grid;
- the hard-coded three-position dither pattern;
- the fixed empirical ADR curve;
- mutable object attributes populated outside the constructor;
- one array called `weights` serving as PSF value, coupling, normalized
  extraction coefficient, and coverage;
- heuristic uncertainty formulas in place of the explicit weighted linear
  covariance;
- silent broad exception handling;
- a collapsed-image routine mixed into the source-extraction API.

The Remedy collapsed-image procedure is useful for display and morphology, but
it renormalizes wavelength chunks and interpolates fiber values onto a grid. It
should be treated as a separate imaging operation, not as quantitative
flux-conserving source extraction.

## Initial empirical VIRUS DAR model from Remedy

Implement the Remedy convention directly before generalizing it.

The tabulated values define a scalar chromatic displacement in arcseconds:

```python
dar_wavelength = np.array([3500., 4000., 4500., 5000., 5500.])
dar_displacement = np.array([-0.74, -0.40, -0.08, 0.08, 0.20])
```

Evaluate the same cubic polynomial fit used in `extract.py` on the exposure wavelength grid:

```python
dar_scalar = np.polyval(
    np.polyfit(dar_wavelength, dar_displacement, 3),
    wavelength,
)
```

In the Remedy coordinate convention, `angle=0` places the full displacement along the instrument (+x) direction:

```python
delta_x = np.cos(np.deg2rad(angle)) * dar_scalar
delta_y = np.sin(np.deg2rad(angle)) * dar_scalar
```

Thus, the angle is measured counterclockwise from instrument (+x). At the initial default `angle=0`:

```text
delta_x(lambda) = dar_scalar(lambda)
delta_y(lambda) = 0
```

Convert these instrument-plane offsets to sky-coordinate offsets using the selected exposure astrometric tangent-plane transform, following `Extract.get_ADR_RAdec`:

```python
ra_wave, dec_wave = astrometric_transform(delta_x, delta_y)

delta_ra = (
    (ra_wave - reference_ra)
    * 3600.0
    * np.cos(np.deg2rad(reference_dec))
)
delta_dec = (dec_wave - reference_dec) * 3600.0
```

Use the actual VIRUSFlow astrometry interface rather than reproducing the legacy object API. Let the astrometric transform determine the sky orientation and handedness; do not apply a second independent rotation after this conversion.

The resulting chromatic source position is:

```text
source_ra(lambda)  = reference_ra  + delta_ra(lambda)
source_dec(lambda) = reference_dec + delta_dec(lambda)
```

or equivalently in a local tangent-plane frame:

```text
source_x(lambda) = reference_x + delta_x(lambda)
source_y(lambda) = reference_y + delta_y(lambda)
```

## Lessons from the Antigen PSF utility

The Antigen code contains the important upstream measurement procedure:

1. robustly collapse spectra into wavelength intervals;
2. fit source centroid, FWHM, and amplitude from fiber measurements;
3. smooth centroid and FWHM versus wavelength;
4. compare the measured centroid motion with a header-based DAR model;
5. evaluate wavelength-dependent fiber covering fractions;
6. preserve detailed fit diagnostics.

Several details should be corrected or made explicit in VIRUSFlow:

- the fiber radius must come from instrument geometry rather than a default
  embedded in the fitting code;
- centroid, FWHM, amplitude, and interpolation domain need physical bounds;
- the robust flux-weighted centroid should actually initialize the nonlinear
  fit;
- unsuccessful intervals must be masked rather than filled from neighboring
  intervals and then used as measurements;
- the smooth chromatic fit should use interval uncertainties and validity;
- spectral windows must exclude emission lines, sky residuals, and other
  contamination when measuring a stellar PSF;
- the measured chromatic centroid coefficients must become part of the
  evaluated model, not merely a diagnostic comparison;
- physical covering fractions must not be normalized over the fibers that
  happen to be available.

The Antigen implementation also demonstrates that diagnostics at individual
fiber locations are important. Fiber residuals, model-to-data ratios, selected
fibers, and interval fit status are compact evidence that should be retained or
reconstructable from retained measurements.

## Relationship to spectrophotometry

A directly measured relative-response baseline depends on source capture,
extraction method, PSF treatment, and contribution correction. The spatial
model therefore conditions spectrophotometric response measurement.

For a standard source, the response fit should use a stated coupling model and
retain the captured fraction. Otherwise wavelength-dependent centering, DAR,
seeing, or IFU-edge loss can be absorbed into the baseline response.

The intended factorization is:

```text
fiber response and amp normalization
+ baseline instrument/reduction response
+ atmospheric extinction
+ exposure gray states
+ source-to-fiber coupling and aperture capture
= measured source spectrum under an explicit model
```

The spatial coupling term should not be folded invisibly into the baseline.
The baseline may remain method-dependent, but the method identity and source
capture model must be retained so that a change in PSF or extraction method
requires a new compatible baseline rather than another correction layer.

## Validation invariants

A future implementation should establish the following invariants.

### Coupling

- $C_{e,f}(\lambda)\ge 0$.
- The coupling is evaluated using the actual fiber aperture.
- The coupling is not normalized over available fibers.
- $\eta_e(\lambda)=\sum_f C_{e,f}(\lambda)$ is retained or reproducible.
- Coupling outside the supported PSF/interpolator domain is masked or fails
  explicitly.

### Spatial measurements

- Failed wavelength intervals are not treated as measured values.
- Every fitted interval retains the fibers, wavelengths, masks, and model used.
- Centroid and width uncertainties are finite when the fit is claimed valid.
- The coordinate frame and sign convention are explicit.
- Guider and header-DAR values remain identifiable as external conditioning
  measurements.

### Extraction

- Injected sources with known coupling are recovered without bias.
- Conditional variance agrees with the linear covariance.
- Adding a background or companion column produces the expected covariance and
  degeneracy.
- Missing fibers reduce captured fraction rather than renormalizing the source
  to the observed footprint.
- Extraction from multiple dithers uses each exposure's own PSF and chromatic
  centroid state.

### Spectrophotometric transfer

- A standard observed at different centering or seeing produces the same
  response after the measured coupling is applied.
- A method change in extraction, PSF, contribution correction, or aperture
  integration requires a new compatible baseline.
- The response fit retains held-out validation and finite uncertainty before it
  supersedes the provisional transformed Remedy baseline.

## Suggested implementation sequence

This resource supports staged implementation without requiring the final model
architecture in advance.

1. **Define the spatial measurement payload.** *(Done — `spatial_psf_measurement`.)*
   Establish interval measurements, uncertainties, masks, selected fibers,
   selected wavelengths, residual evidence, and external guider/header context.

2. **Implement bounded local Moffat fits.** *(Done — `spatial_psf.fit_wavelength_interval_psf`.)*
   Fit centroid, FWHM, and amplitude with robust loss and physical aperture
   integration.

3. **Implement the chromatic spatial model.** *(Done — `chromatic_psf_model` / `spatial_psf.fit_chromatic_psf_model`.)*
   Fit centroid and width versus wavelength from valid interval measurements,
   retaining the measurements independently of the smoothing representation.

4. **Implement the coupling evaluator.** *(Done — `spatial_psf.integrate_moffat_over_apertures`, recomputed per run, never persisted.)*
   Produce unnormalized physical $C_{e,f}(\lambda)$ and captured fraction.

5. **Implement the general linear source solve.** *(Done — `point_source_extraction` / `source_extraction.extract_source_spectrum`, one source plus optional local background.)*
   Begin with one source and optional local background, then extend by adding
   explicit design-matrix columns.

6. **Use the coupling model in direct baseline remeasurement.** *(Not started — a later goal; no standard-star-specific plumbing exists yet. See Horizon 2 in the crosswalk document.)*
   Prevent source-capture color from entering the next measured
   `baseline_relative_response`.

7. **Promote stable retained results.** *(Partially done — `dar_seed_model`, `spatial_psf_measurement`, `chromatic_psf_model`, `point_source_extraction`, and the new observation-scope `observation_source_spectrum` are registered canonical Artifact kinds and are production-published; validation to date uses synthetic injected sources, not real standard/held-out sources.)*
   Begin through bounded analysis materialization. Add or revise canonical
   Artifact kinds only after the retained scientific contract is demonstrated.

## Concrete decisions made in the production implementation

The design questions raised throughout this document have now been resolved
by an actual production wiring in `ExposureTask._run_point_source_extraction`
(called from `ExposureTask.run()`) and a combination step in `ObservationTask`.
This section records exactly what was decided, so this document reflects
implementation reality rather than only open design space. See
`docs/architecture/artifact-kinds-and-equation-inversion.md` for the
Artifact-kind and equation-term crosswalk.

**DAR seed angle convention.** The instrument-frame angle used by
`dar.evaluate_dar_seed` (`angle_deg` in `DAR_SEED_CONFIGURATION`) is fixed at
`0.0`, matching Remedy's own convention of applying the DAR curve directly
along instrument +x with no separate rotation. This was chosen because the
"correct" per-exposure parallactic angle is genuinely uncertain from the
available headers, and because the empirical seed is explicitly a fallback:
real per-exposure deviation from the fixed-angle seed is intended to be
absorbed by the fitted `chromatic_psf_model` centroid residual term, not
derived from an unverified per-exposure formula. This is a documented,
accepted limitation, not a placeholder awaiting a formula — see the
limitations discussion in the crosswalk document.

**Wavelength binning / representative grid.** `_run_point_source_extraction`
does not fit the PSF on every native pixel wavelength column independently.
It first computes one representative per-pixel wavelength grid as the
median, across the fibers selected by the distance rule below, of each
fiber's native wavelength array (`np.nanmedian(selected_wavelength, axis=0)`).
That representative grid is then split into `psf_interval_count`
(from `SOURCE_EXTRACTION_CONFIGURATION`, default 10) equal-width wavelength
intervals via `spatial_psf.build_wavelength_intervals(min, max, count)`. Each
interval's flux is collapsed per fiber via
`spatial_psf.bin_flux_by_wavelength_interval` (inverse-variance weighted,
excluding masked samples) before the local Moffat fit
(`fit_wavelength_interval_psf`) is run once per interval. The final
`point_source_extraction` spectrum, however, is solved at the full
representative-wavelength resolution (one linear solve per representative
wavelength sample), using the chromatic model evaluated at each of those
wavelengths — only the PSF/centroid *fitting* stage is binned into intervals,
not the extracted spectrum's own resolution.

**Fiber-selection distance rule.** Fiber selection for both the PSF fit and
the final extraction is purely distance-based:
`source_extraction.select_source_fibers(fiber_x, fiber_y, source_x, source_y,
max_distance_arcsec=...)` returns a boolean exclusion mask built from
`max_fiber_distance_arcsec` (`SOURCE_EXTRACTION_CONFIGURATION`, default 6.0
arcsec), ORed with any pre-existing per-fiber mask. It is never used to
renormalize the coupling matrix — only to decide which fibers enter the fit
and solve. If no fibers remain within range, extraction is skipped for that
exposure (`status="skipped_no_fibers_in_range"`) rather than falling back to
a wider or unselected set.

**`observation_source_spectrum` combination.** `ObservationTask` publishes
`observation_source_spectrum` only when *every* ordered member exposure state
of a completed observation carries a non-`None` `point_source_spectrum`
(i.e. every member exposure successfully ran source extraction). The
combination itself is `source_extraction.combine_observation_source_spectra`,
an inverse-variance combine of each exposure's `wavelength`/`amplitude`/
`variance`/`mask`/`captured_fraction` arrays onto a common wavelength grid.
Per-exposure wavelength grids must agree within
`wavelength_grid_tolerance_angstrom` (`SOURCE_EXTRACTION_CONFIGURATION`,
default 1.0 Angstrom) for the fast inverse-variance path; if they do not
agree within tolerance, the combiner marks `status="degraded"` and falls back
to an unweighted mean rather than raising. The published Artifact's parents
are the observation's `calibrated_fiber_observation` id plus each
contributing exposure's `point_source_extraction` artifact id, and metadata
records which exposures contributed and their individual
`point_source_extraction` artifact ids.

## Boundary with the current inversion crosswalk

Production source extraction, as described above, is now wired into
`ExposureTask`/`ObservationTask` and validated with synthetic injected point
sources through the full task pipeline (see
`tests/test_source_extraction_task.py` and the crosswalk document). This
document remains the measurement and modeling resource that motivated that
implementation; it does not itself track the Artifact-kind registration or
production-wiring status, which lives in the crosswalk document.

The implementation boundary that was followed:

```text
current calibrated_fiber_observation
  -> retained spatial measurements
  -> evaluated exposure PSF/chromatic-centroid state
  -> recomputable physical fiber coupling
  -> source-domain extraction or response measurement
```

The durable scientific content is what was measured and how strongly it was
constrained. The particular fitting function and dense evaluated coupling are
replaceable algorithmic representations.
