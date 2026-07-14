from __future__ import annotations

from typing import Optional, Dict

from .registry_adapter import RegistryAdapter
from .models import DiagnosticRecord


class DiagnosticsFacade:
    """First-class diagnostics facade for the artifact subsystem.

    Stores/retrieves compact QA status and metrics via the registry adapter.
    Also provides a convenience method to evaluate-and-save using the shared
    QA evaluators under virusflow.qa.diagnostics.
    """

    def __init__(self, adapter: RegistryAdapter) -> None:
        self.adapter = adapter

    def get(self, artifact_id: int) -> Optional[dict]:
        return self.adapter.get_diagnostics(int(artifact_id))

    def set(self, artifact_id: int, status: str, metrics: Optional[Dict] = None) -> None:
        self.adapter.set_diagnostics(int(artifact_id), status=status, metrics=metrics)

    def evaluate_and_save(self, *, artifact_id: int, kind: str, meta: Optional[Dict] = None) -> Optional[str]:
        """Evaluate diagnostics for an artifact kind using algo metadata and persist.

        Returns the status string (e.g., pass/marginal/fail) when available.
        Never raises: evaluation/persistence errors are swallowed for task safety.
        """
        try:
            from ..qa import diagnostics as qa_diag
            status = qa_diag.evaluate_and_save(artifact_id=int(artifact_id), kind=kind, meta=dict(meta or {}), db_path=self.adapter.db_path)
            return status
        except Exception:
            return None
