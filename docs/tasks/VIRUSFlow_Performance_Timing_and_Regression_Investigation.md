# VIRUSFlow Performance Instrumentation and Regression Investigation

## Purpose

A representative observation reduction was stopped after approximately 20 minutes because it was only about halfway through the base calibration work, including master frames, trace, and wavelength processing.

This is a serious performance regression:

- The previous VIRUSFlow implementation completed the full workflow in approximately 10 minutes.
- Remedy is also faster than the current implementation.
- The current implementation appears likely to require more than 40 minutes for the base calibration portion alone.

Do not continue full-observation validation runs until the pipeline can explain where the time is being spent.

The immediate goal is not speculative optimization. The first goal is to make performance measurable at the task, raw-I/O, database, artifact, scheduler, and graph levels. Once the dominant regression is identified, fix it and verify the improvement against the previous implementation.

Codex should complete this work without pausing for approval or checking in.

---

# 1. Required outcomes

The implementation must answer the following questions quantitatively:

1. Which task kinds consume the most total wall-clock time?
2. Which individual tasks are the slowest?
3. How much time is spent waiting for a worker, locating and reading raw data, loading artifacts, computing, serializing, hashing, publishing, waiting on database locks, and cleaning scratch?
4. Are raw frames still accessed near the previous warm-access baseline of approximately 10 ms?
5. Is the same raw frame being read more than once unnecessarily?
6. Are tar archives or indexes being reopened or rebuilt repeatedly?
7. Is multiprocessing causing large arrays to be copied or pickled between processes?
8. Are compact calibration or model artifacts being repeatedly materialized?
9. Are SQLite writes serializing the workers?
10. Is the graph actually using four workers effectively?
11. Does the raw-data catalog participate unnecessarily after planning?
12. Would separating the raw catalog from the mutable artifact registry materially improve performance?
13. What specific change explains the regression relative to the previous VIRUSFlow implementation?

---

# 2. First-class task timing

Task timing must become part of the execution model rather than ad hoc log statements.

For every task execution, record at least:

```text
task queued timestamp
worker start timestamp
task completion timestamp
queue wait time
raw-data lookup time
raw archive or file open time
raw member lookup time
raw byte-read time
FITS header parse time
pixel-array load time
artifact lookup time
artifact load time
scientific compute time
serialization time
content-hash time
artifact publication time
database transaction time
database lock-wait time
scratch cleanup time
total wall time
```

Also record:

```text
task kind
task identity
target identity
observation identity
exposure identity
detector, amplifier, or other unit identity where applicable
worker identity
process identity
retry number
cache or skip status
raw frames requested
raw frames actually read
raw bytes read
database query count
database identities touched
artifacts loaded
artifact bytes loaded
artifacts written
artifact bytes written
scratch bytes written
success, failure, skipped, cached, or blocked state
```

Use a monotonic high-resolution timer for durations. Timing fields that do not apply should remain explicitly absent or zero according to a documented convention.

---

# 3. Timing scopes and instrumentation API

Create a reusable timing mechanism rather than scattering manual timestamp subtraction throughout tasks.

A suitable API may resemble:

```python
with timing.phase("raw_lookup"):
    ...

with timing.phase("raw_read", bytes_read=nbytes):
    ...

with timing.phase("compute"):
    ...

with timing.phase("artifact_publish"):
    ...
```

The exact API should fit the repository.

Requirements:

- Nested phases must be handled consistently.
- Exclusive and inclusive durations must not be confused.
- Timing overhead should be small and measured.
- Failed phases must still record elapsed time.
- Timing data must survive task failure and interruption.
- Parallel tasks must not corrupt each other's metrics.
- Instrumentation must not alter task identity or scientific output.
- Performance records should be associated with run and task provenance.

The outer task wall time alone is insufficient; the decomposition is essential.

---

# 4. Progress monitor integration

Extend the progress monitor so performance summaries update during execution.

For each task kind, show information equivalent to:

```text
BiasTask         48 / 96   running 4   mean 1.8 s   median 1.5 s   p95 3.4 s
TraceTask        12 / 96   running 0   mean 6.2 s   median 5.9 s   p95 8.1 s
WavelengthTask    0 / 96   waiting
```

Use the repository's actual task names.

The run-level summary should include:

```text
workers active / configured
elapsed time
estimated remaining time
mean raw-frame access time
median raw-frame access time
p95 raw-frame access time
database share of task wall time
artifact publication share of task wall time
compute share of task wall time
```

## 4.1 ETA behavior

ETA must be based on task-kind-specific observations, not one global average. It should consider remaining tasks by kind, observed duration by kind, worker concurrency, graph dependencies, critical-path constraints, and tasks not yet observed.

