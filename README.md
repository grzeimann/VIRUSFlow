# VIRUSFlow

VIRUSFlow is an alpha-quality, artifact-driven calibration, reduction, QA, analysis, and validation system for the VIRUS spectrograph. It preserves exposures as atomic measurements, groups real registry-derived members into observations, and records immutable products with provenance and lifecycle state.

The currently supported production path covers calibration graphs, complete science exposures, and calibrated complete-observation fiber products. Cube reconstruction, a physical LSF model, full covariance propagation, and an accepted non-unity spectrophotometric response are not yet available.

## Installation

Python 3.10 or newer is required.

```bash
conda env create -f environment.yml
conda activate virusflow
python -m pip install -e '.[dev]'
virusflow --help
```

## Quick start

```bash
virusflow init --db ./run/registry.sqlite3
virusflow scan --db ./run/registry.sqlite3 /path/to/raw/virus
virusflow exposures --db ./run/registry.sqlite3 --start-date 20260609 --end-date 20260609

virusflow run observation \
  --db ./run/registry.sqlite3 \
  --workdir ./run/artifacts \
  --configuration-root . \
  --observation-id 20260609-OBSID6 \
  --progress-file ./run/progress.jsonl

virusflow artifact list --db ./run/registry.sqlite3 --kind calibrated_fiber_observation
virusflow storage report --db ./run/registry.sqlite3
virusflow cleanup scratch --workdir ./run/artifacts
```

Execution uses four graph workers by default. Add `--serial` to a `run` command to force one worker. Terminal progress updates in place; redirected output receives plain periodic summaries. `--progress-mode json` emits JSON to standard output, and `--progress-file` always records JSONL events.

## Architecture and data boundaries

Targets describe requested calibration, exposure, or observation work. Tasks resolve inputs and call deterministic algorithms. The dependency-aware `PlanningExecutor` runs each graph node once and reports succeeded, failed, blocked, cached, skipped, and retried work. `ArtifactService` is the only production persistence boundary and records components, checksums, dtype, shape, units, validity, producer, parents, QA, lifecycle, and state.

Storage has five deliberately distinct classes:

- Raw data remain in their source filesystem or tar archives and are indexed in SQLite.
- Accepted compact models and calibrations are immutable registered products.
- The final `calibrated_fiber_observation` is the normal dense science product.
- Detector, extracted-spectrum, sky-prediction, and sky-subtracted intermediate arrays are run-local scratch or memory, not production artifacts.
- Cache payloads are reproducible and evictable; analysis materializations are bounded by a study record and retention budget.

Candidate analysis models remain linked to their study and accepted comparison model. Validation records do not promote candidates automatically.

Large non-astrometric floating arrays are stored as `float32`. Final flux and variance use explicit FITS scaling metadata and `BUNIT`. Normal production does not persist reduced amplifier images, full-CCD scattered-light evaluations, extracted spectra/variance, per-fiber sky predictions, or standalone sky-subtracted spectra.

## Principal commands

```text
virusflow init|scan|exposures
virusflow run calibrations|exposure|observation
virusflow artifact list|show
virusflow model list|show
virusflow qa list|show|evaluate
virusflow study create|list|show|validate|complete
virusflow analyze
virusflow storage report
virusflow cleanup scratch|cache|legacy
virusflow config show
virusflow validate observation
```

Cleanup commands inventory candidates by default. Scratch/cache deletion requires `--execute`. Legacy retirement separates `--deactivate` from `--delete-payloads`; deletion also requires `--validation-succeeded`.

## Documentation

- [Getting started](docs/getting-started.md)
- [CLI reference](docs/cli-reference.md)
- [Worked examples](docs/examples.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Stage 11 migration](docs/migration/stage-11.md)
- [Target architecture](docs/architecture/VIRUSFlow_Target_Architecture.md)
- [Stages 8–10 storage specification](docs/tasks/VIRUSFlow_Stages_8_10_Storage_Materialization_Sky_Parallel_Revision.md)

## Testing and development

```bash
python -m pytest -q
python -m virusflow.cli.verify_steps_8_10 --help
```

The repository separates `algorithms`, `tasks`, `planning`, `executors`, `artifacts`, `analytics`, `registry`, and `storage`. See [CODING_STYLE.md](CODING_STYLE.md) and [docs/AGENTS.md](docs/AGENTS.md) before changing scientific or persistence contracts. Scientific changes require characterization and acceptance tests; direct task filesystem writes are migration debt.

## Troubleshooting, contribution, license, and citation

See the [troubleshooting guide](docs/troubleshooting.md) for catalog-network degradation, missing calibrations, retained failed work, SQLite contention, and cleanup. Contributions should keep scientific objects distinct from algorithms, preserve complete hardware lineage, and include focused tests.

VIRUSFlow is distributed under the [BSD 3-Clause license](LICENSE). No formal citation file is defined yet; publications should acknowledge VIRUS/VIRUSFlow and record the repository commit, configuration references, and product revisions used.
