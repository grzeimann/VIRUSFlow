from __future__ import annotations

from typing import Optional, Dict

from .registry_adapter import RegistryAdapter
from .models import DiagnosticRecord


class DiagnosticsFacade:
    """First-class diagnostics facade for the artifact subsystem.

    Stores/retrieves compact QA status and metrics via the registry adapter.
    """

    def __init__(self, adapter: RegistryAdapter) -> None:
        self.adapter = adapter

    def get(self, artifact_id: int) -> Optional[dict]:
        return self.adapter.get_diagnostics(int(artifact_id))

    def set(self, artifact_id: int, status: str, metrics: Optional[Dict] = None) -> None:
        self.adapter.set_diagnostics(int(artifact_id), status=status, metrics=metrics)
