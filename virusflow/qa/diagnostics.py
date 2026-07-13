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

def _num(meta: Dict, *keys: str) -> Optional[float]:
    for k in keys:
        try:
            if k in meta and meta[k] is not None:
                v = float(meta[k])
                if v == v:  # not NaN
                    return v
        except Exception:
            continue
    return None


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


def _eval_master_dark(meta: Dict) -> Tuple[QAResult, str]:
    """Evaluate master_dark QA based on fraction of masked/bad pixels.

    Uses meta['bad_fraction'] produced by algorithms.dark.step_dark.
    Policy:
      - pass:     bad_fraction < 0.02 ( <2% )
      - marginal: 0.02 <= bad_fraction < 0.05
      - fail:     bad_fraction >= 0.05 or missing/NaN
    """
    bf = _num(meta, "bad_fraction")
    metrics = dict(meta or {})
    if bf is None:
        return QAResult(status="fail", metrics=metrics, notes="missing bad_fraction"), "missing bad_fraction"
    if bf < 0.02:
        return QAResult(status="pass", metrics=metrics, notes=f"bad_fraction {bf:.4f} < 0.02"), "ok"
    if bf < 0.05:
        return QAResult(status="marginal", metrics=metrics, notes=f"bad_fraction {bf:.4f} in [0.02,0.05)"), "marginal"
    return QAResult(status="fail", metrics=metrics, notes=f"bad_fraction {bf:.4f} >= 0.05"), "high bad fraction"


def _eval_master_flat(meta: Dict) -> Tuple[QAResult, str]:
    """Evaluate master_flat QA based on fraction of masked/bad pixels.

    Uses meta['bad_fraction'] produced by algorithms.flat.step_flt.
    Policy (flats often show more cosmetic features):
      - pass:     bad_fraction < 0.05 ( <5% )
      - marginal: 0.05 <= bad_fraction < 0.10
      - fail:     bad_fraction >= 0.10 or missing/NaN
    """
    bf = _num(meta, "bad_fraction")
    metrics = dict(meta or {})
    if bf is None:
        return QAResult(status="fail", metrics=metrics, notes="missing bad_fraction"), "missing bad_fraction"
    if bf < 0.05:
        return QAResult(status="pass", metrics=metrics, notes=f"bad_fraction {bf:.4f} < 0.05"), "ok"
    if bf < 0.10:
        return QAResult(status="marginal", metrics=metrics, notes=f"bad_fraction {bf:.4f} in [0.05,0.10)"), "marginal"
    return QAResult(status="fail", metrics=metrics, notes=f"bad_fraction {bf:.4f} >= 0.10"), "high bad fraction"


essential_cmp_keys = ("bad_fraction_mask", "bad_fraction")

def _eval_master_cmp(meta: Dict) -> Tuple[QAResult, str]:
    """Evaluate master_cmp QA using fraction of pixels flagged by union mask (if any).

    algorithms.cmp.step_cmp may report 'bad_fraction_mask'. Fall back to 'bad_fraction' if present.
    Policy (CMP often has a few repaired columns acceptable):
      - pass:     frac < 0.10 ( <10% )
      - marginal: 0.10 <= frac < 0.20
      - fail:     frac >= 0.20 or missing/NaN
    """
    bf = _num(meta, *essential_cmp_keys)
    metrics = dict(meta or {})
    if bf is None:
        return QAResult(status="unknown", metrics=metrics, notes="no mask fraction available"), "no metric"
    if bf < 0.10:
        return QAResult(status="pass", metrics=metrics, notes=f"bad_fraction_mask {bf:.4f} < 0.10"), "ok"
    if bf < 0.20:
        return QAResult(status="marginal", metrics=metrics, notes=f"bad_fraction_mask {bf:.4f} in [0.10,0.20)"), "marginal"
    return QAResult(status="fail", metrics=metrics, notes=f"bad_fraction_mask {bf:.4f} >= 0.20"), "high bad fraction"


def _eval_wave(meta: Dict) -> Tuple[QAResult, str]:
    """Evaluate wave solution quality using per-row RMS residuals.

    Expects algorithms.wave.step_wave to provide 'rms_rows' (1D array). We compute the
    median of finite, positive residuals and apply thresholds consistent with
    reference summarize_amp_metrics (warn=0.2, fail=0.5 in wavelength units).
      - pass:     median_rms < 0.2
      - marginal: 0.2 <= median_rms < 0.5
      - fail:     median_rms >= 0.5 or missing/empty
    """
    import numpy as _np
    rms_rows = meta.get("rms_rows")
    metrics = dict(meta or {})
    med = None
    try:
        if rms_rows is not None:
            arr = _np.asarray(rms_rows, dtype=float).ravel()
            fin = arr[_np.isfinite(arr) & (arr > 0)]
            if fin.size:
                med = float(_np.nanmedian(fin))
                metrics["rms_median"] = med
    except Exception:
        med = None
    if med is None:
        return QAResult(status="fail", metrics=metrics, notes="missing rms_rows"), "missing rms"
    if med < 0.2:
        return QAResult(status="pass", metrics=metrics, notes=f"median RMS {med:.4f} < 0.2"), "ok"
    if med < 0.5:
        return QAResult(status="marginal", metrics=metrics, notes=f"median RMS {med:.4f} in [0.2,0.5)"), "marginal"
    return QAResult(status="fail", metrics=metrics, notes=f"median RMS {med:.4f} >= 0.5"), "high rms"


def _eval_trace(meta: Dict) -> Tuple[QAResult, str]:
    """Evaluate trace solution quality using per-fiber RMS metrics when available.

    Expects algorithms.trace.step_trace to provide 'rms_fibers' (preferred) or
    'trace_rms_per_fiber'. We compute the median of finite, positive RMS values
    and apply thresholds consistent with reference defaults (warn=0.2, fail=0.5):
      - pass:     median_rms < 0.2
      - marginal: 0.2 <= median_rms < 0.5
      - fail:     median_rms >= 0.5 or missing/empty
    """
    import numpy as _np
    metrics = dict(meta or {})
    arr = None
    try:
        if "rms_fibers" in meta and meta["rms_fibers"] is not None:
            arr = _np.asarray(meta["rms_fibers"], dtype=float).ravel()
        elif "trace_rms_per_fiber" in meta and meta["trace_rms_per_fiber"] is not None:
            arr = _np.asarray(meta["trace_rms_per_fiber"], dtype=float).ravel()
    except Exception:
        arr = None
    if arr is None:
        return QAResult(status="unknown", metrics=metrics, notes="missing per-fiber RMS"), "no metric"
    fin = arr[_np.isfinite(arr) & (arr > 0)]
    if fin.size == 0:
        return QAResult(status="unknown", metrics=metrics, notes="no finite per-fiber RMS"), "no data"
    med = float(_np.nanmedian(fin))
    metrics["rms_median"] = med
    if med < 0.2:
        return QAResult(status="pass", metrics=metrics, notes=f"median RMS {med:.4f} < 0.2"), "ok"
    if med < 0.5:
        return QAResult(status="marginal", metrics=metrics, notes=f"median RMS {med:.4f} in [0.2,0.5)"), "marginal"
    return QAResult(status="fail", metrics=metrics, notes=f"median RMS {med:.4f} >= 0.5"), "high rms"


# Register default evaluators
Diagnostics.register("master_bias", _eval_master_bias)
Diagnostics.register("master_dark", _eval_master_dark)
Diagnostics.register("master_flat", _eval_master_flat)
Diagnostics.register("master_cmp", _eval_master_cmp)
Diagnostics.register("wave", _eval_wave)
Diagnostics.register("trace", _eval_trace)


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
