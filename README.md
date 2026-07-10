# VIRUSFlow
Orchestration, provenance, calibration, and reduction framework for the VIRUS spectrograph, designed to scale from nightly operations to petabyte-scale archival processing.

This repository currently includes an initial, minimal implementation aligned with the Architecture Vision document, targeting phases 1–6:

- Phase 1: Registry (SQLite) with raw file discovery and metadata parsing (best-effort from filenames), exposure grouping, and amplifier placeholders.
- Phase 2: Artifact representation with provenance.
- Phase 3: Task wrappers (stubs) for bias and dark calibrations.
- Phase 4: Simple orchestration and dependency graph with a local executor.
- Phase 5: Basic QA schema and saving mechanism.
- Phase 6: Pluggable algorithms with versioned tasks (bias v1 and v2 demo).

Project layout (partial):

- virusflow/core: data model, provenance, and simple DAG
- virusflow/registry: SQLite-backed registry
- virusflow/storage: filesystem storage stub
- virusflow/tasks: task base class and calibration task stubs (bias, dark), with a simple plugin registry
- virusflow/executors: local executor
- virusflow/cli: minimal CLI

Installation

- pip (editable for development):

```bash
pip install -e .[dev]
```

- conda (via environment.yml):

```bash
conda env create -f environment.yml
conda activate virusflow
```

Quickstart

- Ensure Python 3.10+ (the environment.yml sets this automatically).
- From repository root, run the CLI via module or the installed entry point:

```bash
# using module
python -m virusflow.cli.virusflow init --db ./virusflow.sqlite3
python -m virusflow.cli.virusflow scan /path/to/night/root --db ./virusflow.sqlite3
python -m virusflow.cli.virusflow tasks
python -m virusflow.cli.virusflow exposures --db ./virusflow.sqlite3 --limit 20
python -m virusflow.cli.virusflow plan calibrations --start-date 20260601 --end-date 20260604 --bias-version v1 > plan.yaml
python -m virusflow.cli.virusflow run plan.yaml --db ./virusflow.sqlite3 --workdir ./work

# or using the installed console script
virusflow init --db ./virusflow.sqlite3
virusflow scan /path/to/night/root --db ./virusflow.sqlite3
virusflow tasks
virusflow exposures --db ./virusflow.sqlite3 --limit 20
virusflow plan calibrations --start-date 20260601 --end-date 20260604 --bias-version v1 > plan.yaml
virusflow run plan.yaml --db ./virusflow.sqlite3 --workdir ./work
```

Exposures table

After scanning, you can quickly inspect what was ingested using the exposures table command. This joins exposures with exposure_details for a readable summary.

Examples:

```bash
# Show first 20 rows
virusflow exposures --db ./virusflow.sqlite3 --limit 20

# Filter by date window (inclusive)
virusflow exposures --db ./virusflow.sqlite3 --start-date 20260401 --end-date 20260601

# Export CSV for spreadsheet use
virusflow exposures --db ./virusflow.sqlite3 --start-date 20260401 --end-date 20260601 --csv > exposures.csv
```

Columns shown: exposure_id, when_utc (YYYYMMDD), frame_type, expnum, qobject, qprog, pexptime, date (from FITS header), qra, qdec, tar_path

Notes

- DB-only mode: All operations now rely on the registry database. You must run `virusflow scan` before planning or running reductions. Reading FITS members from `.tar` archives at runtime requires a DB-backed tar index built during `scan`. If you skip scanning, `virusflow run` will fail with a clear instruction to run `scan` first.
- Plans and task listings are YAML by default. Use `> plan.yaml` in examples; JSON input is still accepted by `virusflow run` for backward compatibility.
- The scanner looks for .fits files recursively (including inside .tar archives) and extracts exposure_id, frame_type, and amplifier ZipCode components directly from filenames (including tar member names) like 20260511T035810.4_074LL_cmp.fits. It also inspects the enclosing directory or tarball name for an observation identifier of the form virusXXXXXXX or virusXXXXXXX.tar. Per operations policy, any observation with a 7-digit number >= 999 is treated as a test frame and is ignored during registration and analysis. ZipCode records are created during scanning without opening FITS files, and the planner later discovers available ZipCodes via the registry. During scanning, uncompressed `.tar` files are indexed into the DB (offset and size per FITS member) for fast runtime access.
- The current tasks are stubs that create placeholder output files and register them in the registry with provenance. They demonstrate orchestration, provenance, and algorithm replacement (phase 6).
- Additional tasks (flat, trace, wavelength, extraction, sky, astrometry), richer metadata parsing (from FITS headers), amplifier mapping, QA metrics, and Slurm/TACC executors are intended next steps.
