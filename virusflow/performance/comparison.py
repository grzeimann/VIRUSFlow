from __future__ import annotations

"""Comparison helpers for saved timing reports and controlled Product registries."""

from pathlib import Path
from typing import Any
import json

import numpy as np


def _ratio(before: float, after: float) -> dict[str, float | None]:
    return {
        "before": float(before),
        "after": float(after),
        "change_fraction": ((float(after) / float(before)) - 1.0) if before else None,
    }


def compare_performance_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    kinds = sorted(set(before.get("task_kind_summary", {})) | set(after.get("task_kind_summary", {})))
    phases = sorted(set(before.get("phase_totals", {})) | set(after.get("phase_totals", {})))
    return {
        "schema": "virusflow.performance-comparison.v1",
        "wall_seconds": _ratio(before.get("wall_seconds", 0), after.get("wall_seconds", 0)),
        "task_cpu_seconds": _ratio(
            before.get("task_cpu_seconds", 0), after.get("task_cpu_seconds", 0)
        ),
        "worker_utilization": {
            "before": before.get("worker_utilization"),
            "after": after.get("worker_utilization"),
        },
        "critical_path": {
            "before": before.get("critical_path"), "after": after.get("critical_path"),
        },
        "raw_access": {"before": before.get("raw_io"), "after": after.get("raw_io")},
        "database": {"before": before.get("database"), "after": after.get("database")},
        "artifacts": {"before": before.get("artifacts"), "after": after.get("artifacts")},
        "phase_seconds": {
            name: _ratio(
                (before.get("phase_totals", {}) or {}).get(name, 0),
                (after.get("phase_totals", {}) or {}).get(name, 0),
            )
            for name in phases
        },
        "task_kinds": {
            kind: _ratio(
                (before.get("task_kind_summary", {}).get(kind) or {}).get("total_seconds", 0),
                (after.get("task_kind_summary", {}).get(kind) or {}).get("total_seconds", 0),
            )
            for kind in kinds
        },
    }


def _compare_values(before: Any, after: Any) -> dict[str, Any]:
    left, right = np.asarray(before), np.asarray(after)
    shape_equal = left.shape == right.shape
    dtype_equal = left.dtype == right.dtype
    try:
        equal = bool(shape_equal and np.array_equal(left, right, equal_nan=True))
    except TypeError:
        equal = bool(shape_equal and np.array_equal(left, right))
    maximum = None
    if shape_equal and np.issubdtype(left.dtype, np.number) and np.issubdtype(right.dtype, np.number):
        difference = np.abs(left.astype(np.float64) - right.astype(np.float64))
        finite = difference[np.isfinite(difference)]
        maximum = float(finite.max()) if finite.size else 0.0
    return {
        "equal": equal, "shape_equal": shape_equal, "dtype_equal": dtype_equal,
        "before_shape": list(left.shape), "after_shape": list(right.shape),
        "before_dtype": str(left.dtype), "after_dtype": str(right.dtype),
        "maximum_absolute_difference": maximum,
    }


def compare_artifact_registries(before_db: str, after_db: str) -> dict[str, Any]:
    """Compare active Products by physical identity, checksum, and loaded component values."""

    from ..artifacts import ArtifactService

    before_service, after_service = ArtifactService(before_db), ArtifactService(after_db)

    def keyed(service: ArtifactService) -> dict[tuple[str, ...], dict[str, Any]]:
        output = {}
        for row in service.adapter.list_all():
            if str(row.get("state") or "active") != "active":
                continue
            key = tuple(str(row.get(name) or "") for name in (
                "canonical_kind", "amp_key", "exposure_id", "observation_id", "dither_set_id",
            ))
            output[key] = row
        return output

    before_rows, after_rows = keyed(before_service), keyed(after_service)
    missing_before = sorted("|".join(key) for key in after_rows.keys() - before_rows.keys())
    missing_after = sorted("|".join(key) for key in before_rows.keys() - after_rows.keys())
    artifacts = []
    for key in sorted(before_rows.keys() & after_rows.keys()):
        left, right = before_rows[key], after_rows[key]
        left_components = {
            item["name"]: item for item in before_service.adapter.list_components(int(left["id"]))
        }
        right_components = {
            item["name"]: item for item in after_service.adapter.list_components(int(right["id"]))
        }
        component_results = {}
        for name in sorted(left_components.keys() & right_components.keys()):
            left_payload = before_service.load_component(left, name, verify_checksum=True)
            right_payload = after_service.load_component(right, name, verify_checksum=True)
            values = _compare_values(left_payload["data"], right_payload["data"])
            values["checksum_equal"] = (
                left_components[name].get("checksum") == right_components[name].get("checksum")
            )
            component_results[name] = values
        missing_components_before = sorted(right_components.keys() - left_components.keys())
        missing_components_after = sorted(left_components.keys() - right_components.keys())
        passed = (
            not missing_components_before and not missing_components_after
            and all(value["equal"] and value["checksum_equal"] for value in component_results.values())
        )
        artifacts.append({
            "identity": "|".join(key),
            "revision_equal": left.get("revision") == right.get("revision"),
            "checksum_equal": left.get("checksum") == right.get("checksum"),
            "payload_bytes_equal": int(left.get("payload_bytes") or 0) == int(right.get("payload_bytes") or 0),
            "missing_components_before": missing_components_before,
            "missing_components_after": missing_components_after,
            "components": component_results,
            "passed": passed,
        })
    passed = (
        not missing_before and not missing_after
        and all(
            item["passed"] and item["revision_equal"] and item["checksum_equal"]
            for item in artifacts
        )
    )
    return {
        "schema": "virusflow.performance-scientific-equivalence.v1",
        "passed": passed,
        "before_database": str(Path(before_db).resolve()),
        "after_database": str(Path(after_db).resolve()),
        "artifact_count": len(artifacts),
        "component_count": sum(len(item["components"]) for item in artifacts),
        "missing_before": missing_before, "missing_after": missing_after,
        "equal_revisions": sum(item["revision_equal"] for item in artifacts),
        "equal_checksums": sum(item["checksum_equal"] for item in artifacts),
        "equal_components": sum(
            component["equal"] and component["checksum_equal"]
            for item in artifacts for component in item["components"].values()
        ),
        "artifacts": artifacts,
    }


def load_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


__all__ = [
    "compare_artifact_registries", "compare_performance_reports", "load_report",
]
