from __future__ import annotations

"""
Instrument Health analytics study: post-run health indicators derived from existing
calibration artifacts. This study is registry-only and does not import algorithms/tasks.

Outputs (registered as first-class analytics artifacts):
- fiber_throughput_trend: time-series of per-artifact median flat intensity for a selection
  (proxy for fiber throughput drift). One figure per zipcode selection.
- bad_fiber_map: thumbnail map indicating likely bad fibers based on per-fiber median
  response in master_flat (low-response heuristic). One figure per source artifact.

Naming and provenance follow analytics.outputs.register_static_file conventions.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import numpy as np
import matplotlib
matplotlib.use("Agg")  # enforce non-interactive backend
import matplotlib.pyplot as plt  # noqa: E402

from ...artifacts.service import ArtifactService
from ...core.identity import ZipCode
from ..queries import list_artifacts, load_array
from ..outputs import save_fig, register_static_file


@dataclass(frozen=True)
class InstrumentHealthParams:
    out_dir: Path
    zipcode: Optional[ZipCode] = None
    limit: Optional[int] = None
    make_throughput_trend: bool = True
    make_bad_fiber_map: bool = True


class InstrumentHealthStudy:
    # Inputs
    kind_flat: str = "master_flat"
    # Outputs
    kind_throughput_trend: str = "fiber_throughput_trend"
    kind_bad_fiber_map: str = "bad_fiber_map"

    def __init__(self, svc: ArtifactService) -> None:
        self.svc = svc

    def _collect_flat_series(self, zipcode: ZipCode | None, limit: Optional[int]) -> List[Tuple[int, ZipCode | None, float, Any]]:
        rows = list_artifacts(svc=self.svc, kind=self.kind_flat, zipcode=zipcode, limit=limit)
        series: List[Tuple[int, ZipCode | None, float, Any]] = []
        for r in rows:
            try:
                art_id = int(r.get("id"))
                zc = r.get("zipcode") if isinstance(r.get("zipcode"), ZipCode) else zipcode
                payload = load_array(svc=self.svc, row=r)
                if payload is None or payload.get("data") is None:
                    continue
                arr = np.asarray(payload.get("data"), dtype=float)
                with np.errstate(all="ignore"):
                    med = float(np.nanmedian(arr)) if arr.size else np.nan
                series.append((art_id, zc, med, r.get("created_at")))
            except Exception:
                continue
        return series

    def _plot_throughput_trend(self, base_dir: Path, zkey: str, series: List[Tuple[int, ZipCode | None, float, Any]]) -> Optional[Path]:
        # Sort by created_at display key; fallback to index order
        try:
            from datetime import datetime as _dt
            def _to_dt(x):
                ca = x[3]
                if ca is None:
                    return _dt.min
                if isinstance(ca, str):
                    try:
                        return _dt.fromisoformat(ca)
                    except Exception:
                        try:
                            return _dt.strptime(ca.split(".")[0], "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            return _dt.min
                return ca
            series = sorted(series, key=_to_dt)
        except Exception:
            pass
        if not series:
            return None
        y = [s[2] for s in series]
        x = list(range(len(series)))
        fig = plt.figure(figsize=(7, 3.2))
        ax = fig.add_subplot(1, 1, 1)
        ax.plot(x, y, marker="o", ms=3, lw=0.8)
        ax.set_title(f"Flat median intensity trend ({zkey})")
        ax.set_xlabel("artifact index (time-ordered)")
        ax.set_ylabel("median intensity (ADU)")
        saved = save_fig(fig, base_dir, base="fiber_throughput_trend", formats=("png",))
        plt.close(fig)
        return saved[0].path if saved else None

    def _plot_bad_fiber_map(self, base_dir: Path, art_id: int, arr: np.ndarray) -> Optional[Path]:
        # Heuristic: compute per-fiber median across dispersion (axis=1), mark low responders
        if arr.ndim != 2:
            return None
        with np.errstate(all="ignore"):
            per_fiber = np.nanmedian(arr, axis=1)
            thr = np.nanpercentile(per_fiber[np.isfinite(per_fiber)], 5) if np.isfinite(per_fiber).any() else np.nan
            bad = per_fiber <= thr if thr == thr else np.zeros_like(per_fiber, dtype=bool)
        fig = plt.figure(figsize=(6, 2.8))
        ax = fig.add_subplot(1, 1, 1)
        ax.plot(per_fiber, lw=0.6)
        if thr == thr:
            ax.axhline(thr, color="r", ls="--", lw=0.8, label=f"p5={thr:.1f}")
            ax.legend(loc="best", fontsize=8)
        ax.set_title(f"Bad fiber heuristic (id={art_id})")
        ax.set_xlabel("fiber index")
        ax.set_ylabel("median(flat) ADU")
        saved = save_fig(fig, base_dir, base="bad_fiber_map", formats=("png",))
        plt.close(fig)
        return saved[0].path if saved else None

    def run(self, params: InstrumentHealthParams) -> Dict[str, Any]:
        produced = 0
        # 1) Throughput trend per zipcode selection
        if params.make_throughput_trend:
            series = self._collect_flat_series(params.zipcode, params.limit)
            if series:
                # Output under <out>/instrument_health/<zipcode>/trend
                zkey = (params.zipcode.key() if isinstance(params.zipcode, ZipCode) else "__mixed__") if params.zipcode else "__all__"
                trend_dir = Path(params.out_dir) / "instrument_health" / zkey / "trend"
                trend_dir.mkdir(parents=True, exist_ok=True)
                p = self._plot_throughput_trend(trend_dir, zkey, series)
                if p is not None:
                    # Register one artifact for the trend; parent ids = all source flats used
                    parent_ids = [sid for (sid, _zc, _m, _ca) in series]
                    register_static_file(
                        svc=self.svc,
                        kind=self.kind_throughput_trend,
                        file_path=Path(p),
                        parent_ids=parent_ids,
                        zipcode=params.zipcode,
                        params={"source_kind": self.kind_flat, "source_artifact_id": parent_ids[:1] if parent_ids else []},
                        payload_type="image",
                        storage_format=Path(p).suffix.lstrip("."),
                        study_name="instrument_health",
                    )
                    produced += 1
        # 2) Bad fiber map per artifact (for selection)
        if params.make_bad_fiber_map:
            rows = list_artifacts(svc=self.svc, kind=self.kind_flat, zipcode=params.zipcode, limit=params.limit)
            for r in rows:
                try:
                    art_id = int(r.get("id"))
                    zc = r.get("zipcode") if isinstance(r.get("zipcode"), ZipCode) else params.zipcode
                    payload = load_array(svc=self.svc, row=r)
                    if payload is None or payload.get("data") is None:
                        continue
                    arr = np.asarray(payload.get("data"), dtype=float)
                    zkey = (zc.key() if isinstance(zc, ZipCode) else None) or "__unknown__"
                    base_dir = Path(params.out_dir) / "instrument_health" / zkey / str(art_id)
                    base_dir.mkdir(parents=True, exist_ok=True)
                    p = self._plot_bad_fiber_map(base_dir, art_id, arr)
                    if p is not None:
                        register_static_file(
                            svc=self.svc,
                            kind=self.kind_bad_fiber_map,
                            file_path=Path(p),
                            parent_ids=[art_id],
                            zipcode=zc,
                            params={"source_kind": self.kind_flat, "source_artifact_id": art_id},
                            payload_type="image",
                            storage_format=Path(p).suffix.lstrip("."),
                            study_name="instrument_health",
                        )
                        produced += 1
                except Exception:
                    continue
        return {"produced": produced}
