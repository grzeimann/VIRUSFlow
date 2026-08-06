from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
import warnings
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np

from ..core.pathutils import sanitize_for_filename
from ..core.scientific_metadata import (
    SCIENTIFIC_METADATA_FIELDS,
    normalize_scientific_metadata,
)
from ..ontology.artifact_kinds import (
    LEGACY_KIND_ALIASES,
    canonical_kind,
    kind_candidates,
    kind_spec,
)
from ..ontology.lifecycle import ArtifactLifecycle
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
from .retention import eviction_candidate_kinds, is_eviction_trigger, retention_rule
from .serializers import Serializer, SerializerRegistry
from .serializers import array_fits as _array_fits
from .serializers import mask_fits as _mask_fits
from .storage_conventions import normalize_component


class ArtifactLoadError(RuntimeError):
    pass


class ArtifactPayloadEvictedError(ArtifactLoadError):
    """A requested payload was intentionally removed and can be rebuilt."""


class ArtifactPayloadMissingError(ArtifactLoadError):
    """A payload declared present is unexpectedly absent from storage."""


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
        db.init_artifact_db(db_path)
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
        unknown_scientific_fields = (
            set(artifact.scientific_metadata or {}) - set(SCIENTIFIC_METADATA_FIELDS)
        )
        if unknown_scientific_fields:
            raise ValueError(
                "unknown scientific metadata fields: "
                + ", ".join(sorted(unknown_scientific_fields))
            )
        artifact.scientific_metadata = normalize_scientific_metadata(
            artifact.scientific_metadata
        )
        requested_kind = str(artifact.kind).strip().lower()
        if requested_kind in LEGACY_KIND_ALIASES:
            raise ValueError(
                f"legacy Artifact kind {artifact.kind!r} is read-only; publish "
                f"{LEGACY_KIND_ALIASES[requested_kind]!r} instead"
            )
        spec = None
        try:
            spec = kind_spec(artifact.kind)
            lifecycle = spec.lifecycle
        except KeyError:
            if str(artifact.role).lower() not in {"analysis", "analytic", "diagnostic"}:
                raise ValueError(f"unregistered production Artifact kind: {artifact.kind!r}")
            lifecycle = artifact.lifecycle
        if lifecycle == ArtifactLifecycle.SCRATCH:
            raise ValueError(f"scratch-only kind cannot be registered permanently: {artifact.kind}")
        components = list(getattr(artifact, "_component_records", []) or [])
        if spec is not None:
            names = {str(component.get("name")) for component in components}
            missing = set(spec.required_components) - names
            if missing:
                raise ValueError(
                    f"canonical Artifact registration requires persisted components: {sorted(missing)}"
                )
        from ..performance import phase
        with phase("artifact_publish"):
            return self.adapter.register(artifact, components=components)

    def persist_request(self, request: ArtifactRequest, *, context: Any, policy: Any, base_dir: str) -> Artifact:
        if not request.components:
            raise ValueError("ArtifactRequest has no components")
        requested_kind = str(request.kind).strip().lower()
        if requested_kind in LEGACY_KIND_ALIASES:
            raise ValueError(
                f"legacy Artifact kind {request.kind!r} is read-only; publish "
                f"{LEGACY_KIND_ALIASES[requested_kind]!r} instead"
            )
        kind = canonical_kind(request.kind)
        spec = kind_spec(kind)
        if spec.lifecycle == ArtifactLifecycle.SCRATCH:
            raise ValueError(f"scratch-only kind cannot be persisted: {kind}")
        lifecycle = request.lifecycle or spec.lifecycle
        if lifecycle == ArtifactLifecycle.SCRATCH:
            raise ValueError(f"scratch lifecycle cannot be persisted: {kind}")
        scope = request.scope or Scope(zipcode=None)
        validity = request.validity
        if validity.start is None and scope.start_time is not None:
            validity = Validity(scope.start_time, scope.end_time, validity.policy)
        scope_parts = []
        if scope.zipcode is not None:
            scope_parts.append(sanitize_for_filename(scope.zipcode.key()))
        for value in (scope.exposure_id, scope.observation_id, scope.dither_set_id):
            if value:
                scope_parts.append(sanitize_for_filename(str(value)))
        scope_token = "__".join(scope_parts) or "global"
        validity_token = "open"
        if validity.start is not None or validity.end is not None:
            start = validity.start.strftime("%Y%m%dT%H%M%S") if validity.start else "open"
            end = validity.end.strftime("%Y%m%dT%H%M%S") if validity.end else "open"
            validity_token = f"{start}_{end}"
        tokens = {
            "kind": kind,
            "scope": scope_token,
            "validity": validity_token,
            "revision": "pending",
        }

        request_parents = [int(x) for x in request.parents]
        raw_parents = [int(x) for x in request.raw_parents]
        raw_catalog = str(request.raw_catalog or "") or None
        context_parents = [int(x) for x in (getattr(context, "parent_ids", []) or [])]
        if request_parents and context_parents and request_parents != context_parents:
            raise ValueError("ArtifactRequest.parents conflicts with deprecated PublicationContext.parent_ids")
        parents = request_parents or context_parents

        normalized_components = {
            name: normalize_component(kind, component)
            for name, component in request.components.items()
        }
        missing_components = set(spec.required_components) - set(normalized_components)
        if missing_components:
            raise ValueError(
                f"missing required components for {kind}: {sorted(missing_components)}"
            )
        storage_root = Path(base_dir)
        storage_root.mkdir(parents=True, exist_ok=True)
        projected_bytes = int(sum(
            getattr(component.value, "nbytes", 0) or 0
            for component in normalized_components.values()
        ))
        available_bytes = int(shutil.disk_usage(storage_root).free)
        if projected_bytes > int(0.9 * available_bytes):
            raise OSError(
                f"projected {kind} payload ({projected_bytes} bytes) exceeds the safe available-disk budget ({available_bytes} bytes free)"
            )
        stable_parents = [self._stable_parent_identity(parent_id) for parent_id in parents]
        stable_parents.extend(
            f"raw:{raw_id}" for raw_id in raw_parents
        )
        revision = request.revision or self._logical_revision(
            kind, scope, normalized_components, stable_parents, context,
            request.configuration_refs, request.scientific_metadata,
        )
        from ..performance import current_task_timing, phase
        with phase("artifact_lookup"):
            existing = self.adapter.find_by_revision(revision)
        if existing is not None and str(existing.get("state") or "active") == "active":
            return self._artifact_from_row(existing)
        tokens["revision"] = revision

        component_records = []
        checksums = []
        units: Dict[str, str] = {}
        coordinates: Dict[str, str] = {}
        primary_path: Optional[str] = None
        written_paths: list[Path] = []
        for name, component in normalized_components.items():
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
            with phase("serialization"):
                serializer.save(path, component.value, metadata=metadata)
            written_paths.append(Path(path))
            with phase("content_hash"):
                checksum = _sha256(path)
            checksums.append(f"{name}:{checksum}")
            if primary_path is None:
                primary_path = path
            if component.units:
                units[name] = component.units
            if component.coordinates:
                coordinates[name] = component.coordinates
            sidecar = Path(path).with_suffix(Path(path).suffix + ".json")
            payload_bytes = Path(path).stat().st_size + (sidecar.stat().st_size if sidecar.exists() else 0)
            value = component.value
            shape = list(getattr(value, "shape", ()) or ())
            dtype = str(getattr(value, "dtype", "")) or None
            described = serializer.describe(path) or {}
            component_metadata = {**dict(component.metadata or {})}
            if described.get("mask_encoding"):
                component_metadata["mask_encoding"] = described["mask_encoding"]
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
                    "metadata": component_metadata,
                    "payload_bytes": int(payload_bytes),
                    "dtype": dtype,
                    "shape": shape,
                }
            )

        aggregate_checksum = hashlib.sha256("\n".join(sorted(checksums)).encode("utf-8")).hexdigest()
        total_payload_bytes = int(sum(record["payload_bytes"] for record in component_records))
        maximum = int((request.metadata or {}).get("maximum_payload_bytes") or self._default_maximum_bytes(kind))
        if maximum > 0 and total_payload_bytes > maximum:
            self._remove_written(written_paths)
            raise ValueError(
                f"{kind} payload is {total_payload_bytes} bytes, exceeding configured maximum {maximum}"
            )
        storage_warnings = []
        for record in component_records:
            shape = record.get("shape") or []
            if lifecycle == ArtifactLifecycle.MODEL and len(shape) == 2 and min(shape) >= 1000:
                storage_warnings.append(
                    f"model component {record['name']} has detector-like dimensions {shape}"
                )
        for message in storage_warnings:
            warnings.warn(message, RuntimeWarning, stacklevel=2)
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
            raw_parents=raw_parents,
            raw_catalog=raw_catalog,
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
            metadata={
                **dict(request.summaries or {}), **dict(request.metadata or {}),
                "components": [r["name"] for r in component_records],
                "raw_parent_ids": list(raw_parents),
                "raw_catalog": raw_catalog,
                "payload_bytes": total_payload_bytes,
                "storage_warnings": storage_warnings,
                "projected_payload_bytes": projected_bytes,
            },
            scientific_metadata=normalize_scientific_metadata(
                request.scientific_metadata
            ),
            provenance=provenance,
            validity=validity,
            units=units,
            coordinates=coordinates,
            configuration_refs=list(request.configuration_refs or []),
            relations=relations,
            revision=revision,
            checksum=aggregate_checksum,
            lifecycle=lifecycle,
            state="active",
            payload_bytes=total_payload_bytes,
        )
        setattr(artifact, "_component_records", component_records)
        try:
            artifact_id = self.register(artifact)
        except sqlite3.IntegrityError:
            concurrent = self.adapter.find_by_revision(revision)
            if concurrent is None:
                self._remove_written(written_paths)
                raise
            return self._artifact_from_row(concurrent)
        artifact.id = int(artifact_id)
        timing = current_task_timing()
        if timing is not None:
            timing.increment("artifacts_written")
            timing.increment("artifact_bytes_written", total_payload_bytes)
            timing.identity("artifacts_written", f"{kind}:{artifact.id}")
            timing.artifact_events.append({
                "operation": "write", "artifact_id": artifact.id,
                "kind": kind, "bytes": total_payload_bytes,
            })
        return artifact

    def select_best(
        self,
        *,
        kind: str,
        scope: Scope,
        at_time: Optional[datetime] = None,
        policy: str = "latest_valid",
    ) -> Optional[dict]:
        nearest = policy in {"nearest", "nearest_valid"} and at_time is not None
        at = None if policy == "latest" or nearest else at_time
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
                if str(row.get("state") or "active") == "active"
                and all(value is None or row.get(field) == value for field, value in filters.items())
            ]
        if not rows:
            return None
        if nearest:
            def distance(row: dict) -> float:
                start = _dt(row.get("validity_start"))
                end = _dt(row.get("validity_end"))
                if start is not None and at_time < start:
                    return (start - at_time).total_seconds()
                if end is not None and at_time > end:
                    return (at_time - end).total_seconds()
                if start is not None or end is not None:
                    return 0.0
                return float("inf")

            rows.sort(key=lambda row: (
                distance(row),
                -(_dt(row.get("created_at")).timestamp() if _dt(row.get("created_at")) else 0.0),
                -int(row.get("id") or 0),
            ))
            return self.adapter.get_row(int(rows[0]["id"])) or rows[0]
        rows.sort(key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)), reverse=True)
        return self.adapter.get_row(int(rows[0]["id"])) or rows[0]

    def query_observation(self, observation_id: str, *, kind: Optional[str] = None) -> list[dict]:
        """Canonical read boundary for all Products related to one Observation."""

        return [
            row for row in self.adapter.list_all(kind=canonical_kind(kind) if kind else None)
            if row.get("observation_id") == str(observation_id)
            and str(row.get("state") or "active") == "active"
        ]

    def query_dither_set(self, dither_set_id: str, *, kind: Optional[str] = None) -> list[dict]:
        """Canonical read boundary for one explicit DitherSet relationship."""

        return [
            row for row in self.adapter.list_all(kind=canonical_kind(kind) if kind else None)
            if row.get("dither_set_id") == str(dither_set_id)
            and str(row.get("state") or "active") == "active"
        ]

    def query_observation_set(
        self, observation_ids: Iterable[str], *, kind: Optional[str] = None
    ) -> list[dict]:
        """Read-only query-defined ObservationSet; no exposure state is merged."""

        identities = {str(value) for value in observation_ids}
        return [
            row for row in self.adapter.list_all(kind=canonical_kind(kind) if kind else None)
            if row.get("observation_id") in identities
            and str(row.get("state") or "active") == "active"
        ]

    def get(self, artifact_id: int, *, include_payload: bool = False) -> Optional[ArtifactDescription]:
        row = self.adapter.get_row(int(artifact_id))
        return self._describe_row(row, include_payload=include_payload) if row else None

    def get_scientific_metadata(self, artifact_id: int) -> dict[str, Any]:
        """Return compact scientific state without opening Artifact components."""

        values = self.adapter.get_scientific_metadata(int(artifact_id))
        return normalize_scientific_metadata(values)

    def find_artifacts(
        self,
        *,
        kind: Optional[str] = None,
        hardware_scope=None,
        observation_time=None,
        ambient_temperature=None,
        humidity=None,
        pressure=None,
        program_id: Optional[str] = None,
        object: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Return filterable lightweight summaries without component storage I/O."""

        canonical = canonical_kind(kind) if kind is not None else None
        rows = self.adapter.find_summaries(
            kind=canonical,
            hardware_scope=hardware_scope,
            observation_time=observation_time,
            ambient_temperature=ambient_temperature,
            humidity=humidity,
            pressure=pressure,
            program_id=program_id,
            object_name=object,
            limit=limit,
        )
        summaries = []
        for row in rows:
            scientific = normalize_scientific_metadata(row)
            hardware_identity = None
            if row.get("amp_key"):
                hardware_identity = {
                    "ifuslot": row.get("ifuslot"),
                    "ifuid": row.get("ifuid"),
                    "specid": row.get("specid"),
                    "amp": row.get("amp"),
                    "controller": row.get("controller"),
                }
                if not hardware_identity["ifuslot"]:
                    try:
                        from ..core.identity import parse_zipcode_key

                        parsed = parse_zipcode_key(str(row["amp_key"]))
                        hardware_identity = {
                            "ifuslot": parsed.ifuslot,
                            "ifuid": parsed.ifuid,
                            "specid": parsed.specid,
                            "amp": parsed.amp,
                            "controller": parsed.controller,
                        }
                    except (SystemExit, ValueError):
                        pass
            summaries.append({
                "id": int(row["artifact_id"]),
                "artifact_id": int(row["artifact_id"]),
                "kind": str(row["kind"]),
                "scope": {
                    "physical_scope": row.get("physical_scope"),
                    "hardware_scope": row.get("amp_key"),
                    "hardware_identity": hardware_identity,
                    "exposure_id": row.get("exposure_id"),
                    "observation_id": row.get("observation_id"),
                    "dither_set_id": row.get("dither_set_id"),
                },
                "qa_status": row.get("qa_status"),
                "usability": row.get("usability"),
                "scientific_metadata": scientific,
                "parent_ids": list(row.get("parent_ids") or []),
                "component_names": list(row.get("component_names") or []),
                "payload_state": row.get("payload_state") or "present",
                "validity_start": _dt(row.get("validity_start")),
                "validity_end": _dt(row.get("validity_end")),
                "created_at": _dt(row.get("created_at")),
            })
        return summaries

    def describe(self, artifact_id_or_row) -> Dict[str, Any]:
        row = artifact_id_or_row if isinstance(artifact_id_or_row, dict) else self.adapter.get_row(int(artifact_id_or_row))
        if not row:
            raise FileNotFoundError("Artifact row not found")
        desc = self._describe_row(row, include_payload=False)
        components = self.adapter.list_components(int(desc.id))
        return {
            "id": desc.id,
            "kind": desc.kind,
            "canonical_kind": row.get("canonical_kind") or canonical_kind(desc.kind),
            "role": desc.role,
            "payload_type": desc.payload_type,
            "storage_format": desc.storage_format,
            "path": desc.storage.uri if desc.storage else None,
            "scope": {
                "physical_scope": desc.scope.physical_scope.value,
                "zipcode": desc.scope.zipcode.key() if desc.scope.zipcode is not None else None,
                "exposure_id": desc.scope.exposure_id,
                "observation_id": desc.scope.observation_id,
                "dither_set_id": desc.scope.dither_set_id,
            },
            "summary": desc.metadata,
            "scientific_metadata": desc.scientific_metadata,
            "qa": self.adapter.get_qa_bundle(int(desc.id)),
            "model_type": desc.model_type,
            "components": components,
            "payload_state": self._component_payload_state(components),
            "validity": asdict(desc.validity) if desc.validity else None,
            "revision": desc.revision,
            "checksum": desc.checksum,
            "lifecycle": desc.lifecycle.value,
            "state": desc.state,
            "payload_bytes": desc.payload_bytes,
            "relations": [asdict(x) for x in desc.relations],
            "provenance": {
                "producer": desc.provenance.algorithm,
                "parents": list(desc.provenance.parents),
                "raw_parents": list(desc.provenance.raw_parents),
                "raw_catalog": desc.provenance.raw_catalog,
                "created_at": desc.provenance.created_at.isoformat(),
                "configuration_references": [asdict(value) for value in desc.configuration_refs],
            },
            "analysis": {
                "study_id": desc.metadata.get("study_id"),
                "accepted_model_id": desc.metadata.get("accepted_model_id"),
                "promotion_decision": desc.metadata.get("promotion_decision"),
                "candidate": str(row.get("canonical_kind") or desc.kind).startswith("candidate_"),
            },
        }

    def load_component(self, artifact_id_or_row, component_name: Optional[str] = None, *, verify_checksum: bool = True) -> Dict[str, Any]:
        from ..performance import current_task_timing, phase
        with phase("artifact_lookup"):
            row = artifact_id_or_row if isinstance(artifact_id_or_row, dict) else self.adapter.get_row(int(artifact_id_or_row))
        if not row:
            raise FileNotFoundError("Artifact row not found")
        artifact_id = int(row["id"])
        with phase("artifact_lookup"):
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
        payload_state = str(component.get("payload_state") or "present")
        if payload_state == "evicted_rebuildable":
            raise ArtifactPayloadEvictedError(
                f"Artifact {artifact_id} component {component.get('name')!r} "
                "was intentionally evicted; its registry record, checksum, "
                "storage description, and provenance were retained for rebuilding"
            )
        if payload_state == "missing_error":
            raise ArtifactPayloadMissingError(
                f"Artifact {artifact_id} component {component.get('name')!r} "
                "is unexpectedly missing from storage"
            )
        if not path:
            raise ArtifactLoadError(f"Artifact {artifact_id} component has no path")
        if components and not Path(str(path)).is_file():
            self._record_missing_component(
                artifact_id, component, reason="payload file does not exist"
            )
            raise ArtifactPayloadMissingError(
                f"Artifact {artifact_id} component {component.get('name')!r} "
                f"was declared present but is missing from {path}"
            )
        serializer = self.serializers.get(component.get("payload_type") or "array", component.get("storage_format") or "fits")
        if serializer is None:
            raise ArtifactLoadError(f"No serializer for artifact {artifact_id} component {component.get('name')}")
        try:
            with phase("artifact_load"):
                payload = serializer.load(str(path))
        except Exception as exc:
            raise ArtifactLoadError(f"Failed loading artifact {artifact_id} component {component.get('name')}: {exc}") from exc
        if verify_checksum and component.get("checksum"):
            with phase("content_hash"):
                actual = _sha256(path)
            if actual != component["checksum"]:
                raise ArtifactLoadError(f"Checksum mismatch for artifact {artifact_id} component {component.get('name')}")
        timing = current_task_timing()
        if timing is not None:
            try:
                payload_bytes = int(component.get("payload_bytes") or Path(path).stat().st_size)
            except OSError:
                payload_bytes = int(getattr(payload.get("data"), "nbytes", 0))
            timing.increment("artifacts_loaded")
            timing.increment("artifact_bytes_loaded", payload_bytes)
            timing.identity("artifacts_loaded", f"{artifact_id}:{component.get('name')}")
            timing.artifact_events.append({
                "operation": "load", "artifact_id": artifact_id,
                "component": component.get("name"), "bytes": payload_bytes,
                "kind": row.get("canonical_kind") or row.get("kind"),
                "lifecycle": row.get("lifecycle"),
            })
        return {**payload, "component": component}

    def load_payload(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            return self.load_component(row)
        except (ArtifactPayloadEvictedError, ArtifactPayloadMissingError):
            raise
        except (FileNotFoundError, KeyError, ArtifactLoadError):
            return None

    def set_diagnostics(self, artifact_id: int, *, status: str, metrics: Optional[Dict[str, Any]] = None) -> None:
        self.diagnostics.set(int(artifact_id), status=status, metrics=metrics)

    def _default_serializers(self) -> SerializerRegistry:
        registry = SerializerRegistry()
        registry.register("array", "fits", Serializer(describe=_array_fits.describe, load=_array_fits.load, save=_array_fits.save))
        registry.register("mask", "fits", Serializer(describe=_mask_fits.describe, load=_mask_fits.load, save=_mask_fits.save))
        return registry

    @staticmethod
    def _payload_type_for(model_type: str) -> str:
        return "array" if str(model_type).lower() in {"array1d", "array2d"} else str(model_type).lower()

    @staticmethod
    def _default_maximum_bytes(kind: str) -> int:
        return {
            "ccd_scattered_light_model": 64 * 1024**2,
            "sky_model": 128 * 1024**2,
            "fiber_response_model": 256 * 1024**2,
            "calibrated_fiber_observation": 4 * 1024**3,
        }.get(kind, 0)

    @staticmethod
    def _remove_written(paths: Iterable[Path]) -> None:
        for path in paths:
            for target in (path, path.with_suffix(path.suffix + ".json")):
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass

    def _stable_parent_identity(self, parent_id: int) -> str:
        row = self.adapter.get_row(int(parent_id))
        if row is not None and row.get("revision"):
            return (
                f"artifact:{row.get('canonical_kind') or canonical_kind(row.get('kind') or 'unknown')}:"
                f"{row['revision']}"
            )
        # Raw-frame identities use stable scan row IDs in the current schema.
        # Their future replacement should be a typed raw-frame checksum identity.
        return f"registry-row:{int(parent_id)}"

    @staticmethod
    def _logical_revision(
        kind, scope, components, parents, context, configuration_refs=(),
        scientific_metadata=None,
    ) -> str:
        digest = hashlib.sha256()
        identity = {
            "kind": kind,
            "scope": asdict(scope),
            "parents": list(parents),
            "task": [getattr(context, "task_name", None), getattr(context, "task_version", None)],
            "algorithm": [getattr(context, "algorithm_name", None), getattr(context, "algorithm_version", None)],
            "parameters": dict(getattr(context, "parameters", {}) or {}),
            "configuration_refs": sorted(
                (asdict(value) for value in (configuration_refs or [])),
                key=lambda value: json.dumps(value, sort_keys=True, default=str),
            ),
            "scientific_metadata": normalize_scientific_metadata(
                scientific_metadata
            ),
        }
        digest.update(json.dumps(identity, sort_keys=True, default=str).encode("utf-8"))
        for name, component in sorted(components.items()):
            value = component.value
            array = value.payload if hasattr(value, "payload") else value
            array = np.ascontiguousarray(array)
            digest.update(name.encode("utf-8"))
            digest.update(str(array.dtype).encode("ascii"))
            digest.update(str(array.shape).encode("ascii"))
            # Python 3.14 rejects casting a memoryview whose shape contains a
            # zero-length dimension. Dtype and shape already distinguish empty
            # arrays, and their byte payload is canonically empty.
            if array.nbytes:
                digest.update(memoryview(array).cast("B"))
            digest.update(json.dumps(component.metadata, sort_keys=True, default=str).encode("utf-8"))
        return digest.hexdigest()[:32]

    def _artifact_from_row(self, row: Dict[str, Any]) -> Artifact:
        desc = self._describe_row(row, include_payload=False)
        return Artifact(
            id=desc.id,
            kind=desc.kind,
            role=desc.role or "reduction",
            payload_type=desc.payload_type or "array",
            storage_format=desc.storage_format or "fits",
            storage=desc.storage or StorageRef("", "fits", "fs"),
            scope=desc.scope or Scope(zipcode=None),
            metadata=dict(desc.metadata),
            scientific_metadata=dict(desc.scientific_metadata),
            provenance=desc.provenance,
            validity=desc.validity or Validity(),
            units=dict(desc.units),
            coordinates=dict(desc.coordinates),
            configuration_refs=list(desc.configuration_refs),
            relations=list(desc.relations),
            revision=desc.revision,
            checksum=desc.checksum,
            lifecycle=desc.lifecycle,
            state=desc.state,
            payload_bytes=desc.payload_bytes,
        )

    def storage_summary(self, *, largest: int = 10) -> Dict[str, Any]:
        rows = [row for row in self.adapter.list_all() if str(row.get("state") or "active") == "active"]
        by_kind: Dict[str, Dict[str, int]] = {}
        for row in rows:
            kind = str(row.get("canonical_kind") or canonical_kind(row.get("kind") or "unknown"))
            bucket = by_kind.setdefault(kind, {"count": 0, "total_bytes": 0})
            bucket["count"] += 1
            components = self.adapter.list_components(int(row["id"]))
            resident_bytes = (
                sum(
                    int(component.get("payload_bytes") or 0)
                    for component in components
                    if str(component.get("payload_state") or "present") == "present"
                )
                if components
                else int(
                    row.get("payload_bytes")
                    or (row.get("metadata") or {}).get("payload_bytes")
                    or 0
                )
            )
            row["resident_payload_bytes"] = resident_bytes
            bucket["total_bytes"] += resident_bytes
        ordered = sorted(
            rows,
            key=lambda row: int(row.get("resident_payload_bytes") or 0),
            reverse=True,
        )
        return {
            "total_count": len(rows),
            "total_bytes": sum(item["total_bytes"] for item in by_kind.values()),
            "by_kind": dict(sorted(by_kind.items(), key=lambda item: item[1]["total_bytes"], reverse=True)),
            "largest": [
                {
                    "artifact_id": int(row["id"]),
                    "kind": row.get("canonical_kind") or row.get("kind"),
                    "payload_bytes": int(row.get("resident_payload_bytes") or 0),
                }
                for row in ordered[: max(0, int(largest))]
            ],
        }

    def payload_status(
        self, artifact_id: int, *, verify_files: bool = True
    ) -> str:
        """Return present, evicted_rebuildable, or missing_error."""

        row = self.adapter.get_row(int(artifact_id))
        if row is None:
            raise FileNotFoundError(f"artifact not found: {artifact_id}")
        components = self.adapter.list_components(int(artifact_id))
        stored = self._component_payload_state(components)
        if stored != "present" or not verify_files:
            return stored
        missing = [
            component for component in components
            if not Path(str(component.get("path") or "")).is_file()
        ]
        if missing:
            self.adapter.set_component_payload_states(
                int(artifact_id),
                [
                    self._payload_state_update(
                        component,
                        "missing_error",
                        reason="payload file does not exist",
                    )
                    for component in missing
                ],
            )
            return "missing_error"
        if not components and not Path(str(row.get("path") or "")).is_file():
            return "missing_error"
        return "present"

    def evict_payload(self, artifact_id: int) -> int:
        """Evict only policy-approved payload components after evidence validation.

        The Artifact, provenance, QA, checksums, component descriptions, and
        applicability stay active and queryable.  Files are first atomically
        moved out of their published locations; component-state updates are
        committed together, then the staged files are deleted.
        """

        artifact_id = int(artifact_id)
        row = self.adapter.get_row(artifact_id)
        if row is None:
            raise FileNotFoundError(f"artifact not found: {artifact_id}")
        if str(row.get("state") or "active") != "active":
            raise ValueError(f"only active Artifact payloads may be evicted: {artifact_id}")
        kind = canonical_kind(row.get("canonical_kind") or row.get("kind") or "")
        rule = retention_rule(kind)
        lifecycle = str(row.get("lifecycle") or ArtifactLifecycle.CANONICAL.value)
        if rule is None and lifecycle != ArtifactLifecycle.CACHE.value:
            raise ValueError(
                f"Artifact kind {kind!r} is permanently retained; payload eviction refused"
            )
        components = self.adapter.list_components(artifact_id)
        requested = (
            set(rule.evictable_components) if rule is not None
            else {str(component.get("name")) for component in components}
        )
        targets = [
            component for component in components
            if str(component.get("name")) in requested
        ]
        missing_descriptions = requested - {
            str(component.get("name")) for component in targets
        }
        if missing_descriptions:
            raise ValueError(
                f"Artifact {artifact_id} is missing policy components: "
                f"{sorted(missing_descriptions)}"
            )
        if not targets:
            raise ValueError(f"Artifact {artifact_id} has no component payloads to evict")
        # Idempotent trigger calls are common in resumed or overlapping runs.
        # Do not re-walk and revalidate the descendant graph after the payload
        # has already been safely classified as rebuildable.
        if all(
            str(component.get("payload_state") or "present")
            == "evicted_rebuildable"
            for component in targets
        ):
            return 0
        if rule is not None:
            source_qa = self.adapter.get_qa_bundle(artifact_id) or {}
            self._require_validated(
                {
                    **row,
                    "qa_status": source_qa.get("status"),
                    "qa_usability": source_qa.get("usability"),
                },
                label=f"source {kind}",
            )
            self._require_eviction_descendants(artifact_id, rule.required_descendant_kinds)

        for component in targets:
            state = str(component.get("payload_state") or "present")
            if state == "missing_error":
                raise ArtifactPayloadMissingError(
                    f"Artifact {artifact_id} component {component.get('name')!r} "
                    "is unexpectedly missing; it cannot be recorded as an intentional eviction"
                )

        unexpectedly_missing = [
            component for component in targets
            if str(component.get("payload_state") or "present") == "present"
            and not Path(str(component.get("path") or "")).is_file()
        ]
        if unexpectedly_missing:
            self.adapter.set_component_payload_states(
                artifact_id,
                [
                    self._payload_state_update(
                        component,
                        "missing_error",
                        reason="payload file was missing during eviction preflight",
                    )
                    for component in unexpectedly_missing
                ],
            )
            names = [str(component.get("name")) for component in unexpectedly_missing]
            raise ArtifactPayloadMissingError(
                f"Artifact {artifact_id} has unexpectedly missing payloads {names}; "
                "intentional eviction was refused"
            )

        evicted_at = datetime.utcnow().isoformat()
        updates = []
        staged: list[tuple[Path, Path]] = []
        removed_bytes = 0
        try:
            for component in targets:
                if str(component.get("payload_state") or "present") == "evicted_rebuildable":
                    continue
                path = Path(str(component["path"]))
                sidecar = path.with_suffix(path.suffix + ".json")
                storage = []
                for original in (path,):
                    size = int(original.stat().st_size)
                    removed_bytes += size
                    storage.append({"path": str(original), "bytes": size})
                    staged_path = original.with_name(
                        f".{original.name}.evicting-{uuid.uuid4().hex}"
                    )
                    original.replace(staged_path)
                    staged.append((original, staged_path))
                update = self._payload_state_update(
                    component,
                    "evicted_rebuildable",
                    reason="retention policy",
                )
                update["eviction"].update(
                    {
                        "evicted_at": evicted_at,
                        "kind": kind,
                        "removed_files": storage,
                        "retained_description_files": (
                            [{"path": str(sidecar), "bytes": int(sidecar.stat().st_size)}]
                            if sidecar.is_file()
                            else []
                        ),
                    }
                )
                updates.append(update)
            self.adapter.set_component_payload_states(artifact_id, updates)
        except BaseException:
            for original, staged_path in reversed(staged):
                if staged_path.exists():
                    staged_path.replace(original)
            raise
        for _, staged_path in staged:
            staged_path.unlink(missing_ok=True)
        return removed_bytes

    def evict_payloads_triggered_by(self, artifact_id: int) -> Dict[str, Any]:
        """Evict eligible ancestors at a newly validated Product boundary.

        This is deliberately lineage-local: it examines only ancestors of the
        Product that just completed, rather than sweeping the Artifact registry.
        ``evict_payload`` remains the authoritative validation and deletion
        boundary for every candidate.
        """

        artifact_id = int(artifact_id)
        row = self.adapter.get_row(artifact_id)
        if row is None:
            raise FileNotFoundError(f"artifact not found: {artifact_id}")
        trigger_kind = canonical_kind(
            row.get("canonical_kind") or row.get("kind") or ""
        )
        evicted_ids: list[int] = []
        removed_bytes = 0
        refused: list[Dict[str, Any]] = []
        if not is_eviction_trigger(trigger_kind):
            return {
                "trigger_artifact_id": artifact_id,
                "trigger_kind": trigger_kind,
                "evicted_artifact_ids": evicted_ids,
                "removed_bytes": removed_bytes,
                "refused": refused,
                "candidate_count": 0,
            }
        candidate_kinds = eviction_candidate_kinds(trigger_kind)
        candidates = self.adapter.list_ancestors(
            artifact_id, kinds=candidate_kinds
        )
        for ancestor in candidates:
            ancestor_id = int(ancestor["id"])
            ancestor_kind = canonical_kind(ancestor.get("kind") or "")
            rule = retention_rule(ancestor_kind)
            if rule is None or canonical_kind(rule.eviction_trigger_kind) != trigger_kind:
                continue
            try:
                removed = self.evict_payload(ancestor_id)
            except (ValueError, ArtifactLoadError, OSError) as exc:
                refused.append({
                    "artifact_id": ancestor_id,
                    "kind": ancestor_kind,
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                continue
            if removed:
                evicted_ids.append(ancestor_id)
                removed_bytes += int(removed)
        return {
            "trigger_artifact_id": artifact_id,
            "trigger_kind": trigger_kind,
            "evicted_artifact_ids": evicted_ids,
            "removed_bytes": removed_bytes,
            "refused": refused,
            "candidate_count": len(candidates),
        }

    def _purge_payload(self, artifact_id: int) -> int:
        """Remove payloads for an already-obsolete internal cleanup record."""

        row = self.adapter.get_row(int(artifact_id))
        if row is None:
            raise FileNotFoundError(f"artifact not found: {artifact_id}")
        components = self.adapter.list_components(int(artifact_id))
        paths = [component.get("path") for component in components] or [row.get("path")]
        removed = 0
        for value in dict.fromkeys(paths):
            path = Path(str(value or ""))
            if not path.name:
                continue
            for target in (path, path.with_suffix(path.suffix + ".json")):
                try:
                    removed += target.stat().st_size
                    target.unlink()
                except FileNotFoundError:
                    pass
        return removed

    def invalidate_kinds(self, kinds: Iterable[str], *, delete_payloads: bool = False) -> Dict[str, int]:
        selected = {canonical_kind(kind) for kind in kinds}
        count = 0
        removed = 0
        for row in self.adapter.list_all():
            if canonical_kind(row.get("canonical_kind") or row.get("kind") or "") not in selected:
                continue
            if str(row.get("state") or "active") != "active":
                continue
            self.adapter.set_state(int(row["id"]), "obsolete")
            count += 1
            if delete_payloads:
                removed += self._purge_payload(int(row["id"]))
        return {"invalidated": count, "removed_bytes": removed}

    @staticmethod
    def _component_payload_state(components: Iterable[Dict[str, Any]]) -> str:
        states = {
            str(component.get("payload_state") or "present")
            for component in components
        }
        if "missing_error" in states:
            return "missing_error"
        if "evicted_rebuildable" in states:
            return "evicted_rebuildable"
        return "present"

    @staticmethod
    def _payload_state_update(
        component: Dict[str, Any], state: str, *, reason: str
    ) -> Dict[str, Any]:
        return {
            "name": str(component.get("name")),
            "payload_state": state,
            "eviction": {
                "reason": reason,
                "path": component.get("path"),
                "storage_format": component.get("storage_format"),
                "checksum": component.get("checksum"),
                "payload_bytes": int(component.get("payload_bytes") or 0),
                "detected_at": datetime.utcnow().isoformat(),
            },
        }

    def _record_missing_component(
        self, artifact_id: int, component: Dict[str, Any], *, reason: str
    ) -> None:
        self.adapter.set_component_payload_states(
            int(artifact_id),
            [self._payload_state_update(component, "missing_error", reason=reason)],
        )

    @staticmethod
    def _require_validated(row: Dict[str, Any], *, label: str) -> None:
        if str(row.get("state") or "active") != "active":
            raise ValueError(f"{label} is not an active Product")
        status = str(row.get("qa_status") or "").lower()
        usability = str(row.get("qa_usability") or "").lower()
        if not status:
            raise ValueError(f"{label} has no completed QA validation")
        if status in {"fail", "failed", "error"} or usability == "unusable":
            raise ValueError(
                f"{label} is not valid (QA status={status!r}, usability={usability!r})"
            )

    def _require_eviction_descendants(
        self, artifact_id: int, required_kinds: Iterable[str]
    ) -> None:
        required = {canonical_kind(kind) for kind in required_kinds}
        descendants = self.adapter.list_descendants(
            int(artifact_id), kinds=required
        )
        valid_by_kind: dict[str, list[int]] = {}
        invalid_by_kind: dict[str, list[int]] = {}
        invalid_reasons: dict[str, list[str]] = {}
        for descendant in descendants:
            kind = canonical_kind(descendant.get("kind") or "")
            if kind not in required:
                continue
            if kind in valid_by_kind:
                continue
            try:
                self._require_validated(descendant, label=f"descendant {kind}")
                required_components = set(kind_spec(kind).required_components)
                components = {
                    str(component.get("name")): component
                    for component in self.adapter.list_components(int(descendant["id"]))
                }
                missing_components = sorted(required_components - set(components))
                if missing_components:
                    raise ValueError(
                        f"descendant {kind} lacks required evidence components "
                        f"{missing_components}"
                    )
                for component_name in sorted(required_components):
                    component = components[component_name]
                    state = str(component.get("payload_state") or "present")
                    path = Path(str(component.get("path") or ""))
                    checksum = str(component.get("checksum") or "")
                    if state != "present" or not path.is_file():
                        raise ValueError(
                            f"descendant {kind} component {component_name!r} "
                            "has missing payload evidence"
                        )
                    if not checksum or _sha256(path) != checksum:
                        raise ValueError(
                            f"descendant {kind} component {component_name!r} "
                            "has unverifiable payload evidence"
                        )
            except ValueError as exc:
                invalid_by_kind.setdefault(kind, []).append(int(descendant["id"]))
                invalid_reasons.setdefault(kind, []).append(str(exc))
            else:
                valid_by_kind.setdefault(kind, []).append(int(descendant["id"]))
        missing = sorted(required - set(valid_by_kind))
        if missing:
            details = ", ".join(
                f"{kind} (invalid IDs {invalid_by_kind.get(kind, [])}; "
                f"reasons {invalid_reasons.get(kind, [])})"
                for kind in missing
            )
            raise ValueError(
                f"Artifact {artifact_id} payload eviction requires validated "
                f"provenance descendants: {details}"
            )

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
        raw_relations = self.adapter.list_raw_relations(int(row["id"]))
        provenance = Provenance(
            algorithm=str(row.get("algorithm") or "unknown"),
            params={},
            parents=[int(x) for x in str(row.get("parents") or "").split(",") if x],
            raw_parents=[int(item["raw_id"]) for item in raw_relations],
            raw_catalog=(
                str(raw_relations[0].get("raw_catalog") or "") or None
                if raw_relations else None
            ),
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
            scientific_metadata=self.get_scientific_metadata(int(row["id"])),
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
            lifecycle=ArtifactLifecycle(row.get("lifecycle") or "canonical"),
            state=str(row.get("state") or "active"),
            payload_bytes=int(row.get("payload_bytes") or summary.get("payload_bytes") or 0),
        )
        if include_payload:
            summary["payload"] = self.load_component(row)
        return desc
