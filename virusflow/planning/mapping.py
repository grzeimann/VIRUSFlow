from __future__ import annotations

"""
Helpers for downstream mapping (science → calibrations) based on edge policy.

This small utility centralizes how we consult ArtifactService for selecting
parent calibrations given a planning edge policy and tolerance. It is intended
for use by callers outside the planner (e.g., tasks or runners) when they need
an explicit mapping at execution time.

Notes
- This module stays within the planning layer but only depends on
  ArtifactService and models; no algorithms/storage imports.
- Tolerance handling mirrors the logic used in ReductionGraph.plan for
  tolerance-aware idempotency.
"""

from datetime import datetime
from typing import Optional

from ..artifacts.service import ArtifactService
from ..artifacts.models import Scope


def select_for_edge(
    *,
    kind: str,
    scope: Scope,
    at_time: Optional[datetime],
    policy: str = "latest_valid",
    tolerance_days: Optional[int] = None,
    service: ArtifactService,
) -> Optional[dict]:
    """Select a parent artifact per edge policy with optional tolerance filter.

    Parameters
    - kind: parent artifact kind to select (e.g., master_flat)
    - scope: Scope(zipcode=...) used for zipcode-level selection
    - at_time: reference time for time-aware policies (usually science exposure time)
    - policy: selection policy; defaults to "latest_valid"
    - tolerance_days: if provided and at_time provided, ensure the selected
      artifact's provenance.created_at is within ±tolerance_days of at_time
    - service: an initialized ArtifactService

    Returns the selected registry row (dict) if a match passes tolerance checks,
    otherwise None.
    """
    row = service.select_best(kind=kind, scope=scope, at_time=at_time, policy=policy)
    if row is None:
        return None
    if tolerance_days is None or at_time is None:
        return row
    # Parse created_at from row if available; fall back to acceptance on parse issues
    try:
        created = row.get("created_at")
        if isinstance(created, str):
            from datetime import datetime as _dt
            try:
                created_dt = _dt.fromisoformat(created)
            except Exception:
                created_dt = _dt.strptime(created.split(".")[0], "%Y-%m-%d %H:%M:%S")
        else:
            created_dt = created  # may be datetime or None
        if created_dt is None:
            return row
        delta_days = abs((created_dt - at_time).days)
        if delta_days <= int(tolerance_days):
            return row
        return None
    except Exception:
        # On any parsing/field error, accept the selection rather than failing hard
        return row
