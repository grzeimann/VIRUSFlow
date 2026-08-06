from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from ...artifacts.models import Scope, Validity
from ...artifacts.requests import ArtifactRequest, LogicalComponent
from ...artifacts.service import ArtifactService
from ...core.identity import ZipCode
from ...persistence.policy import DefaultPersistencePolicy
from ...publication.context import PublicationContext


@dataclass(frozen=True)
class BiasStabilityParams:
    out_dir: Path
    zipcode: ZipCode
    limit: Optional[int] = None


class BiasStabilityStudy:
    """Publish a queryable Bias time series using only ArtifactService access."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service

    def run(self, params: BiasStabilityParams):
        rows = self.service.adapter.find(
            kind="master_bias", zipcode=params.zipcode, at_time=None, limit=params.limit
        )
        rows = sorted(rows, key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)))
        if not rows:
            raise ValueError(f"No master_bias Products for {params.zipcode.key()}")

        ids = []
        levels = []
        scatters = []
        starts = []
        ends = []
        for row in rows:
            artifact_id = int(row["id"])
            master = np.asarray(self.service.load_component(row, "master")["data"], dtype=float)
            scatter = np.asarray(
                self.service.load_component(row, "per_pixel_bias_scatter")["data"], dtype=float
            )
            ids.append(artifact_id)
            levels.append(float(np.nanmedian(master)))
            scatters.append(float(np.nanmedian(scatter)))
            if row.get("validity_start"):
                starts.append(datetime.fromisoformat(str(row["validity_start"])))
            if row.get("validity_end"):
                ends.append(datetime.fromisoformat(str(row["validity_end"])))

        request = ArtifactRequest(
            kind="bias_stability",
            components={
                "source_artifact_id": LogicalComponent("source_artifact_id", "array1d", np.asarray(ids), "1", "none"),
                "median_bias_level": LogicalComponent("median_bias_level", "array1d", np.asarray(levels), "electron", "none"),
                "median_bias_scatter": LogicalComponent("median_bias_scatter", "array1d", np.asarray(scatters), "electron", "none"),
            },
            summaries={"n_products": len(ids)},
            scope=Scope(zipcode=params.zipcode),
            parents=ids,
            validity=Validity(min(starts) if starts else None, max(ends) if ends else None, "source_union"),
            labels=["analytic", "bias", "stability"],
        )
        context = PublicationContext(
            task_name="bias_stability",
            task_version="1",
            algorithm_name="virusflow.analytics.studies.bias.BiasStabilityStudy",
            algorithm_version="1",
            parameters={},
            parent_ids=[],
        )
        artifact = self.service.persist_request(
            request,
            context=context,
            policy=DefaultPersistencePolicy(),
            base_dir=str(params.out_dir),
        )
        return {"artifact_id": artifact.id, "n_products": len(ids), "source_ids": ids}
