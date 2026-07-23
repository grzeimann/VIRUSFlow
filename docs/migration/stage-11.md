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
| `virusflow tasks` and `v1` task selectors | Canonical planning-kind mapping used internally by `run` |

The retired `ArtifactMaterializer`, task-level direct registration helper, optional planning mapping helper, stages 8–10 mutation function, and old steps 1–7 validator were removed. The supported flow is:

```text
run → ReductionGraph.plan → planning.schedule → PlanningExecutor
    → Task → algorithm → DefaultPublicationService → ArtifactService.persist_request
```

Tasks and diagnostics load components through `ArtifactService.load_component`. The old `master_sci` stack remains removed; the later cadence work reintroduced a new canonical calibration implementation through this same Task/algorithm/publication path, without reviving the deleted stack. Per-exposure science still continues through complete-observation publication. The only retained Artifact-name compatibility is `LEGACY_KIND_ALIASES`, explicitly limited to reading existing registries and locating migration candidates. It cannot publish new records.

Execution defaults to four workers and progress enabled. Planning YAML execution fields are `nworkers`, `progress`, `progress_mode`, `progress_interval`, `progress_path`, and `max_retries`. Explicit CLI values override them; `--serial` forces one.

Planning node and edge names must use canonical calibration kinds. The post-Phase-11 cadence implementation adds `master_hg`, `master_cd`, and `master_sci` while retaining `master_arc` as an explicitly composed Hg+Cd Product. Dependencies have one configuration representation: explicit edges. The removed `preprocess_requires` and mapping-helper flags are not interpreted.

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
