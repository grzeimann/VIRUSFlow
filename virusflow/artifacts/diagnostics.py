from __future__ import annotations

"""Process-local QA tallies used by graph progress reporting."""

import threading
from typing import Dict, Optional


_QA_LOCK = threading.Lock()
_QA_TALLIES: Dict[str, Dict[str, int]] = {
    "__all__": {"pass": 0, "warn": 0, "fail": 0}
}


def _qa_inc(kind: str, status: Optional[str]) -> None:
    if not status:
        return
    normalized = str(status).lower()
    if normalized not in {"pass", "warn", "fail"}:
        return
    artifact_kind = (kind or "").strip().lower() or "unknown"
    with _QA_LOCK:
        _QA_TALLIES.setdefault(artifact_kind, {"pass": 0, "warn": 0, "fail": 0})
        _QA_TALLIES[artifact_kind][normalized] += 1
        _QA_TALLIES["__all__"][normalized] += 1


def qa_tallies_snapshot() -> Dict[str, Dict[str, int]]:
    with _QA_LOCK:
        return {kind: dict(values) for kind, values in _QA_TALLIES.items()}
