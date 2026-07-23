# Worked examples

All examples assume a scanned `./run/registry.sqlite3` and configuration files in the repository root.

The examples use canonical Product names. Legacy names may be queried when inspecting an old registry, but new planning configuration and publication reject them.

## Calibration

```bash
virusflow run calibrations --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --start-date 20260609 --end-date 20260609 --plan-only
virusflow run calibrations --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --start-date 20260609 --end-date 20260609
virusflow model list --db ./run/registry.sqlite3 --state active
virusflow qa list --db ./run/registry.sqlite3 --kind trace_map
```

Inspect `planning_report.yml` before the second command. For a known unstable
dark period, a planning YAML override may set `master_dark.cadence.type` to
`weekly`; explicit dark-time intervals and `master_sci` sufficiency examples
are in [calibration cadence](calibration-cadence.md).

## Single exposure

```bash
virusflow run exposure --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --configuration-root . --exposure-id 20260609T031649.6 \
  --progress-mode plain --progress-file ./run/exposure-progress.jsonl
virusflow artifact list --db ./run/registry.sqlite3 --kind sky_model --summary
virusflow storage report --db ./run/registry.sqlite3
```

The final spectral state remains run-local until observation assembly; inspect compact exposure models and provenance rather than expecting a persistent extracted-spectrum artifact.

## Complete observation

```bash
virusflow run observation --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --configuration-root . --observation-id 20260609-OBSID6
virusflow run observation --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --configuration-root . --observation-id 20260609-OBSID6 --serial
virusflow artifact list --db ./run/registry.sqlite3 --kind calibrated_fiber_observation --summary
virusflow cleanup legacy --db ./run/registry.sqlite3
```

The first run defaults to four workers. Membership is registry-derived. The legacy command is an inventory only and confirms whether superseded dense records remain.

## Bounded analysis and candidate lifecycle

Create a study authorizing selected scattered-light residual materialization with a 100 MB budget:

```bash
virusflow study create --db ./run/registry.sqlite3 --output-dir ./run/analysis \
  --study-id scatter-check-20260609 \
  --question 'Are physical-CCD scattered-light residuals spatially structured?' \
  --selection '{"date":"20260609","ifuslot":["060"]}' \
  --observation 20260609-OBSID6 \
  --intermediate-kind scatter_residual \
  --retention selected --expected-bytes 104857600
virusflow study show --db ./run/registry.sqlite3 --output-dir ./run/analysis scatter-check-20260609
```

Materialization itself uses `AnalysisStudyService.materialize` with a production-algorithm callable, parent artifact IDs, and explicit `selected`/`outlier` flags. A candidate created through `publish_candidate` links the study and accepted model. Record its comparison without promotion:

```bash
virusflow study validate --db ./run/registry.sqlite3 --output-dir ./run/analysis \
  scatter-check-20260609 --candidate-artifact-id 456 \
  --metrics '{"residual_rms":2.91}' --comparison '{"accepted_residual_rms":2.99}' \
  --decision retain-for-review
virusflow study complete --db ./run/registry.sqlite3 --output-dir ./run/analysis \
  scatter-check-20260609 --summary '{"result":"candidate retained; not promoted"}'
```

## Model inspection

```bash
virusflow model list --db ./run/registry.sqlite3 --kind ccd_scattered_light_model
virusflow model list --db ./run/registry.sqlite3 --kind sky_model
virusflow artifact show --db ./run/registry.sqlite3 456
```

Inspection reports compact representation, latent sky sampling, dtype, units, lifecycle, size, provenance, validity, and candidate/accepted relationship. No physical LSF artifact is listed when none exists. Response composition is split between baseline response, illumination correction, and compact fiber response products.

## Noninteractive batch job

```bash
#!/bin/bash
# Scheduler resource directives are site-specific.
set -uo pipefail
virusflow run observation --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --configuration-root . --observation-id 20260609-OBSID6 \
  --workers 4 --progress-mode plain --progress-interval 60 \
  --progress-file ./run/batch-progress.jsonl
status=$?
if [ "$status" -ne 0 ]; then
  virusflow cleanup scratch --workdir ./run/artifacts
  exit "$status"
fi
virusflow cleanup scratch --workdir ./run/artifacts --execute
```

Plain progress contains no terminal controls. Failed-run inspection remains a dry run until `--execute` is supplied.
