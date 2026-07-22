from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Iterable, List, Optional
from datetime import datetime

from ..registry import database as db
from .models import Artifact, Provenance, StorageRef, Scope, DiagnosticRecord
from ..ontology.artifact_kinds import canonical_kind


class RegistryAdapter:
    """Thin adapter to the existing registry database API.

    Keeps the new artifacts.service decoupled from current DB schema.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    # --- Persistence ---
    def register(self, art: Artifact, *, components: Iterable[Dict] = ()) -> int:
        """Transactionally register artifact and provenance.
        Uses the existing db.save_artifact which handles both.
        """
        # Map to a simple dict structure expected by db.save_artifact (decoupled from legacy dataclasses)
        zipcode = art.scope.zipcode if art.scope else None
        # Prefer first-class validity; legacy provenance parameters remain readable.
        vstart = getattr(getattr(art, "validity", None), "start", None)
        vend = getattr(getattr(art, "validity", None), "end", None)
        try:
            p = (art.provenance.params if art.provenance else {}) or {}
            vstart = vstart or p.get("validity_start")
            vend = vend or p.get("validity_end")
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
        artifact_id = db.save_artifact(legacy_like, prov_row, db_path=self.db_path)
        scope = art.scope
        relation_rows = []
        seen = set()
        for relation in list(getattr(art, "relations", []) or []):
            key = (int(relation.parent_id), str(relation.relation))
            if key not in seen:
                relation_rows.append({"parent_id": key[0], "relation": key[1]})
                seen.add(key)
        for parent_id in (art.provenance.parents if art.provenance else []):
            key = (int(parent_id), "derived_from")
            if key not in seen:
                relation_rows.append({"parent_id": key[0], "relation": key[1]})
                seen.add(key)
        db.save_artifact_details(
            artifact_id,
            record={
                "canonical_kind": canonical_kind(art.kind),
                "role": art.role,
                "payload_type": art.payload_type,
                "storage_format": art.storage_format,
                "physical_scope": getattr(getattr(scope, "physical_scope", None), "value", getattr(scope, "physical_scope", None)),
                "exposure_id": getattr(scope, "exposure_id", None),
                "observation_id": getattr(scope, "observation_id", None),
                "dither_set_id": getattr(scope, "dither_set_id", None),
                "revision": getattr(art, "revision", None),
                "checksum": getattr(art, "checksum", None),
                "units": dict(getattr(art, "units", {}) or {}),
                "coordinates": dict(getattr(art, "coordinates", {}) or {}),
                "configuration_refs": [asdict(x) for x in (getattr(art, "configuration_refs", []) or [])],
                "metadata": dict(art.metadata or {}),
                "validity_policy": getattr(getattr(art, "validity", None), "policy", None),
                "created_at": getattr(getattr(art, "provenance", None), "created_at", datetime.utcnow()).isoformat(),
            },
            components=list(components or []),
            relations=relation_rows,
            db_path=self.db_path,
        )
        return artifact_id

    # --- Retrieval ---
    def get_row(self, artifact_id: int) -> Optional[dict]:
        row = db.get_artifact(int(artifact_id), db_path=self.db_path)
        if row:
            details = db.get_artifact_details(int(artifact_id), db_path=self.db_path)
            if details:
                row.update(details)
        return row

    def list_components(self, artifact_id: int) -> List[dict]:
        return db.list_artifact_components(int(artifact_id), db_path=self.db_path)

    def list_relations(self, artifact_id: int) -> List[dict]:
        return db.list_artifact_relations(int(artifact_id), db_path=self.db_path)

    def find(self, *, kind: Optional[str], zipcode, at_time: Optional[datetime], limit: Optional[int] = None) -> List[dict]:
        return db.find_artifacts(kind=kind, zipcode=zipcode, at_time=at_time, db_path=self.db_path, limit=limit)

    # --- Diagnostics ---
    def get_diagnostics(self, artifact_id: int) -> Optional[dict]:
        return db.get_qa_results(int(artifact_id), db_path=self.db_path)

    def set_diagnostics(self, artifact_id: int, status: str, metrics: Optional[dict]) -> None:
        db.save_qa_results(int(artifact_id), status=status, metrics=metrics, db_path=self.db_path)

    def set_qa_bundle(
        self,
        artifact_id: int,
        *,
        facts: Dict[str, Dict],
        status: str,
        usability: str,
        policy_version: str,
        rules: Iterable[Dict] = (),
    ) -> None:
        db.save_qa_bundle(
            int(artifact_id),
            facts=facts,
            status=status,
            usability=usability,
            policy_version=policy_version,
            rules=rules,
            db_path=self.db_path,
        )
