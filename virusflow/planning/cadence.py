from __future__ import annotations

"""
Cadence helper utilities.

These helpers intentionally use only read-only registry/database queries and do
not import algorithms or storage layers.

The initial implementation is conservative and returns a single open window when
no better enumeration is available. It can be iteratively refined to honor
min_n/minimum counts and real calendar windows using exposure timestamps.
"""

from datetime import datetime
from typing import List

from ..artifacts.models import Scope
from ..registry import database as db
from .targets import TemporalWindow


def time_cadence_windows(*, db_path: str, scope: Scope, frame_type: str, every_days: int, min_n_inputs: int = 1) -> List[TemporalWindow]:
    """Enumerate periodic windows for a zipcode.

    Minimal stub behavior:
    - If there is at least one raw file of the given frame_type for the scope.zipcode,
      return a single open window (start=None, end=None) to indicate "build a target".
    - Otherwise, return an empty list.
    """
    z = scope.zipcode
    if z is None:
        return []
    # Probe raw files presence for the zipcode and frame_type, limiting to a tiny range by listing the exposure table
    # and using list_raw_files_scoped over approximate broad dates. As a safe stub, just check existence via list_raw_filesScop ed with None window.
    try:
        rows = db.list_raw_files_scoped(frame_type=frame_type, start_date=None, end_date=None, zipcode=z, db_path=db_path)
    except TypeError:
        # Older signature might not accept None dates; fall back to list_raw_files and filter exposure table
        rows = db.list_raw_files(exposure_id=None, db_path=db_path)
        rows = [r for r in rows if (str(r.get("frame_type")) == frame_type and str(r.get("zipcode")) == z.key())]
    if not rows:
        return []
    # In a future refinement, split by every_days buckets and enforce min_n_inputs in each.
    return [TemporalWindow(start=None, end=None)]


def exposure_count_windows(*, db_path: str, scope: Scope, frame_type: str, min_n: int, max_span_days: int) -> List[TemporalWindow]:
    """Enumerate windows by rolling exposure counts.

    Minimal stub behavior:
    - If at least min_n raw files exist for the given frame_type and zipcode, return a single open window.
    - Otherwise, return empty.
    """
    z = scope.zipcode
    if z is None:
        return []
    try:
        rows = db.list_raw_files_scoped(frame_type=frame_type, start_date=None, end_date=None, zipcode=z, db_path=db_path)
    except TypeError:
        rows = db.list_raw_files(exposure_id=None, db_path=db_path)
        rows = [r for r in rows if (str(r.get("frame_type")) == frame_type and str(r.get("zipcode")) == z.key())]
    if len(rows) < int(min_n):
        return []
    return [TemporalWindow(start=None, end=None)]
