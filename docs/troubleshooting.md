# Troubleshooting

## No observation members

Run `virusflow exposures --db PATH` and confirm scanning populated science exposures. Observation IDs must use `YYYYMMDD-OBSID<number>` and resolve through `exposure_details.expnum`. Use repeated `--exposure-id` only for an intentional explicit override.

## Missing calibration or wavelength-dependent extraction

Inspect task failure context and `virusflow artifact list`. The known real input `095+004+426+RU+S/N 0048` has zero comparison arrays, so wavelength-dependent extraction is unavailable while the rest of its exposure remains represented. Do not fabricate a wavelength solution.

## Catalog query failure

Astrometric catalog access uses the Pan-STARRS STScI CSV service. Network failure is retained as an environmental limitation and may degrade QA. It is not evidence of a successful catalog validation.

## Progress appears quiet

Redirected output defaults to a 30-second plain heartbeat. Reduce `--progress-interval`, choose `--progress-mode json`, or set `--progress-file events.jsonl`. JSONL continues to record state changes even when terminal output is rate-limited.

## Failed or blocked workflow

The first task with the root exception is recorded as a task error and dependent nodes are not run. Interactive calibration runs finish with a concise summary and retain full tracebacks in `execution_report.yml`; add `--strict-task-failures` when a nonzero batch status is required. A rerun treats current-policy hard QA results as terminal evidence rather than repeating them unless `--force-replan` is supplied. Inspect any intentionally retained failure scratch with `virusflow cleanup scratch --workdir PATH` before executing deletion.

## SQLite contention

The registry uses WAL, autocommit, and a busy timeout. Keep external transactions short and do not share SQLite connection objects across worker threads. Rerunning immutable publications is safe; concurrent publication resolves identical logical revisions atomically.

## Storage unexpectedly large

Run `virusflow storage report --db PATH --largest 20`, then dry-run `cleanup cache` and `cleanup legacy`. Calibration tasks normally release cacheable dense masters at their validated evidence stage. For a database created before stage eviction was enabled, `virusflow cleanup cache --db PATH --execute` safely backfills eligible evictions and reports candidates refused by QA or incomplete provenance. Normal production must not actively register reduced science images, scattered-light-evaluated CCD images, aperture-extracted spectra/variance, per-fiber sky predictions, or standalone sky-subtracted spectra. Never delete legacy payloads until representative validation has passed and reports are preserved.

## Matplotlib/font cache warnings

On batch systems, set `MPLBACKEND=Agg` and point `MPLCONFIGDIR` at a writable job-local directory before analytics or diagnostic-figure generation.
