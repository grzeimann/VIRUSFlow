from __future__ import annotations

from typing import Iterable, List, Protocol, Dict, Any
from pathlib import Path

from ..artifacts.models import Artifact, StorageRef, Scope, Provenance
from ..artifacts.requests import ArtifactRequest, LogicalComponent
from ..artifacts.service import ArtifactService
from ..contracts.artifact import (
    ArtifactContract,
    ArtifactContractSpec,
    MasterBiasContract,
    MasterDarkContract,
    MasterFlatContract,
    MasterCmpContract,
    MasterTwiContract,
    TraceContract,
    WaveContract,
)
from ..persistence.policy import PersistencePolicy, RepresentationDecision
from ..artifacts.serializers import Serializer
from ..artifacts.io_fits import write_array_fits
from .context import PublicationContext


class PublicationService(Protocol):
    """Protocol for publication orchestration.

    Publication coordinates:
    - ArtifactContract validation (format-agnostic)
    - PersistencePolicy decisions (format/backends/sidecars/layout)
    - Serializer/backend execution
    - ArtifactService registration producing durable Artifacts

    Publication does not evaluate QA.
    """

    def publish(self, requests: Iterable[ArtifactRequest], context: PublicationContext) -> List[Artifact]:  # pragma: no cover - Protocol signature
        ...


# ---------------- Implementation (Section 2) ----------------

_KIND_TO_CONTRACT: Dict[str, ArtifactContract] = {
    "master_bias": MasterBiasContract(),
    "master_dark": MasterDarkContract(),
    "master_flat": MasterFlatContract(),
    "master_cmp": MasterCmpContract(),
    "master_twi": MasterTwiContract(),
    "trace": TraceContract(),
    "wave": WaveContract(),
}


def _get_contract(kind: str) -> ArtifactContract:
    k = (kind or "").strip().lower()
    if k in _KIND_TO_CONTRACT:
        return _KIND_TO_CONTRACT[k]
    # Fallback minimal contract: accept a single component named 'data'
    class _Adhoc(ArtifactContract):
        def spec(self) -> ArtifactContractSpec:  # type: ignore[override]
            return ArtifactContractSpec(kind=k, components=[], optional_components=[], summaries=[], required_metadata=[])
    return _Adhoc()


class DefaultPublicationService:
    """Concrete Publication implementation coordinating policy, persistence, and registration.

    Notes:
    - QA is not invoked here by design.
    - This Section 2 implementation supports single-component array artifacts (array2d/array1d),
      which matches current calibration products. Multi-component bundling can be added in later sections.
    """

    def __init__(self, *, svc: ArtifactService, policy: PersistencePolicy, base_dir: str) -> None:
        self.svc = svc
        self.policy = policy
        self.base_dir = str(base_dir)

    def publish(self, requests: Iterable[ArtifactRequest], context: PublicationContext) -> List[Artifact]:
        results: List[Artifact] = []
        for req in (list(requests) or []):
            art = self._publish_one(req, context)
            results.append(art)
        return results

    # ---- internals ----
    def _publish_one(self, req: ArtifactRequest, ctx: PublicationContext) -> Artifact:
        spec = _get_contract(req.kind).spec()
        self._validate_against_contract(req, spec)
        return self.svc.persist_request(req, context=ctx, policy=self.policy, base_dir=self.base_dir)

    def _payload_type_for(self, model_type: str) -> str:
        mt = (model_type or "").strip().lower()
        if mt in ("array2d", "array1d"):
            return "array"
        if mt == "image":
            return "image"
        if mt == "table":
            return "table"
        if mt == "scalar":
            return "scalar"
        return "collection"

    def _select_primary_component(self, req: ArtifactRequest, spec: ArtifactContractSpec) -> tuple[str, LogicalComponent]:
        # Prefer the first required component in the contract
        names_req = [c.name for c in (spec.components or []) if c.required]
        for nm in names_req:
            comp = req.get_component(nm)
            if comp is not None:
                return nm, comp
        # Fallback: first component provided
        for nm in req.component_names():
            comp = req.get_component(nm)
            if comp is not None:
                return nm, comp
        raise ValueError("ArtifactRequest has no components to persist")

    def _validate_against_contract(self, req: ArtifactRequest, spec: ArtifactContractSpec) -> None:
        # Ensure required components exist and model_types match when provided
        missing = []
        for c in (spec.components or []):
            if c.required and req.get_component(c.name) is None:
                missing.append(c.name)
        if missing:
            raise ValueError(f"Missing required components for kind={req.kind}: {', '.join(missing)}")
        # Model type checks (best-effort)
        for c in (spec.components or []):
            rc = req.get_component(c.name)
            if rc is not None and c.model_type and str(rc.model_type).lower() != str(c.model_type).lower():
                raise ValueError(f"Component '{c.name}' has model_type={rc.model_type}, expected {c.model_type}")

    def _filename_tokens(self, *, req: ArtifactRequest, ctx: PublicationContext) -> Dict[str, str]:
        # Minimal tokens; tasks may pass more later (zipcode, dates)
        toks: Dict[str, str] = {
            "kind": (req.kind or "artifact"),
            "task": (ctx.task_name or "task"),
            "tver": (ctx.task_version or "v1"),
        }
        return toks
