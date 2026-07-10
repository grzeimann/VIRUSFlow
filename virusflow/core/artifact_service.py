from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from .identity import ZipCode
from .artifacts import (
    Artifact,
    CalibrationProduct,
    load_master_bias,
    load_master_dark,
)
from .provenance import build_provenance
from ..registry import database as db


@dataclass(frozen=True)
class Scope:
    """Scope for initial implementation (amplifier-level, with optional time context).

    Future versions can extend this to exposure/observation/instrument scopes.
    """

    zipcode: ZipCode
    exposure_id: Optional[str] = None
    start_date: Optional[str] = None  # YYYYMMDD
    end_date: Optional[str] = None    # YYYYMMDD


class ArtifactService:
    """Initial artifact service facade.

    Provides create/get/find/load APIs using the SQLite-backed registry and
    on-disk FITS artifacts via core.artifacts helpers.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    # --- QA ---
    def save_qa(self, artifact_id: int, *, status: str, metrics: Optional[Dict[str, Any]] = None) -> None:
        """Persist QA status/metrics for an artifact."""
        db.save_qa_results(artifact_id, status=status, metrics=metrics, db_path=self.db_path)

    # --- Creation ---
    def create(
        self,
        *,
        kind: str,
        name: str,
        scope: Scope,
        output_path: str,
        parents: list[int] | None = None,
        validity_start: Optional[datetime] = None,
        validity_end: Optional[datetime] = None,
        algorithm: str = "unknown",
        params: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Register an artifact previously materialized on disk.

        Reduction algorithms should write outputs via core.artifacts helpers,
        then call service.create(...) to persist identity+provenance.
        """
        art = CalibrationProduct(
            id=None,
            kind=kind,
            name=name,
            path=output_path,
            zipcode=scope.zipcode,
            validity_start=validity_start,
            validity_end=validity_end,
        )
        prov = build_provenance(algorithm=algorithm, params=dict(params or {}), parents=[str(p) for p in (parents or [])])
        return db.save_artifact(art, prov, db_path=self.db_path)

    # --- Retrieval ---
    def get(self, artifact_id: int) -> Optional[Dict[str, Any]]:
        return db.get_artifact(artifact_id, db_path=self.db_path)

    def find_best(
        self,
        *,
        kind: str,
        scope: Scope,
        at_time: Optional[datetime] = None,
        policy: str = "latest_valid",
    ) -> Optional[Dict[str, Any]]:
        """Find best-matching artifact by kind/scope/time.

        Policies:
        - latest_valid (default): respect validity window if at_time provided; newest otherwise
        - latest: ignore validity (equivalent to at_time=None)
        """
        at = None if policy == "latest" else at_time
        rows = db.find_artifacts(
            kind=kind,
            zipcode=scope.zipcode,
            at_time=at,
            db_path=self.db_path,
            limit=1,
        )
        return rows[0] if rows else None

    # --- Loading ---
    def load_summary(self, artifact_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Load a lightweight JSON sidecar next to the artifact path, if present.

        Returns a dict of summary metrics or None if missing/unreadable.
        """
        path = artifact_row.get("path")
        if not path:
            return None
        try:
            from pathlib import Path
            import json as _json
            p = Path(path)
            side = p.with_suffix(p.suffix + ".json")
            if not side.exists():
                return None
            return _json.loads(side.read_text())
        except Exception:
            return None

    def load(self, artifact_row: Dict[str, Any]) -> Dict[str, Any]:
        """Load payload for a known artifact row (from get/find)."""
        kind = str(artifact_row.get("kind", ""))
        path = artifact_row.get("path")
        if not path:
            raise FileNotFoundError("Artifact has no path to load")
        if kind == "master_bias":
            master, hdr = load_master_bias(path)
            return {"master": master, "header": hdr}
        if kind == "master_dark":
            master, mask, hdr = load_master_dark(path)
            return {"master": master, "dark_mask": mask, "header": hdr}
        # Fallback: return path only
        return {"path": path}
