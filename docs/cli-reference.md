# CLI reference

Run `virusflow COMMAND --help` or `virusflow COMMAND SUBCOMMAND --help` for authoritative option details.

| Command | Purpose |
|---|---|
| `init` | Initialize the SQLite registry. |
| `scan` | Index filesystem/tar FITS inputs and exposure metadata. |
| `exposures` | List raw `OBJECT`/`Q*` and interpreted exposure metadata; filter with `--requested-target`, `--requested-program`, or `--observing-mode`. |
| `run calibrations` | Plan and optionally execute the calibration graph. `--plan-only` writes compact `planning_report.json`; execution writes `execution_report.yml`. Current-policy hard QA results are terminal on rerun unless `--force-replan` is used. Isolated task errors are reported as recorded issues after graph completion, while `--strict-task-failures` additionally returns status 1 for batch enforcement. |
| `run exposure` | Reduce one atomic exposure. |
| `run observation` | Resolve real observation membership and run exposure nodes followed by observation publication. |
| `artifact list/show` | Inspect lifecycle, components, dtype, shape, units, bytes, validity, producer, parents, QA, and analysis links. |
| `model list/show` | Inspect compact accepted and candidate models. |
| `qa list/show/evaluate` | Query or evaluate product QA. |
| `study create/list/show/validate/complete` | Manage bounded materialization and candidate-validation records without promotion. |
| `analyze` | Run existing read-only post-run studies. |
| `storage report` | Summarize active artifact count/bytes by kind and largest products. |
| `cleanup scratch/cache/legacy` | Inventory or explicitly clean lifecycle-specific storage. |
| `config show` | Show resolved worker/progress, registry, artifact, scratch, and configuration-root values. |
| `validate observation` | Run the representative real-observation acceptance workflow. |
| `performance show` | Summarize a saved timing report. |
| `performance compare` | Compare timing reports and optionally verify exact Product equivalence between two registries. |
| `performance overhead` | Measure context-local instrumentation overhead. |

## Execution options

`run` subcommands accept `--nworkers`/`--workers`, `--serial`, `--progress`/`--no-progress`, `--progress-mode auto|tty|plain|json`, `--progress-interval`, `--progress-file`, `--max-retries`, and `--performance-report`. Defaults are progress enabled, automatic rendering, 30-second noninteractive heartbeat, zero retries, and four workers. `--serial` always forces one worker. A performance report path writes both JSON and a same-stem Markdown report.

Planning configuration supports the same values under `execution`: `nworkers`, `progress`, `progress_mode`, `progress_interval`, `progress_path`, and `max_retries`. CLI values override YAML.

Planning YAML node and edge names must be canonical: `master_bias`, `master_dark`, `master_ldls`, `master_hg`, `master_cd`, `master_arc`, `master_twilight`, `master_sci`, `trace_map`, `wavelength_map`, `extracted_master_ldls_spectrum`, `extracted_master_twilight_spectrum`, `extracted_master_sci_spectrum`, and `exposure_fiber_response`. Cadence types include `nightly`, `rolling_24h`, `monthly`, `weekly`, `isolated`, `paired`, `observing_block`, and `dark_time` through the purpose-policy interface. The CLI does not expose task-class or task-version selection. `--plan-only` writes exact membership, exclusions, sufficiency, temperature, pairing, applicability, computation identity, and downstream requesters; see [calibration cadence](calibration-cadence.md).

See [performance.md](performance.md) for timing fields, controlled-comparison commands, measured regression findings, and current database boundaries.

## Exit and safety behavior

Malformed targets/options produce exit status 2. Graph failures produce a nonzero status with failed and blocked nodes separated. Cleanup is non-destructive unless `--execute` or `--deactivate` is present. Canonical/model artifacts are never cache eviction candidates. Legacy payload deletion requires all three flags: `--deactivate --delete-payloads --validation-succeeded`.

Default `master_bias` QA is a hard upstream health gate: read noise above 4.5
electrons produces a warning/degraded Product, and above 6 electrons produces a
failed/unusable Product. The latter remains inspectable through `artifact` and
`qa`, while dependent calibration nodes are reported as blocked rather than
allowing a later wavelength failure to become the apparent root cause.

Removed public interfaces are not emulated: the nonfunctional `plan` command family, `tasks`, `debug-raw`, plural `artifacts`, and immediate `storage cleanup-scratch` path are retired. Use `run calibrations --plan-only`, `artifact`, and `cleanup`. Deprecated Artifact names remain lookup/migration vocabulary only and are rejected for publication.
