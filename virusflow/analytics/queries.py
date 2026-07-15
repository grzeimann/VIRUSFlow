from __future__ import annotations

"""
Registry-driven query helpers for analytics.

This module provides thin, typed helpers to discover artifacts and QA
information via the ArtifactService and underlying registry adapter.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator, Optional, List, Dict, Any

from ..artifacts.service import ArtifactService
from ..core.identity import ZipCode, parse_zipcode_key


@dataclass(frozen=True)
class ArtifactRow:
    id: int
    kind: str
    path: str | None
    zipcode: ZipCode | None
    created_at: Optional[datetime]
    qa_status: Optional[str]


def list_artifacts(
    *,
    svc: ArtifactService,
    kind: str,
    zipcode: ZipCode | None = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return raw registry rows for artifacts, filtered by kind/zipcode.

    Uses the RegistryAdapter.find/list under ArtifactService.
    """
    # Prefer adapter.find as a stable path
    rows = svc.adapter.find(kind=kind, zipcode=zipcode, at_time=None, limit=limit)
    return rows or []


def load_array(
    *, svc: ArtifactService, row: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Attempt to load an array payload for array/fits artifacts.

    Returns dict with keys {data, header} or None if not an array/fits artifact or load fails.
    """
    try:
        path = row.get("path")
        if not path:
            return None
        # Use serializers assuming array/fits; tolerant to failures
        ser = svc.serializers.get("array", "fits")
        if not ser:
            return None
        return ser.load(str(path))
    except Exception:
        return None


def get_qa(
    *, svc: ArtifactService, artifact_id: int
) -> Optional[Dict[str, Any]]:
    """Fetch QA diagnostics for an artifact id via the adapter."""
    try:
        return svc.adapter.get_diagnostics(int(artifact_id))
    except Exception:
        return None
