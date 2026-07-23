# VIRUSFlow Steps 8–10 Scientific Acceptance

Status: Steps 8 through 10 passed their internal implementation and real-data gates on 2026-07-22. Step 11 is unauthorized and unstarted.

## Selected 20260609 identities

- Physical CCD exposure: `20260609T031649.6`.
- Left physical CCD: `060+003+206+LL+S/N 0039` plus `060+003+206+LU+S/N 0039`.
- Right physical CCD: `060+003+206+RU+S/N 0039` plus `060+003+206+RL+S/N 0039`.
- Full-exposure candidate: `20260609T031649.6`, with 300 raw amplifier members across 75 IFUSLOTs.
- Observation/dither candidate: `OBSID=6`, exposures `20260609T031649.6`, `20260609T031859.3`, and `20260609T032112.2`. Direct headers identify the same `WD1327-083` target, pointing, operational observation, and sequential exposure membership. No member is fabricated.

The full 20260609 inventory contains 14,100 FITS members: 4,200 zro, 900 dark, 900 LDLS, 2,400 comparison, 1,500 twilight, and 4,200 science files. There are 14 science exposures; each has 300 raw amplifier members and 75 IFUSLOTs. Complete execution corrected the preliminary inventory inference: repository trace configuration resolves for all 300 selected-exposure amplifiers. One amplifier, `095+004+426+RU+S/N 0048`, has eight real comparison inputs whose pixel arrays are identically zero, so its wavelength and wavelength-dependent extraction Products are explicitly unavailable.

## Step 8 real-data evidence

The gate used an isolated temporary SQLite registry and artifact directory. It did not modify `virusflow.sqlite3`.

| Fact | Left LL+LU | Right RU+RL |
|---|---:|---:|
| Gap samples | 70,751 | 67,198 |
| Retained fit samples | 55,516 | 52,667 |
| Holdout samples | 15,230 | 14,525 |
| Fit residual robust sigma (electron) | 2.95525 | 2.93268 |
| Holdout residual robust sigma (electron) | 2.99245 | 2.94504 |
| Boundary residual robust sigma (electron) | 2.79946 | 2.83313 |
| Cross-amplifier model discontinuity (electron) | 0.000228016 | 0.000174002 |
| Model/source p95 amplitude ratio | 0.08230 | 0.07745 |
| QA / usability | pass / usable | pass / usable |

Each `ccd_scattered_light_model` retains the model, gap/fit/holdout masks, residual image, coefficients, seam mask, explicit zero-row imaging-gap mask, source-amplifier map, and inverse source-row coordinates. Each `scatter_subtracted_image` separately retains the corrected image, variance, pixel mask, seam/gap evidence, and source coordinates. All components loaded through ArtifactService with checksum verification and normalized lineage to both immutable amplifier Products and both trace Products.

The baseline is intentionally a robust total-degree-two physical-CCD surface fitted to trace-derived group gaps, with deterministic held-out column chunks. It is the simplest approved gap-constrained baseline, not a forward scattered-light model. The retained masks, residuals, fit parameters, algorithm version, and QA allow later comparison with spline, mesh, and forward refinements.

## Step 9 real-data evidence

The corrected gate used `/tmp/virusflow-step9-acceptance.AsfiSO`, an isolated SQLite registry and artifact directory. It did not modify `virusflow.sqlite3`. The preliminary immutable revisions exposed and retained a cascade defect in which wavelength failure incorrectly suppressed a whole physical CCD. The corrected revisions remove that false dependency and localize unavailable extraction to its originating amplifier.

| Fact | Result |
|---|---:|
| Raw / reduced amplifiers | 300 / 300 |
| Physical CCDs | 150 / 150 |
| Extracted amplifiers / IFUSLOTs | 299 / 75 |
| Explicit unavailable wavelength/extraction | `095+004+426+RU+S/N 0048` |
| Pan-STARRS catalog / candidate / accepted rows | 3,682 / 53 / 5 |
| Final astrometric residual RMS | 0.724692 arcsec |
| Selected sky fibers / IFUSLOTs | 32,819 / 75 |
| Sky residual robust sigma | 9.68230 electron |
| Amplifier normalization robust sigma | 0.113885 |
| Final response median / outlier fraction | 1.00001 / 0.00334448 |
| Effective time | 67.399394048 s from `EXPTIME` |
| Exposure completion QA / usability | warn / degraded |

The Pan-STARRS provider uses the supplied robust STScI CSV interface and the actual DR2 stack schema (`gPSFMag` normalized to the provider-independent `gMeanPSFMag` contract). Live access succeeded; no real catalog matches were fabricated. The final astrometry retains initial TAN parameters, 3,682 catalog rows, all candidate/rejected/accepted match facts, shift/rotation fit evidence, and fiber sky coordinates.

