"""
Thin adapter to bridge planning.Target to legacy CalibrationTask target expectations.

Legacy CalibrationTask-based tasks expect a target object with attributes:
- zipcode: ZipCode (from core.identity)
- start_date: str in YYYYMMDD
- end_date: str in YYYYMMDD

Planning Target provides:
- scope.zipcode (ZipCode)
- window.start / window.end as datetimes (optional)

This module provides:
- PlanningTargetAdapter: wrapper exposing the legacy attributes based on a planning Target
- adapt_target: small helper to adapt a planning Target for a given task class

No task imports are performed here to keep the planning layer independent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..artifacts.models import Scope
from .targets import Target, TemporalWindow

_DATE_FMT = "%Y%m%d"


def _dt_to_date_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    # Normalize to UTC-date footprint YYYYMMDD; assume dt is timezone-aware or naive UTC
    return dt.strftime(_DATE_FMT)


@dataclass(frozen=True)
class PlanningTargetAdapter:
    """Expose legacy CalibrationTask target fields for a planning Target.

    Attributes
    - zipcode: ZipCode from the planning target's scope
    - start_date: YYYYMMDD derived from window.start (or None if absent)
    - end_date: YYYYMMDD derived from window.end (or None if absent)
    - start_dt/end_dt: precise datetimes preserved from planning window for filename disambiguation
    """

    zipcode: object
    start_date: Optional[str]
    end_date: Optional[str]
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    group_id: Optional[str] = None
    raw_ids: tuple[int, ...] = ()
    group_metadata: dict[str, Any] | None = None

    @classmethod
    def from_planning(cls, t: Target) -> "PlanningTargetAdapter":
        scope = getattr(t, "scope", None)
        z = getattr(scope, "zipcode", None) if isinstance(scope, Scope) else None
        w = getattr(t, "window", None)
        ws = getattr(w, "start", None) if isinstance(w, TemporalWindow) else None
        we = getattr(w, "end", None) if isinstance(w, TemporalWindow) else None
        return cls(
            zipcode=z,
            start_date=_dt_to_date_str(ws),
            end_date=_dt_to_date_str(we),
            start_dt=ws,
            end_dt=we,
            group_id=(t.group.group_id if t.group else None),
            raw_ids=(t.group.raw_ids if t.group else ()),
            group_metadata=(dict(t.group.metadata) if t.group else None),
        )


def adapt_target(t: Target) -> PlanningTargetAdapter:
    """Return a legacy-compatible target wrapper for CalibrationTask consumers.

    For now we unconditionally adapt planning.Target to PlanningTargetAdapter.
    """
    return PlanningTargetAdapter.from_planning(t)