Omit ETA or label it low-confidence when insufficient timing history exists.

## 4.2 Final and interrupted summaries

At completion or interruption, print and persist a summary sorted by total consumed time:

| Task kind | Count | Total | Mean | Median | p95 | Queue | Raw I/O | DB | Compute | Publish |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|

Also report:

- Slowest individual tasks
- Graph critical path
- Worker utilization
- Total CPU time versus wall time where available
- Total raw frames read
- Unique raw frames read
- Repeated raw reads
- Total raw bytes
- Total artifact bytes loaded and written
- Total database query and lock time
- Total serialization and hashing time

Interrupted runs must emit the timing summary gathered so far.

---

# 5. Raw-frame access instrumentation

Raw access must be decomposed into meaningful operations:

```text
raw catalog lookup
filesystem or archive open
tar-member or archive-member lookup
member-byte read
FITS header parsing
pixel-array loading
```

For each operation, report:

```text
count
total time
mean
median
p90
p95
maximum
bytes read where applicable
```

## 5.1 Frame identity and repeated reads

Track a stable raw-frame identity so the run can report:

- Unique raw frames requested
- Total raw-frame requests
- Unique raw frames physically read
- Total physical reads
- Repeated physical reads
- Repeated reads by task kind
- Repeated reads by worker
- Cache hits and misses

This must make unnecessary repeated reading directly visible.

## 5.2 Archive behavior

Measure:

- Archive opens
- Archive-index loads
- Archive-index builds
- Member lookups
- Reuse of already open archives
- Reuse of archive indexes
- Time spent constructing archive indexes

Determine whether each worker is rebuilding or reopening indexes that were previously shared or cached.

## 5.3 Baseline comparison

Use the prior approximately 10 ms warm raw-frame access as a regression reference.

Distinguish:

- Cold archive access
- Warm archive access
- Cached metadata lookup
- Cached pixel-array reuse, if any

Do not claim equivalence using only the mean. Report median and p95.

---

# 6. Database architecture investigation

Inspect and document the current database architecture.

Answer:

1. Is there one database for raw catalog, artifacts, provenance, tasks, runs, analysis, and performance?
2. Are there multiple databases?
3. Which tasks query which database?
4. Which tables are read or written during reduction?
5. How many queries occur per task?
6. How much time is spent querying?
7. How much time is spent waiting for locks?
8. Are database transactions held during filesystem or scientific work?
9. Are workers competing for one SQLite writer?
10. Does the raw catalog need to be queried after a plan has been resolved?

## 6.1 Preferred responsibility boundary

Evaluate this architecture:

```text
raw catalog database
    built or updated by scanning
    effectively read-only during reduction

artifact and provenance registry
    mutable during reduction
    tasks, runs, models, artifacts, lifecycle, publication

analysis and performance records
    stored with the registry initially or separated later if justified
```

Do not split databases merely because separation sounds cleaner. Use measurements to determine whether the current arrangement causes contention or unnecessary work.

## 6.2 Resolved raw references

Planning should ideally resolve raw inputs into immutable references such as:

```python
RawFrameRef(
    frame_id=...,
    path=...,
    archive_member=...,
    exposure=...,
    detector=...,
)
```

Reduction tasks should consume these resolved references.

Workers should not repeatedly query the raw catalog to rediscover file paths, archive members, exposure membership, detector identity, or static raw metadata.

On resume, references may be validated or resolved once, but the raw catalog should not be involved in every task transition without a demonstrated reason.

## 6.3 SQLite behavior

If SQLite is used for the mutable registry, inspect:

- Journal and WAL mode
- Transaction scope
- Connection-per-process behavior
- Busy timeout and lock retries
- Long-running transactions
- Index coverage
- Query plans for frequent operations
- Connection creation overhead

Use short transactions around publication and state changes.

Do not keep a write transaction open while reading raw files, computing scientific results, serializing or hashing arrays, or copying files.

---

# 7. Artifact and process-pool overhead

Instrument and inspect:

- Artifact metadata lookup
- Payload loading
- Model reconstruction
- Serialization
- Compression
- Content hashing
- Atomic copy or rename
- Registry publication
- Process-pool argument serialization
- Process-pool result serialization

Determine:

- Whether large NumPy arrays are sent through process-pool queues
- Whether raw images are copied between processes
- Whether model arrays are reconstructed repeatedly
- Whether content hashing reads large payloads an additional time
- Whether atomic publication copies rather than renames across filesystems
- Whether compression levels are excessive
- Whether artifact publication is on the critical path

Prefer passing compact immutable references between processes rather than large arrays. Keep intermediate arrays within the worker that computes and consumes them when possible.

---

# 8. Graph and worker utilization

