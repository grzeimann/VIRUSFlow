from __future__ import annotations

"""
Output helpers for analytics studies.

Responsibilities:
- Provide simple figure saving utilities.
- Build and register analytics artifacts with provenance linking to source artifacts.
- Keep this module free from algorithm/task/planning imports; use ArtifactService only.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Iterable, List

import matplotlib
matplotlib.use("Agg")  # ensure non-interactive backend
import matplotlib.pyplot as plt  # noqa: E402

from ..artifacts.models import Artifact, StorageRef, Scope, Provenance
from ..artifacts.service import ArtifactService


@dataclass(frozen=True)
class SavedFigure:
    path: Path
    fmt: str


def save_fig(fig: plt.Figure, out_dir: Path, base: str, formats: Iterable[str] = ("png",)) -> List[SavedFigure]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: List[SavedFigure] = []
    for ext in formats:
        p = out_dir / f"{base}.{ext}"
        fig.savefig(str(p), dpi=150, bbox_inches="tight")
        saved.append(SavedFigure(path=p, fmt=ext))
    return saved


def register_static_file(
    *,
    svc: ArtifactService,
    kind: str,
    file_path: Path,
    parent_ids: Iterable[int],
    zipcode,
    params: Optional[Dict] = None,
    role: str = "analysis",
    payload_type: str = "image",
    storage_format: Optional[str] = None,
    study_name: Optional[str] = None,
    study_version: str = "1.0",
) -> int:
    """Register a static file (e.g., PNG) as an analytics artifact.

    Conventions:
    - kind: descriptive scientific name (e.g., 'trace_preview')
    - provenance.params includes searchable fields: study_name, study_version,
      output_kind, source_kind, source_artifact_id, created_at (ISO), and any
      additional params provided by the study.

    Note: The current RegistryAdapter persists provenance.params; Artifact.metadata
    is retained in-memory for future DB support.
    """
    from datetime import datetime as _dt
    storage_format = storage_format or file_path.suffix.lstrip(".")
    # Normalize and enrich params according to conventions
    base_params: Dict[str, object] = {
        "study_name": (study_name or "unknown"),
        "study_version": study_version,
        "output_kind": str(kind),
        "created_at": _dt.utcnow().isoformat(timespec="seconds"),
    }
    if isinstance(params, dict):
        base_params.update(params)
    art = Artifact(
        id=None,
        kind=str(kind),
        role=role,
        payload_type=payload_type,
        storage_format=storage_format,
        storage=StorageRef(uri=str(file_path), storage_format=storage_format, backend="fs"),
        scope=Scope(zipcode=zipcode),
        metadata={
            "payload_type": payload_type,
            "storage_format": storage_format,
            "study_name": base_params.get("study_name"),
            "study_version": base_params.get("study_version"),
        },
        provenance=Provenance(
            algorithm="analytics",
            params=base_params,
            parents=[int(p) for p in (parent_ids or [])],
        ),
    )
    return svc.register(art)
