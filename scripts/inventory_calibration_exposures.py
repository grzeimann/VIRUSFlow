#!/usr/bin/env python3
"""Read-only inventory of calibration exposures for selected VIRUS ZipCodes.

The inventory deliberately does not name or filter a particular calibration
frame type.  It uses the stored exposure classification, reports the exact
stored ``raw_files.frame_type`` values, and groups rows by physical
``exposure_id``.  It performs SELECTs against the raw catalog only; it never
opens a FITS payload and never writes either database.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from virusflow.core.identity import parse_zipcode_key  # noqa: E402
from virusflow.registry import database as db  # noqa: E402


FIELDS = (
    "observing_night",
    "physical_exposure_id",
    "stored_frame_types",
    "timestamp",
    "exposure_time_seconds",
    "exposure_time_source",
    "selected_zipcodes_present",
    "selected_zipcodes_missing",
    "selected_detector_count",
    "selected_detector_total",
    "complete_across_selected_detectors",
    "lamp",
    "observing_block",
    "raw_file_row_count",
)


def _date8(value: object) -> str | None:
    text = str(value or "")
    token = text.split("T", 1)[0].replace("-", "")
    return token[:8] if len(token) >= 8 and token[:8].isdigit() else None


def _night(row: dict) -> str | None:
    value = db.observing_night_from_provenance(
        str(row.get("path") or ""),
        tar_member=row.get("tar_member"),
        outer_tar_member=row.get("outer_tar_member"),
    )
    return value or _date8(row.get("when_utc"))


def _value_set(rows: list[dict], key: str) -> list[str]:
    return sorted({str(row[key]) for row in rows if row.get(key) not in (None, "")})


def inventory(*, raw_db: str, zipcodes: list[str], first_night: str, last_night: str) -> list[dict]:
    selected = [parse_zipcode_key(value).key() for value in zipcodes]
    selected_set = set(selected)
    placeholders = ",".join("?" for _ in selected)
    sql = (
        "SELECT rf.exposure_id, rf.frame_type, rf.amp_key, rf.path, "
        "rf.tar_member, rf.outer_tar_member, rf.observation_time, "
        "e.when_utc, e.frame_type AS exposure_frame_type, "
        "d.exptime, d.pexptime, d.observing_mode, d.lamp, d.observing_block "
        "FROM raw_files rf "
        "JOIN exposures e ON e.id=rf.exposure_id "
        "LEFT JOIN exposure_details d ON d.exposure_id=rf.exposure_id "
        f"WHERE rf.amp_key IN ({placeholders}) "
        "AND d.observing_mode='calibration' "
        "ORDER BY rf.exposure_id, rf.id"
    )
    with db.connect(raw_db) as conn:
        source_rows = [dict(row) for row in conn.execute(sql, tuple(selected)).fetchall()]

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in source_rows:
        night = _night(row)
        if night is None or not (first_night <= night <= last_night):
            continue
        row["observing_night"] = night
        grouped[str(row["exposure_id"])].append(row)

    result = []
    for exposure_id, rows in sorted(grouped.items(), key=lambda item: (item[1][0]["observing_night"], item[0])):
        present = _value_set(rows, "amp_key")
        missing = sorted(selected_set - set(present))
        exposure_seconds = next(
            (row.get("exptime") for row in rows if row.get("exptime") is not None),
            next((row.get("pexptime") for row in rows if row.get("pexptime") is not None), None),
        )
        source = (
            "EXPTIME" if any(row.get("exptime") is not None for row in rows)
            else "PEXPTIME" if any(row.get("pexptime") is not None for row in rows)
            else None
        )
        timestamps = _value_set(rows, "observation_time") or _value_set(rows, "when_utc")
        result.append({
            "observing_night": rows[0]["observing_night"],
            "physical_exposure_id": exposure_id,
            "stored_frame_types": "|".join(_value_set(rows, "frame_type")),
            "timestamp": timestamps[0] if timestamps else "",
            "exposure_time_seconds": exposure_seconds,
            "exposure_time_source": source or "",
            "selected_zipcodes_present": "|".join(present),
            "selected_zipcodes_missing": "|".join(missing),
            "selected_detector_count": len(present),
            "selected_detector_total": len(selected),
            "complete_across_selected_detectors": "yes" if not missing else "no",
            "lamp": "|".join(_value_set(rows, "lamp")),
            "observing_block": "|".join(_value_set(rows, "observing_block")),
            "raw_file_row_count": len(rows),
        })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-db", required=True, help="Raw registry SQLite database")
    parser.add_argument("--first-night", required=True, help="Inclusive YYYYMMDD observing night")
    parser.add_argument("--last-night", required=True, help="Inclusive YYYYMMDD observing night")
    parser.add_argument("--zipcode", action="append", required=True, help="Selected ZipCode; repeat once per detector")
    parser.add_argument("--csv", action="store_true", help="Emit CSV instead of a readable table")
    args = parser.parse_args(argv)
    first, last = _date8(args.first_night), _date8(args.last_night)
    if first is None or last is None or first > last:
        parser.error("--first-night and --last-night must be ordered YYYYMMDD values")
    rows = inventory(
        raw_db=args.raw_db, zipcodes=args.zipcode,
        first_night=first, last_night=last,
    )
    if args.csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return 0
    if not rows:
        print("No calibration exposures found")
        return 0
    widths = {field: max(len(field), *(len(str(row.get(field, ""))) for row in rows)) for field in FIELDS}
    print("  ".join(field.ljust(widths[field]) for field in FIELDS))
    print("  ".join("-" * widths[field] for field in FIELDS))
    for row in rows:
        print("  ".join(str(row.get(field, "")).ljust(widths[field]) for field in FIELDS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