Measure whether the graph is actually parallel.

Record over time:

- Configured workers
- Active workers
- Idle workers
- Ready tasks
- Running tasks
- Dependency-blocked tasks
- Database-blocked tasks where detectable
- I/O-blocked time where detectable

Report:

```text
worker utilization percentage
parallel efficiency
total task CPU time / wall time
critical-path duration
```

Inspect whether calibration dependencies accidentally serialize work that was previously parallel.

Confirm that independent amplifier or detector work is represented as independent graph nodes, large global tasks do not unnecessarily encompass parallelizable units, planning does not create hidden sequential barriers, and nested worker behavior is neither disabling required parallelism nor causing oversubscription.

---

# 9. Performance regression hypotheses to test

Explicitly test these likely causes:

1. Raw frames are reopened for each amplifier or downstream task.
2. Raw metadata is repeatedly queried from the database.
3. Archive indexes are rebuilt in each worker.
4. Archive handles are not reused.
5. Multiprocessing causes large-array pickling.
6. Compact calibration models are repeatedly materialized.
7. Content hashing rereads large payloads.
8. Atomic publication performs expensive cross-filesystem copies.
9. SQLite writes serialize four workers.
10. Transactions remain open too long.
11. The graph contains unnecessary global barriers.
12. Progress or provenance instrumentation has significant overhead.
13. Removing persisted intermediates caused repeated computation within one run.
14. Each task reloads calibrations that could remain cached in a worker.
15. Scientific algorithms changed complexity relative to the previous implementation.
16. New validation checks run repeatedly rather than once per artifact or run.

The final report must state which hypotheses were supported, rejected, or remain unresolved.

---

# 10. Performance records and persistence

Persist run-level and task-level timing metrics in a compact form.

The data should support later queries such as:

- Mean `BiasTask` duration by date
- Raw access p95 by archive
- Artifact publication time by artifact kind
- Worker utilization by run
- Performance before and after a revision
- Slowest tasks for one observation
- Repeated raw reads by task kind
- Database lock time by process
- Critical path by run

Avoid storing one high-frequency progress sample per screen refresh unless needed. Persist semantic timing events and aggregated summaries rather than excessive telemetry.

Performance data should include:

```text
run identity
task identity
software revision
configuration
worker count
host information
raw-data root
artifact root
scratch root
database identities
start and completion time
status
```

---

# 11. Performance report

Add a supported command or run output that produces both human-readable and machine-readable reports.

## 11.1 Run summary

Include:

- Total wall time
- Total task CPU time where available
- Configured and mean active workers
- Worker utilization
- Critical-path duration
- Completed, failed, blocked, skipped, and cached tasks
- Total raw frames and bytes
- Total artifact bytes loaded and written
- Total database time
- Total compute time
- Total publication time

## 11.2 Task-kind summary

For every task kind, include:

- Count
- Total wall time
- Mean
- Median
- p90
- p95
- Maximum
- Queue wait
- Raw lookup
- Raw read
- Artifact load
- Database
- Compute
- Serialization
- Hashing
- Publication
- Cleanup

## 11.3 Raw-I/O summary

Include:

- Unique frames
- Physical reads
- Repeated reads
- Cache hits
- Archive opens
- Index builds
- Mean, median, and p95 access time
- Cold versus warm access
- Bytes read

## 11.4 Database summary

Include:

- Database files or services used
- Query counts
- Query time
- Transaction time
- Lock-wait time
- Slowest queries where available
- Connections created
- Writes by task kind

## 11.5 Slowest operations

Include:

- Slowest tasks
- Slowest raw reads
- Slowest artifact publications
- Slowest database transactions
- Tasks on the critical path

## 11.6 Comparison

Support comparison with:

- A previous run
- A saved baseline report
- Serial versus four-worker execution
- Previous VIRUSFlow timing when available

---

# 12. Optimization phase

Do not optimize blindly before instrumentation is working.

After collecting measurements on a representative calibration subset:

1. Identify the dominant one or two regressions.
2. Implement targeted fixes.
3. Rerun the same subset.
4. Compare timing distributions.
5. Confirm scientific equivalence.
6. Continue only if another dominant bottleneck remains.

Potential fixes may include:

- Carry resolved raw references in plans
- Split raw catalog from mutable registry
- Enable or tune WAL
- Shorten transactions
- Add missing indexes
- Reuse archive indexes
- Reuse open archive handles within workers
- Cache compact calibration models per worker
- Avoid large-array process-pool transfer
- Avoid duplicate hashing
- Use same-filesystem atomic rename
- Restore graph parallelism
- Fuse temporary operations within a worker
- Eliminate repeated model evaluation

Every optimization must be justified by measured timing.

---

