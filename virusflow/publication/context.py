from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PublicationContext:
    """Execution context supplied by a Task when publishing artifacts.

    Carries task and algorithm identity, parameters, resolved parent IDs, and
    coarse timings. This is distinct from the logical ArtifactRequest.
    """

    task_name: str
    task_version: str
    algorithm_name: Optional[str] = None
    algorithm_version: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    parent_ids: List[int] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)
    submitted_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
