from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Callable

from ..registry import database as db


@dataclass
class QAResult:
    status: str  # e.g., pass/marginal/fail
    metrics: Dict[str, float] = field(default_factory=dict)
    notes: Optional[str] = None


class Diagnostics:
    """Diagnostics registry and evaluators for automatic QA.

    Provides a class-based architecture where each artifact kind maps to an
    evaluator callable that receives algorithm metadata and returns a QAResult
    and a short human-readable reason string.
    """

    _registry: Dict[str, Callable[[Dict], Tuple[QAResult, str]]] = {}

    @classmethod
    def register(cls, kind: str, fn: Callable[[Dict], Tuple[QAResult, str]]) -> None:
        cls._registry[str(kind).strip().lower()] = fn

    @classmethod
    def evaluate(cls, kind: str, meta: Dict) -> Tuple[QAResult, str]:
        k = (kind or "").strip().lower()
        fn = cls._registry.get(k)
        if fn is None:
            # Unknown kind: return 'unknown' status so callers may skip persistence
            return QAResult(status="unknown", metrics=dict(meta or {}), notes="no automatic diagnostics"), "no diagnostics rule"
        return fn(meta or {})


# ---------------- Default evaluators ----------------

def _eval_master_bias(meta: Dict) -> Tuple[QAResult, str]:
    """Evaluate master_bias QA based on readnoise thresholds.

    Policy (readnoise in e-):
      - pass:     1e-4 < rn < 4.5
      - marginal: 4.5 <= rn < 6.0
      - fail:     rn >= 6.0 or rn <= 1e-4 or missing/NaN
    """
    rn = None
    for key in ("readnoise", "bias_readnoise", "rn"):
        if key in meta and meta[key] is not None:
            try:
                rn = float(meta[key])
                break
            except Exception:
                pass
    metrics = dict(meta or {})
    if rn is None or not (rn == rn):  # NaN-safe
        return QAResult(status="fail", metrics=metrics, notes="missing or invalid readnoise"), "missing or invalid readnoise"
    if rn <= 1e-4:
        return QAResult(status="fail", metrics=metrics, notes=f"readnoise too small ({rn:.3f})"), "rn <= 1e-4"
    if rn < 4.5:
        return QAResult(status="pass", metrics=metrics, notes=f"readnoise {rn:.3f} within (1e-4, 4.5)") , "ok"
    if rn < 6.0:
        return QAResult(status="marginal", metrics=metrics, notes=f"readnoise {rn:.3f} in [4.5,6.0)") , "marginal"
    return QAResult(status="fail", metrics=metrics, notes=f"readnoise too large ({rn:.3f} >= 6.0)"), "rn >= 6.0"


# Register default evaluators
Diagnostics.register("master_bias", _eval_master_bias)


# ---------------- Persistence helpers ----------------

def evaluate_and_save(artifact_id: int, *, kind: str, meta: Dict, db_path: str) -> Optional[str]:
    """Evaluate diagnostics for an artifact and persist QA.

    Returns the status string (e.g., 'pass', 'marginal', 'fail'). For unknown kinds,
    returns 'unknown' and does not persist. Swallows errors by design.
    """
    try:
        qa, _reason = Diagnostics.evaluate(kind, meta)
        status = qa.status
        if status != "unknown":
            with db.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO qa_results(artifact_id, status, metrics_json) VALUES(?, ?, ?)\n                     ON CONFLICT(artifact_id) DO UPDATE SET status=excluded.status, metrics_json=excluded.metrics_json",
                    (int(artifact_id), str(status), json.dumps(qa.metrics, sort_keys=True, separators=(",", ":"))),
                )
        return status
    except Exception:
        return None
