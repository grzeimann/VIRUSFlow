from __future__ import annotations

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
