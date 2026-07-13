from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional
from datetime import datetime

from ..registry import database as db
from .models import Artifact, Provenance, StorageRef, Scope, DiagnosticRecord


class RegistryAdapter:
    """Thin adapter to the existing registry database API.

    Keeps the new artifacts.service decoupled from current DB schema.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    # --- Persistence ---
    def register(self, art: Artifact) -> int:
        """Transactionally register artifact and provenance.
        Uses the existing db.save_artifact which handles both.
        """
        # Map to legacy Artifact-like structure expected by db.save_artifact
        from ..core.artifacts import CalibrationProduct
        zipcode = art.scope.zipcode if art.scope else None
        # Validity timing will remain stored via task-level policy (dates in names)
        legacy = CalibrationProduct(
            id=None,
            kind=art.kind,
            name=art.kind,
            path=art.storage.uri,
            zipcode=zipcode,
            validity_start=None,
            validity_end=None,
        )
        prov = {
            "algorithm": art.provenance.algorithm if art.provenance else "unknown",
            "params": (art.provenance.params if art.provenance else {}),
            "parents": [int(p) for p in (art.provenance.parents if art.provenance else [])],
        }
        from ..core.provenance import build_provenance
        prov_row = build_provenance(algorithm=prov["algorithm"], params=prov["params"], parents=[str(p) for p in prov["parents"]])
        return db.save_artifact(legacy, prov_row, db_path=self.db_path)

    # --- Retrieval ---
    def get_row(self, artifact_id: int) -> Optional[dict]:
        return db.get_artifact(int(artifact_id), db_path=self.db_path)

    def find(self, *, kind: Optional[str], zipcode, at_time: Optional[datetime], limit: Optional[int] = None) -> List[dict]:
        return db.find_artifacts(kind=kind, zipcode=zipcode, at_time=at_time, db_path=self.db_path, limit=limit)

    # --- Diagnostics ---
    def get_diagnostics(self, artifact_id: int) -> Optional[dict]:
        return db.get_qa_results(int(artifact_id), db_path=self.db_path)

    def set_diagnostics(self, artifact_id: int, status: str, metrics: Optional[dict]) -> None:
        db.save_qa_results(int(artifact_id), status=status, metrics=metrics, db_path=self.db_path)
