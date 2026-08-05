# Artifact kinds and inversion of the canonical equation

> Status: code-to-science crosswalk for the current repository, verified
> 2026-08-05. The executable vocabulary remains
> [`virusflow/ontology/artifact_kinds.py`](../../virusflow/ontology/artifact_kinds.py).
> This document classifies the existing kinds; it does not add or redefine any
> Product contract.

## Purpose

The canonical forward model in [`notes.md`](notes.md#the-canonical-equation) can
be written compactly as

$$D_{e,p} = B_{e,p} + t_e d_p + L_{e,p} + A_{e,p}\left[\int_\Omega C_{e,f}(\theta,\lambda_p) T^{\mathrm{atmosphere}}_e(\theta,\lambda_p) S^{\mathrm{source}}_e(\theta,\lambda_p)\,d\theta + F^{\mathrm{sky}}_{e,f}(\lambda_p)\right] + N_{e,p},$$

where

$$A_{e,p} = t_e \sum_f P_{e,f,p}(\lambda_p) R^{\mathrm{pixel}}_p R^{\mathrm{fiber}}_{e,f}(\lambda_p) R^{\mathrm{amp}}_{e,a} T^{\mathrm{instrument}}_e(\lambda_p).$$

The scientific purpose of an Artifact is therefore not merely to be a pipeline
stage. It should do at least one of the following:

- preserve evidence that constrains a term in the forward model;
- preserve a fitted model or an evaluated state for a term;
- carry an intermediate or final estimate made while inverting the model;
- preserve uncertainty, masks, QA, provenance, or observing context needed to
  interpret that estimate.

This crosswalk answers two separate questions that are easy to conflate:

1. What scientific role can be assigned to each registered kind?
2. How far does the current implementation actually support that role?

The ontology `lifecycle` is a storage contract, not a complete scientific
classification. For example, `master_bias` is lifecycle `canonical` but is
scientifically inferred calibration evidence, while `ccd_scattered_light_model`
is lifecycle `model` and records either an exposure-specific prediction or a
calibration-illumination prediction of $L_{e,p}$.

Artifact kinds should retain measurement evidence and scientifically useful
materialized model states. They do not need to encode every model
specification. A functional form, parameter schema, fitting recipe, or other
model specification may remain a flexible, versioned description that consumes
retained evidence and produces evaluated calibration states. It needs its own
Artifact kind only when the specification or one of its outputs is itself a
scientific quantity worth retaining for reuse, comparison, QA, modeling, or
reconstruction.

## Three inversion horizons

The same equation is used here for three different horizons. Keeping them
separate prevents an implemented detector-to-fiber correction, completion of
the existing calibration system, and eventual inference of source surface
brightness from being described as the same result.

### Horizon 1: current implemented inverse

```text
raw D(e,p)
  -> overscan, orientation, gain, detector variance              [run-local]
  -> subtract master bias and scaled dark residual                [run-local]
  -> assemble paired amplifiers and fit/subtract physical-CCD L   [model retained]
  -> trace-guided five-pixel fractional aperture extraction       [run-local]
  -> attach wavelength map and reject invalid wavelength rows
  -> select one coherent calibration response build
  -> divide calibration-time within-amplifier and amp-to-amp factors
  -> solve exposure astrometry and fiber sky coordinates
  -> select sky fibers, fit common sky, predict and subtract sky
  -> select one instrument-epoch empirical effective baseline
  -> if baseline atmospheric_content = removed_with_model:
       require explicit AIRMASS and apply one selected extinction model
  -> apply baseline once; keep extinction, fiber illumination, mirror
     illumination, gray transparency, and seeing as distinct states
                                                                  [run-local]
  -> pass calibrated exposure state to observation assembly       [run-local]
  -> concatenate a complete dither sequence as
     calibrated_fiber_observation                                 [retained]
```

The arrow chain is an approximate inverse, not yet the full inverse implied by
the equation. In particular, the extraction operator is a fixed aperture rather
than an explicit fitted $P_{e,f,p}$; absolute instrumental throughput, variable
atmospheric transmission beyond the selected mean extinction model, PSF, DAR,
and the full coupling function are not currently solved; and the final Product
is a collection of sky-subtracted fiber measurements rather than an estimate of
the intrinsic surface-brightness field $S^{\mathrm{source}}(\theta,\lambda)$.

Reusable response construction is also part of the implemented inverse. Raw
LDLS, twilight, and optional selected science calibration frames receive the
common detector reduction followed by the same implemented bias plus
exposure-time-scaled dark-residual convention used for science. Their retained
masters carry that detector-correction lineage. Each retained master spectrum
is then extracted from a jointly assembled amplifier pair after a
physical-CCD gap-scatter fit; the corrected dense CCD state remains run-local,
while an amplifier-addressable compact projection of the jointly fitted model
and residual samples is retained. Extraction validity and effective aperture
evidence are retained with each spectrum.

Dark application is now Product-local. Every publishable `master_dark` requires
`reference_exposure_time_seconds` and
`bias_convention="included_in_electron_master"`. Dark construction enforces one
common positive input exposure time for that electron-valued representation.
Science and response-calibration consumers both use the selected Product's
state in the shared detector-correction algorithm; neither looks up a
representative raw dark at application time. The retained dark mask is merged
into the detector mask and follows extraction. No retained dark-uncertainty
plane exists yet, so there is no dark-model variance available to propagate.

`within_amp_fiber_normalization` is fitted at calibration time from those
corrected spectra. A calibration-build task then compares every sibling
twilight anchor in one coherent center-track build and retains one
`amp_to_amp_normalization` with complete amplifier coverage and build identity.
`ExposureTask` selects that group Product, follows its exact
`within_amp_fiber_normalization` parents, applies both factors, and does not
refit either factor from science data. It still materializes the
exposure-scoped `fiber_response_model` because exposure illumination remains an
exposure-specific factor.

The default selected `baseline_relative_response` is a non-unity empirical payload
imported from the legacy Remedy `throughput / normalization` curves. The two
legacy curves are import inputs only: production retains wavelength, effective
response, uncertainty state, and mask in one independently selectable Product,
not separate throughput and normalization response layers. The imported curve
has no supplied uncertainty, so its uncertainty array is `NaN` with an explicit
`uncertainty_unavailable` mask bit. The application code propagates statistical
variance through the response-square divisor and adds a baseline-uncertainty
term whenever a later selected Product supplies finite uncertainty.

This legacy baseline is method-dependent. Its metadata identifies Remedy's five-pixel
fractional aperture average, trace-centered aperture without a fitted detector
PSF, gap-sampled smooth scattered-light subtraction, and
LDLS/twilight/master-science fiber-normalization convention, plus the current
provisional VIRUSFlow application configuration. Remedy's guider mirror
illumination and transparency measurements remain exposure-specific rather
than baseline components. A finite positive header transparency is divided once
as its own gray exposure factor; because no transparency uncertainty is
available, it is treated as fixed in the current conditional variance. This is
not a wavelength-dependent atmospheric-extinction correction. The exact
curve-producing release and instrument-
epoch dates were not recovered. Its convention is therefore
`atmospheric_content="absorbed_unknown"`: atmospheric extinction was not
separately removed during the legacy derivation, and the effective calibration
airmass cannot be reconstructed from current evidence. The Product is an
atmosphere-contaminated empirical effective response, not an isolated
$T^{\mathrm{instrument}}$. No separate wavelength-dependent extinction factor
is permitted with this convention, so its numerical application is unchanged.
A newer valid Product supersedes it by selection;
response Products are alternatives, never accumulated correction layers.
Selection requires an exact match to the recorded extraction, PSF-treatment,
contribution-correction, and response-convention algorithm versions. A method
version change therefore refuses the bundled seed until a compatible baseline
is regenerated and published with a new identity.

The second implemented convention is
`atmospheric_content="removed_with_model"`. Such a baseline represents the
instrument and named reduction-method response, and must retain both the
extinction-model identity and calibration-exposure airmasses used to remove
atmospheric color during construction. Only this convention activates the
retained `atmospheric_extinction_model`. The default model is the McDonald
Observatory table in magnitudes per airmass, with wavelength, coefficient,
unknown-uncertainty markers, mask, units, site, provenance, and applicability.
For explicit exposure airmass $X$ and interpolated coefficient $k(\lambda)$,

$$T_{\mathrm{atmosphere}}(\lambda,X)=10^{-0.4 k(\lambda)X},$$

and the response application multiplies the baseline- and gray-corrected
spectrum by $10^{0.4 k(\lambda)X}$. It linearly interpolates only inside the
model range; outside samples are masked by default or may be configured to fail.
No airmass default is supplied. Finite coefficient uncertainty is propagated
through the analytic correction derivative; the imported McDonald table has no
uncertainty, so that omission remains explicit.

Gray transparency and mirror illumination divide as exposure scalars, fiber
illumination remains its own per-fiber factor, and seeing is recorded but is not
a response multiplier. None is absorbed into the wavelength-dependent
extinction model or the method-dependent baseline. The final response state
records the baseline convention, selected extinction Product, airmass, and
which gray factors were applied exactly once.

The dense-calibration release boundary is likewise part of the implemented
inverse. Trace Products retain their discrete sample-validity state, fit
residuals, per-fiber sample counts, and reference-interpolated flags.
Wavelength Products retain mandatory arc identifications, detected candidates,
accepted lines, seed-fit status and coefficients, interpolation/extrapolation
flags, and the exact applied detector-mask indices. Extracted response-master
spectra retain a compact exact encoding of the fractional aperture rows and
boundary weights in addition to validity and effective width. The
within-amplifier Product requires its LDLS, broad-twilight, residual-twilight,
wavelength, and amplifier-anchor factorization rather than treating those
terms as optional diagnostics.

Those compact Products define the current safe eviction boundary:

```text
dense Hg, Cd, arc, LDLS, twilight, and optional master-science payloads
  -> required compact line, trace, mask, aperture, scatter, response, and residual evidence
  -> one QA-valid descendant instance of every gated kind
  -> every required descendant component present on disk with its registered checksum
  -> release only the rebuildable dense component
  -> preserve registry, provenance, QA, component descriptions, checksums, and compact evidence
```

The service refuses release when a required component is absent, missing,
non-present, lacks a checksum, or no longer matches its checksum. Optional
`master_sci` remains validation evidence: it may be evaluated in the response
task and retained as an exact parent, but it does not change the fitted response
coefficients. Dense parents remain rebuildable only from raw data and the
versioned algorithms; the compact descendants preserve the evidence needed to
audit the inference, not a lossless reconstruction of the dense illumination.

### Horizon 2: construct and validate the first atmosphere-separated baseline

The next bounded goal is to use the implemented convention and extinction
application to publish the first evidence-backed baseline that can supersede
the atmosphere-contaminated Remedy seed:

```text
retained standard-source measurements plus observing-condition evidence
  -> reduce them with one stated extraction, PSF, contribution-correction,
     and calibration configuration
  -> require measured calibration airmass for every standard exposure
  -> remove the selected extinction model while retaining model and airmass lineage
  -> account for source centering, aperture capture, and DAR well enough to
     prevent those color terms from entering the response
  -> fit an epoch- and method-matched baseline with finite uncertainty and mask
  -> validate transfer across held-out standards, IFUs, and track positions
  -> publish atmospheric_content=removed_with_model so it supersedes rather
     than multiplies the Remedy seed
```

This horizon remains bounded to a relative instrument-and-reduction-method
response. It does not fold gray transparency, mirror or fiber illumination, or
seeing into the baseline; establish an absolute scale; model non-photometric
chromatic clouds or telluric structure; or reconstruct a source-domain field.

### Horizon 3: long-term physical inversion

The long-term inverse would estimate a stated source-domain quantity related to
$S^{\mathrm{source}}(\theta,\lambda)$ after measured instrumental and
atmospheric response, profile/extraction behavior, PSF, DAR, spatial coupling,
and the necessary uncertainty terms are accounted for. That result may be a
spatial/spectral reconstruction or a source-specific estimate, but its exact
scientific estimand is not defined by the current repository. Until it is,
`calibrated_fiber_observation` remains a retained collection of positioned,
sky-subtracted, relatively normalized fiber measurements, not intrinsic source
surface brightness.

## Equation-term coverage

| Equation quantity | Current Artifact support | What the code currently does | Scientific limitation |
|---|---|---|---|
| $D_{e,p}$ | `oriented_detector_image`; raw records live in the separate raw catalog | Raw science and calibration arrays are overscan-corrected, oriented, and converted to electrons in memory. | There is no permanently registered raw-detector Artifact; raw-file identity and headers are provenance instead. |
| $B_{e,p}$ | `master_bias`, `overscan_model`, `overscan_corrected_image`, `bias_stability` | A robust amplifier master is retained and subtracted from science and reusable response-calibration inputs; row overscan is applied in memory and long-term bias summaries are analytic. | The exposure-specific overscan prediction is not retained as its own Product. |
| $t_e d_p$ | `master_dark`, `effective_exposure_time` | The electron-valued, bias-included master requires a Product-local reference exposure time and bias convention. Dark construction requires a common positive input exposure time. Science and reusable LDLS/twilight/master-science inputs use the same selected-Product state to subtract an exposure-time-scaled `(master_dark - master_bias)` residual and merge its mask through extraction. | The retained master is not the preferred smooth dark-rate state in electrons per second, hot pixels are represented only by the current empirical mask, and no dark-model uncertainty component is available for variance propagation. |
| $L_{e,p}$ | `ccd_scattered_light_model`, `scatter_subtracted_image`, `candidate_scattered_light_model` | Compact gap-constrained surfaces and residual samples are retained for science exposures and for each paired response-master extraction; evaluated corrected CCD images remain in memory. | The baseline is an empirical smooth gap surface, not the forward fiber-wing/crosstalk component; science and each calibration illumination are fitted independently rather than sharing a learned wing kernel. |
| $P_{e,f,p}(\lambda)$ | `trace_map`, `aperture_extracted_spectrum`; retained master spectra carry exact compact aperture and validity evidence | Trace geometry guides the same exact fractional five-pixel top-hat aperture for science and response calibrations, with detector masks applied to retained master spectra. Calibration spectra retain the start row, contributing-row bit mask, boundary weights, effective width, and validity needed to reconstruct that aperture operator exactly. | There is no registered empirical fiber-profile or 2D PSF/LSF Product, so $P$ is approximated by aperture geometry rather than inferred as a detector profile; science-exposure aperture evidence remains run-local. |
| $\lambda_p$ | `master_hg`, `master_cd`, `master_arc`, `wavelength_map` | Separate lamp masters are composed, identified, fitted, and interpolated into per-fiber wavelength rows. The Product requires candidate and accepted-line evidence, seed status and coefficients, interpolation/extrapolation flags, residuals, and exact applied-mask indices. Invalid or non-increasing rows are excluded. | Barycentric correction and an observation-frame wavelength Product are absent. Identification is still the current sparse seed-row heuristic rather than a globally constrained physical dispersion model with formal line-association probabilities. |
| $R^{\mathrm{pixel}}_p$ | `master_ldls`, `pixel_mask` | LDLS residuals supply a flat-response defect mask. Dark/LDLS masks condition response extraction and wavelength-input interpolation; the wavelength Product retains exact applied-mask indices and parent identity. | The science image is not divided by a pixel-sensitivity map. `master_ldls` is intentionally not treated as a pure pixel flat, and there is no standalone time-aware defect Product. |
| $R^{\mathrm{fiber}}_{e,f}(\lambda)$ | `within_amp_fiber_normalization`, `fiber_normalization`, `fiber_response_model` | The retained calibration-time within-amplifier Product combines scatter-corrected LDLS fine structure with scatter-corrected twilight broad and residual structure and requires that factorization, wavelength grid, validity, and amplifier anchor as payload evidence. `ExposureTask` selects the exact Product named by the group response and divides by it in memory. | `fiber_normalization` has no current producer. The implemented normalization is an empirical illumination/response combination, not a uniquely identified intrinsic fiber throughput. |
| $R^{\mathrm{amp}}_{e,a}$ | `amp_to_amp_normalization`, `fiber_response_model` | One calibration-build Product compares all sibling center-track twilight anchors, retains complete amplifier keys and factors, and is selected and applied before global science-fiber assembly. | The uniform center-track twilight assumption remains explicit and unvalidated as a general illumination model; the build Product is relative, not an absolute throughput scale. |
| $T^{\mathrm{instrument}}_e(\lambda)$ | `baseline_relative_response`, `fiber_response_model`, `final_exposure_response` | One selected instrument-epoch `baseline_relative_response` is interpolated and divided exactly once after sky subtraction. The default Remedy Product has `atmospheric_content=absorbed_unknown`; a future `removed_with_model` Product instead represents instrument plus named reduction-method response and carries the extinction identity and calibration airmasses used during construction. | The imported baseline remains atmosphere-contaminated because its calibration airmass is unknown. No atmosphere-separated measured baseline has yet been published, and neither convention establishes absolute throughput. `final_exposure_response` is scratch-only and superseded. |
| $T^{\mathrm{atmosphere}}_e(\theta,\lambda)$ | `atmospheric_extinction_model`; exposure airmass and evaluated factors are retained in response-state metadata | The McDonald model retains $k(\lambda)$ in mag/airmass. With a compatible `removed_with_model` baseline and explicit header `AIRMASS=X`, the code multiplies by $10^{0.4k(\lambda)X}$ once, propagates finite model uncertainty, and masks or fails outside the model range. Gray transparency, mirror illumination, fiber illumination, and seeing remain separate. | The default Remedy path forbids this correction. The McDonald uncertainty is unavailable, airmass uncertainty is not propagated, and variable chromatic clouds, telluric absorption, and directional dependence are not modeled. |
| $\mathcal{M}_e(\theta)$ | `initial_astrometry`, `source_detection_catalog`, `catalog_match_table`, `final_astrometry`, `fiber_sky_coordinates` | Header TAN geometry is retained and a catalog shift/rotation fit is attempted; failed catalog refinement falls back to header astrometry with degraded QA. | The current fit is simpler than the full spatial model and does not supply formal mapping uncertainty as a separate Product. |
| PSF, $\Delta\theta_e^{\mathrm{DAR}}(\lambda)$, and $C_{e,f}$ | Astrometry and dither Products provide partial geometry only | Fiber focal-plane and sky coordinates, nominal/refined dither offsets, and footprint coverage are retained. | There is no PSF, DAR, aperture-coupling, or spatial reconstruction Product, so source-to-fiber coupling is not inverted. |
| $F^{\mathrm{sky}}_{e,f}(\lambda)$ | `sky_fiber_mask`, `sky_model`, `fiber_sky_prediction`, `sky_subtracted_spectrum`, `candidate_sky_model` | A supersampled common incident sky is fitted at exposure scope, with per-fiber illumination coefficients, then integrated onto each native fiber grid and subtracted in memory. | The baseline assumes one incident sky spectrum, has no accepted fiber-specific LSF, and does not propagate sky-model covariance into final variance. |
| $S^{\mathrm{source}}_e(\theta,\lambda)$ | `source_detection_catalog`, `calibrated_fiber_observation` | Source detections support astrometry; a complete observation retains sky-subtracted, response-normalized fiber samples with positions, wavelength, variance, masks, and the response convention. Compatible atmosphere-separated baselines can additionally produce above-atmosphere relative samples. | The default Remedy result remains atmosphere-contaminated. The Product concatenates exposures rather than coadding or reconstructing them and is not corrected for PSF/DAR coupling or absolute throughput. |
| $N_{e,p}$ and propagated uncertainty | `read_noise`, `detector_variance`, `pixel_mask`, `extracted_variance`, final variance/mask planes | Read noise plus non-negative Poisson variance is propagated through science extraction with exact weights. Bias scatter is added to the corrected response-frame variance state, while dark/LDLS masks condition response-master extraction; retained master spectra preserve the exact compact aperture/mask validity needed to audit which detector samples contributed. | The robust calibration combine and retained master-spectrum extraction do not yet carry response-frame variance forward. Standalone noise kinds are not published, and dark, sky, response, wavelength, astrometric, systematic, and covariance contributions remain incomplete. |
| $t_e$ and exposure/observation state | `exposure_mode_classification`, `effective_exposure_time`, `exposure_completion_manifest`, observation/dither kinds | The code preserves primary/parallel classification, the applied exposure-time policy, amplifier coverage, membership, offsets, and per-exposure state. | These Products condition the inverse and its applicability; they do not themselves estimate an optical term. |

## Product-local dark state and remaining physical gap

`master_bias` and `master_dark` are retained calibration Products and should
remain retained. They are not members of the cacheable dense-master policy.
The current `master_dark` payload is an electron-valued dense master that still
contains bias. Its Artifact contract now requires a finite positive
`reference_exposure_time_seconds` and the explicit
`bias_convention="included_in_electron_master"`; its producer accepts only
inputs sharing that exposure time. This makes `(master_dark - master_bias) /
reference_exposure_time_seconds` reproducible from the selected Product for
both science and reusable response-calibration consumers. Exact raw parents
remain lineage evidence, but are not application-time inputs.

This completes the repository's existing representation rather than claiming
the preferred physical model from the dark-current knowledge note. A future
dark revision should normalize compatible bias-subtracted inputs into a smooth
electron-per-second state, separate unstable hot-pixel evidence, and retain a
dark uncertainty component. Until that evidence exists, the current mask is
propagated but dark-model variance cannot be.

## Complete registered-kind inventory

The following tables account for all 59 keys currently in `ARTIFACT_KINDS`.
Lifecycle abbreviations are **C** = canonical, **M** = model, **A** = analysis,
and **S** = scratch. “Production” means a normal calibration, exposure, or
observation task currently publishes the kind. “Run-local/embedded” means the
quantity is computed and used, but the standalone registered kind is not
published. `ArtifactService` rejects permanent publication of every S kind.

### Detector state and additive terms

| Kind | Scope / lifecycle | Scientific layer and equation role | Current support |
|---|---|---|---|
| `master_bias` | amplifier / C | Inferred calibration evidence for $B_{e,p}$; includes the master and per-pixel scatter. | Production calibration Product; master payload is permanently retained. |
| `master_dark` | amplifier / C | Inferred evidence used to predict $t_e d_p$; includes a dense electron master and dark-pixel mask. | Production calibration Product; permanently retained. Publication requires its reference exposure time and included-bias convention, construction requires one common positive input exposure time, and both science and response calibration consume only that selected Product-local state. No dark uncertainty component is currently produced. |
| `read_noise` | amplifier / C | Measurement constraining the stochastic term $N_{e,p}$. | Run-local/embedded as detector and master-bias summaries plus QA; no standalone publisher. |
| `gain` | amplifier / C | Configuration-like conversion from ADU to electrons, required before noise calculation. | Run-local/embedded from the header with a 0.85 electron/ADU fallback; no standalone publisher. |
| `pixel_mask` | pixel / C | Detector-defect evidence conditioning which $D_{e,p}$ values enter the inverse. | Run-local/embedded by combining dark, LDLS, and non-finite masks. Response-master spectra retain exact compact aperture/sample validity, and wavelength maps retain the applied detector-mask indices and mask-parent lineage; no standalone pixel-mask Product or time-aware defect model is published. |
| `detector_variance` | pixel / C | Diagonal estimate of detector-level $N_{e,p}$. | Computed and propagated through science extraction in memory. Response-frame correction also updates it, but the current robust calibration combine does not consume or retain it; there is no standalone publisher. |
| `oriented_detector_image` | amplifier / C | Evaluated detector state after overscan, orientation, and gain. | Computed in memory; no standalone publisher. |
| `overscan_model` | amplifier / C | Exposure-specific electronic-offset evidence contributing to $B_{e,p}$. | Computed as a per-row raw-coordinate model in memory; no standalone publisher. |
| `overscan_corrected_image` | amplifier / C | Evaluated raw-coordinate detector state after overscan subtraction. | Computed in memory; no standalone publisher. |
| `reduced_science_image` | amplifier / S | Dense intermediate after detector and additive calibration. | Scratch-only, superseded legacy kind; normal production keeps an in-memory `ReducedAmplifierState`. |
| `ccd_scattered_light_model` | physical CCD / M | Fitted compact model and residual evidence for $L_{e,p}$. | Production science-exposure and calibration-response Product. Calibration fits are joint physical-CCD computations retained as amplifier-addressable projections for scope-local lineage; evaluated dense surfaces are deliberately not retained. |
| `scatter_subtracted_image` | physical CCD / S | Evaluated state $D-B-t d-L$ with variance, masks, and CCD seam lineage. | Scratch-only; computed in the run-local physical-CCD state. |

### Calibration illumination, geometry, and extraction

| Kind | Scope / lifecycle | Scientific layer and equation role | Current support |
|---|---|---|---|
| `master_ldls` | amplifier / C | Detector-corrected robust LDLS evidence for traces, masks, and fine relative response. | Production calibration Product with bias/dark parents and explicit correction convention. Its dense component is releasable only after QA-valid trace, compact physical-CCD scatter, extracted-spectrum, and response descendants each have all required checksum-verified payload components; the LDLS mask remains. |
| `master_hg` | amplifier / C | Hg lamp evidence for $\lambda_p$. | Production calibration Product; dense release requires QA-valid `master_arc` and `wavelength_map` descendants with every required component present and checksum-verified, including mandatory line evidence. |
| `master_cd` | amplifier / C | Cd lamp evidence for $\lambda_p$. | Production calibration Product; same component-complete retention boundary as Hg. |
| `master_arc` | amplifier / C | Composed Hg+Cd evidence used to infer $\lambda_p$. | Production calibration Product; its rebuildable dense component is releasable only after a QA-valid, component-complete, checksum-verified `wavelength_map`. |
| `master_twilight` | amplifier / C | Detector-corrected twilight illumination evidence anchoring broad fiber response and amplifier level. | Production calibration Product with bias/dark lineage; dense release requires QA-valid compact physical-CCD scatter, exact-aperture extracted-spectrum, and factorization-complete response descendants with verified required components. |
| `master_sci` | amplifier / C | Detector-corrected science-like illumination evidence used as an independent response/mask validation measurement. | Optional production calibration Product; it does not fit the response. Dense release requires QA-valid compact physical-CCD scatter, exact-aperture extracted-spectrum, and terminal spectral-mask descendants with verified required components. |
| `extracted_master_ldls_spectrum` | fiber / C | Mask-aware aperture-extracted, scatter-subtracted LDLS evidence constraining fine $R^{\mathrm{fiber}}$. | Production calibration Product retaining spectrum, valid-pixel fraction, effective aperture width, extraction-valid mask, exact compact start-row/contributing-row/boundary-weight evidence, and compact scatter lineage. |
| `extracted_master_twilight_spectrum` | fiber / C | Mask-aware aperture-extracted, scatter-subtracted twilight evidence constraining broad $R^{\mathrm{fiber}}$ and $R^{\mathrm{amp}}$. | Production calibration Product with the same exact compact aperture, physical-CCD scatter, and extraction policy as LDLS. |
| `extracted_master_sci_spectrum` | fiber / C | Scatter-subtracted extracted transfer-test evidence for response validation. | Optional production calibration diagnostic Product with exact compact aperture/mask validity evidence and compact scatter lineage. |
| `fiber_wavelength_spectral_mask` | fiber / C | Wavelength-dependent usability evidence derived from master-science spectra and $\lambda_p$. | Production calibration Product; terminal evidence for master-science payload retention. |
| `trace_map` | amplifier / C | Evaluated geometry locating fibers on the detector; the current proxy needed for $P_{e,f,p}$. | Production calibration Product requiring sampled positions, sample-valid masks, fit residuals, valid-sample counts, per-fiber RMS, and reference-interpolated fiber flags. |
| `wavelength_map` | amplifier / C | Evaluated $\lambda_p$ state on the fiber-by-dispersion grid. | Production calibration Product requiring per-fiber residual RMS, arc identifications, detected candidates, accepted lines, seed attempt/success/failure state and coefficients, interpolation/extrapolation flags, and exact applied-mask indices; applied LDLS/dark masks are explicit parents. |
| `aperture_extracted_spectrum` | fiber / S | Inverse intermediate collapsing detector pixels to native-grid fiber counts. | Scratch-only; exact fractional aperture products are computed in memory. |
| `extracted_variance` | fiber / S | Diagonal uncertainty paired with aperture extraction. | Scratch-only; computed in memory and later carried in the final observation variance plane. |

### Relative response, sky, astrometry, and science result

| Kind | Scope / lifecycle | Scientific layer and equation role | Current support |
|---|---|---|---|
| `within_amp_fiber_normalization` | fiber / C | Fitted empirical factor for wavelength-dependent within-amplifier $R^{\mathrm{fiber}}$. | Production calibration Product fitted from detector- and scatter-corrected LDLS/twilight spectra, with optional master-science validation and explicit calibration-build lineage. Its LDLS fine term, twilight broad and residual terms, wavelength grid, validity, and amplifier anchor are required payload evidence. |
| `amp_to_amp_normalization` | exposure / C | Evaluated center-track-build-wide $R^{\mathrm{amp}}$ factors. | Production calibration Product with no science `exposure_id`; one coherent build compares all sibling amplifier anchors and retains full amplifier keys, levels, factors, reference scale, coverage, and exact within-amplifier parents. |
| `fiber_normalization` | fiber / C | Intended multiplied within-amplifier and amplifier factor. | Registered gap: no current producer or consumer. Its operational role is split across normalization in memory and `fiber_response_model`. |
| `initial_astrometry` | exposure / C | Header-derived prior for $\mathcal{M}_e$. | Production exposure Product. |
| `source_detection_catalog` | exposure / C | Detection evidence used to constrain $\mathcal{M}_e$; not yet a final science-source catalog. | Production exposure Product. |
| `catalog_match_table` | exposure / C | External-catalog association and residual evidence for $\mathcal{M}_e$. | Production exposure Product; empty/degraded output is retained when the catalog is unavailable or the fit fails. |
| `final_astrometry` | exposure / C | Evaluated astrometric mapping parameters. | Production exposure Product; catalog fit when successful, otherwise an explicit header-TAN fallback. |
| `fiber_sky_coordinates` | fiber / C | Evaluated mapping of each fiber aperture into sky coordinates. | Production exposure Product with fiber identity and focal-plane coordinates. |
| `sky_fiber_mask` | fiber / C | Selection evidence identifying samples used to infer $F^{\mathrm{sky}}$. | Production exposure Product with broadband evidence and fiber identity. |
| `incident_sky_spectrum` | exposure / S | Dense common-sky intermediate before per-fiber prediction. | Scratch-only, superseded by the retained compact `sky_model`. |
| `sky_model` | exposure / M | Fitted latent common $F^{\mathrm{sky}}$ model with variance, sample counts, and fiber coefficients. | Production exposure model Product. |
| `fiber_sky_prediction` | fiber / S | Evaluated $F^{\mathrm{sky}}_{e,f}(\lambda)$ on each native fiber grid. | Scratch-only; computed inside sky subtraction. |
| `sky_subtracted_spectrum` | fiber / S | Inverse intermediate estimating source-plus-residual counts after removing sky. | Scratch-only; computed in memory. |
| `baseline_relative_response` | instrument epoch / C | Independently selectable empirical response state with wavelength, response, uncertainty, mask, units, provenance, method identity, applicability, and an atmospheric-content convention. | The imported Remedy `throughput / normalization` seed is `absorbed_unknown`, with unknown uncertainty and atmospheric contamination. A later `removed_with_model` baseline must identify its construction extinction model and calibration airmasses and supersedes rather than composes with the seed. |
| `atmospheric_extinction_model` | instrument epoch / M | Selectable site extinction coefficient $k(\lambda)$ with wavelength, mag/airmass coefficient, uncertainty, mask, units, site, provenance, and applicability. | The default McDonald Product is imported on demand only for a compatible atmosphere-separated baseline. It has unavailable uncertainty, is linearly interpolated without extrapolation, and is never applied with the Remedy seed. |
| `exposure_illumination_correction` | exposure / C | Exposure-specific empirical fiber/amplifier factor derived from selected sky fibers. | Production exposure Product; used both as sky-model coefficients and the final numerical response division. |
| `final_exposure_response` | exposure / S | Intended dense evaluated combination of response factors. | Scratch-only, superseded legacy kind; not produced by the current path. |
| `fiber_response_model` | exposure / M | Compact evaluated state linking selected calibration-build within-amplifier knots and amplifier factors with exposure illumination. | Production exposure model Product. It links the independently selected baseline and, only for `removed_with_model`, the selected extinction Product; metadata records airmass and separate gray factors. It does not encode absolute calibration. |
| `calibrated_fiber_observation` | observation / C | Current terminal inverse result: concatenated native-grid fiber flux, variance, mask, wavelength, identity, and position for the member exposures. | Production observation Product only when the dither assignment is complete and every member has a run-local calibrated state. It is not a coadd or spatial reconstruction. |

### Operational context, QA, and analytics

| Kind | Scope / lifecycle | Scientific layer and equation role | Current support |
|---|---|---|---|
| `validation_report` | observation / C | Science-acceptance evidence comparing a representative reduction and recording limitations. | Published by the dedicated observation-validation CLI, not by every normal observation run. |
| `analysis_materialization` | observation / A | Bounded retained intermediate for a declared analytic study. | Supported by `AnalysisStudyService` with selection, byte budget, lineage, and retention policy. |
| `bias_stability` | amplifier / A | Time-series evidence testing whether a stable/nightly $B_{e,p}$ model is adequate. | Produced by the bias stability analysis. |
| `candidate_sky_model` | exposure / A | Unpromoted analytic alternative for $F^{\mathrm{sky}}$. | Supported through the candidate-model materialization path; never selected as production calibration without separate promotion. |
| `candidate_scattered_light_model` | physical CCD / A | Unpromoted analytic alternative for $L_{e,p}$. | Supported through the same candidate-model path. |
| `exposure_mode_classification` | exposure / C | Operational state selecting the exposure-time policy and interpretation of $t_e$. | Production exposure Product. |
| `effective_exposure_time` | exposure / C | Evaluated $t_e$ under the primary/parallel shutter policy. | Production exposure Product retaining source header fields and policy version. |
| `exposure_completion_manifest` | exposure / C | Coverage and failure evidence defining where the inverse was actually evaluated. | Production exposure Product; records amplifier and excluded-wavelength-fiber coverage. |
| `observation_exposure_state` | exposure / C | Per-exposure seeing, transparency proxy, response summary, astrometry, and coverage context. | Production observation-assembly Product; preserves atomic exposure state. |
| `observation_membership` | observation / C | Evidence defining which exposure measurements belong together. | Production observation Product. |
| `dither_assignment` | dither set / C | Nominal sequence and assignment evidence used to relate fiber samples spatially. | Production observation Product; primary observations use the configured nominal pattern and parallel observations use no dither. |
| `dither_registration` | dither set / C | Evaluated nominal/refined relative offsets and residuals. | Production observation Product with nominal fallback where catalog-refined astrometry is unavailable. |
| `dither_coverage_map` | dither set / C | Geometric footprint evidence for spatial sampling. | Production observation Product; explicitly a footprint, not cube reconstruction. |
| `observation_summary` | observation / C | QA/usability summary over member exposure states and coverage. | Production observation Product; does not average away the per-exposure measurements. |

## Retention boundaries

### Dense calibration masters

`master_bias` and `master_dark` are permanently retained and are outside the
dense-component release policy. For `master_dark`, that retention includes the
dense electron master, dark-pixel mask, required reference exposure time and
bias convention, registry record, raw-parent lineage, and checksums.

The implemented retention service treats only the dense payload components of
`master_hg`, `master_cd`, `master_arc`, `master_twilight`, `master_ldls`, and
`master_sci` as rebuildable. It keeps their registry records, provenance, QA,
checksums, component descriptions, and non-evictable components such as the
LDLS response mask. It also requires active descendants whose QA is completed
and neither failed nor unusable. For each gated descendant kind, one such
Product must contain every ontology-required component; every component must
be in the `present` state, exist on disk, carry a registered checksum, and
match that checksum. The current descendant gates are:

| Dense payload | Current validated-descendant gate |
|---|---|
| `master_hg`, `master_cd` | `master_arc` and `wavelength_map` |
| `master_arc` | `wavelength_map` |
| `master_twilight` | `ccd_scattered_light_model`, `extracted_master_twilight_spectrum`, and `within_amp_fiber_normalization` |
| `master_ldls` | `trace_map`, `ccd_scattered_light_model`, `extracted_master_ldls_spectrum`, and `within_amp_fiber_normalization` |
| `master_sci` | `ccd_scattered_light_model`, `extracted_master_sci_spectrum`, and `fiber_wavelength_spectral_mask` |

This is now an evidence-complete boundary for the current algorithms rather
than only a Product-kind gate. `arc_identification` and the candidate, accepted
line, seed-fit, interpolation/extrapolation, and applied-mask evidence are
required `wavelength_map` components. Trace sample/rejection state, exact
compact response-master aperture geometry, compact physical-CCD scatter
evidence, and the response factorization are likewise required by their
canonical kinds. A fitted map or normalization alone is therefore
insufficient, and a missing or checksum-tampered required component blocks
release.

The boundary does not claim that the compact descendants reproduce the dense
illumination image or make raw data dispensable. These six dense components are
cacheable because the raw parents and versioned methods can rebuild them, while
their compact descendants preserve every currently required measurement and
decision needed to audit the downstream inference. New algorithms that depend
on additional non-reconstructable evidence must add it to the canonical
required-component contract before they may use this release boundary.

### Registration does not require publication

The 58 registered kinds are a vocabulary, not a mandate to publish every
quantity on every run. Retain a quantity when it supports scientific reuse,
comparison, QA, modeling, or reconstruction without preserving a much larger
parent. Dense evaluated detector states and ordinary computational
intermediates should remain run-local when their retained parents, compact
evidence, and reproducible method are sufficient. This is the current reason
for keeping `scatter_subtracted_image`, `aperture_extracted_spectrum`,
`extracted_variance`, `fiber_sky_prediction`, `sky_subtracted_spectrum`, and
`final_exposure_response` scratch-only; registration of detector-state kinds
such as `oriented_detector_image` and `detector_variance` does not by itself
justify permanent publication.

## What can be claimed scientifically today

The current code supports a scientifically traceable detector-to-fiber
reduction in which:

- bias, a scaled dark residual, and a physical-CCD scattered-light estimate are
  removed, with dark scaling derived only from the selected `master_dark`'s
  required Product-local scaling state;
- detector variance and masks are propagated through a fractional aperture;
- trace and wavelength geometry are selected with explicit calibration
  provenance and retained sample, line, residual, interpolation, and applied-mask
  evidence;
- empirical within-amplifier, amplifier-to-amplifier, and exposure illumination
  factors are applied, with the first two selected from one coherent calibration
  response build rather than refitted from the science exposure;
- one independently selected, non-unity empirical baseline effective response is
  divided exactly once, with baseline, exposure illumination, and measured gray
  transparency retained as separate factors and finite response uncertainty
  propagated when available;
- a compatible `removed_with_model` baseline conditionally activates one
  retained wavelength-dependent McDonald extinction model at explicit exposure
  airmass, with no extrapolation and finite coefficient uncertainty propagated;
- dense Hg, Cd, arc, LDLS, twilight, and optional master-science components may
  be released only after QA-valid descendants preserve all currently required
  compact evidence with verified checksums;
- a common exposure sky is inferred and subtracted on native fiber wavelength
  grids;
- astrometry, dither identity, coverage, QA, and the state of each exposure are
  retained; and
- a complete observation can be published as positioned, sky-subtracted fiber
  spectra with diagonal variance and masks.

The current code labels the terminal spectral planes as response-corrected
electrons rather than physical flux. The default Remedy result is not
atmosphere-corrected: its `absorbed_unknown` convention forbids a separate
extinction layer and does not separately invert $T^{\mathrm{instrument}}$ and
$T^{\mathrm{atmosphere}}$. A compatible future `removed_with_model` baseline
can produce an above-atmosphere relative spectrum using the selected mean
extinction curve, but this still does **not** support the stronger claim that
`calibrated_fiber_observation` is an absolutely spectrophotometric measurement
of intrinsic source surface brightness. PSF/DAR coupling, aperture loss,
variable chromatic transparency, and absolute scale remain unresolved.

## Implementation priorities supported by this crosswalk

1. **Construct the first validated atmosphere-separated measured baseline.**
   Reduce retained primary-standard evidence with explicit calibration
   airmasses and the selected extinction Product under fully identified
   extraction, PSF, source-capture, contribution-correction, and calibration
   configurations. Retain finite uncertainty and held-out validation, then
   supersede the imported Remedy Product without composing response layers.
2. **Measure chromatic atmospheric variability and absolute response.** Keep
   gray transparency separate from the mean extinction curve, add uncertainty
   for airmass and atmospheric coefficients, model departures from the mean
   atmosphere where evidence permits, and add an absolute scale only before
   claiming spectrophotometric source flux.
3. **Develop profile, PSF, DAR, and coupling models.** Measure the operators
   currently approximated by the fixed aperture and positional geometry.
   Introduce an Artifact kind only for evidence or evaluated states that meet
   the retention rule above; versioned model specifications may remain outside
   the Artifact vocabulary.
4. **Expand uncertainty propagation and define the eventual source-domain
   result.** Add a physical dark-rate/uncertainty model and the missing sky,
   response, wavelength, astrometric, coupling, systematic, and covariance
   terms, then state precisely what estimate of
   $S^{\mathrm{source}}(\theta,\lambda)$ the eventual result represents. Until
   then, keep describing the current terminal Product as calibrated fiber
   measurements.

## Maintenance rule

When `ARTIFACT_KINDS` changes, update this document in the same change and
answer four questions for every added or altered kind:

1. Which forward-model term, inverse quantity, or conditioning state does it
   represent?
2. Is it evidence, a fitted model, an evaluated state, an inverse intermediate,
   or a terminal science/QA result?
3. Which task publishes it, or is it deliberately run-local/scratch-only?
4. What scientific claim remains unsafe even when the Product exists?

These questions keep the Artifact vocabulary connected to scientific inference
rather than allowing it to become only a list of pipeline filenames.
