# VIRUSFlow Steps 8–10 Scientific Acceptance

Status: Step 8 passed its internal implementation and real-data gate on 2026-07-22. Steps 9–10 remain in progress in the authorized autonomous tranche. Step 11 is unauthorized and unstarted.

## Selected 20260609 identities

- Physical CCD exposure: `20260609T031649.6`.
- Left physical CCD: `060+003+206+LL+S/N 0039` plus `060+003+206+LU+S/N 0039`.
- Right physical CCD: `060+003+206+RU+S/N 0039` plus `060+003+206+RL+S/N 0039`.
- Full-exposure candidate: `20260609T031649.6`, with 300 raw amplifier members across 75 IFUSLOTs.
- Observation/dither candidate: `OBSID=6`, exposures `20260609T031649.6`, `20260609T031859.3`, and `20260609T032112.2`. Direct headers identify the same `WD1327-083` target, pointing, operational observation, and sequential exposure membership. No member is fabricated.

The full 20260609 inventory contains 14,100 FITS members: 4,200 zro, 900 dark, 900 LDLS, 2,400 comparison, 1,500 twilight, and 4,200 science files. There are 14 science exposures; each has 300 raw amplifier members and 75 IFUSLOTs. Two IFUSLOTs, `067`/SPECID `025` and `098`/SPECID `027`, have raw data but no repository trace-reference configuration; later extraction coverage must retain them as explicit unavailable inputs.

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

## Test ledger

- Pre-change baseline: `55 passed`.
- Step 8 focused transform, algorithm, Product, and Task gate: `12 passed`.
- Full suite after Step 8: `61 passed` with one pre-existing flat median-filter warning.
