from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..ontology.artifact_kinds import canonical_kind, kind_candidates
from ..ontology.relations import RelationKind
from ..registry import database as db
from .qa_service import QADiagnosticsService
from .models import (
    Artifact,
    ArtifactDescription,
    ArtifactRelation,
    ConfigurationReference,
    DiagnosticRecord,
    Provenance,
    Scope,
    StorageRef,
    Validity,
)
from .registry_adapter import RegistryAdapter
from .requests import ArtifactRequest
from .serializers import Serializer, SerializerRegistry
from .serializers import array_fits as _array_fits


class ArtifactLoadError(RuntimeError):
    pass


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class ArtifactService:
    """Canonical Product persistence, loading, selection, lineage and diagnostics boundary."""

    def __init__(self, db_path: str) -> None:
        db.init_db(db_path)
        self.adapter = RegistryAdapter(db_path)
        self.db_path = db_path
        self.serializers = self._default_serializers()
        self.diagnostics = QADiagnosticsService(self.adapter, component_loader=self.load_component)

    def register(self, artifact: Artifact) -> int:
        if not artifact.kind:
            raise ValueError("Artifact.kind is required")
        if not artifact.role:
            raise ValueError("Artifact.role is required")
        if not artifact.payload_type:
            raise ValueError("Artifact.payload_type is required")
        if not artifact.storage_format:
            raise ValueError("Artifact.storage_format is required")
        if not artifact.storage or not artifact.storage.uri:
            raise ValueError("Artifact.storage.uri is required")
        components = list(getattr(artifact, "_component_records", []) or [])
        return self.adapter.register(artifact, components=components)

    def persist_request(self, request: ArtifactRequest, *, context: Any, policy: Any, base_dir: str) -> Artifact:
        if not request.components:
            raise ValueError("ArtifactRequest has no components")
        kind = canonical_kind(request.kind)
        revision = request.revision or uuid.uuid4().hex
        scope = request.scope or Scope(zipcode=None)
        validity = request.validity
        if validity.start is None and scope.start_time is not None:
            validity = Validity(scope.start_time, scope.end_time, validity.policy)
        scope_parts = []
        if scope.zipcode is not None:
            scope_parts.append(scope.zipcode.key())
        for value in (scope.exposure_id, scope.observation_id, scope.dither_set_id):
            if value:
                scope_parts.append(str(value))
        scope_token = "__".join(scope_parts) or "global"
        validity_token = "open"
        if validity.start is not None or validity.end is not None:
            start = validity.start.strftime("%Y%m%dT%H%M%S") if validity.start else "open"
            end = validity.end.strftime("%Y%m%dT%H%M%S") if validity.end else "open"
            validity_token = f"{start}_{end}"
        tokens = {
            "kind": kind,
            "scope": scope_token.replace("/", "_"),
            "validity": validity_token,
            "revision": revision,
        }

        request_parents = [int(x) for x in request.parents]
        context_parents = [int(x) for x in (getattr(context, "parent_ids", []) or [])]
        if request_parents and context_parents and request_parents != context_parents:
            raise ValueError("ArtifactRequest.parents conflicts with deprecated PublicationContext.parent_ids")
        parents = request_parents or context_parents

        component_records = []
        checksums = []
        units: Dict[str, str] = {}
        coordinates: Dict[str, str] = {}
        primary_path: Optional[str] = None
        for name, component in request.components.items():
            decision = policy.decide(artifact_kind=kind, component_name=name, model_type=component.model_type)
            payload_type = self._payload_type_for(component.model_type)
            serializer = self.serializers.get(payload_type, decision.storage_format)
            if serializer is None or serializer.save is None:
                raise NotImplementedError(
                    f"No writer for payload_type={payload_type} storage_format={decision.storage_format}"
                )
            path = policy.filename(
                artifact_kind=kind,
                component_name=name,
                base_dir=base_dir,
                tokens=tokens,
            )
            metadata = {
                "kind": kind,
                "component": name,
                "role": request.role,
                "payload_type": payload_type,
                "storage_format": decision.storage_format,
                "model_type": component.model_type,
                "units": component.units,
                "coordinates": component.coordinates,
                "revision": revision,
                "n_inputs": request.metadata.get("n_inputs", request.summaries.get("n_inputs", 0)),
                "algo_version": getattr(context, "algorithm_version", None) or request.metadata.get("algo_version"),
                **dict(request.summaries or {}),
                **dict(component.metadata or {}),
            }
            try:
                metadata["shape"] = list(component.value.shape)
            except Exception:
                pass
            serializer.save(path, component.value, metadata=metadata)
            checksum = _sha256(path)
            checksums.append(f"{name}:{checksum}")
            if primary_path is None:
                primary_path = path
            if component.units:
                units[name] = component.units
            if component.coordinates:
                coordinates[name] = component.coordinates
            component_records.append(
                {
                    "name": name,
                    "model_type": component.model_type,
                    "path": path,
                    "payload_type": payload_type,
                    "storage_format": decision.storage_format,
                    "checksum": checksum,
                    "units": component.units,
                    "coordinates": component.coordinates,
                    "metadata": dict(component.metadata or {}),
                }
            )

        aggregate_checksum = hashlib.sha256("\n".join(sorted(checksums)).encode("utf-8")).hexdigest()
        context_params = dict(getattr(context, "parameters", {}) or {})
        provenance = Provenance(
            algorithm=f"{getattr(context, 'algorithm_name', None) or 'unknown'}:{getattr(context, 'algorithm_version', None) or 'unknown'}",
            params={
                **context_params,
                "task": {"name": getattr(context, "task_name", None), "version": getattr(context, "task_version", None)},
                "algorithm": {"name": getattr(context, "algorithm_name", None), "version": getattr(context, "algorithm_version", None)},
                "timings": dict(getattr(context, "timings", {}) or {}),
                "assumptions": list(request.assumptions or []),
            },
            parents=parents,
        )
        relations = list(request.relations or [])
        related = {(int(r.parent_id), str(r.relation)) for r in relations}
        for parent_id in parents:
            if (parent_id, RelationKind.DERIVED_FROM.value) not in related:
                relations.append(ArtifactRelation(parent_id=parent_id, relation=RelationKind.DERIVED_FROM.value))
        artifact = Artifact(
            id=None,
            kind=kind,
            role=request.role,
            payload_type=component_records[0]["payload_type"],
            storage_format=component_records[0]["storage_format"],
            storage=StorageRef(primary_path or "", component_records[0]["storage_format"], component_records[0]["storage_format"]),
            scope=scope,
            metadata={**dict(request.summaries or {}), **dict(request.metadata or {}), "components": [r["name"] for r in component_records]},
            provenance=provenance,
            validity=validity,
            units=units,
            coordinates=coordinates,
            configuration_refs=list(request.configuration_refs or []),
            relations=relations,
            revision=revision,
            checksum=aggregate_checksum,
        )
        setattr(artifact, "_component_records", component_records)
        artifact_id = self.register(artifact)
        artifact.id = int(artifact_id)
        return artifact

    def select_best(
        self,
        *,
        kind: str,
        scope: Scope,
        at_time: Optional[datetime] = None,
        policy: str = "latest_valid",
    ) -> Optional[dict]:
        at = None if policy == "latest" else at_time
        rows = []
        for candidate in kind_candidates(kind):
            rows.extend(self.adapter.find(kind=candidate, zipcode=(scope.zipcode if scope else None), at_time=at, limit=None))
        if scope is not None:
            filters = {
                "exposure_id": scope.exposure_id,
                "observation_id": scope.observation_id,
                "dither_set_id": scope.dither_set_id,
            }
            rows = [
                row for row in rows
                if all(value is None or row.get(field) == value for field, value in filters.items())
            ]
        if not rows:
            return None
        rows.sort(key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)), reverse=True)
        return self.adapter.get_row(int(rows[0]["id"])) or rows[0]

    def query_observation(self, observation_id: str, *, kind: Optional[str] = None) -> list[dict]:
        """Canonical read boundary for all Products related to one Observation."""

        return [
            row for row in self.adapter.list_all(kind=canonical_kind(kind) if kind else None)
            if row.get("observation_id") == str(observation_id)
        ]

    def query_dither_set(self, dither_set_id: str, *, kind: Optional[str] = None) -> list[dict]:
        """Canonical read boundary for one explicit DitherSet relationship."""

        return [
            row for row in self.adapter.list_all(kind=canonical_kind(kind) if kind else None)
            if row.get("dither_set_id") == str(dither_set_id)
        ]

    def query_observation_set(
        self, observation_ids: Iterable[str], *, kind: Optional[str] = None
    ) -> list[dict]:
        """Read-only query-defined ObservationSet; no exposure state is merged."""

        identities = {str(value) for value in observation_ids}
        return [
            row for row in self.adapter.list_all(kind=canonical_kind(kind) if kind else None)
            if row.get("observation_id") in identities
        ]

    def get(self, artifact_id: int, *, include_payload: bool = False) -> Optional[ArtifactDescription]:
        row = self.adapter.get_row(int(artifact_id))
        return self._describe_row(row, include_payload=include_payload) if row else None

    def describe(self, artifact_id_or_row) -> Dict[str, Any]:
        row = artifact_id_or_row if isinstance(artifact_id_or_row, dict) else self.adapter.get_row(int(artifact_id_or_row))
        if not row:
            raise FileNotFoundError("Artifact row not found")
        desc = self._describe_row(row, include_payload=False)
        return {
            "id": desc.id,
            "kind": desc.kind,
            "canonical_kind": row.get("canonical_kind") or canonical_kind(desc.kind),
            "role": desc.role,
            "payload_type": desc.payload_type,
            "storage_format": desc.storage_format,
            "path": desc.storage.uri if desc.storage else None,
            "summary": desc.metadata,
            "qa": self.adapter.get_qa_bundle(int(desc.id)),
            "model_type": desc.model_type,
            "components": self.adapter.list_components(int(desc.id)),
            "validity": asdict(desc.validity) if desc.validity else None,
            "revision": desc.revision,
            "checksum": desc.checksum,
            "relations": [asdict(x) for x in desc.relations],
        }

    def load_component(self, artifact_id_or_row, component_name: Optional[str] = None, *, verify_checksum: bool = True) -> Dict[str, Any]:
        row = artifact_id_or_row if isinstance(artifact_id_or_row, dict) else self.adapter.get_row(int(artifact_id_or_row))
        if not row:
            raise FileNotFoundError("Artifact row not found")
        artifact_id = int(row["id"])
        components = self.adapter.list_components(artifact_id)
        component = None
        if components:
            if component_name is None:
                component = components[0]
            else:
                component = next((x for x in components if x.get("name") == component_name), None)
            if component is None:
                raise KeyError(f"Artifact {artifact_id} has no component {component_name!r}")
        else:
            if component_name not in (None, "master", "data"):
                raise KeyError(f"Legacy artifact {artifact_id} has no named component {component_name!r}")
            component = {
                "name": component_name or "data",
                "path": row.get("path"),
                "payload_type": row.get("payload_type") or "array",
                "storage_format": row.get("storage_format") or "fits",
                "checksum": None,
            }
        path = component.get("path")
        if not path:
            raise ArtifactLoadError(f"Artifact {artifact_id} component has no path")
        serializer = self.serializers.get(component.get("payload_type") or "array", component.get("storage_format") or "fits")
        if serializer is None:
            raise ArtifactLoadError(f"No serializer for artifact {artifact_id} component {component.get('name')}")
        try:
            payload = serializer.load(str(path))
        except Exception as exc:
            raise ArtifactLoadError(f"Failed loading artifact {artifact_id} component {component.get('name')}: {exc}") from exc
        if verify_checksum and component.get("checksum"):
            actual = _sha256(path)
            if actual != component["checksum"]:
                raise ArtifactLoadError(f"Checksum mismatch for artifact {artifact_id} component {component.get('name')}")
        return {**payload, "component": component}

    def load_payload(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            return self.load_component(row)
        except (FileNotFoundError, KeyError, ArtifactLoadError):
            return None

    def set_diagnostics(self, artifact_id: int, *, status: str, metrics: Optional[Dict[str, Any]] = None) -> None:
        self.diagnostics.set(int(artifact_id), status=status, metrics=metrics)

    def _default_serializers(self) -> SerializerRegistry:
        registry = SerializerRegistry()
        registry.register("array", "fits", Serializer(describe=_array_fits.describe, load=_array_fits.load, save=_array_fits.save))
        return registry

    @staticmethod
    def _payload_type_for(model_type: str) -> str:
        return "array" if str(model_type).lower() in {"array1d", "array2d"} else str(model_type).lower()

    @staticmethod
    def _infer_model_type(summary: Optional[Dict[str, Any]]) -> Optional[str]:
        shape = (summary or {}).get("shape") if isinstance(summary, dict) else None
        return "array2d" if isinstance(shape, (list, tuple)) and len(shape) == 2 else ("array1d" if isinstance(shape, (list, tuple)) and len(shape) == 1 else None)

    def _describe_row(self, row: Dict[str, Any], *, include_payload: bool) -> ArtifactDescription:
        path = row.get("path")
        components = self.adapter.list_components(int(row["id"]))
        if components:
            path = components[0].get("path")
        payload_type = row.get("payload_type") or (components[0].get("payload_type") if components else "array")
        storage_format = row.get("storage_format") or (components[0].get("storage_format") if components else "fits")
        summary = dict(row.get("metadata") or {})
        if path:
            serializer = self.serializers.get(payload_type, storage_format)
            if serializer:
                try:
                    summary = {**(serializer.describe(path) or {}), **summary}
                except Exception:
                    pass
        zipcode = None
        if row.get("amp_key"):
            try:
                from ..core.identity import parse_zipcode_key

                zipcode = parse_zipcode_key(row["amp_key"])
            except Exception:
                zipcode = None
        try:
            from ..ontology.scopes import PhysicalScope

            physical_scope = PhysicalScope(row.get("physical_scope") or "amplifier")
        except Exception:
            from ..ontology.scopes import PhysicalScope

            physical_scope = PhysicalScope.AMPLIFIER
        scope = Scope(
            zipcode,
            exposure_id=row.get("exposure_id"),
            physical_scope=physical_scope,
            observation_id=row.get("observation_id"),
            dither_set_id=row.get("dither_set_id"),
        )
        config_refs = [ConfigurationReference(**x) for x in (row.get("configuration_refs") or [])]
        relations = [ArtifactRelation(**x) for x in self.adapter.list_relations(int(row["id"]))]
        provenance = Provenance(
            algorithm=str(row.get("algorithm") or "unknown"),
            params={},
            parents=[int(x) for x in str(row.get("parents") or "").split(",") if x],
            created_at=_dt(row.get("created_at")) or datetime.utcnow(),
        )
        desc = ArtifactDescription(
            id=int(row["id"]),
            kind=str(row.get("kind") or row.get("canonical_kind")),
            role=row.get("role") or summary.get("role"),
            payload_type=payload_type,
            storage_format=storage_format,
            storage=StorageRef(str(path), storage_format, "fs") if path else None,
            scope=scope,
            metadata=summary,
            provenance=provenance,
            diagnostics=DiagnosticRecord(**self.adapter.get_diagnostics(int(row["id"]))) if False else None,
            model_type=(components[0].get("model_type") if components else self._infer_model_type(summary)),
            validity=Validity(
                _dt(row.get("validity_start")),
                _dt(row.get("validity_end")),
                row.get("validity_policy") or "explicit",
            ),
            units=dict(row.get("units") or {}),
            coordinates=dict(row.get("coordinates") or {}),
            configuration_refs=config_refs,
            relations=relations,
            revision=row.get("revision"),
            checksum=row.get("checksum"),
        )
        if include_payload:
            summary["payload"] = self.load_component(row)
        return desc
