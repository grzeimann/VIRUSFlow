from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from .. import get_version


def current_git_commit() -> Optional[str]:
    try:
        commit = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
        return commit
    except Exception:
        return None


def param_hash(params: Dict[str, Any]) -> str:
    """Canonical JSON parameter hashing (stable across Python versions).

    - Serialize with json.dumps(sort_keys=True, separators=(",", ":"))
    - Hash with SHA-256 and return a short prefix for readability.
    """
    import json

    payload = json.dumps(params or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return h[:16]


def build_provenance(
    algorithm: str,
    params: Dict[str, Any],
    parents: Iterable[str] | None = None,
    *,
    raw_parents: Iterable[str] | None = None,
    raw_catalog: str | None = None,
) -> Dict[str, Any]:
    """Build a plain dict provenance record for registry/database.save_artifact.

    Located under virusflow.artifacts to fully decouple from legacy core.* modules.
    """
    return {
        "software_version": get_version(),
        "git_commit": current_git_commit(),
        "algorithm": algorithm,
        "parameters_hash": param_hash(params),
        "parents": list(parents) if parents else [],
        "raw_parents": list(raw_parents) if raw_parents else [],
        "raw_catalog": raw_catalog,
        "created_at": datetime.utcnow(),
    }
