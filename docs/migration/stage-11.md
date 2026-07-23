# Stage 11 migration

Stage 11 consolidates execution around the dependency-aware planning executor and its graph progress state. It removes the nonfunctional public `plan` stubs, public raw-debug branch, plural artifact command, and immediately destructive scratch path.

| Retired path | Current path |
|---|---|
| `virusflow run ...` calibration-only overload | `virusflow run calibrations ...` |
| `virusflow plan calibrations` | `virusflow run calibrations --plan-only` |
| `virusflow artifacts` | `virusflow artifact list` |
| `virusflow storage cleanup-scratch` | `virusflow cleanup scratch [--execute]` |
| `virusflow storage migrate-stages-8-10` | `virusflow cleanup legacy` with separate deactivation/deletion flags |
| `virusflow debug-raw` | Registry/task diagnostics and focused developer tests |

Execution defaults to four workers and progress enabled. Planning YAML execution fields are `nworkers`, `progress`, `progress_mode`, `progress_interval`, `progress_path`, and `max_retries`. Explicit CLI values override them; `--serial` forces one.

The stages 8–10 migration identifies active superseded dense kinds and old dense scattered-light representations. Dry-run inventory is the default:

```bash
virusflow cleanup legacy --db ./run/registry.sqlite3
```

After regenerated complete observations have passed scientific and serial/parallel comparison, deactivate records while retaining payloads:

```bash
virusflow cleanup legacy --db ./run/registry.sqlite3 --deactivate
```

Payload deletion is irreversible and separately gated:

```bash
virusflow cleanup legacy --db ./run/registry.sqlite3 \
  --deactivate --delete-payloads --validation-succeeded
```

Canonical products and accepted models are never cache cleanup targets. Candidate models stay unpromoted until an external reviewed decision. Stage 11 does not invent an LSF, replace the provisional unity response, or claim covariance propagation.
