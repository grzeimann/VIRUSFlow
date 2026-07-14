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
        # Map to a simple dict structure expected by db.save_artifact (decoupled from legacy dataclasses)
        zipcode = art.scope.zipcode if art.scope else None
        # Extract optional validity_start/end from provenance params if provided
        vstart = None
        vend = None
        try:
            p = (art.provenance.params if art.provenance else {}) or {}
            vstart = p.get("validity_start")
            vend = p.get("validity_end")
        except Exception:
            p = {}
            vstart = None
            vend = None
        # Ensure JSON-serializable params: convert datetime values to ISO strings for provenance params
        def _jsonable(val):
            try:
                from datetime import datetime as _dt
                if isinstance(val, _dt):
                    return val.isoformat()
            except Exception:
                pass
            return val
        p = {k: (_jsonable(v)) for k, v in dict(p).items()}
        legacy_like = {
            "kind": art.kind,
            "name": art.kind,
            "path": art.storage.uri if art.storage else None,
            "zipcode": zipcode,
            "validity_start": vstart,
            "validity_end": vend,
        }
        prov = {
            "algorithm": art.provenance.algorithm if art.provenance else "unknown",
            "params": p,
            "parents": [int(p) for p in (art.provenance.parents if art.provenance else [])],
        }
        from ..artifacts.provenance import build_provenance
        prov_row = build_provenance(algorithm=prov["algorithm"], params=prov["params"], parents=[str(p) for p in prov["parents"]])
        return db.save_artifact(legacy_like, prov_row, db_path=self.db_path)

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
