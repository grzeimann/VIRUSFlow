# VIRUSFlow Legacy Vocabulary Map

Status: authoritative compatibility map; Steps 1–7 are implemented and accepted, Steps 8–10 are authorized as one autonomous tranche, and Step 11 remains unauthorized. No legacy term has been retired.

## Mapping rules

1. Canonical names describe physical or inferential scientific objects, not the current algorithm or file field.
2. An ambiguous legacy term remains readable and is not forced into a false one-to-one mapping.
3. New writes should use a canonical kind only after the kind, scope, units, components, validity, and lineage contract is approved.
4. Compatibility ends per term only after old persisted rows, CLI/query consumers, Tasks, analytics and tests can read the canonical replacement and a rollback reader remains available.
5. No rename is performed by this audit.

## Legacy-to-canonical map

| Legacy term | Canonical term | Context and verified evidence | Migration strategy | Compatibility duration |
|---|---|---|---|---|
| `masterflt`, `master_flat` | `master_ldls` | `algorithms.flat.step_flt` combines raw `flt`; `FlatTask.artifact_name="master_flat"`. The illumination note identifies this calibration-unit continuum Product as LDLS and explicitly says it is not the science fiber normalization. | Register `master_ldls`; keep `master_flat` as read/query alias; preserve `flat_response_mask` as a separate component/Product. Do not silently reinterpret it as fiber throughput. | Through registry-row migration and one full release after all Task, CLI, analytics and test consumers use `master_ldls`. |
| `mastercmp`, `master_cmp` | `master_arc` for the current unlabeled comparison-lamp aggregate | `CmpTask.frame_type="cmp"`, `step_cmp.arrays["master_comparison_lamp"]`, and 4,368 database raw rows expose only `cmp`; no lamp discriminator is stored. `WaveTask` consumes this as its arc image. | Introduce `master_arc` as the safe aggregate alias. Record lamp composition when available. Do not assume current inputs are separable Hg and Cd. | Keep read alias until old rows and Wave/analytics/CLI consumers migrate and lamp metadata policy is deployed. |
| `master_hg`, `master_cd` | Same names, optional conditional Products | Architecture notes describe separate Hg and Cd averages, but current registry evidence cannot select them. | Enable only when raw input metadata distinguishes lamp states. If distinct masters exist, derive `master_arc` with explicit parents. | Not a legacy-removal issue; kinds remain optional until supported by evidence. |
| `master_hgcd` | `master_arc` only if a combined Hg+Cd exposure is verified | No current source or registry field proves that `cmp` means a combined Hg+Cd lamp state. | Reserve rather than canonize. If hardware metadata proves a combined lamp state, register lamp composition and decide whether `master_hgcd` adds useful identity beyond `master_arc`. | Unresolved; no write alias until verified. |
| `mastersci`, `master_sci` | No automatic one-to-one mapping; current object is `science_diagnostic_stack` | `algorithms.sci.build_master_science` robustly combines multiple raw science frames; `SciTask` labels it diagnostic. It is not the target per-exposure `reduced_science_image`. | Give the existing diagnostic object an explicit diagnostic kind during compatibility migration. Build `reduced_science_image` separately per Exposure. | Preserve `master_sci` reads until diagnostic consumers migrate; never alias it to reduced science data. |
| `ftf` | Ambiguous: `within_amp_fiber_normalization`, `amp_to_amp_normalization`, or final `fiber_normalization` | The normalization note decomposes the final exposure Product into within-amp and amp-to-amp factors. No current `virusflow/` producer establishes which historical `ftf` field contains. | Require producing-file/context evidence. Map each field to a named component; retain raw legacy field and provenance if it combines factors. | Until every legacy dataset family has a documented field-level mapping and parity test. |
| `plaw` | `legacy_empirical_scatter_term`; potentially a component of `ccd_scattered_light_model` | Knowledge notes identify power-law scatter as one empirical method, not the physical Product. No current scatter implementation exists in `virusflow/`. | Preserve as an explicit empirical model term. Promote to a `ccd_scattered_light_model` component only after coordinate/scope and parity characterization. | Indefinite read compatibility until historical scatter Products are understood; no direct rename on static evidence. |
| `maskspec` | Ambiguous mask; likely one of `pixel_mask`, `spectral_validity_mask`, `sky_fiber_mask`, or extraction mask | No current symbol or schema field defines its domain. The target architecture requires detector, spectral, fiber-selection and validity masks to remain distinct. | Determine axes, bit meanings, producer and consumer. Store the original mask plus a typed canonical interpretation; never infer solely from the name. | Until each source format/version has a tested mapping. |
| `wave` | `wavelength_map` Product; related evidence becomes `arc_identification` and `per_fiber_wavelength_residual_rms` | `fit_wavelength_solution` returns `wavelength_map` and residual RMS; `WaveTask.artifact_name="wave"`. | Register canonical Product and facts; keep `wave` as kind/query alias and preserve arc matches/rejections as separate components. | Through migration of persisted rows, WaveTask, graph, CLI, analytics and tests. |
| `trace` | `trace_map`; related evidence becomes `trace_samples` and `per_fiber_trace_residual_rms` | `fit_fiber_traces` returns `fiber_trace_map`, sample columns/positions and residual RMS; `TraceTask` persists only the map under `trace`. | Register `trace_map`; retain `trace` alias; publish samples and residual facts without downsampled-name leakage into the canonical fact. | Through migration of rows, Tasks, graph, Wave dependency, analytics and tests. |
| `spec` | Usually `aperture_extracted_spectrum`, but context-dependent | `algorithms.wave` uses extracted comparison-lamp spectra while `fiber.get_spectra` is generic. Historical `spec` can represent arc, twilight or science spectra. | Resolve from producer, input kind, units and scope. Use role-specific canonical Product names and preserve original field metadata. | Until every producer/file version has a typed mapping. |
| `res` | Ambiguous residual array/table | Wavelength and trace code both produce residual concepts; the bare term does not encode domain, units or axes. | Map only with producer evidence, e.g. wavelength residuals versus trace-position residuals. Preserve unclassified legacy data. | Until field-level characterization and unit tests exist. |
| `rms_fibers` | `per_fiber_trace_residual_rms` when produced by trace fitting | `trace.fit_fiber_traces` currently emits `per_fiber_trace_residual_rms`; `AlgoResult` documentation still gives `rms_fibers` as an example. | Canonicalize the explicit fact name. Use a different domain-qualified name for wavelength RMS. | One compatibility release after all QA YAML, analytics and persisted summaries use the canonical fact. |
| `per_fiber_trace_residual_rms_ds` | `per_fiber_trace_residual_rms` with explicit preview/downsampling metadata | `TraceContract.summaries` and `TraceTask` use the `_ds` summary while the algorithm emits the full fact. | Keep downsampling as representation metadata, not a different scientific fact name. | Until Trace contract and analytics consume the full component/fact. |
| `per_fiber_wavelength_residual_rms_ds` | `per_fiber_wavelength_residual_rms` with explicit preview/downsampling metadata | `WaveContract.summaries` contains `_ds`; `fit_wavelength_solution` emits the full array. | Same representation-versus-fact separation as trace RMS. | Until Wave contract, QA and analytics migrate. |
| `readnoise` | `read_noise` | Current algorithm/Task/tests use `read_noise`; historical database QA evidence contains `readnoise`. | Normalize reads into `read_noise`, record original key in imported provenance, and write only canonical fact after contract approval. | Until historical QA JSON and all query consumers are normalized. |
| `master_twi` | `master_twilight` | `TwiTask.artifact_name="master_twi"`; `step_twi` component is `master_twilight`. | Use `master_twilight` as Product kind and component, with exposure/track-state identity; retain kind alias. | Through Task, registry, CLI and query migration. |
| `flat_response_mask` | Candidate component of canonical `pixel_mask` with a detector-response reason bit | `step_flt` derives local-response and bad-column flags. | Preserve the original mask and convert into a versioned bit vocabulary; do not merge silently with dark defects. | Until mask-bit parity and consumers are tested. |
| `dark_pixel_mask` | Candidate component of canonical `pixel_mask` with dark-current reason bits | `step_dark` uses residual and full-column heuristics. | Preserve as evidence component and derive canonical bitmask with explicit algorithm/version. | Until mask-bit parity and consumers are tested. |
| `trace_preview`, `trace_row_dispersion` | Analytic Products derived from `trace_map` and trace facts | Database contains 1,248 rows of each; analytics registers static outputs. | Retain as analytic kinds with explicit `derived_from` relations, never as source calibration replacements. | Permanent as canonical analytic roles after relation normalization. |