# 13. Verification workflow

Before another complete observation run, follow this sequence.

## Phase 1: Instrumented small subset

Run a small calibration subset large enough to include:

- Bias
- Dark or another master frame
- Trace
- Wavelength
- Multiple amplifiers or detectors
- Parallel execution

Generate the full timing report.

## Phase 2: Regression diagnosis

Identify:

- Dominant task kind
- Dominant timing phase
- Raw access behavior
- Database behavior
- Worker utilization
- Repeated reads
- Publication overhead
- Critical path

## Phase 3: Targeted optimization

Implement and test the primary fix or fixes.

## Phase 4: Subset comparison

Compare before and after:

```text
total wall time
task-kind totals
raw access median and p95
database time
publication time
worker utilization
scientific outputs
```

## Phase 5: Representative observation

Only after the subset regression is understood, rerun a representative complete observation in default four-worker and explicit serial modes.

Compare with the previous VIRUSFlow implementation and Remedy where equivalent timing is available.

---

# 14. Tests

## 14.1 Timing

Test that:

1. Every task receives a timing record.
2. Failed tasks retain timing data.
3. Phase durations are monotonic and nonnegative.
4. Nested phases are accounted for correctly.
5. Timing does not alter task identity.
6. Timing does not alter scientific output.
7. Interrupted runs produce partial summaries.

## 14.2 Raw access

Test that:

1. Raw lookup and raw read are measured separately.
2. Unique and repeated reads are counted correctly.
3. Archive opens and index builds are counted.
4. Cache hits and misses are counted.
5. Byte counts are recorded.
6. Warm and cold categories are represented when supported.

## 14.3 Database

Test that:

1. Query counts are recorded.
2. Query durations are recorded.
3. Lock waits are recorded where supported.
4. Raw catalog access after planning is detectable.
5. Long transaction scope is prevented or tested.
6. Multiple workers publish atomically.

## 14.4 Progress

Test that:

1. Mean, median, and p95 by task kind are correct.
2. ETA uses task-kind-specific timing.
3. Failed and blocked tasks are represented.
4. Serial and parallel summaries are correct.
5. Interrupted runs retain progress and timing summaries.

## 14.5 Reporting

Test:

1. Human-readable report generation
2. Machine-readable report generation
3. Stable report schema
4. Baseline comparison
5. Parallel-versus-serial comparison
6. Critical-path reporting
7. Slowest-task reporting

## 14.6 Performance safeguards

Add regression tests or benchmarks that detect:

- Repeated raw-catalog discovery
- Repeated archive-index construction
- Large-array process-pool transfer where avoidable
- Excessive database queries per task
- Persisted intermediate recomputation within one run

Avoid fragile absolute timing assertions in normal unit tests. Use operation counts and bounded benchmarks where appropriate.

---

# 15. Performance acceptance criteria

Do not proceed to the full observation validation until:

```text
Every task has decomposed timing metrics.

Progress reports mean, median, and p95 by task kind.

Raw access reports count, bytes, mean, median, and p95.

Unique and repeated raw reads are visible.

Archive opens and index builds are visible.

Database query, transaction, and lock-wait time are visible.

Artifact loading, serialization, hashing, and publication are separate.

Worker utilization and graph critical path are reported.

The current database architecture is documented.

Raw catalog access after planning is measured.

A small representative run is compared with the previous implementation.

The primary regression is identified.

The primary regression is fixed or explicitly justified.

The optimized subset preserves scientific equivalence.

Any remaining slowdown greater than 20 percent is explained.
```

For the complete representative observation, require:

```text
Parallel and serial runs produce scientifically equivalent outputs.

The four-worker run demonstrates meaningful parallel utilization.

Raw warm access is compared against the prior approximately 10 ms baseline.

The final performance report identifies the dominant remaining costs.

The complete run does not proceed silently without periodic timing summaries.
```

---

# 16. Required final Codex report

At completion, report:

1. Timing architecture implemented
2. Timing phases recorded
3. Progress-monitor performance fields
4. Raw-I/O measurements
5. Current database architecture
6. Whether raw catalog and artifact registry are shared or separate
7. Raw-catalog query behavior after planning
8. Database query and lock measurements
9. Worker utilization
10. Critical path
11. Repeated raw-read findings
12. Artifact serialization, hashing, and publication findings
13. Process-pool transfer findings
14. Primary regression cause
15. Optimizations implemented
16. Before-and-after subset timing
17. Scientific-equivalence checks
18. Tests run and results
19. Representative observation timing if actually completed
20. Remaining limitations

Do not claim a performance cause or improvement without measured evidence.

Do not continue a long full-observation run merely to satisfy the specification if the instrumented subset already shows an unresolved severe regression.
