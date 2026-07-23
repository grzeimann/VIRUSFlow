# CLI reference

Run `virusflow COMMAND --help` or `virusflow COMMAND SUBCOMMAND --help` for authoritative option details.

| Command | Purpose |
|---|---|
| `init` | Initialize the SQLite registry. |
| `scan` | Index filesystem/tar FITS inputs and exposure metadata. |
| `exposures` | List scanned exposure metadata. |
| `run calibrations` | Plan and optionally execute the calibration graph. `--plan-only` writes `planning_report.yml`. |
| `run exposure` | Reduce one atomic exposure. |
| `run observation` | Resolve real observation membership and run exposure nodes followed by observation publication. |
| `artifact list/show` | Inspect lifecycle, components, dtype, shape, units, bytes, validity, producer, parents, QA, and analysis links. |
| `model list/show` | Inspect compact accepted and candidate models. |
| `qa list/show/evaluate` | Query or evaluate product QA. |
| `study create/list/show/validate/complete` | Manage bounded materialization and candidate-validation records without promotion. |
| `analyze` | Run existing read-only post-run studies. |
| `storage report` | Summarize active artifact count/bytes by kind and largest products. |
| `cleanup scratch/cache/legacy` | Inventory or explicitly clean lifecycle-specific storage. |
| `config show` | Show resolved worker/progress and path configuration. |
| `validate observation` | Run the representative real-observation acceptance workflow. |

## Execution options

`run` subcommands accept `--nworkers`/`--workers`, `--serial`, `--progress`/`--no-progress`, `--progress-mode auto|tty|plain|json`, `--progress-interval`, `--progress-file`, and `--max-retries`. Defaults are progress enabled, automatic rendering, 30-second noninteractive heartbeat, zero retries, and four workers. `--serial` always forces one worker.

Planning configuration supports the same values under `execution`: `nworkers`, `progress`, `progress_mode`, `progress_interval`, `progress_path`, and `max_retries`. CLI values override YAML.

## Exit and safety behavior

Malformed targets/options produce exit status 2. Graph failures produce a nonzero status with failed and blocked nodes separated. Cleanup is non-destructive unless `--execute` or `--deactivate` is present. Canonical/model artifacts are never cache eviction candidates. Legacy payload deletion requires all three flags: `--deactivate --delete-payloads --validation-succeeded`.

Removed public interfaces are not emulated: the nonfunctional `plan` command family, `debug-raw`, plural `artifacts`, and immediate `storage cleanup-scratch` path are retired. Use `run calibrations --plan-only`, `artifact`, and `cleanup`.
