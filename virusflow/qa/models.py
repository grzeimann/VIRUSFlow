from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class QAPacket:
    """Standard on-disk QA packet schema.

    This object is JSON-serializable via ``dataclasses.asdict`` and is intended
    to be written beside plot artifacts so that later collectors can aggregate
    QA across runs, amps, or zip-code groupings.
    """

    kind: str  # e.g., "wave"
    artifact_id: Optional[int] = None

    # Identifiers to facilitate grouping/collection
    amp_id: Optional[str] = None
    run_id: Optional[str] = None
    obs_time: Optional[str] = None  # ISO timestamp
    zip_code: Optional[str] = None

    # Diagnostics outcome and numeric metrics
    status: Optional[str] = None  # pass/marginal/fail/unknown
    metrics: Dict[str, float] = field(default_factory=dict)

    # Human notes (optional)
    notes: Optional[str] = None

    # File references (relative to the packet directory)
    plots: Dict[str, str] = field(default_factory=dict)

    # Small JSON-serializable snippets; keep compact
    blobs: Dict[str, object] = field(default_factory=dict)

    # Original algorithm meta to retain (trim to compact fields)
    meta: Dict[str, object] = field(default_factory=dict)

    def to_json_dict(self) -> Dict[str, object]:
        return asdict(self)
