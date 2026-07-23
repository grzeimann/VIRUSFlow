from __future__ import annotations

# Public entrypoints for the new artifact subsystem
from .models import (
    Artifact,
    ArtifactDescription,
    ArtifactRelation,
    ConfigurationReference,
    DiagnosticRecord,
    Provenance,
    Scope,
    StorageRef,
    Validity,
)
from .service import ArtifactService
from ..ontology.lifecycle import ArtifactLifecycle
