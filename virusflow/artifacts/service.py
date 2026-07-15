from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from .models import Artifact, Scope, StorageRef, Provenance, ArtifactDescription
from .serializers import SerializerRegistry, Serializer
from .serializers import array_fits as _array_fits
from .registry_adapter import RegistryAdapter
from .diagnostics import DiagnosticsFacade


class ArtifactService:
    """Clean artifact service orchestrating models, persistence, and serializers.

    - No product-specific branching
    - Serializer dispatch by (payload_type, storage_format)
    - Lightweight by default: numeric payloads only when requested
    - Provides logical payload accessors for analytics to remain storage-agnostic
    """

    def __init__(self, db_path: str) -> None:
        self.adapter = RegistryAdapter(db_path)
        self.db_path = db_path
        self.serializers = self._default_serializers()
        self.diagnostics = DiagnosticsFacade(self.adapter)

    # ---------------- Public API ----------------
    def register(self, artifact: Artifact) -> int:
        # Validate minimal required fields
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
        return self.adapter.register(artifact)

    def select_best(self, *, kind: str, scope: Scope, at_time: Optional[datetime] = None, policy: str = "latest_valid") -> Optional[dict]:
        at = None if policy == "latest" else at_time
        return (self.adapter.find(kind=kind, zipcode=(scope.zipcode if scope else None), at_time=at, limit=1) or [None])[0]

    def get(self, artifact_id: int, *, include_payload: bool = False) -> Optional[ArtifactDescription]:
        row = self.adapter.get_row(int(artifact_id))
        if not row:
            return None
        return self._describe_row(row, include_payload=include_payload)

    def describe(self, artifact_id_or_row) -> Dict[str, Any]:
        row = artifact_id_or_row if isinstance(artifact_id_or_row, dict) else self.adapter.get_row(int(artifact_id_or_row))
        if not row:
            raise FileNotFoundError("Artifact row not found")
        desc = self._describe_row(row, include_payload=False)
        # Present as plain dict for CLI friendliness
        return {
            "id": desc.id,
            "kind": desc.kind,
            "role": desc.role,
            "payload_type": desc.payload_type,
            "storage_format": desc.storage_format,
            "path": (desc.storage.uri if desc.storage else None),
            "summary": desc.metadata,
            "qa": (self.adapter.get_diagnostics(int(desc.id)) if desc.id is not None else None),
            "model_type": desc.model_type,
        }

    def load_payload(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Load a payload for a registry row using logical identifiers only.

        - Determines payload_type/storage_format from describe() if not present in the row.
        - Dispatches to the registered serializer; returns serializer.load(path) or None.
        - Keeps analytics decoupled from storage backends.
        """
        if not isinstance(row, dict):
            return None
        # Prefer explicit hints on the row
        path = row.get("path")
        payload_type = row.get("payload_type")
        storage_format = row.get("storage_format")
        if not (path and payload_type and storage_format):
            try:
                d = self.describe(row)
            except Exception:
                d = None
            if d:
                path = path or d.get("path")
                payload_type = payload_type or d.get("payload_type")
                storage_format = storage_format or d.get("storage_format")
        if not (path and payload_type and storage_format):
            return None
        ser = self.serializers.get(str(payload_type), str(storage_format))
        if not ser:
            return None
        try:
            return ser.load(str(path))
        except Exception:
            return None

    def set_diagnostics(self, artifact_id: int, *, status: str, metrics: Optional[Dict[str, Any]] = None) -> None:
        self.diagnostics.set(int(artifact_id), status=status, metrics=metrics)

    # ---------------- Internal helpers ----------------
    def _default_serializers(self) -> SerializerRegistry:
        reg = SerializerRegistry()
        reg.register("array", "fits", Serializer(describe=_array_fits.describe, load=_array_fits.load))
        return reg

    def _infer_model_type(self, summary: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(summary, dict):
            return None
        shp = summary.get("shape")
        if isinstance(shp, (list, tuple)):
            if len(shp) == 2:
                return "array2d"
            if len(shp) == 1:
                return "array1d"
        return None

    def _describe_row(self, row: Dict[str, Any], *, include_payload: bool) -> ArtifactDescription:
        # Derive minimal fields from row and sidecar/header via serializers
        path = row.get("path")
        payload_type = None
        storage_format = None
        summary: Dict[str, Any] = {}
        if path:
            # Prefer sidecar or header-only read via serializer
            # We expect the sidecar to include payload_type/storage_format; if not, we still pass through
            # For now, assume FITS array artifacts as primary outputs
            payload_type = (row.get("payload_type") or "array")
            storage_format = (row.get("storage_format") or "fits")
            ser = self.serializers.get(payload_type, storage_format)
            if ser:
                try:
                    summary = ser.describe(path) or {}
                except Exception:
                    summary = {}
        model_type = self._infer_model_type(summary)
        art_desc = ArtifactDescription(
            id=int(row.get("id")),
            kind=str(row.get("kind")),
            role=summary.get("role") or row.get("role"),
            payload_type=summary.get("payload_type") or payload_type,
            storage_format=summary.get("storage_format") or storage_format,
            storage=StorageRef(uri=path, storage_format=(summary.get("storage_format") or storage_format or "fits"), backend="fs") if path else None,
            scope=None,  # Scope can be reconstructed when needed from zipcode/exposure if required in the future
            metadata=summary,
            provenance=None,  # Provenance retrieval not yet part of adapter; avoid guessing
            diagnostics=None,
            model_type=model_type,
        )
        if include_payload and path and art_desc.payload_type and art_desc.storage_format:
            ser = self.serializers.get(art_desc.payload_type, art_desc.storage_format)
            if ser:
                try:
                    _payload = ser.load(path)
                    # We avoid mutating the frozen description; expose header via metadata copy in return dict of describe()
                    # For get(..., include_payload=True) callers, no additional action is needed here.
                except Exception:
                    pass
        return art_desc
