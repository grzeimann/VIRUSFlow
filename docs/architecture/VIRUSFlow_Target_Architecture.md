# VIRUSFlow Target Architecture

> Status: Proposed implementation architecture derived from the scientific
> knowledge-note collection

This document defines the software and data architecture needed to support the
full breadth of the VIRUSFlow knowledge notes while keeping implementation
contracts compact.

---

# 1. Architectural Goal

VIRUSFlow should be:

```text
A scientific knowledge-producing reduction system
```

rather than only:

```text
A serial pipeline that writes final files
```

Every reduction should produce:

- calibrated data Products;
- scientific facts;
- provenance;
- QA evidence;
- and reusable models.

The system should support a conservative production path immediately while
allowing increasingly sophisticated models to coexist.

---

# 2. Core Layering

```text
Configuration and Ontology
        ↓
Registry and Artifact Service
        ↓
Targets and Planning
        ↓
Tasks
        ↓
Algorithms
        ↓
QA
        ↓
Persistence
        ↓
Analytics and Model Learning
        ↓
Query and Scientific Assembly
```

The dependencies flow downward during execution.

Knowledge and evidence flow back upward into the registry.

---

# 3. Repository Package Layout

```text
virusflow/
├── ontology/
│   ├── entities.py
│   ├── scopes.py
│   ├── artifact_kinds.py
│   ├── relations.py
│   ├── units.py
│   ├── coordinates.py
│   └── assumptions.py
│
├── config/
│   ├── hardware/
│   ├── fplane/
│   ├── fiber_maps/
│   ├── orientations/
│   ├── controller_history/
│   ├── gains/
│   ├── dead_fibers/
│   ├── arc_lines/
│   ├── dither_patterns/
│   └── shutter_policies/
│
├── registry/
│   ├── models.py
│   ├── artifact_registry.py
│   ├── configuration_registry.py
│   ├── lineage.py
│   ├── validity.py
│   └── selection.py
│
├── artifacts/
│   ├── service.py
│   ├── requests.py
│   ├── records.py
│   ├── serializers/
│   ├── materializers/
│   └── storage/
│
├── targets/
│   ├── hardware.py
│   ├── calibration.py
│   ├── exposure.py
│   ├── ccd.py
│   ├── observation.py
│   └── studies.py
│
├── algorithms/
│   ├── detector/
│   ├── calibration/
│   ├── geometry/
│   ├── extraction/
│   ├── scatter/
│   ├── astrometry/
│   ├── sky/
│   ├── response/
│   └── reconstruction/
│
├── tasks/
│   ├── detector/
│   ├── calibration/
│   ├── science/
│   ├── exposure/
│   ├── observation/
│   └── analytics/
│
├── graphs/
│   ├── task_graph.py
│   ├── planners.py
│   ├── calibration_graph.py
│   ├── exposure_graph.py
│   └── observation_graph.py
│
├── qa/
│   ├── facts.py
│   ├── rules.py
│   ├── policy.py
│   ├── statuses.py
│   └── configs/
│
├── analytics/
│   ├── studies/
│   ├── models/
│   ├── reports/
│   └── plots/
│
├── query/
│   ├── selectors.py
│   ├── collections.py
│   └── observation_sets.py
│
├── io/
│   ├── filesystem.py
│   ├── tar_index.py
│   ├── raw_headers.py
│   └── catalogs.py
│
├── cli/
│   ├── scan.py
│   ├── plan.py
│   ├── run.py
│   ├── query.py
│   └── study.py
│
└── tests/
    ├── unit/
    ├── contracts/
    ├── integration/
    ├── regression/
    └── science_acceptance/
```

---

# 4. Ontology Layer

The ontology layer contains compact, stable vocabulary.

It must not contain large arrays or algorithm implementations.

## 4.1 Scope

```python
class Scope(str, Enum):
    PIXEL = "pixel"
    FIBER = "fiber"
    AMPLIFIER = "amplifier"
    PHYSICAL_CCD = "physical_ccd"
    SPECTROGRAPH = "spectrograph"
    IFU = "ifu"
    EXPOSURE = "exposure"
    DITHER_SET = "dither_set"
    OBSERVATION = "observation"
    OBSERVATION_SET = "observation_set"
    INSTRUMENT_EPOCH = "instrument_epoch"
```

## 4.2 Artifact kind

Artifact kinds are registered, not invented ad hoc by tasks.

