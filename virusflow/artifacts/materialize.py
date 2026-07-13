from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Tuple, Any

from .service import ArtifactService
from .serializers import SerializerRegistry
from .models import Scope
from .io_fits import write_array_fits, read_array_fits


@dataclass
class LoadResult:
    data: Any
    header: Dict
    row: Dict
    description: Dict


class ArtifactMaterializer:
    """Generic, product-agnostic materializer for artifact payloads.

    - Persist representation-level payloads with explicit sidecars.
    - Load payloads by artifact id or by artifact-centric selection (kind + scope + time).
    - Dispatches by (payload_type, storage_format) via the service's serializer registry.
    """

    def __init__(self, svc: ArtifactService) -> None:
        self.svc = svc
        self._serializers: SerializerRegistry = svc.serializers

    # ---------------- Persist (representation-only) ----------------
    def persist_array(
        self,
        output_path: str,
        *,
        data,
        n_inputs: int = 0,
        algo_version: str = "unknown",
        header_cards: Optional[Dict] = None,
        extra_header: Optional[Dict] = None,
        sidecar: Optional[Dict] = None,
    ) -> None:
        """Persist a 1D/2D array as FITS with a compact sidecar.

        Sidecar must be explicit about scientific meaning and representation, e.g.:
          {"kind": "trace", "role": "calibration", "payload_type": "array", "storage_format": "fits", ...}
        """
        # Ensure required representation keys exist in sidecar
        sc = dict(sidecar or {})
        sc.setdefault("payload_type", "array")
        sc.setdefault("storage_format", "fits")
        write_array_fits(
            output_path,
            data=data,
            n_inputs=n_inputs,
            algo_version=algo_version,
            extra_primary_cards=header_cards,
            extra_header=extra_header,
            sidecar=sc,
        )

    # ---------------- Load by artifact identity ----------------
    def load_by_id(self, artifact_id: int, *, expect: Tuple[str, str] = ("array", "fits")) -> LoadResult:
        desc = self.svc.describe(int(artifact_id))
        pt, sf = desc.get("payload_type"), desc.get("storage_format")
        if expect and (pt, sf) != expect:
            raise TypeError(f"Artifact {artifact_id} has ({pt},{sf}); expected {expect}")
        path = desc.get("path")
        if not path:
            raise FileNotFoundError(f"Artifact {artifact_id} has no storage path")
        ser = self._serializers.get(pt, sf) if (pt and sf) else None
        payload = ser.load(path) if ser else read_array_fits(path)
        row = self.svc.adapter.get_row(int(artifact_id)) or {}
        return LoadResult(data=payload.get("data"), header=payload.get("header", {}), row=row, description=desc)

    # ---------------- Artifact-centric selection and load ----------------
    def load_best(
        self,
        *,
        kind: str,
        scope: Scope,
        at_time=None,
        policy: str = "latest_valid",
        expect: Tuple[str, str] = ("array", "fits"),
    ) -> LoadResult:
        row = self.svc.select_best(kind=kind, scope=scope, at_time=at_time, policy=policy)
        if not row:
            raise FileNotFoundError(f"No artifact found for kind={kind} in the given scope")
        desc = self.svc.describe(row)
        pt, sf = desc.get("payload_type"), desc.get("storage_format")
        if expect and (pt, sf) != expect:
            raise TypeError(f"Selected artifact has ({pt},{sf}); expected {expect}")
        path = desc.get("path")
        if not path:
            raise FileNotFoundError("Selected artifact has no storage path")
        ser = self._serializers.get(pt, sf) if (pt and sf) else None
        payload = ser.load(path) if ser else read_array_fits(path)
        return LoadResult(data=payload.get("data"), header=payload.get("header", {}), row=row, description=desc)
