from __future__ import annotations

from enum import Enum


class ArtifactLifecycle(str, Enum):
    """Storage lifecycle, deliberately separate from scientific identity."""

    CANONICAL = "canonical"
    MODEL = "model"
    ANALYSIS = "analysis"
    CACHE = "cache"
    SCRATCH = "scratch"


PERSISTENT_LIFECYCLES = frozenset(
    {
        ArtifactLifecycle.CANONICAL,
        ArtifactLifecycle.MODEL,
        ArtifactLifecycle.ANALYSIS,
        ArtifactLifecycle.CACHE,
    }
)