```python
@dataclass(frozen=True)
class ArtifactKindSpec:
    name: str
    scope: Scope
    payload_type: str
    units: str | None
    required_metadata: tuple[str, ...]
    allowed_roles: tuple[str, ...]
```

## 4.3 Relation vocabulary

```text
contains
member_of
derived_from
calibrated_by
supersedes
valid_for
measures
predicts
refines
compares_to
uses_configuration
```

## 4.4 Assumption registry

```python
@dataclass(frozen=True)
class AssumptionSpec:
    assumption_id: str
    statement: str
    domain: str
    validation_study: str | None
```

Products reference assumption IDs instead of duplicating free-form prose.

---

# 5. Configuration Architecture

Configuration is versioned data.

It should not be embedded in algorithms.

Examples:

```text
F-plane geometry
Fiber positions
Amplifier orientations
Physical CCD transforms
Controller assignments
Gain values
Dead-fiber maps
Arc-line list
Dither pattern
Shutter timing policy
```

## 5.1 Physical CCD transform configuration

The inferred baseline transforms are:

```yaml
left_ccd:
  lower_amp: LL
  upper_amp: LU
  x_transform: x
  lower_y_transform: y
  upper_y_transform: 2064 - y

right_ccd:
  lower_amp: RU
  upper_amp: RL
  x_transform: x
  lower_y_transform: y
  upper_y_transform: 2064 - y
```

The configuration should distinguish continuous trace coordinates from indexed
array placement.

A transform record should therefore include:

```text
coordinate_convention
pixel_center_origin
seam_coordinate
array_index_transform
continuous_coordinate_transform
```

The physical ordering is resolved; the exact `2063 - y` versus `2064 - y`
materialization convention remains a validation test.

## 5.2 Configuration record

```python
@dataclass(frozen=True)
class ConfigurationRecord:
    kind: str
    version: str
    valid_from: datetime | None
    valid_to: datetime | None
    identity: dict[str, str]
    payload: dict | ArrayRef
    provenance: dict
```

## 5.3 Timeline-aware resolution

```python
config = configuration_registry.resolve(
    kind="controller_assignment",
    identity={"ifuslot": "047", "amp": "LL"},
    at=exposure.time,
)
```

Configuration selection must be deterministic and recorded.

---

# 6. Target Architecture

A Target describes what scientific Product is requested.

A Target does not hold the Product payload.

## 6.1 Common target protocol

```python
class Target(Protocol):
    kind: str
    scope: Scope

    def identity(self) -> dict[str, object]:
        ...
```

## 6.2 Representative targets

```python
AmplifierTarget(zipcode, date_range)
PhysicalCCDExposureTarget(exposure_id, specid, ccd_id)
ExposureTarget(exposure_id)
DitherSetTarget(dither_set_id)
ObservationSetTarget(observation_set_id)
```

Specific Products may have typed targets:

```python
MasterBiasTarget(zipcode, validity_window)
TraceTarget(ifuslot, ifuid, specid, amp, validity_window)
SkyTarget(exposure_id)
AstrometryTarget(exposure_id)
```

Targets must be hashable and serializable.

---

# 7. Algorithm Contract

Algorithms:

- perform numerical or scientific transformations;
- receive arrays and plain typed metadata;
- return storage-neutral results;
- do not query the database;
- do not choose calibration Products;
- do not write files;
- do not create plots;
- do not decide QA status.

## 7.1 AlgoResult

```python
@dataclass
class AlgoResult:
    kind: str
    arrays: dict[str, np.ndarray]
    scalars: dict[str, float | int | str | bool]
    meta: dict[str, object]
    messages: list[str]
    timings: dict[str, float]
    version: str

    def as_meta(self) -> dict[str, object]:
        return {**self.meta, **self.scalars}
```

## 7.2 Required algorithm properties

Every algorithm should be:

- deterministic for fixed inputs and parameters;
- independently testable;
- explicit about units and coordinate conventions;
- explicit about failure or partial-result behavior;
- and free of persistence concerns.

---

# 8. Task Contract

Tasks own orchestration.

A Task:

1. resolves its target;
2. selects input Artifacts and configuration;
3. loads payloads through ArtifactService;
4. invokes an algorithm;
5. publishes algorithm facts;
6. runs QA policy;
7. creates ArtifactRequests;
8. persists Products.

## 8.1 Task protocol

```python
class Task(Protocol):
    task_kind: str

    def requires(self, target: Target) -> list[Target]:
        ...

    def run(
        self,
        target: Target,
        context: TaskContext,
    ) -> TaskResult:
        ...
```

