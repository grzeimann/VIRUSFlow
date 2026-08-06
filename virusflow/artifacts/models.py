from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.identity import ZipCode
from ..ontology.relations import RelationKind
from ..ontology.lifecycle import ArtifactLifecycle
from ..ontology.scopes import PhysicalScope


@dataclass(frozen=True)
class Scope:
    zipcode: Optional[ZipCode]
    exposure_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    physical_scope: PhysicalScope = PhysicalScope.AMPLIFIER
    observation_id: Optional[str] = None
    dither_set_id: Optional[str] = None


@dataclass(frozen=True)
class Validity:
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    policy: str = "explicit"


@dataclass(frozen=True)
class ConfigurationReference:
    kind: str
    version: str
    identity: Optional[str] = None
    evidence_state: str = "unknown"


@dataclass(frozen=True)
class StorageRef:
    uri: str                   # absolute or relative filesystem path or URL
    storage_format: str        # e.g., "fits", "json", "png", "txt"
    backend: str = "fs"        # e.g., "fs" (filesystem), "s3", "db"


@dataclass(frozen=True)
class Provenance:
    algorithm: str
    params: Dict[str, Any] = field(default_factory=dict)
    parents: List[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    raw_parents: List[int] = field(default_factory=list)
    raw_catalog: Optional[str] = None


@dataclass(frozen=True)
class DiagnosticRecord:
    status: Optional[str] = None
    summary: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    related_artifact_ids: List[int] = field(default_factory=list)
    plots: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRelation:
    parent_id: int
    child_id: Optional[int] = None
    relation: str = RelationKind.DERIVED_FROM.value


@dataclass
class Artifact:
    id: Optional[int]
    kind: str                  # scientific meaning, e.g., "master_bias", "trace"
    role: str                  # calibration | reduction | diagnostic | report | metric
    payload_type: str          # array | table | image | text | scalar | collection
    storage_format: str        # fits | json | png | txt | ...
    storage: StorageRef        # where/how it is stored
    scope: Scope
    metadata: Dict[str, Any] = field(default_factory=dict)
    scientific_metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Optional[Provenance] = None
    validity: Validity = field(default_factory=Validity)
    units: Dict[str, str] = field(default_factory=dict)
    coordinates: Dict[str, str] = field(default_factory=dict)
    configuration_refs: List[ConfigurationReference] = field(default_factory=list)
    relations: List[ArtifactRelation] = field(default_factory=list)
    revision: Optional[str] = None
    checksum: Optional[str] = None
    lifecycle: ArtifactLifecycle = ArtifactLifecycle.CANONICAL
    state: str = "active"
    payload_bytes: int = 0


@dataclass(frozen=True)
class ArtifactDescription:
    id: int
    kind: str
    role: Optional[str]
    payload_type: Optional[str]
    storage_format: Optional[str]
    storage: Optional[StorageRef]
    scope: Optional[Scope]
    metadata: Dict[str, Any]
    scientific_metadata: Dict[str, Any]
    provenance: Optional[Provenance]
    diagnostics: Optional[DiagnosticRecord]
    model_type: Optional[str] = None  # array2d | array1d | image | table | text | scalar | collection | unknown
    validity: Optional[Validity] = None
    units: Dict[str, str] = field(default_factory=dict)
    coordinates: Dict[str, str] = field(default_factory=dict)
    configuration_refs: List[ConfigurationReference] = field(default_factory=list)
    relations: List[ArtifactRelation] = field(default_factory=list)
    revision: Optional[str] = None
    checksum: Optional[str] = None
    lifecycle: ArtifactLifecycle = ArtifactLifecycle.CANONICAL
    state: str = "active"
    payload_bytes: int = 0
