from __future__ import annotations

"""
Calibration analytics studies: post-run plots for master_flat and master_cmp.

Outputs (artifact kinds):
- flat_p95_hist: Histogram of pixel values with p95 marker for each master_flat artifact
- flat_badfrac_trend: Time-series of BADFRAC/bad_fraction over artifacts (per zipcode selection)
- cmp_p95_hist: Histogram of pixel values with p95 marker for each master_cmp artifact
- cmp_zero_map: Downsampled binary map showing zero-valued regions in master_cmp

Study is read-only over the registry and registers outputs as first-class
analytics artifacts with provenance linking to the source artifact id.
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
class CalibrationStudyParams:
    out_dir: Path
    zipcode: Optional[ZipCode] = None
    limit: Optional[int] = None
    kinds: Tuple[str, ...] = ("master_flat", "master_cmp")
    # Toggles for outputs
    make_p95_hist: bool = True
    make_badfrac_trend: bool = True  # only for master_flat
    make_zero_map: bool = True       # only for master_cmp


class CalibrationStudy:
    kind_flat_in: str = "master_flat"
    kind_cmp_in: str = "master_cmp"
    # Output artifact kinds
    kind_flat_p95_hist: str = "flat_p95_hist"
    kind_flat_badfrac_trend: str = "flat_badfrac_trend"
    kind_cmp_p95_hist: str = "cmp_p95_hist"
    kind_cmp_zero_map: str = "cmp_zero_map"

    def __init__(self, svc: ArtifactService) -> None:
        self.svc = svc

    def _compute_p95(self, arr: np.ndarray) -> Optional[float]:
        with np.errstate(all="ignore"):
            return float(np.nanpercentile(arr, 95)) if arr.size else None

    def _bad_fraction_from_header(self, header: Dict[str, Any] | None, sidecar: Dict[str, Any] | None) -> Optional[float]:
        # Prefer FITS header BADFRAC, else sidecar bad_fraction in summary when present
        if isinstance(header, dict) and "BADFRAC" in header:
            try:
                return float(header.get("BADFRAC"))
            except Exception:
                pass
        if isinstance(sidecar, dict):
            try:
                bf = sidecar.get("bad_fraction")
                return float(bf) if bf is not None else None
            except Exception:
                return None
        return None

    def run(self, params: CalibrationStudyParams) -> Dict[str, Any]:
        selected_total = 0
        produced = 0
        out_root = Path(params.out_dir)

        # Helper to process a single artifact row for p95 histogram
        def _emit_p95_hist(kind_out: str, base_dir: Path, art_id: int, arr: np.ndarray, title: str) -> None:
            nonlocal produced
            # Histogram with log-y and 95th percentile marker
            with np.errstate(all="ignore"):
                finite = arr[np.isfinite(arr)]
            if finite.size == 0:
                return
            p95 = self._compute_p95(finite)
            fig = plt.figure(figsize=(5, 3))
            ax = fig.add_subplot(1, 1, 1)
            ax.hist(finite.ravel(), bins=256, color="#3366cc", alpha=0.8)
            if p95 is not None:
                ax.axvline(p95, color="red", lw=1.2, label=f"p95={p95:.1f}")
                ax.legend(loc="best", fontsize=8)
            ax.set_title(title)
            ax.set_xlabel("value (ADU)")
            ax.set_ylabel("count")
            ax.set_yscale("log")
            saved = save_fig(fig, base_dir, base=kind_out, formats=("png",))
            plt.close(fig)
            if saved:
                register_static_file(
                    svc=self.svc,
                    kind=kind_out,
                    file_path=saved[0].path,
                    parent_ids=[art_id],
                    zipcode=zc,
                    params={"source_kind": src_kind, "source_artifact_id": art_id},
                    payload_type="image",
                    storage_format=saved[0].fmt,
                    study_name="calibration",
                )
                produced += 1

        # Master_flat: per-artifact histogram, plus a trend line over time in this selection
        if self.kind_flat_in in params.kinds:
            rows_flat = list_artifacts(svc=self.svc, kind=self.kind_flat_in, zipcode=params.zipcode, limit=params.limit)
            selected_total += len(rows_flat)
            trend_points: List[Tuple[float, float]] = []  # (ordinal/time, badfrac)
            for r in rows_flat:
                try:
                    art_id = int(r.get("id"))
                    zc = r.get("zipcode") if isinstance(r.get("zipcode"), ZipCode) else params.zipcode
                    src_kind = self.kind_flat_in
                    payload = load_array(svc=self.svc, row=r) or {}
                    arr = np.asarray(payload.get("data"), dtype=float) if payload.get("data") is not None else None
                    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
                    # Build base output directory: <out>/master_flat/<zipcode>/<id>
                    zkey = (zc.key() if isinstance(zc, ZipCode) else None) or "__unknown__"
                    base_dir = out_root / self.kind_flat_in / zkey / str(art_id)
                    base_dir.mkdir(parents=True, exist_ok=True)
                    if params.make_p95_hist and arr is not None:
                        _emit_p95_hist(self.kind_flat_p95_hist, base_dir, art_id, arr, title=f"master_flat id={art_id} p95 hist")
                    # Accumulate trend point from BADFRAC
                    bf = self._bad_fraction_from_header(header, r)
                    if bf is not None:
                        # Use creation time ordinal if available, else artifact id as order proxy
                        try:
                            from datetime import datetime
                            ca = r.get("created_at")
                            if isinstance(ca, str):
                                t = datetime.fromisoformat(ca)
                                x = t.timestamp()
                            elif isinstance(ca, datetime):
                                x = ca.timestamp()
                            else:
                                x = float(art_id)
                        except Exception:
                            x = float(art_id)
                        trend_points.append((x, float(bf)))
                except Exception:
                    continue
            if params.make_badfrac_trend and trend_points:
                # Sort by x and plot
                trend_points.sort(key=lambda t: t[0])
                xs = np.array([p[0] for p in trend_points], dtype=float)
                ys = np.array([p[1] for p in trend_points], dtype=float)
                # Normalize x to index if timestamps vary wildly
                if np.ptp(xs) <= 0:
                    xs_plot = np.arange(xs.size)
                    xlab = "index"
                else:
                    xs_plot = (xs - xs.min()) / max(1.0, (xs.max() - xs.min()))
                    xlab = "time (normalized)"
                fig = plt.figure(figsize=(6, 3))
                ax = fig.add_subplot(1, 1, 1)
                ax.plot(xs_plot, ys, marker="o", ms=2, lw=0.8)
                ax.set_title("master_flat BADFRAC trend")
                ax.set_xlabel(xlab)
                ax.set_ylabel("BADFRAC")
                # Place under a zipcode-level directory
                zkey = (params.zipcode.key() if isinstance(params.zipcode, ZipCode) else "__any__")
                trend_dir = out_root / self.kind_flat_in / zkey / "__trend__"
                saved = save_fig(fig, trend_dir, base="flat_badfrac_trend", formats=("png",))
                plt.close(fig)
                if saved:
                    register_static_file(
                        svc=self.svc,
                        kind=self.kind_flat_badfrac_trend,
                        file_path=saved[0].path,
                        parent_ids=[int(r.get("id")) for r in rows_flat if r.get("id") is not None][:8],  # include a few parents
                        zipcode=params.zipcode,
                        params={"source_kind": self.kind_flat_in, "source_artifact_ids": [int(r.get("id")) for r in rows_flat if r.get("id") is not None]},
                        payload_type="image",
                        storage_format=saved[0].fmt,
                        study_name="calibration",
                    )
                    produced += 1

        # master_cmp: per-artifact histogram, plus zero map thumbnail
        if self.kind_cmp_in in params.kinds:
            rows_cmp = list_artifacts(svc=self.svc, kind=self.kind_cmp_in, zipcode=params.zipcode, limit=params.limit)
            selected_total += len(rows_cmp)
            for r in rows_cmp:
                try:
                    art_id = int(r.get("id"))
                    zc = r.get("zipcode") if isinstance(r.get("zipcode"), ZipCode) else params.zipcode
                    src_kind = self.kind_cmp_in
                    payload = load_array(svc=self.svc, row=r) or {}
                    arr = np.asarray(payload.get("data"), dtype=float) if payload.get("data") is not None else None
                    # Build base output directory: <out>/master_cmp/<zipcode>/<id>
                    zkey = (zc.key() if isinstance(zc, ZipCode) else None) or "__unknown__"
                    base_dir = out_root / self.kind_cmp_in / zkey / str(art_id)
                    base_dir.mkdir(parents=True, exist_ok=True)
                    if params.make_p95_hist and arr is not None:
                        _emit_p95_hist(self.kind_cmp_p95_hist, base_dir, art_id, arr, title=f"master_cmp id={art_id} p95 hist")
                    if params.make_zero_map and arr is not None:
                        # Create a binary zero mask and downsample for visualization
                        zero_mask = (arr == 0)
                        # Downsample to at most 256x256 by simple stride slicing
                        ny, nx = zero_mask.shape
                        sy = max(1, ny // 256)
                        sx = max(1, nx // 256)
                        thumb = zero_mask[::sy, ::sx]
                        fig = plt.figure(figsize=(5, 4))
                        ax = fig.add_subplot(1, 1, 1)
                        im = ax.imshow(thumb, aspect="auto", origin="lower", cmap="gray_r")
                        ax.set_title("CMP zero map (downsampled)")
                        ax.set_xlabel("x (downsampled)")
                        ax.set_ylabel("y (downsampled)")
                        saved = save_fig(fig, base_dir, base="cmp_zero_map", formats=("png",))
                        plt.close(fig)
                        if saved:
                            register_static_file(
                                svc=self.svc,
                                kind=self.kind_cmp_zero_map,
                                file_path=saved[0].path,
                                parent_ids=[art_id],
                                zipcode=zc,
                                params={"source_kind": src_kind, "source_artifact_id": art_id},
                                payload_type="image",
                                storage_format=saved[0].fmt,
                                study_name="calibration",
                            )
                            produced += 1
                except Exception:
                    continue

        return {"selected": selected_total, "produced": produced}
