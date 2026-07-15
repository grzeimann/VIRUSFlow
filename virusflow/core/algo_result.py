from __future__ import annotations

"""
Unified algorithm return contract (lightweight).

This module defines AlgoResult, a small, optional container that algorithms may
return to structure their outputs without constraining scientific freedom. It
unifies how Tasks, QA (DiagnosticsFacade), and Analytics can interact with
algorithm returns while remaining fully backward‑compatible with plain dicts.

Usage options (non‑breaking):
- Algorithms may keep returning dicts as today. Tasks/QA continue to work.
- Algorithms may start returning AlgoResult. Tasks/QA will transparently
  consume the standardized meta via .as_meta().

Fields
- kind: computation identity (algorithm/result family), e.g., 'bias', 'dark', 'flat', 'trace', 'wave' — not the eventual artifact kind.
- meta: primary metadata/measurements dict (free‑form).
- scalars: optional flat scalar measurements to merge into meta for QA.
- arrays: optional named arrays (e.g., 'rms_fibers', 'rms_rows'). Analytics may
  choose to persist/consume these via tasks or post‑run studies.
- version: algorithm version string.
- messages: optional human‑readable notes from the algorithm.
- timings: optional coarse timing breakdown.

Helpers
- ensure_algo_result(obj, kind=None): return obj if already AlgoResult; if obj is
  a dict, wrap it as AlgoResult(meta=obj); otherwise, create an empty AlgoResult
  with best‑effort extraction.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List


@dataclass(frozen=True)
class AlgoResult:
    kind: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    arrays: Dict[str, Any] = field(default_factory=dict)
    scalars: Dict[str, Any] = field(default_factory=dict)
    version: Optional[str] = None
    messages: List[str] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)

    def as_meta(self) -> Dict[str, Any]:
        """Return a flattened, QA‑ready metadata dict.

        Merge meta + scalars and include optional version/kind under reserved keys
        to avoid collisions with scientific metric names.
        """
        out: Dict[str, Any] = {}
        try:
            out.update(self.meta or {})
        except Exception:
            pass
        try:
            # Scalars are merged on top so rules can reference them directly
            for k, v in (self.scalars or {}).items():
                out.setdefault(k, v)
        except Exception:
            pass
        # Include informative reserved keys using a leading underscore to reduce
        # collision risk with domain metrics. These are optional for QA rules.
        if self.version:
            out.setdefault("_algo_version", self.version)
        if self.kind:
            out.setdefault("_algo_kind", self.kind)
        # Optionally, expose shapes of known arrays for convenience
        try:
            shapes = {name: getattr(val, "shape", None) for name, val in (self.arrays or {}).items()}
            if any(v is not None for v in shapes.values()):
                out.setdefault("_arrays_shape", shapes)
        except Exception:
            pass
        return out

    def get_array(self, name: str):
        return (self.arrays or {}).get(name)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict for logging/debugging."""
        return {
            "kind": self.kind,
            "meta": dict(self.meta or {}),
            "scalars": dict(self.scalars or {}),
            "arrays": list((self.arrays or {}).keys()),
            "version": self.version,
            "messages": list(self.messages or []),
            "timings": dict(self.timings or {}),
        }


def ensure_algo_result(obj: Any, kind: Optional[str] = None) -> AlgoResult:
    """Coerce any object to an AlgoResult without breaking existing code.

    - If obj is already an AlgoResult, return it.
    - If obj is a dict, wrap it as AlgoResult(meta=obj).
    - Otherwise, attempt a best‑effort wrap by extracting common fields.
    """
    if isinstance(obj, AlgoResult):
        return obj
    if isinstance(obj, dict):
        # Heuristically separate arrays/scalars if present
        meta = dict(obj)
        arrays = {}
        scalars = {}
        try:
            for k, v in list(meta.items()):
                # Treat 1D/2D numpy arrays or large lists as arrays
                tname = type(v).__name__
                if tname in ("ndarray",):
                    arrays[k] = v
                elif isinstance(v, (list, tuple)) and (len(v) > 16):
                    arrays[k] = v
                elif isinstance(v, (int, float)):
                    scalars[k] = v
        except Exception:
            pass
        ver = None
        try:
            ver = obj.get("version") or obj.get("algo_version")
        except Exception:
            ver = None
        return AlgoResult(kind=kind, meta=meta, arrays=arrays, scalars=scalars, version=ver)
    # Fallback empty wrapper
    return AlgoResult(kind=kind, meta={})
