"""
Cadence helper utilities.

These helpers intentionally use only read-only registry/database queries and do
not import algorithms or storage layers.

The initial implementation is conservative and returns a single open window when
no better enumeration is available. It can be iteratively refined to honor
min_n/minimum counts and real calendar windows using exposure timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import statistics
from typing import Any, Iterable, List

from ..artifacts.models import Scope
from ..registry import database as db
from .targets import CalibrationGroup, PurposeCadence, TemporalWindow


@dataclass(frozen=True)
class GroupingResult:
    groups: tuple[CalibrationGroup, ...]
    exclusions: tuple[dict[str, Any], ...] = ()


_ALGORITHM_IDENTITIES = {
    "master_bias": "bias-1.1",
    "master_dark": "dark-1.1",
    "master_ldls": "flat-1.1",
    "master_hg": "cmp-1.1",
    "master_cd": "cmp-1.1",
    "master_twilight": "twi-1.1",
    "master_sci": "master-sci-2.0",
}


def _parse_exposure_id(eid: str) -> datetime | None:
    value = str(eid)
    for fmt, candidate in (
        ("%Y%m%dT%H%M%S.%f", value),
        ("%Y%m%dT%H%M%S", value.split(".", 1)[0]),
        ("%Y%m%d", value[:8]),
    ):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            pass
    return None


def _stats(values: Iterable[float]) -> dict[str, float | int | None]:
    data = [float(value) for value in values if value is not None]
    if not data:
        return {"count": 0, "mean": None, "median": None, "minimum": None,
                "maximum": None, "spread": None}
    return {
        "count": len(data), "mean": statistics.fmean(data),
        "median": statistics.median(data), "minimum": min(data),
        "maximum": max(data), "spread": max(data) - min(data),
    }


def _lamp_kind(row: dict[str, Any]) -> str | None:
    frame_type = str(row.get("frame_type") or "").lower()
    if frame_type in {"hg", "cd"}:
        return frame_type
    evidence = " ".join(str(row.get(key) or "") for key in (
        "lamp", "object_name", "qobject"
    )).lower()
    if any(token in evidence for token in ("mercury", " hg", "hg ", "hg_", "_hg")) or evidence.strip() == "hg":
        return "hg"
    if any(token in evidence for token in ("cadmium", " cd", "cd ", "cd_", "_cd")) or evidence.strip() == "cd":
        return "cd"
    return None


def _calendar_key(at: datetime, policy: str, row: dict[str, Any], options: dict[str, Any]):
    if policy in {"nightly", "rolling_24h"}:
        return (at.year, at.month, at.day)
    if policy == "weekly":
        monday = (at - timedelta(days=at.weekday())).date()
        return monday.isoformat()
    if policy in {"observing_block", "dark_time"}:
        block = row.get("observing_block")
        if block:
            return str(block)
    return (at.year, at.month)


def _split_isolated(rows: list[dict[str, Any]], hours: float) -> list[list[dict[str, Any]]]:
    """Create deterministic non-overlapping groups with total span <= hours."""

    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    limit = timedelta(hours=float(hours))
    for row in rows:
        if current and row["timestamp"] - current[0]["timestamp"] > limit:
            out.append(current)
            current = []
        current.append(row)
    if current:
        out.append(current)
    return out


def _make_group(
    *, kind: str, zipcode: str, policy: str, options: dict[str, Any],
    rows: list[dict[str, Any]], sufficient: bool = True,
    sufficiency: dict[str, Any] | None = None,
) -> CalibrationGroup:
    rows = sorted(rows, key=lambda row: (row["timestamp"], int(row["raw_id"])))
    times = tuple(row["timestamp"] for row in rows)
    raw_ids = tuple(int(row["raw_id"]) for row in rows)
    exposure_ids = tuple(str(row["exposure_id"]) for row in rows)
    center = times[0] + (times[-1] - times[0]) / 2
    applicability_start = times[0]
    applicability_end = times[-1]
    if rows[0].get("applicability_start") is not None:
        applicability_start = rows[0]["applicability_start"]
        applicability_end = rows[0]["applicability_end"]
    elif policy == "nightly":
        applicability_start = times[0].replace(hour=0, minute=0, second=0, microsecond=0)
        applicability_end = applicability_start + timedelta(days=1)
    elif policy == "rolling_24h":
        applicability_start = times[0]
        applicability_end = applicability_start + timedelta(hours=24)
    elif policy == "weekly":
        applicability_start = (times[0] - timedelta(days=times[0].weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        applicability_end = applicability_start + timedelta(days=7)
    elif policy == "monthly":
        applicability_start = times[0].replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if applicability_start.month == 12:
            applicability_end = applicability_start.replace(
                year=applicability_start.year + 1, month=1
            )
        else:
            applicability_end = applicability_start.replace(month=applicability_start.month + 1)
    exposure_times = [row.get("exposure_time") for row in rows]
    temperatures = [row.get("ambient_temperature") for row in rows]
    identity_payload = {
        "kind": kind, "zipcode": zipcode, "raw_ids": raw_ids,
        "algorithm_version": options.get("algorithm_version", _ALGORITHM_IDENTITIES.get(kind)),
        "algorithm_parameters": options.get("algorithm_parameters", {}),
        "configuration_references": options.get("configuration_references", ()),
    }
    computation_id = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    group_id = f"{kind}:{zipcode}:{computation_id[:12]}"
    metadata = {
        "grouping_configuration": dict(options),
        "frame_membership": [
            {
                "raw_id": int(row["raw_id"]), "exposure_id": row["exposure_id"],
                "timestamp": row["timestamp"].isoformat(timespec="microseconds"),
                "exposure_time_seconds": row.get("exposure_time"),
                "exposure_time_source": row.get("exposure_time_source"),
                "ambient_temperature": row.get("ambient_temperature"),
                "observing_block": row.get("observing_block"),
                "lamp": row.get("lamp_kind"), "decision": "included",
            }
            for row in rows
        ],
        "n_exposures": len(rows),
        "exposure_time_seconds": _stats(exposure_times),
        "total_exposure_seconds": sum(float(value) for value in exposure_times if value is not None),
        "ambient_temperature": _stats(temperatures),
        "missing_temperature": any(value is None for value in temperatures),
        "temporal_center": center.isoformat(timespec="microseconds"),
        "temporal_span_seconds": (times[-1] - times[0]).total_seconds(),
        "sufficiency": dict(sufficiency or {}),
    }
    return CalibrationGroup(
        group_id=group_id, computation_id=computation_id, policy=policy,
        raw_ids=raw_ids, exposure_ids=exposure_ids, timestamps=times,
        metadata=metadata,
        applicability={
            "start": applicability_start.isoformat(timespec="microseconds"),
            "end": applicability_end.isoformat(timespec="microseconds"),
            "center": center.isoformat(timespec="microseconds"),
            "policy": policy,
        },
        sufficient=bool(sufficient), decision="planned" if sufficient else "insufficient",
        coherence_key=hashlib.sha256(json.dumps({
            "kind": kind, "purpose": policy, "exposure_ids": sorted(exposure_ids),
            "observing_blocks": sorted({str(row.get("observing_block") or "") for row in rows}),
            "grouping_configuration": dict(options),
        }, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
    )


def resolve_calibration_groups(
    *, kind: str, cadence: PurposeCadence, scope: Scope, db_path: str,
    start_date: str | None = None, end_date: str | None = None,
    source_rows: Iterable[dict[str, Any]] | None = None,
) -> GroupingResult:
    """Resolve exact membership before planner deduplication."""

    if scope.zipcode is None:
        return GroupingResult(())
    options = dict(cadence.options)
    policy = cadence.policy
    frame_types = {
        "master_bias": ("zro",), "master_dark": ("drk",),
        "master_twilight": ("twi",), "master_ldls": ("flt",),
        "master_hg": ("cmp", "hg"), "master_cd": ("cmp", "cd"),
        "master_sci": ("sci",),
    }.get(kind, tuple(options.get("frame_types", ())))
    if source_rows is None:
        source = db.list_calibration_grouping_rows(
            db_path=db_path, zipcode=scope.zipcode, frame_types=frame_types,
            start_date=start_date, end_date=end_date,
        )
    else:
        accepted_types = set(frame_types)
        source = [
            row for row in source_rows
            if str(row.get("frame_type") or "").lower() in accepted_types
        ]
    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    threshold = float(options.get("minimum_exposure_seconds", 300.0))
    for original in source:
        row = dict(original)
        at = _parse_exposure_id(row.get("exposure_id"))
        if at is None:
            exclusions.append({"raw_id": row["raw_id"], "exposure_id": row["exposure_id"],
                               "decision": "excluded", "reason": "unparseable_timestamp"})
            continue
        row["timestamp"] = at
        value = row.get("exptime") if row.get("exptime") is not None else row.get("pexptime")
        row["exposure_time"] = float(value) if value is not None else None
        row["exposure_time_source"] = "EXPTIME" if row.get("exptime") is not None else (
            "PEXPTIME" if row.get("pexptime") is not None else None
        )
        row["lamp_kind"] = _lamp_kind(row)
        if kind in {"master_hg", "master_cd"} and row["lamp_kind"] != kind.removeprefix("master_"):
            exclusions.append({"raw_id": row["raw_id"], "exposure_id": row["exposure_id"],
                               "decision": "excluded", "reason": "different_or_missing_lamp_identity",
                               "lamp": row["lamp_kind"]})
            continue
        if kind == "master_sci":
            if row["exposure_time"] is None:
                exclusions.append({"raw_id": row["raw_id"], "exposure_id": row["exposure_id"],
                                   "decision": "excluded", "reason": "missing_exposure_time"})
                continue
            if row["exposure_time"] <= threshold:
                exclusions.append({"raw_id": row["raw_id"], "exposure_id": row["exposure_id"],
                                   "decision": "excluded", "reason": "exposure_time_not_strictly_above_threshold",
                                   "exposure_time_seconds": row["exposure_time"], "threshold_seconds": threshold})
                continue
        rows.append(row)
    rows.sort(key=lambda row: (row["timestamp"], int(row["raw_id"])))

    if policy == "isolated":
        buckets = _split_isolated(rows, float(options.get("maximum_span_hours", 3.0)))
    elif policy == "rolling_24h":
        buckets = _split_isolated(rows, 24.0)
    else:
        keyed: dict[Any, list[dict[str, Any]]] = {}
        for row in rows:
            key = None
            if policy in {"observing_block", "dark_time"} and options.get("intervals"):
                for interval in options["intervals"]:
                    start = datetime.fromisoformat(str(interval["start"]))
                    end = datetime.fromisoformat(str(interval["end"]))
                    if start <= row["timestamp"] < end:
                        key = str(interval.get("name") or f"{start.isoformat()}..{end.isoformat()}")
                        row["observing_block"] = key
                        row["applicability_start"] = start
                        row["applicability_end"] = end
                        break
                if key is None:
                    exclusions.append({
                        "raw_id": row["raw_id"], "exposure_id": row["exposure_id"],
                        "decision": "excluded", "reason": "outside_configured_observing_intervals",
                    })
                    continue
            key = key if key is not None else _calendar_key(row["timestamp"], policy, row, options)
            keyed.setdefault(key, []).append(row)
        buckets = list(keyed.values())

    groups: list[CalibrationGroup] = []
    minimum = int(options.get("minimum_exposures", 1))
    for bucket in buckets:
        if kind == "master_sci":
            total = sum(float(row["exposure_time"]) for row in bucket)
            min_total = float(options.get("minimum_total_exposure_seconds", 1800.0))
            count_ok = len(bucket) >= minimum
            total_ok = total >= min_total
            illumination = options.get("measured_illumination")
            min_illumination = options.get("minimum_robust_illumination")
            measurement_pending = min_illumination is not None and illumination is None
            illumination_ok = measurement_pending or min_illumination is None or (
                float(illumination) >= float(min_illumination)
            )
            sufficiency = {
                "sufficient": count_ok and total_ok and illumination_ok,
                "minimum_exposures": minimum, "measured_exposures": len(bucket),
                "minimum_total_exposure_seconds": min_total,
                "measured_total_exposure_seconds": total,
                "minimum_robust_illumination": min_illumination,
                "measured_robust_illumination": illumination,
                "illumination_measurement_pending": measurement_pending,
                "criteria_provisional": True,
            }
            group = _make_group(
                kind=kind, zipcode=scope.zipcode.key(), policy=policy,
                options=options, rows=bucket, sufficient=bool(sufficiency["sufficient"]),
                sufficiency=sufficiency,
            )
            if group.sufficient:
                groups.append(group)
            else:
                exclusions.append({
                    "group_id": group.group_id, "exposure_ids": list(group.exposure_ids),
                    "decision": "unresolved", "reason": "master_sci_insufficient",
                    "sufficiency": sufficiency,
                })
            continue
        if len(bucket) < minimum:
            exclusions.append({
                "exposure_ids": [row["exposure_id"] for row in bucket],
                "decision": "unresolved", "reason": "minimum_exposures_not_met",
                "minimum_exposures": minimum, "measured_exposures": len(bucket),
            })
            continue
        groups.append(_make_group(
            kind=kind, zipcode=scope.zipcode.key(), policy=policy,
            options=options, rows=bucket,
        ))

    # The same exact inputs/configuration are one computation regardless of
    # applicability boundaries.
    unique: dict[str, CalibrationGroup] = {}
    for group in groups:
        if group.computation_id in unique:
            exclusions.append({
                "group_id": group.group_id, "decision": "deduplicated",
                "reason": "duplicate_effective_raw_inputs",
                "collapsed_into": unique[group.computation_id].group_id,
            })
        else:
            unique[group.computation_id] = group
    return GroupingResult(tuple(unique.values()), tuple(exclusions))


def pair_lamp_groups(
    hg_groups: Iterable[CalibrationGroup], cd_groups: Iterable[CalibrationGroup],
    *, maximum_separation_hours: float = 3.0,
) -> tuple[list[tuple[CalibrationGroup, CalibrationGroup, float]], list[dict[str, Any]]]:
    """Pair lamp groups one-to-one by nearest center with stable tie-breaking."""

    hg = sorted(hg_groups, key=lambda group: (group.timestamps[0], group.group_id))
    available = {group.group_id: group for group in cd_groups}
    limit = float(maximum_separation_hours) * 3600.0
    pairs = []
    unresolved = []
    for left in hg:
        left_center = left.timestamps[0] + (left.timestamps[-1] - left.timestamps[0]) / 2
        candidates = []
        for right in available.values():
            right_center = right.timestamps[0] + (right.timestamps[-1] - right.timestamps[0]) / 2
            separation = abs((right_center - left_center).total_seconds())
            if separation <= limit:
                candidates.append((separation, right_center, right.group_id, right))
        if not candidates:
            unresolved.append({"group_id": left.group_id, "lamp": "hg", "decision": "unresolved",
                               "reason": "no_cd_group_within_pairing_tolerance"})
            continue
        separation, _, _, right = min(candidates, key=lambda item: item[:3])
        pairs.append((left, right, separation))
        del available[right.group_id]
    unresolved.extend(
        {"group_id": group.group_id, "lamp": "cd", "decision": "unresolved",
         "reason": "no_hg_group_within_pairing_tolerance"}
        for group in available.values()
    )
    return pairs, unresolved


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
            times: List[datetime] = []
            for (_rid, rf) in rows:
                t = _parse_exposure_id(getattr(rf, "exposure_id", None))
                if t is not None:
                    times.append(t)
            if times:
                return [TemporalWindow(start=min(times), end=max(times))]
        return []
    # Derive concrete window bounds from exposure_id timestamps
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
    items = []
    for (_rid, rf) in rows:
        t = _parse_exposure_id(getattr(rf, "exposure_id", None))
        if t is not None:
            items.append((t, rf))
    items.sort(key=lambda x: x[0])
    if not items:
        return []
    # Roll windows
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
