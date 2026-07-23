from __future__ import annotations

"""Safe inventory and cleanup operations for scratch, cache, and legacy payloads."""

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
from typing import Iterable

from ..artifacts.migration import SUPERSEDED_STAGE_8_10_KINDS, find_legacy_dense_artifacts
from ..artifacts.service import ArtifactService
from ..ontology.lifecycle import ArtifactLifecycle


@dataclass(frozen=True)
class CleanupReport:
    category: str
    dry_run: bool
    candidates: int
    candidate_bytes: int
    affected: int
    removed_bytes: int
    artifact_ids: tuple[int, ...] = ()
    paths: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


def _tree_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def cleanup_scratch(workdir: str | Path, *, execute: bool = False) -> CleanupReport:
    root = Path(workdir).resolve() / ".scratch"
    if not root.exists():
        return CleanupReport("scratch", not execute, 0, 0, 0, 0)
    files = tuple(str(path) for path in root.rglob("*") if path.is_file())
    size = _tree_bytes(root)
    if execute:
        shutil.rmtree(root)
    return CleanupReport(
        "scratch", not execute, len(files), size,
        len(files) if execute else 0, size if execute else 0, paths=files,
    )


def cleanup_cache(db_path: str, *, execute: bool = False) -> CleanupReport:
    service = ArtifactService(db_path)
    rows = [
        row for row in service.adapter.list_all()
        if str(row.get("state") or "active") == "active"
        and str(row.get("lifecycle") or "canonical") == ArtifactLifecycle.CACHE.value
    ]
    ids = tuple(int(row["id"]) for row in rows)
    size = sum(int(row.get("payload_bytes") or 0) for row in rows)
    removed = 0
    if execute:
        for artifact_id in ids:
            removed += service.evict_payload(artifact_id, require_cache=True)
    return CleanupReport(
        "cache", not execute, len(ids), size,
        len(ids) if execute else 0, removed, artifact_ids=ids,
    )


def cleanup_legacy(
    db_path: str,
    *,
    deactivate: bool = False,
    delete_payloads: bool = False,
    validation_succeeded: bool = False,
) -> CleanupReport:
    """Inventory or retire superseded dense records.

    Payload deletion is deliberately gated by both explicit deletion and an
    explicit statement that representative validation succeeded. Registry
    deactivation remains separate and recoverable while payloads are retained.
    """

    if delete_payloads and not deactivate:
        raise ValueError("legacy payload deletion requires --deactivate")
    if delete_payloads and not validation_succeeded:
        raise ValueError("legacy payload deletion requires --validation-succeeded")
    service = ArtifactService(db_path)
    rows = find_legacy_dense_artifacts(service)
    ids = tuple(int(row["id"]) for row in rows)
    size = sum(int(row.get("payload_bytes") or 0) for row in rows)
    removed = 0
    if deactivate:
        for artifact_id in ids:
            service.adapter.set_state(artifact_id, "obsolete")
            if delete_payloads:
                removed += service.evict_payload(artifact_id, require_cache=False)
                service.adapter.set_state(artifact_id, "obsolete")
    return CleanupReport(
        "legacy", not deactivate, len(ids), size,
        len(ids) if deactivate else 0, removed, artifact_ids=ids,
    )


__all__ = ["CleanupReport", "cleanup_cache", "cleanup_legacy", "cleanup_scratch"]