## 8.2 TaskResult

```python
@dataclass
class TaskResult:
    artifact_ids: list[str]
    qa_result_ids: list[str]
    facts: dict[str, object]
    messages: list[str]
```

---

# 9. Artifact Service

ArtifactService is the only supported path for Product persistence and loading.

## 9.1 Responsibilities

```text
Register Product metadata
Persist payloads
Load payloads
Resolve serializers
Calculate checksums
Maintain source lineage
Support immutable revisions
Apply selection policy
Materialize requested formats
```

## 9.2 ArtifactRequest

```python
@dataclass
class ArtifactRequest:
    kind: str
    role: str
    target: Target
    payload_type: str
    payload: object
    metadata: dict[str, object]
    provenance: Provenance
    validity: Validity
    qa_status: str
```

## 9.3 Artifact record

```python
@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    kind: str
    role: str
    scope: Scope
    identity: dict[str, object]
    payload_type: str
    storage_uri: str
    metadata: dict[str, object]
    provenance: Provenance
    validity: Validity
    qa_status: str
    created_at: datetime
```

---

# 10. Provenance Contract

```python
@dataclass(frozen=True)
class Provenance:
    source_artifact_ids: tuple[str, ...]
    raw_frame_ids: tuple[str, ...]
    configuration_versions: dict[str, str]
    complete_zipcodes: tuple[str, ...]
    algorithm: str
    algorithm_version: str
    software_version: str
    parameters_hash: str
    execution_id: str
```

The source graph should be queryable in both directions:

```text
What produced this Product?
What Products depend on this Product?
```

---

# 11. Validity and Selection

Validity and Product selection are separate from algorithm implementation.

## 11.1 Validity

```python
@dataclass(frozen=True)
class Validity:
    valid_from: datetime | None
    valid_to: datetime | None
    domain: dict[str, object]
    extrapolation: str
```

## 11.2 Selection policy

A selection request specifies:

```text
required kind
target identity
observation time
hardware identity
QA minimum
fallback allowance
```

The selector returns:

```python
@dataclass
class SelectionResult:
    artifact_id: str
    match_quality: str
    reason: str
    inherited: bool
    extrapolated: bool
```

## 11.3 Match-quality vocabulary

```text
EXACT
INTERPOLATED
INHERITED_PRIOR
NEAREST_VALID
DEGRADED_FALLBACK
UNAVAILABLE
```

Every non-exact choice is preserved in provenance.

---

# 12. QA Architecture

Algorithms emit facts.

QA interprets facts.

## 12.1 Fact

```python
@dataclass(frozen=True)
class QAFact:
    name: str
    value: object
    units: str | None
    scope: Scope
    source_artifact_id: str | None
```

## 12.2 Rule

Rules are configuration driven.

```yaml
metric: read_noise_e
scope: amplifier
pass:
  max: 4.5
warn:
  max: 6.0
fail:
  min: 6.0
```

## 12.3 Status vocabulary

```text
PASS
WARN
FAIL
INVALID
DEGRADED
EXPERIMENTAL
NOT_EVALUATED
```

## 12.4 Usability is contextual

One Product may be:

- unacceptable for precision science;
- but useful for diagnostics;
- or valid as a prior.

Therefore:

```text
QA status
    ≠
universal deletion decision
```

Selection policies declare acceptable QA states.

---

# 13. Analytics Architecture

Analytics is post-run, read-only with respect to source Products.

It may create new analytic Products, but it does not mutate reduction Products.

## 13.1 Study contract

```python
class Study(Protocol):
    study_kind: str

    def query(self, registry) -> Dataset:
        ...

    def analyze(self, dataset: Dataset) -> AlgoResult:
        ...
```

## 13.2 Studies implied by the notes

```text
bias stability
read-noise history
dark-current evolution
hot-pixel persistence
temperature trace drift
wavelength drift
aperture capture stability
LDLS/twilight/science profile comparison
scatter-kernel evolution
dither repeatability
F-plane residuals
sky residual floor
variance calibration
relative-response evolution
mirror-cycle response
track-position response
```

---

# 14. Graph Planning

The planner builds a TaskGraph from requested Targets.

## 14.1 Graph properties

- directed acyclic graph;
- explicit dependencies;
- topological execution;
- parallel nodes where scopes are independent;
- cache reuse through ArtifactService;
- resumability;
- failure isolation;
- and degraded-mode policy.

