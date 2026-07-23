from __future__ import annotations

from dataclasses import dataclass

from .service import ArtifactService


SUPERSEDED_STAGE_8_10_KINDS = (
    "reduced_science_image",
    "scatter_subtracted_image",
    "aperture_extracted_spectrum",
    "extracted_variance",
    "incident_sky_spectrum",
    "fiber_sky_prediction",
    "sky_subtracted_spectrum",
    "final_exposure_response",
)


def _is_legacy_dense_scatter(service: ArtifactService, row: dict) -> bool:
    if (row.get("canonical_kind") or row.get("kind")) != "ccd_scattered_light_model":
        return False
    names = {component["name"] for component in service.adapter.list_components(int(row["id"]))}
    compact = {
        "model_parameters", "detector_shape", "gap_sample_indices", "fit_sample_indices",
        "holdout_sample_indices", "residual_sample_indices", "residual_sample_values",
    }
    return "model" in names or not compact.issubset(names)


@dataclass(frozen=True)
class MigrationResult:
    invalidated: int
    removed_bytes: int
    delete_payloads: bool


def find_legacy_dense_artifacts(service: ArtifactService) -> list[dict]:
    """Return active superseded dense records without mutating the registry."""

    superseded = set(SUPERSEDED_STAGE_8_10_KINDS)
    return [
        row for row in service.adapter.list_all()
        if str(row.get("state") or "active") == "active"
        and (
            (row.get("canonical_kind") or row.get("kind")) in superseded
            or _is_legacy_dense_scatter(service, row)
        )
    ]


def migrate_stages_8_10_storage(
    db_path: str,
    *,
    delete_payloads: bool = False,
) -> MigrationResult:
    """Invalidate legacy dense records; deletion is a separate explicit choice.

    Upstream calibration and raw-data records are intentionally untouched.
    Callers should request deletion only after validating regenerated final
    observations, as required by the migration specification.
    """

    service = ArtifactService(db_path)
    result = service.invalidate_kinds(
        SUPERSEDED_STAGE_8_10_KINDS,
        delete_payloads=delete_payloads,
    )
    legacy_scatter_ids = [
        int(row["id"])
        for row in service.adapter.list_all(kind="ccd_scattered_light_model")
        if str(row.get("state") or "active") == "active"
        and _is_legacy_dense_scatter(service, row)
    ]
    removed = 0
    for artifact_id in legacy_scatter_ids:
        service.adapter.set_state(artifact_id, "obsolete")
        if delete_payloads:
            removed += service.evict_payload(artifact_id, require_cache=False)
            service.adapter.set_state(artifact_id, "obsolete")
    return MigrationResult(
        invalidated=int(result["invalidated"]) + len(legacy_scatter_ids),
        removed_bytes=int(result["removed_bytes"]) + removed,
        delete_payloads=bool(delete_payloads),
    )
