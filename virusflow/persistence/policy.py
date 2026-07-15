from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Protocol
from pathlib import Path


@dataclass(frozen=True)
class RepresentationDecision:
    """A format-agnostic decision bundle returned by a PersistencePolicy.

    Contains serializer/backend identifiers and representation choices. This is a
    placeholder for Section 1 to define the types; behavior will arrive in
    Section 2.
    """

    storage_format: str  # e.g., "fits", "zarr"
    serializer: str      # logical serializer id, e.g., "array", "image"
    uri_scheme: str      # e.g., "file", "s3"


class PersistencePolicy(Protocol):
    """Protocol for physical representation decisions.

    Owns all storage representation choices. No scientific logic here.
    """

    def decide(self, *, artifact_kind: str, component_name: str, model_type: str) -> RepresentationDecision:  # pragma: no cover - Protocol
        ...

    def filename(self, *, artifact_kind: str, component_name: str, base_dir: str, tokens: Dict[str, str]) -> str:  # pragma: no cover - Protocol
        ...

    def extname_for(self, *, artifact_kind: str, component_name: str) -> str | None:  # pragma: no cover - Protocol
        """Optional hint for container-specific component naming (e.g., FITS EXTNAME).
        Returns a string like 'FLATMASK' or None if no special handling is required.
        """
        ...


class DefaultPersistencePolicy:
    """Simple policy that maps array-like components to (array,fits) and builds filenames.

    This implementation centralizes all representation choices and can be swapped
    in tests to prove backend replaceability.
    """

    def __init__(self, *, default_backend: str = "fs") -> None:
        self.backend = default_backend

    def decide(self, *, artifact_kind: str, component_name: str, model_type: str) -> RepresentationDecision:
        mt = (model_type or "").strip().lower()
        if mt in ("array2d", "array1d"):
            return RepresentationDecision(storage_format="fits", serializer="array", uri_scheme=self.backend)
        # Future: other mappings (image/png, table/parquet, etc.)
        return RepresentationDecision(storage_format="fits", serializer="array", uri_scheme=self.backend)

    def filename(self, *, artifact_kind: str, component_name: str, base_dir: str, tokens: Dict[str, str]) -> str:
        # Filename scheme: <base>/<kind>/<task>-<tver>/<kind>__<component>.fits
        base = Path(base_dir)
        subdir = base / (tokens.get("kind") or artifact_kind) / f"{tokens.get('task','task')}-{tokens.get('tver','v1')}"
        subdir.mkdir(parents=True, exist_ok=True)
        name = f"{artifact_kind}__{component_name}.fits"
        return str(subdir / name)
