from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional
import json
import numpy as np

from .models import QAPacket
from .diagnostics import evaluate_and_save
from .plot_utils import plot_identify_arc_summary
from .build_qa import build_qa


def build_wave_qa(
    *,
    qa_dir: Path,
    algo_meta: Dict,
    qa_bundle: Optional[Dict],
    identifiers: Dict[str, Optional[str]],
    artifact_id: Optional[int] = None,
    db_path: Optional[str] = None,
    always_plot: bool = True,
) -> QAPacket:
    """Backward-compatible wrapper that delegates to build_qa(kind='wave')."""
    return build_qa(
        kind="wave",
        qa_dir=qa_dir,
        algo_meta=algo_meta,
        qa_bundle=qa_bundle,
        identifiers=identifiers,
        artifact_id=artifact_id,
        db_path=db_path,
        always_plot=always_plot,
    )


def _write_qa_packet(qa_dir: Path, packet: QAPacket) -> Path:
    # Kept for backward import compatibility if used externally
    p = Path(qa_dir) / "wave_qa.json"
    with p.open("w") as f:
        json.dump(packet.to_json_dict(), f, sort_keys=True, separators=(",", ":"))
    return p


def _compact_blobs(qa_bundle: Optional[Dict]) -> Dict[str, object]:
    # Unused by the wrapper now; retain for external imports if any
    out: Dict[str, object] = {}
    if not isinstance(qa_bundle, dict):
        return out
    best = qa_bundle.get("best") or {}
    try:
        out["nmatch"] = int(best.get("nmatch", 0)) if isinstance(best, dict) else 0
    except Exception:
        out["nmatch"] = 0
    try:
        out["rms"] = float(best.get("rms", np.nan)) if isinstance(best, dict) else float("nan")
    except Exception:
        out["rms"] = float("nan")
    try:
        dx = np.asarray(best.get("detected_x", []), dtype=float)
        if dx.size:
            out["detected_x_head"] = dx[:10].astype(float).tolist()
    except Exception:
        pass
    return out


def _maybe_downsample_rms(rms_rows):
    try:
        arr = np.asarray(rms_rows, dtype=float).ravel()
        if arr.size > 4096:
            import numpy as _np
            idx = _np.linspace(0, arr.size - 1, 1024).astype(int)
            return arr[idx].astype(float).tolist()
        return arr.astype(float).tolist()
    except Exception:
        return None
