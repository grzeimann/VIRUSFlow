# Getting started

## Prerequisites and installation

Use Python 3.10 or newer and enough local storage for raw data, compact models, and a roughly 2–4 GB final observation product.

```bash
conda env create -f environment.yml
conda activate virusflow
python -m pip install -e '.[dev]'
virusflow --help
```

The examples use one run directory. The raw root is never modified.

```bash
mkdir -p ./run
virusflow init --db ./run/registry.sqlite3
virusflow scan --db ./run/registry.sqlite3 /path/to/raw/virus
virusflow exposures --db ./run/registry.sqlite3 --limit 20
virusflow exposures --db ./run/registry.sqlite3 --observing-mode parallel
virusflow exposures --db ./run/registry.sqlite3 --requested-target Target_Name
```

`VIRUSFLOW_DB`, `VIRUSFLOW_WORKDIR`, and `VIRUSFLOW_CONFIG_ROOT` provide defaults where the CLI supports them. Explicit options win. Planning YAML values sit between built-in defaults and explicit CLI options. Inspect the resolved execution values with:

```bash
virusflow config show --db ./run/registry.sqlite3 --workdir ./run/artifacts
```

Planning YAML may override only canonical calibration nodes and their explicit edges. These include separate Hg/Cd masters and the three-stage Master Science chain (`master_sci`, `extracted_master_sci_spectrum`, and `fiber_wavelength_spectral_mask`). Historical names (`master_flat`, `master_cmp`, `trace`, and `wave`) remain readable in old registries but cannot be used to create new plans or Products. Rescan an older registry once to backfill exposure time, lamp, ambient-temperature, observing-block headers, and the distinct raw `OBJECT` plus interpreted requested-target/operational-context fields. Legacy `object_name` remains readable, but it cannot always reveal whether an old value originated in `OBJECT` or the former `QOBJECT` fallback.

## Run work

Calibration planning and execution use scanned dates and ZipCodes:

```bash
virusflow run calibrations --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --start-date 20260609 --end-date 20260609 --plan-only
```

Review `./run/artifacts/planning_report.yml`, then omit `--plan-only` to execute
the exact inspected groups. The report includes members, exclusions, lamp
pairing, temperature, exposure-time and `master_sci` sufficiency evidence. See
[calibration cadence](calibration-cadence.md).

Reduce one exposure:

```bash
virusflow run exposure --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --configuration-root . --exposure-id 20260609T031649.6
```

Reduce a complete observation with the default four workers:

```bash
virusflow run observation --db ./run/registry.sqlite3 --workdir ./run/artifacts \
  --configuration-root . --observation-id 20260609-OBSID6 \
  --progress-file ./run/progress.jsonl
```

Observation membership comes from `exposure_details.expnum` populated during scanning. Repeated `--exposure-id` options are an explicit override, not a hidden three-member default. Use `--serial` for one worker.

Progress counts graph nodes. A failed prerequisite is `failed`; work that cannot run is `blocked`. Cached/skipped planner targets remain visible. A failed workflow exits nonzero and preserves the root exception.

## Inspect, rerun, and clean

```bash
virusflow artifact list --db ./run/registry.sqlite3 --kind calibrated_fiber_observation
virusflow artifact show --db ./run/registry.sqlite3 123
virusflow model list --db ./run/registry.sqlite3 --state active
virusflow storage report --db ./run/registry.sqlite3 --largest 20
virusflow qa show --db ./run/registry.sqlite3 --artifact-id 123
```

Immutable logical revisions make safe reruns idempotent when inputs, parameters, and algorithms are unchanged. Inspect failure output and retained scratch before rerunning.

```bash
virusflow cleanup scratch --workdir ./run/artifacts
virusflow cleanup scratch --workdir ./run/artifacts --execute
virusflow cleanup cache --db ./run/registry.sqlite3
virusflow cleanup cache --db ./run/registry.sqlite3 --execute
```

The first command in each pair is a dry-run inventory. See [troubleshooting](troubleshooting.md) before retiring legacy payloads.

## Small validation run

The representative validator is intentionally a complete real observation, not a synthetic smoke test:

```bash
virusflow validate observation --data-root /path/to/20260609/virus \
  --workspace ./validation/parallel --output-dir ./validation/parallel/report \
  --progress-file ./validation/parallel/progress.jsonl
```

It writes human-readable Markdown, machine-readable JSON, and a registered `validation_report` artifact. It fails rather than claiming completion when the required real inputs are absent.
