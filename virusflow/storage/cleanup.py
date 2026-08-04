"""Safe inventory and cleanup operations for scratch, cache, and legacy payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
from ..artifacts.migration import find_legacy_dense_artifacts
from ..artifacts.retention import retention_rule
from ..artifacts.service import ArtifactLoadError, ArtifactService
from ..ontology.artifact_kinds import canonical_kind
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
    refusals: tuple[str, ...] = ()

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
    candidates = []
    for row in service.adapter.list_all():
        if str(row.get("state") or "active") != "active":
            continue
        kind = canonical_kind(row.get("canonical_kind") or row.get("kind") or "")
        rule = retention_rule(kind)
        lifecycle = str(row.get("lifecycle") or ArtifactLifecycle.CANONICAL.value)
        if rule is None and lifecycle != ArtifactLifecycle.CACHE.value:
            continue
        requested = set(rule.evictable_components) if rule is not None else None
        components = service.adapter.list_components(int(row["id"]))
        resident = [
            component for component in components
            if str(component.get("payload_state") or "present") == "present"
            and (requested is None or str(component.get("name")) in requested)
        ]
        if resident:
            candidates.append((row, resident))
    ids = tuple(int(row["id"]) for row, _ in candidates)
    size = sum(
        int(component.get("payload_bytes") or 0)
        for _, components in candidates
        for component in components
    )
    removed = 0
    affected = 0
    refusals = []
    if execute:
        for row, _ in candidates:
            artifact_id = int(row["id"])
            try:
                artifact_removed = service.evict_payload(artifact_id)
            except (ValueError, ArtifactLoadError, OSError) as exc:
                refusals.append(
                    f"artifact_id={artifact_id} kind={row.get('kind')}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if artifact_removed:
                affected += 1
                removed += int(artifact_removed)
    return CleanupReport(
        "cache", not execute, len(ids), size,
        affected, removed, artifact_ids=ids, refusals=tuple(refusals),
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
                removed += service._purge_payload(artifact_id)
                service.adapter.set_state(artifact_id, "obsolete")
    return CleanupReport(
        "legacy", not deactivate, len(ids), size,
        len(ids) if deactivate else 0, removed, artifact_ids=ids,
    )


__all__ = ["CleanupReport", "cleanup_cache", "cleanup_legacy", "cleanup_scratch"]
