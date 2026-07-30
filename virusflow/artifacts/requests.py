from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .models import ArtifactRelation, ConfigurationReference, Scope, Validity
from ..ontology.lifecycle import ArtifactLifecycle


@dataclass(frozen=True)
class LogicalComponent:
    """A named, logical component of a scientific product (format-agnostic).

    Example: name="master", model_type="array2d", value=<numpy array>
    """

    name: str
    model_type: str  # array2d | array1d | image | table | model | scalar | collection
    value: Any
    units: Optional[str] = None
    coordinates: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRequest:
    """Storage-neutral description of a logical scientific product to publish.

    Responsibilities:
    - Express scientific intent: what logical components, summaries, and metadata
      should be published, under which logical kind and scope, with which parents.
    - No QA state. No storage hints or representation decisions.
    """

    kind: str
    role: str = "calibration"
    components: Mapping[str, LogicalComponent] = field(default_factory=dict)
    summaries: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    scientific_metadata: Dict[str, Any] = field(default_factory=dict)
    scope: Optional[Scope] = None
    parents: List[int] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    validity: Validity = field(default_factory=Validity)
    configuration_refs: List[ConfigurationReference] = field(default_factory=list)
    relations: List[ArtifactRelation] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    revision: Optional[str] = None
    lifecycle: Optional[ArtifactLifecycle] = None

    def component_names(self) -> List[str]:
        return list((self.components or {}).keys())

    def get_component(self, name: str) -> Optional[LogicalComponent]:
        return (self.components or {}).get(name)
