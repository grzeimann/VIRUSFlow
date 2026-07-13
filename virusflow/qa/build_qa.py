from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional
import json
import numpy as np

from .models import QAPacket
from .diagnostics import evaluate_and_save
from .plot_utils import plot_identify_arc_summary, plot_trace_overlay


def build_qa(
    *,
    kind: str,
    qa_dir: Path,
    algo_meta: Dict,
    qa_bundle: Optional[Dict],
    identifiers: Dict[str, Optional[str]],
    artifact_id: Optional[int] = None,
    db_path: Optional[str] = None,
    always_plot: bool = True,
) -> QAPacket:
    """Create and persist QA for a given artifact kind (e.g., 'wave' or 'trace').

    This generalizes the previous build_wave_qa utility. Responsibilities:
    - Compute lightweight metrics from algo_meta for the given kind.
    - Optionally generate plots using qa_bundle data.
    - Persist a QAPacket JSON for later collection, and optionally write to DB.
    """
    k = str(kind or "").strip().lower()
    if k not in {"wave", "trace"}:
        raise ValueError(f"Unsupported QA kind: {kind}")

    qa_dir = Path(qa_dir)
    qa_dir.mkdir(parents=True, exist_ok=True)

    metrics: Dict[str, float] = {}
    plots: Dict[str, str] = {}

    if k == "wave":
        # Metrics from per-row RMS
        rms_rows = algo_meta.get("rms_rows")
        if rms_rows is not None:
            arr = np.asarray(rms_rows, dtype=float).ravel()
            fin = arr[np.isfinite(arr) & (arr > 0)]
            if fin.size:
                metrics["rms_median"] = float(np.nanmedian(fin))
                metrics["rms_p90"] = float(np.nanpercentile(fin, 90))
                metrics["seed_rows"] = int(fin.size)
        # Plot: arc identification summary from best row, if provided
        if always_plot and isinstance(qa_bundle, dict):
            ref_profile = qa_bundle.get("ref_profile")
            best = qa_bundle.get("best")
            try:
                p = plot_identify_arc_summary(qa_dir, ref_profile, best, filename="identify_arc_summary.png")
                if p is not None:
                    plots["identify_arc_summary"] = str(p.name)
            except Exception:
                pass
    elif k == "trace":
        # Metrics from per-fiber RMS
        arr = None
        if algo_meta.get("rms_fibers") is not None:
            arr = np.asarray(algo_meta.get("rms_fibers"), dtype=float).ravel()
        elif algo_meta.get("trace_rms_per_fiber") is not None:
            arr = np.asarray(algo_meta.get("trace_rms_per_fiber"), dtype=float).ravel()
        if arr is not None and arr.size:
            fin = arr[np.isfinite(arr) & (arr > 0)]
            if fin.size:
                metrics["rms_median"] = float(np.nanmedian(fin))
                metrics["rms_p90"] = float(np.nanpercentile(fin, 90))
                metrics["n_fibers"] = int(fin.size)
        # Plot: overlay sampled chunks vs full trace if provided
        if always_plot and isinstance(qa_bundle, dict):
            try:
                p = plot_trace_overlay(
                    qa_dir,
                    qa_bundle.get("xchunks"),
                    qa_bundle.get("trace_chunks"),
                    qa_bundle.get("trace"),
                    filename="trace_chunks.png",
                )
                if p is not None:
                    plots["trace_overlay"] = str(p.name)
            except Exception:
                pass

    # Evaluate diagnostics and optionally persist in DB
    status: Optional[str] = None
    if db_path is not None:
        try:
            status = evaluate_and_save(
                artifact_id or -1,
                kind=k,
                meta={**(algo_meta or {}), **metrics},
                db_path=str(db_path),
            )
        except Exception:
            status = None

    # Build compact blobs/meta
    blobs: Dict[str, object] = {}
    meta_extra: Dict[str, object] = {}
    if k == "wave":
        best = (qa_bundle or {}).get("best") if isinstance(qa_bundle, dict) else None
        try:
            blobs["nmatch"] = int((best or {}).get("nmatch", 0))
        except Exception:
            blobs["nmatch"] = 0
        try:
            blobs["rms"] = float((best or {}).get("rms", np.nan))
        except Exception:
            blobs["rms"] = float("nan")
        # Possibly downsample rms_rows for compactness
        try:
            rms_rows = algo_meta.get("rms_rows")
            if rms_rows is not None:
                arr = np.asarray(rms_rows, dtype=float).ravel()
                if arr.size > 4096:
                    idx = np.linspace(0, arr.size - 1, 1024).astype(int)
                    meta_extra["rms_rows"] = arr[idx].astype(float).tolist()
                else:
                    meta_extra["rms_rows"] = arr.astype(float).tolist()
        except Exception:
            pass
    elif k == "trace":
        # Include a head of RMS values for quick inspection
        try:
            arr = None
            if algo_meta.get("rms_fibers") is not None:
                arr = np.asarray(algo_meta.get("rms_fibers"), dtype=float).ravel()
            elif algo_meta.get("trace_rms_per_fiber") is not None:
                arr = np.asarray(algo_meta.get("trace_rms_per_fiber"), dtype=float).ravel()
            if arr is not None and arr.size:
                blobs["rms_head"] = arr[np.isfinite(arr)][:16].astype(float).tolist()
        except Exception:
            pass

    pkt = QAPacket(
        kind=k,
        artifact_id=artifact_id,
        amp_id=identifiers.get("amp_id") if identifiers else None,
        run_id=identifiers.get("run_id") if identifiers else None,
        obs_time=identifiers.get("obs_time") if identifiers else None,
        zip_code=identifiers.get("zip_code") if identifiers else None,
        status=status,
        metrics=metrics,
        notes=None,
        plots=plots,
        blobs=blobs,
        meta={
            "failure_reason": (algo_meta or {}).get("failure_reason"),
            **meta_extra,
        },
    )

    _write_qa_packet(qa_dir, pkt)
    return pkt


def _write_qa_packet(qa_dir: Path, packet: QAPacket) -> Path:
    p = Path(qa_dir) / f"{packet.kind}_qa.json"
    with p.open("w") as f:
        json.dump(packet.to_json_dict(), f, sort_keys=True, separators=(",", ":"))
    return p
