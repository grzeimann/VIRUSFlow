from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from .artifacts import ProvenanceInfo
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
    m = hashlib.sha256()
    for k in sorted(params.keys()):
        v = params[k]
        m.update(str(k).encode())
        m.update(b"=")
        m.update(str(v).encode())
        m.update(b";")
    return m.hexdigest()[:16]


def build_provenance(algorithm: str, params: Dict[str, Any], parents: Iterable[str] | None = None) -> ProvenanceInfo:
    return ProvenanceInfo(
        software_version=get_version(),
        git_commit=current_git_commit(),
        algorithm=algorithm,
        parameters_hash=param_hash(params),
        parents=list(parents) if parents else [],
        created_at=datetime.utcnow(),
    )