## Comparison-lamp decision

The current repository does not justify mandatory decomposition of `master_cmp`:

- `registry.database.register_raw_file` records the filename-derived frame type `cmp`.
- `CmpTask.query_inputs` selects `frame_type="cmp"` with no lamp-state predicate.
- `step_cmp` robustly combines all selected frames into `master_comparison_lamp`.
- Current `exposure_details` rows for these inputs have no usable lamp discriminator.
- `WaveTask` treats the result as an aggregate arc calibration.

Therefore:

1. **Canonical baseline:** `master_arc`.
2. **Optional when inputs are distinct:** `master_hg` and `master_cd`, with `master_arc` derived from both.
3. **Optional when combined-lamp state is explicitly verified:** `master_hgcd`; determine whether it is a distinct measured Product or merely lamp-composition metadata on `master_arc`.

This differs from declaring the knowledge note wrong: the note provides a scientifically useful separate-lamp baseline, while the current repository lacks the identity needed to apply it safely.

## Compatibility mechanics to implement only after review

- A registered alias table maps legacy read names to canonical kinds without rewriting historical rows in place.
- New records retain original imported kind/field names in provenance.
- Query results expose canonical kind plus legacy source name.
- Task/CLI deprecation warnings begin only after canonical write support exists.
- Removal requires parity tests, migrated consumers, and a documented rollback reader.