## 14.2 Example exposure graph

```text
ScanExposure
    ↓
ClassifyObservingMode
    ↓
ResolveConfiguration
    ↓
ReduceAllAmplifiers
    ↓
AssemblePhysicalCCDs
    ↓
CorrectScatteredLight
    ↓
ExtractAllFibers
    ↓
FitAstrometry
    ↓
BuildSkyMask
    ↓
FitExposureIllumination
    ↓
BuildIncidentSky
    ↓
PredictFiberSky
    ↓
SubtractSky
    ↓
ApplyRelativeResponse
    ↓
PublishExposureProducts
```

Calibration Products are dependencies resolved through the registry rather than
rebuilt unconditionally.

---

# 15. Baseline Science-Exposure Architecture

## 15.1 Detector reduction

For each amplifier:

```text
raw image
→ orientation
→ overscan correction
→ gain conversion
→ bias subtraction
→ dark subtraction
→ pixel masking
→ detector variance
```

## 15.2 Physical CCD correction

For each physical CCD:

```text
paired amplifier images
+ physical CCD transforms
+ trace maps
→ gap-constrained scatter model
→ scatter-subtracted images
```

## 15.3 Extraction

For each amplifier:

```text
scatter-subtracted image
+ trace
+ variance
→ five-pixel fractional aperture spectra
+ extracted variance
```

## 15.4 Exposure calibration

Across the full exposure:

```text
fiber normalization
+ astrometry
+ exposure illumination
+ source mask
+ native wavelength samples
→ incident sky
→ per-fiber sky
→ sky-subtracted spectra
```

## 15.5 Response

```text
sky-subtracted spectra
+ baseline response
+ selected perturbations
→ calibrated relative spectra
```

Each input Product remains linked.

---

# 16. Storage Architecture

## 16.1 Metadata database

Use a relational database for:

- entities;
- Artifact records;
- provenance edges;
- validity;
- QA facts;
- configuration timelines;
- and collection membership.

## 16.2 Payload storage

Use file/object storage for:

- FITS arrays;
- HDF5/Zarr collections;
- Parquet tables;
- JSON/YAML metadata;
- PNG/PDF reports.

The database stores storage URIs and checksums, not large array blobs by
default.

## 16.3 Payload format policy

```text
Small tabular data → Parquet
Small structured metadata → JSON
2D detector arrays → FITS or Zarr
Many fiber spectra → HDF5, Zarr, or columnar array store
Plots → PNG/PDF
```

Serializer choice is an ArtifactService concern.

---

# 17. Query Architecture

The query layer assembles scientific collections without forcing reduction-time
coaddition.

Examples:

```python
query.exposures(
    target="M33",
    seeing_lt=2.0,
    qa_at_least="WARN",
)
```

```python
query.fiber_spectra(
    sky_region=region,
    wavelength_range=(3500, 5500),
)
```

```python
query.observation_set(
    membership_rule=...,
)
```

Saved queries become versioned ObservationSet membership definitions.

---

# 18. Degraded Modes

Degraded behavior must be explicit.

Examples:

```text
Missing paired amplifier:
do not silently run physical-CCD scatter;
publish degraded/no-scatter Product if policy permits.

Too few astrometric matches:
retain header astrometry with DEGRADED status.

No suitable twilight:
select inherited normalization prior with recorded match quality.

No blank sky fibers:
use predictive illumination/sky prior or require offset sky.

Invalid trace:
do not extract zero-filled spectra as if valid.
```

---

# 19. Compatibility with Current Code

Legacy functions should be wrapped and tested before being rewritten where
possible.

Examples:

```text
get_spectra
get_spectra_error
trace recovery
arc identification
astrometry fitting
native-grid sky construction
```

The migration process is:

```text
Legacy Function
    ↓
Characterization Test
    ↓
Pure Algorithm Wrapper
    ↓
Typed AlgoResult
    ↓
Task + Artifact Contract
    ↓
Refactor internals
```

This preserves scientific behavior while architecture changes.

---

# 20. Architecture Completion Criteria

The architecture is implementation ready when:

- ontology registries exist;
- Product kinds and scopes are registered;
- configuration timelines are loadable;
- Target identities are serializable;
- ArtifactService can persist and select Products;
- AlgoResult and Task contracts are stable;
- QA facts and statuses are implemented;
- provenance edges are queryable;
- one exposure graph can be planned and resumed;
- and baseline science Products can be reproduced from raw frames.
