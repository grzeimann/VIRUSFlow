from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .identity import ZipCode


@dataclass
class ProvenanceInfo:
    software_version: str
    git_commit: Optional[str]
    algorithm: str
    parameters_hash: str
    parents: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Artifact:
    """Generic artifact description recorded in the registry.

    Note: This dataclass is a light-weight descriptor. Persist to the registry
    to make it durable.
    """

    id: Optional[int]
    kind: str
    name: str
    path: Optional[str]
    zipcode: Optional[ZipCode]
    validity_start: Optional[datetime] = None
    validity_end: Optional[datetime] = None
    provenance: Optional[ProvenanceInfo] = None
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class CalibrationProduct(Artifact):
    pass


@dataclass
class ReductionProduct(Artifact):
    pass