The five-pixel extraction is a sum. The stored variance is `sum(w_i**2 * variance_i)` using the exact stored fractional weights after masks. On interior unmasked samples it agrees with `virusflow.algorithms.fiber.get_spectra` to `1e-5` absolute. At a detector edge, the legacy routine silently zeroes the entire fiber when any column crosses the boundary; the new version intentionally marks only invalid columns NaN and retains valid columns, aperture starts, effective widths, valid fractions, masks, and weights.

Within-amplifier and exposure-wide twilight factors remain separate and the final normalization retains both. Twilight scatter is corrected at physical-CCD scope in memory before factor estimation. The explicit unity relative-response baseline is provisional and the final response remains degraded rather than claiming an unavailable historical response curve. The real primary exposure uses `EXPTIME`; the `PEXPTIME - 8 s` rule remains limited to classifier-identified parallel exposures.

The first pass found that the LDLS response mask for `028+042+413+RL+S/N 0031` masked 98.4496% of detector samples and destroyed an otherwise valid arc solution. The corrected, versioned bounded-mask policy rejects near-global flat masks while retaining their fraction and decision; the amplifier then has 11 seed rows below the 1 Å criterion and a wavelength Product. In contrast, all eight raw comparison arrays for `095+004+426+RU+S/N 0048` are exactly zero, so its missing wavelength Product is retained as a real input limitation.

## Step 10 real-data evidence

All three OBSID 6 members were run as complete, independent Exposure entities in the same isolated acceptance workspace:

| Exposure | Reduced / physical CCD / extracted | Catalog accepted | Astrometric RMS (arcsec) | Response median | Effective time (s) |
|---|---:|---:|---:|---:|---:|
| `20260609T031649.6` | 300 / 150 / 299 | 5 | 0.724692 | 1.00000953 | 67.399394048 |
| `20260609T031859.3` | 300 / 150 / 299 | 5 | 0.724732 | 0.999958771 | 67.448336384 |
| `20260609T032112.2` | 300 / 150 / 299 | 7 | 0.849316 | 0.999908529 | 67.899279872 |

Each Exposure retains its own detector state, sky, astrometry, response, effective time, QA, revisions, and lineage. The real headers do not contain usable seeing or transparency fields; the corresponding per-exposure state values are retained as NaN rather than imputed. Every exposure is degraded by the same explicitly unavailable zero-arc RU amplifier, but all 75 IFUSLOTs and all 150 physical CCDs remain represented.

The explicit membership is `20260609T031649.6`, `20260609T031859.3`, and `20260609T032112.2`, ordered by timestamps and consistent OBSID 6 evidence. No exposure is synthesized. The provisional, versioned nominal offsets are `(0.0, 0.0)`, `(1.27, 0.73)`, and `(0.0, 1.46)` arcsec. Catalog-refined relative offsets are `(0.0, 0.0)`, `(-2.01919, 6.72512)`, and `(0.168890, -0.0472918)` arcsec. Their residual RMS is 2.85951 arcsec, above the explicit 1.5 arcsec warning threshold. Registration and observation-summary QA are therefore `warn/degraded`; the nominal configuration was not tuned to force agreement.

The refined-footprint coverage map has covered-grid fraction 0.457677, hole fraction 0.542323, duplicated-covered fraction 0.700551, and maximum multiplicity three. It is explicitly a fiber-footprint diagnostic, not cube reconstruction. The large registration discrepancy and coverage holes are retained as a scientific investigation target.

Focused tests cover incomplete two-exposure sets, an extra fourth exposure, ambiguous sequence evidence, repeated identities, missing amplifier coverage, nominal-versus-refined offsets, checksum loading, normalized lineage, and preservation of all per-exposure state. Canonical `query_observation`, `query_dither_set`, and read-only `query_observation_set` boundaries return the grouping Products without merging Exposure state.

## Test ledger

- Pre-change baseline: `55 passed`.
- Step 8 focused transform, algorithm, Product, and Task gate: `12 passed`.
- Full suite after Step 8: `61 passed` with one pre-existing flat median-filter warning.
- Step 9 focused algorithm, catalog-provider, Product-contract, serialization, and Task gate: `17 passed`.
- Full suite at the Step 9 milestone boundary: `76 passed` with one pre-existing flat median-filter warning.
- Step 10 focused assignment, registration, coverage, entity, query, and Task gate: `5 passed`.
- Final complete suite: `76 passed` with the same pre-existing flat median-filter warning.
- Reproducible `python -m virusflow.cli.verify_steps_8_10 --workspace /tmp/virusflow-step9-acceptance.AsfiSO --output-dir /tmp/virusflow-step9-acceptance.AsfiSO/report --reuse-products` gate: PASS. It re-inventoried 14,100 inputs, loaded every named component through ArtifactService with checksum verification, validated normalized lineage and current logical revisions, and generated `steps_8_10_scientific_acceptance.md` plus three diagnostic figures and JSON inventory, fact, and manifest files.
