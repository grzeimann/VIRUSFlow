from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class VersionedConfiguration:
    kind: str
    version: str
    value: Any
    identity: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    evidence_state: str = "unknown"
    source: Optional[str] = None


@dataclass(frozen=True)
class EffectiveExposurePolicy:
    version: str = "baseline-1"
    parallel_offset_seconds: float = 8.0
    evidence_state: str = "provisional"

    def effective_seconds(self, *, exptime: Optional[float], pexptime: Optional[float], parallel: bool) -> Optional[float]:
        if exptime is not None:
            return float(exptime)
        if parallel and pexptime is not None:
            return max(0.0, float(pexptime) - self.parallel_offset_seconds)
        return float(pexptime) if pexptime is not None else None


@dataclass(frozen=True)
class DitherPolicy:
    version: str = "baseline-1"
    rule: str = "nominal_standard_sequence_then_astrometric_refinement"
    evidence_state: str = "provisional"

