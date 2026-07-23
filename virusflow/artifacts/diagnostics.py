from __future__ import annotations

from typing import Optional, Dict
import threading

from .registry_adapter import RegistryAdapter
from .models import DiagnosticRecord

# ---------------- Live QA tallies (process-local) ----------------
# Thread-safe counters updated whenever evaluate_and_save runs. Used by
# PlanningExecutor's live table footer to display QA warn/fail counts.
_QA_LOCK = threading.Lock()
_QA_TALLIES: Dict[str, Dict[str, int]] = {"__all__": {"pass": 0, "warn": 0, "fail": 0}}


def _qa_inc(kind: str, status: Optional[str]) -> None:
    if not status:
        return
    s = str(status).lower()
    if s not in ("pass", "warn", "fail"):
        return
    k = (kind or "").strip().lower() or "unknown"
    with _QA_LOCK:
        _QA_TALLIES.setdefault(k, {"pass": 0, "warn": 0, "fail": 0})
        _QA_TALLIES[k][s] = _QA_TALLIES[k].get(s, 0) + 1
        _QA_TALLIES["__all__"][s] = _QA_TALLIES["__all__"].get(s, 0) + 1


def qa_tallies_snapshot() -> Dict[str, Dict[str, int]]:
    with _QA_LOCK:
        # Return a shallow copy to avoid external mutation
        return {k: dict(v) for k, v in _QA_TALLIES.items()}


class DiagnosticsFacade:
    """Diagnostics facade backed by the YAML-driven QA engine.

    - Evaluate QA decisions using virusflow.qa.engine and persist via RegistryAdapter.
    - Provide small helpers for callers (e.g., should_block) without exposing engine internals.
    """

    def __init__(self, adapter: RegistryAdapter, yaml_path: Optional[str] = None) -> None:
        self.adapter = adapter
        # Lazy import to avoid heavy YAML cost at import time
        self._yaml_path = yaml_path
        self._engine = None

    def _engine_or_load(self):
        if self._engine is None:
            from ..qa.engine import QAEngine
            self._engine = QAEngine(self._yaml_path)
        return self._engine

    def get(self, artifact_id: int) -> Optional[dict]:
        return self.adapter.get_diagnostics(int(artifact_id))

    def set(self, artifact_id: int, status: str, metrics: Optional[Dict] = None) -> None:
        self.adapter.set_diagnostics(int(artifact_id), status=status, metrics=metrics)

    def evaluate_and_save(self, *, artifact_id: int, kind: str, meta: Optional[Dict] = None) -> Optional[str]:
        """Evaluate diagnostics for an artifact kind using algo metadata and persist.

        Returns the status string (e.g., pass/warn/fail). Swallows errors for task safety.
        """
        try:
            import os as _os
            import numpy as _np
            from ..core.algo_result import ensure_algo_result  # lightweight, no heavy deps
            eng = self._engine_or_load()
            # Prepare meta from flexible inputs (dict or AlgoResult) and, for certain kinds,
            # enrich with computed metrics (e.g., p95 from payload)
            ar = ensure_algo_result(meta or {}, kind=kind)
            m = dict(ar.as_meta() or {})
            k = (kind or "").strip().lower()
            # Generic component payload exposure for reducers: load primary array as meta.component.data
            try:
                row = self.adapter.get_row(int(artifact_id))
                path = row.get("path") if isinstance(row, dict) else None
                if path:
                    from .serializers import array_fits as _array_fits  # type: ignore
                    payload = _array_fits.load(str(path))
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if data is not None:
                        arr = _np.asarray(data, dtype=float)
                        comp = dict(m.get("component") or {})
                        comp.setdefault("data", arr)
                        m["component"] = comp
                        # Backward-compatible enrichment for legacy configs expecting meta.p95 on some kinds
                        if k in {"master_flat", "master_cmp", "master_sci"} and (m.get("p95") is None):
                            with _np.errstate(all="ignore"):
                                val = float(_np.nanpercentile(arr, 95)) if arr.size else None
                            if val is not None and val == val:
                                m["p95"] = val
            except Exception:
                # Best-effort enrichment; ignore on failure
                pass
            decision = eng.evaluate(kind=kind, meta=m)
            # persist: include messages as a special key in metrics for visibility in CLI/DB
            metrics = dict(decision.metrics or {})
            if getattr(decision, "messages", None):
                try:
                    metrics["__messages"] = list(decision.messages)
                except Exception:
                    pass
            self.adapter.set_diagnostics(int(artifact_id), status=decision.status, metrics=metrics)
            # update live tallies
            _qa_inc(kind, decision.status)
            # Optional verbose print for debugging misconfigured rules
            try:
                if _os.environ.get("VF_QA_VERBOSE", "0") == "1":
                    # Print a concise line with key metrics (limit size) and messages
                    kv = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:6])
                    msgs = "; ".join(decision.messages or [])
                    print(f"[QA] kind={kind} status={decision.status} metrics: {kv} messages: {msgs}")
            except Exception:
                pass
            return decision.status
        except Exception:
            return None

    def should_block(self, *, kind: str, status: Optional[str]) -> bool:
        try:
            eng = self._engine_or_load()
            pol = eng.policy_for(kind)
            return bool(pol == "hard" and (status or "") == "fail")
        except Exception:
            return False