No compatibility mechanism above may be implemented until all three reconciliation documents are reviewed.

## Implemented compatibility status through Step 7

The review gate was satisfied and the following additive compatibility mechanisms are now active:

| Legacy read/run term | Canonical new-write term | Implemented adapter |
|---|---|---|
| `master_flat`, `masterflt` | `master_ldls` | ontology alias, ArtifactService selection candidates, planning/task mapping alias, `FlatTask` legacy output key |
| `master_cmp`, `mastercmp` | `master_arc` | ontology alias, ArtifactService selection candidates, planning/task mapping alias, `CmpTask` legacy output key |
| `master_twi` | `master_twilight` | ontology alias, planning/task mapping alias, `TwiTask` legacy output key |
| `trace` | `trace_map` | ontology alias, ArtifactService selection candidates, planning/task mapping alias, `TraceTask` legacy output key |
| `wave` | `wavelength_map` | ontology alias, ArtifactService selection candidates, planning/task mapping alias, `WaveTask` legacy output key |
| Task version `v1` | canonical Task implementation `v2` | explicit `get_task_class(name, "v1")` compatibility registrations |
| `fit_fiber_traces(raw_inputs, params)` | explicit array/reference inputs | compatible call shape retained; path parameters fail explicitly rather than performing algorithm-side I/O |

New writes use the canonical kinds and complete component contracts. `ArtifactService.describe` exposes the historical stored kind and `canonical_kind`; it does not rewrite old rows. The repository's historical database and files were not migrated, deleted, or renamed. Optional `master_hg`, `master_cd`, and `master_hgcd` remain unresolved because neither 20260609 nor 20260604 inventory adds usable lamp-state identity.

Compatibility duration remains as specified in the table. Step 11 retirement has not begun and is not authorized; no legacy reader, Task, alias, database row, file, or public entry point may be removed during the Steps 8–10 tranche.
