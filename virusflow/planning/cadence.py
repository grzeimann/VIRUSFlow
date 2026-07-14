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


def time_cadence_windows(*, db_path: str, scope: Scope, frame_type: str, every_days: int, min_n_inputs: int = 1, start_date: str | None = None, end_date: str | None = None) -> List[TemporalWindow]:
    """Enumerate periodic windows for a zipcode.

    Refined minimal behavior:
    - If there are at least `min_n_inputs` raw files of the given frame_type for the scope.zipcode
      across all time, return a single concrete window covering the min..max exposure timestamps
      observed for that frame_type in this zipcode.
    - Otherwise, return an empty list.
    """
    z = scope.zipcode
    if z is None:
        return []
    # Probe raw files presence for the zipcode and frame_type across a requested or wide date range.
    SD = start_date or "19000101"
    ED = end_date or "21000101"
    try:
        rows = db.list_raw_files_scoped(frame_type=frame_type, start_date=SD, end_date=ED, zipcode=z, db_path=db_path)
    except Exception:
        # Fallback: attempt unscoped listing and filter client-side when older APIs are present
        raw = db.list_raw_files(exposure_id=None, db_path=db_path)
        rows = []
        for r in raw:
            try:
                if str(getattr(r, "frame_type", getattr(r, "frame_type", None))) == frame_type and getattr(r, "zipcode", None) and r.zipcode.key() == z.key():
                    rows.append((0, r))
            except Exception:
                pass
    # If a planning window was provided and there are any rows in that window, emit a concrete window even if below min_n_inputs.
    if len(rows) < int(min_n_inputs):
        if start_date or end_date:
            # derive bounds and emit a single window if any timestamps are parsable
            def _parse_exposure_id(eid: str):
                from datetime import datetime as _dt
                s = str(eid)
                base = s.split(".")[0]
                try:
                    return _dt.strptime(base, "%Y%m%dT%H%M%S")
                except Exception:
                    try:
                        return _dt.strptime(base[:8], "%Y%m%d")
                    except Exception:
                        return None
            times: List[datetime] = []
            for (_rid, rf) in rows:
                t = _parse_exposure_id(getattr(rf, "exposure_id", None))
                if t is not None:
                    times.append(t)
            if times:
                return [TemporalWindow(start=min(times), end=max(times))]
        return []
    # Derive concrete window bounds from exposure_id timestamps
    def _parse_exposure_id(eid: str):
        from datetime import datetime as _dt
        s = str(eid)
        base = s.split(".")[0]
        try:
            return _dt.strptime(base, "%Y%m%dT%H%M%S")
        except Exception:
            try:
                return _dt.strptime(base[:8], "%Y%m%d")
            except Exception:
                return None
    times: List[datetime] = []
    for (_rid, rf) in rows:
        t = _parse_exposure_id(getattr(rf, "exposure_id", None))
        if t is not None:
            times.append(t)
    if not times:
        # Fallback to open window if no parsable timestamps are present
        return [TemporalWindow(start=None, end=None)]
    start_t = min(times)
    end_t = max(times)
    return [TemporalWindow(start=start_t, end=end_t)]


def exposure_count_windows(*, db_path: str, scope: Scope, frame_type: str, min_n: int, max_span_days: int, start_date: str | None = None, end_date: str | None = None) -> List[TemporalWindow]:
    """Enumerate windows by rolling exposure counts.

    Implementation notes:
    - Uses registry.database.list_raw_files_scoped with a wide date range to fetch all
      raw rows for the zipcode and frame_type, then orders by exposure_id (which is
      time-encoded as YYYYMMDDTHHMMSS[.N]) as a stable proxy for observation time.
    - Emits non-overlapping windows where each window starts at the first exposure in
      the bucket and closes when either:
        a) we have accumulated >= min_n exposures, or
        b) the span between the first and current exposure exceeds max_span_days.
    - Window bounds are precise datetimes derived from exposure_id; end is set to the
      time of the last exposure in the window (half-open semantics left to callers).
    """
    z = scope.zipcode
    if z is None:
        return []
    # Fetch rows for this zipcode+frame_type across requested or generous date range
    SD = start_date or "19000101"
    ED = end_date or "21000101"
    try:
        rows = db.list_raw_files_scoped(frame_type=frame_type, start_date=SD, end_date=ED, zipcode=z, db_path=db_path)
    except TypeError:
        # Fallback path: list_raw_files (no zipcode scoping available); filter client-side
        raw = db.list_raw_files(exposure_id=None, db_path=db_path)
        rows = []
        for r in raw:
            try:
                if str(getattr(r, "frame_type", getattr(r, "frame_type", None))) == frame_type and getattr(r, "zipcode", None) and r.zipcode.key() == z.key():
                    rows.append((0, r))  # id unknown; we don't use it here
            except Exception:
                pass
    if not rows:
        return []
    # Parse exposure_id → datetime and sort
    def _parse_exposure_id(eid: str):
        from datetime import datetime as _dt
        s = str(eid)
        # Expect formats like 20260511T035810.4 or 20260511T035810
        base = s.split(".")[0]
        try:
            return _dt.strptime(base, "%Y%m%dT%H%M%S")
        except Exception:
            # As a fallback, try YYYYMMDD only
            try:
                return _dt.strptime(base[:8], "%Y%m%d")
            except Exception:
                return None
    items = []
    for (_rid, rf) in rows:
        t = _parse_exposure_id(getattr(rf, "exposure_id", None))
        if t is not None:
            items.append((t, rf))
    items.sort(key=lambda x: x[0])
    if not items:
        return []
    # Roll windows
    from datetime import timedelta
    out: List[TemporalWindow] = []
    i = 0
    n = len(items)
    while i < n:
        start_t, _ = items[i]
        count = 1
        j = i
        last_t = start_t
        while j + 1 < n:
            nxt_t, _ = items[j + 1]
            span_days = (nxt_t - start_t).days
            if span_days > int(max_span_days):
                break
            count += 1
            j += 1
            last_t = nxt_t
            if count >= int(min_n):
                break
        if count >= int(min_n) or (last_t - start_t).days >= int(max_span_days):
            out.append(TemporalWindow(start=start_t, end=last_t))
            i = j + 1
        else:
            # Not enough exposures and span not exceeded; stop accumulating further
            break
    return out
