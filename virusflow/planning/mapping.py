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
    otherwise None. If the best match is outside tolerance, the helper searches
    nearby candidates and returns the nearest within tolerance, if any.
    """
    row = service.select_best(kind=kind, scope=scope, at_time=at_time, policy=policy)
    # Fast-path when no tolerance logic needed
    if tolerance_days is None or at_time is None:
        return row
    # Parse created_at and apply tolerance; if outside, try alternate candidates
    def _parse_dt(val):
        if val is None:
            return None
        if isinstance(val, str):
            from datetime import datetime as _dt
            try:
                return _dt.fromisoformat(val)
            except Exception:
                try:
                    return _dt.strptime(val.split(".")[0], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    return None
        return val
    def _within_tol(r) -> bool:
        ct = _parse_dt(r.get("created_at")) if isinstance(r, dict) else None
        if ct is None:
            return True  # accept if unknown
        return abs((ct - at_time).days) <= int(tolerance_days)
    # If initial selection is acceptable, return it
    if row is not None and _within_tol(row):
        return row
    # Otherwise, query additional candidates around at_time and pick nearest within tol
    try:
        # Use adapter.find to retrieve a small set of candidates ordered by policy
        candidates = service.adapter.find(kind=kind, zipcode=scope.zipcode, at_time=at_time, limit=25)
        # Fallback search without time filter if no rows were returned (tight validity windows)
        if not candidates:
            candidates = service.adapter.find(kind=kind, zipcode=scope.zipcode, at_time=None, limit=25)
        # Filter within tolerance and select by minimal absolute delta days
        best = None
        best_delta = None
        for r in candidates:
            ct = _parse_dt(r.get("created_at"))
            if ct is None:
                # treat unknown created_at as acceptable but with worst ranking
                if best is None:
                    best = r
                    best_delta = 10**9
                continue
            d = abs((ct - at_time).days)
            if d <= int(tolerance_days):
                if best is None or d < (best_delta or 10**9):
                    best = r
                    best_delta = d
        return best
    except Exception:
        # If anything goes wrong during alternate selection, fall back to initial row
        return row
