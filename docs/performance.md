# Performance measurement and regression investigation

VIRUSFlow records task, raw-I/O, database, artifact, scheduler, and graph timing for every `PlanningExecutor` run. Durations use `time.perf_counter`; task CPU uses the current thread clock. Nested phase summaries contain both inclusive and exclusive durations, and unclassified task time is reported as scientific compute.

Use `--performance-report` on any `run` command to write JSON and Markdown together:

```bash
virusflow run calibrations --db registry.sqlite3 --workdir work \
  --planning-yaml planning.yml --performance-report reports/performance.json

virusflow performance show reports/performance.json
virusflow performance compare reports/before.json reports/after.json \
  --output reports/comparison.json

virusflow performance overhead --iterations 200000 \
  --output reports/instrumentation-overhead.json
```

For a controlled scientific comparison, add registries produced from identical scans and configuration:

```bash
virusflow performance compare reports/before.json reports/after.json \
  --before-db before/registry.sqlite3 --after-db after/registry.sqlite3 \
  --output reports/comparison.json \
  --scientific-output reports/scientific-equivalence.json
```

The scientific comparison verifies registry identity, revision, aggregate checksum, component checksum, dtype, shape, exact loaded values (including matching NaNs), and maximum absolute difference.

## Recorded fields

Each attempt records queue, wall, thread CPU, status, retry, worker thread, process, target identity, counters, identities, and these exclusive/inclusive phases:

```text
raw_lookup, raw_archive_open, raw_member_lookup, raw_byte_read,
raw_cache_wait, fits_header_parse, pixel_array_load,
artifact_lookup, artifact_load, compute, serialization, content_hash,
artifact_publish, database_query, database_transaction,
database_lock_wait, scratch_cleanup, calibration_singleflight_wait,
process_serialize, process_deserialize
```

The run report includes per-kind mean/median/p90/p95/max, raw operation distributions, cold/warm and cached access, repeated reads by task kind and worker, database tables and query distributions, artifact/model reloads, serialization/hash/publication costs, active-worker utilization, thread CPU, slowest operations, and the dependency critical path. Progress heartbeats expose per-kind completion/running/waiting counts, mean/median/p95, phase shares, and a kind-aware ETA bounded by the estimated remaining dependency path.

Timing rows are persisted in `performance_runs` and `performance_tasks`. JSON files retain the full semantic events; the registry stores one compact run summary and one record per task attempt.

## Database architecture

The current implementation uses one SQLite file for the effectively read-only raw catalog (`raw_files`, `tar_files`, `tar_members`, `exposures`, `exposure_details`, `amplifiers`) and mutable artifact, provenance, QA, analysis, and performance state. Reduction tasks receive tar offsets and sizes in resolved `RawFileId` references. Raw-producing calibration tasks query the catalog once for their input set; derived Trace and Wavelength tasks do not query it.

The controlled optimized subset made 20 necessary raw-catalog queries, 2,176 total queries, and waited 0 s for database locks. Query work was 1.768 task-seconds out of 44.651 task-seconds. These measurements do not justify splitting the catalog and mutable registry. Connections are autocommit, WAL is initialized with the schema rather than renegotiated per connection, and publication does not hold a write transaction while reading raw data, computing, serializing, or hashing.

## Controlled calibration result (2026-07-23)

The representative subset used four amplifiers of `013+043+412`, four workers, and one node per amplifier for Bias, Dark, LDLS, Arc, Twilight, Trace, and Wavelength (28 nodes). The legacy diagnostic switch emulated only the three measured pre-fix paths: tar member scanning, repeated database initialization, and per-connection WAL negotiation. It did not change algorithms, graph identity, or persistence contracts.

| Metric | Legacy-emulated | Optimized | Change |
|---|---:|---:|---:|
| Wall | 16.128 s | 11.565 s | -28.29% |
| Task CPU | 34.179 s | 26.629 s | -22.09% |
| Raw median | 57.60 ms | 3.82 ms | -93.37% |
| Raw p95 | 439.53 ms | 7.26 ms | -98.35% |
| Raw member lookup | 17.377 task-s | 0.0007 task-s | -99.996% |
| DB queries | 4,360 | 2,176 | -50.09% |
| DB query time | 4.145 task-s | 1.768 task-s | -57.35% |
| Raw-catalog queries | 20 | 20 | no unnecessary derived-task access |
| DB connections | 844 | 576 | -31.75% |
| Worker utilization | 97.47% | 96.52% | healthy in both |
| Critical path | 6.515 s | 6.026 s | -7.50% |

All 28 revisions, 28 aggregate checksums, and 60 component checksums and loaded arrays were exactly equal; every maximum absolute difference was zero. The corrected reports are the `validation/performance/controlled-*-v2` files; earlier non-v2 files are superseded diagnostic evidence.

Instrumentation overhead was 573 ns per active phase scope in a 200,000-scope microbenchmark. Optimized report generation plus JSON serialization was about 46 ms and was outside the frozen run wall time.

## Findings

- Supported: repeated tar archive indexing/member scanning was the dominant regression; direct reads through scan-time offsets removed 17.889 task-seconds of member lookup.
- Supported: schema initialization and WAL negotiation were repeated through `ArtifactService`; caching initialization halved query count and removed slow WAL PRAGMAs.
- Supported in multi-window diagnostics, but absent in the corrected subset: repeated raw requests can occur. A bounded 512 MiB run cache with single-flight loading makes physical duplication visible and avoids unbounded full-observation memory.
- Supported for observation startup: concurrent exposures could all miss and compute the same calibration. Keyed single-flight locks prevent duplicate work, while deterministic focal-plane rotation preserves useful concurrency among exposures.
- Rejected: process-pool transfer. The executor is a thread pool, process IDs remain local, and process serialization phases were zero.
- Rejected as primary causes: artifact serialization, hashing, and publication. Optimized totals were 1.086 s, 0.797 s, and 1.467 s of task time respectively; publication uses same-directory atomic replacement, not cross-filesystem copy.
- Rejected: SQLite writer contention on this subset. Lock wait was zero and transactions were microseconds.
- Rejected: poor graph parallelism on this subset. Mean active workers were 3.867 of 4.
- Not exercised: accepted compact model rematerialization. The subset loaded no lifecycle=`model` components; ordinary calibration component loading was 0.143 task-seconds.
- Not a dominant cost: graph/report serialization. Optimized report generation was 3.8 ms and JSON serialization 41.8 ms.

No complete-observation performance run was made during this investigation. The full-observation impact of calibration single-flight, remaining connection churn, and calibration selection across multiple validity windows remains to be measured before claiming complete-observation parity with earlier VIRUSFlow or Remedy.
