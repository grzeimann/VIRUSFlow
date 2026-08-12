from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Iterable, List, Optional
from datetime import datetime

from ..registry import database as db
from .models import Artifact
from ..ontology.artifact_kinds import canonical_kind


class RegistryAdapter:
    """Thin adapter to the existing registry database API.

    Keeps the new artifacts.service decoupled from current DB schema.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    # --- Persistence ---
    def register(self, art: Artifact, *, components: Iterable[Dict] = (),
                 group_declarations: Iterable[Dict] = (), group_memberships: Iterable[Dict] = (),
                 group_inputs: Iterable[Dict] = ()) -> int:
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
            "raw_parents": [
                int(p) for p in (art.provenance.raw_parents if art.provenance else [])
            ],
            "raw_catalog": art.provenance.raw_catalog if art.provenance else None,
        }
        from ..artifacts.provenance import build_provenance
        prov_row = build_provenance(
            algorithm=prov["algorithm"],
            params=prov["params"],
            parents=[str(p) for p in prov["parents"]],
            raw_parents=[str(p) for p in prov["raw_parents"]],
            raw_catalog=prov["raw_catalog"],
        )
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
        raw_relation_rows = [
            {
                "raw_catalog": str(prov["raw_catalog"] or ""),
                "raw_id": int(raw_id),
                "relation": "derived_from",
            }
            for raw_id in prov["raw_parents"]
        ]
        try:
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
                "scientific_metadata": dict(art.scientific_metadata or {}),
                "validity_policy": getattr(getattr(art, "validity", None), "policy", None),
                "lifecycle": getattr(getattr(art, "lifecycle", None), "value", getattr(art, "lifecycle", "canonical")),
                "state": getattr(art, "state", "active"),
                "payload_bytes": int(getattr(art, "payload_bytes", 0) or 0),
                "created_at": getattr(getattr(art, "provenance", None), "created_at", datetime.utcnow()).isoformat(),
                },
                components=list(components or []),
                relations=relation_rows,
                raw_relations=raw_relation_rows,
                group_declarations=group_declarations,
                group_memberships=group_memberships,
                group_inputs=group_inputs,
                db_path=self.db_path,
            )
        except BaseException:
            # The legacy artifact/provenance shell is written by an older API
            # before canonical details. Roll it back if the canonical write
            # fails (notably when another worker won the revision race).
            with db.connect(self.db_path) as connection:
                connection.execute("DELETE FROM provenance WHERE artifact_id=?", (int(artifact_id),))
                connection.execute("DELETE FROM artifacts WHERE id=?", (int(artifact_id),))
            raise
        return artifact_id

    def declare_measurement_group(self, declaration: Dict) -> None:
        db.declare_measurement_group(declaration, db_path=self.db_path)

    def list_measurement_groups(self, member_kind: str) -> List[dict]:
        return db.list_measurement_groups(member_kind, db_path=self.db_path)

    def list_measurement_group_slots(self, group_ids: Iterable[str]) -> List[dict]:
        return db.list_measurement_group_slots(group_ids, db_path=self.db_path)

    def list_measurement_group_memberships(self, artifact_id: int) -> List[dict]:
        return db.list_measurement_group_memberships(artifact_id, db_path=self.db_path)

    def list_measurement_group_inputs(self, artifact_id: int) -> List[dict]:
        return db.list_artifact_measurement_group_inputs(artifact_id, db_path=self.db_path)

    def apply_measurement_group_relations(
        self, artifact_id: int, *, declarations: Iterable[Dict] = (),
        memberships: Iterable[Dict] = (), inputs: Iterable[Dict] = (),
    ) -> None:
        """Apply explicit normalized group relations to an existing Artifact."""
        db.save_artifact_group_relations(
            int(artifact_id), declarations=declarations, memberships=memberships,
            group_inputs=inputs, db_path=self.db_path,
        )

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

    def set_component_payload_states(
        self, artifact_id: int, updates: Iterable[Dict]
    ) -> None:
        db.set_artifact_component_payload_states(
            int(artifact_id), updates, db_path=self.db_path
        )

    def list_descendants(
        self, artifact_id: int, *, kinds: Iterable[str] = ()
    ) -> List[dict]:
        return db.list_artifact_descendants(
            int(artifact_id), db_path=self.db_path, kinds=kinds
        )

    def list_ancestors(
        self, artifact_id: int, *, kinds: Iterable[str] = ()
    ) -> List[dict]:
        return db.list_artifact_ancestors(
            int(artifact_id), db_path=self.db_path, kinds=kinds
        )

    def list_relations(self, artifact_id: int) -> List[dict]:
        return db.list_artifact_relations(int(artifact_id), db_path=self.db_path)

    def list_raw_relations(self, artifact_id: int) -> List[dict]:
        return db.list_raw_artifact_relations(
            int(artifact_id), db_path=self.db_path
        )

    def get_scientific_metadata(self, artifact_id: int) -> Optional[dict]:
        return db.get_artifact_scientific_metadata(
            int(artifact_id), db_path=self.db_path
        )

    def find_summaries(self, **filters) -> List[dict]:
        return db.find_artifact_summaries(db_path=self.db_path, **filters)

    def find_by_revision(self, revision: str) -> Optional[dict]:
        row = db.get_artifact_by_revision(str(revision), db_path=self.db_path)
        if row:
            details = db.get_artifact_details(int(row["id"]), db_path=self.db_path)
            if details:
                row.update(details)
        return row

    def set_state(self, artifact_id: int, state: str) -> None:
        db.set_artifact_state(int(artifact_id), state, db_path=self.db_path)

    def find(self, *, kind: Optional[str], zipcode, at_time: Optional[datetime], limit: Optional[int] = None) -> List[dict]:
        rows = db.find_artifacts(kind=kind, zipcode=zipcode, at_time=at_time, db_path=self.db_path, limit=limit)
        for row in rows:
            details = db.get_artifact_details(int(row["id"]), db_path=self.db_path)
            if details:
                row.update(details)
        return rows

    def list_all(self, *, kind: Optional[str] = None) -> List[dict]:
        rows = db.list_artifacts(kind=kind, db_path=self.db_path)
        details_by_id = db.get_artifact_details_many(
            (int(row["id"]) for row in rows), db_path=self.db_path
        )
        for row in rows:
            details = details_by_id.get(int(row["id"]))
            if details:
                row.update(details)
        return rows

    def find_by_calibration_groups(
        self,
        *,
        kind: str,
        calibration_group_ids: Iterable[str],
        state: str = "active",
    ) -> List[dict]:
        """Resolve exact planner-group parents without a whole-kind scan."""

        return db.find_artifacts_by_calibration_groups(
            kind=canonical_kind(kind),
            calibration_group_ids=calibration_group_ids,
            state=state,
            db_path=self.db_path,
        )

    def list_planning_evidence(self) -> List[dict]:
        """Load the planner's complete Artifact/QA identity snapshot once."""

        return db.list_artifact_planning_evidence(db_path=self.db_path)

    def list_terminal_task_failures(self) -> List[dict]:
        """Load latest persisted task failures for no-op rerun planning."""

        return db.list_latest_terminal_task_failures(db_path=self.db_path)

    # --- Diagnostics ---
    def get_diagnostics(self, artifact_id: int) -> Optional[dict]:
        return db.get_qa_results(int(artifact_id), db_path=self.db_path)

    def get_qa_bundle(self, artifact_id: int) -> Optional[dict]:
        return db.get_qa_bundle(int(artifact_id), db_path=self.db_path)

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
