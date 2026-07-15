from __future__ import annotations

"""
Trending analytics study: render QA metric time-series for a given artifact kind/zipcode/date window.

- Input selection is registry-only via ArtifactService.
- For each (kind, zipcode) selection, render a single time-series figure of the chosen metric
  from diagnostics (QA) records ordered by artifact creation time.
- Register the figure as a first-class analytics artifact with provenance.

This study intentionally has no imports from algorithms/tasks/planning.
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
from ..queries import list_artifacts
from ..outputs import save_fig, register_static_file


@dataclass(frozen=True)
class TrendingStudyParams:
    out_dir: Path
    kind: str
    metric: str
    zipcode: Optional[ZipCode] = None
    limit: Optional[int] = None
    since: Optional[str] = None  # YYYYMMDD
    until: Optional[str] = None  # YYYYMMDD


class TrendingStudy:
    # Output artifact kind
    kind_out: str = "qa_metric_timeseries"

    def __init__(self, svc: ArtifactService) -> None:
        self.svc = svc

    def _parse_time(self, s: object):
        if not s:
            return None
        try:
            from datetime import datetime as _dt
            st = str(s)
            if len(st) == 8 and st.isdigit():
                return _dt.strptime(st, "%Y%m%d")
            return _dt.fromisoformat(st)
        except Exception:
            return None

    def run(self, params: TrendingStudyParams) -> Dict[str, Any]:
        # Validate inputs
        kind = (params.kind or "").strip()
        metric = (params.metric or "").strip()
        if not kind:
            raise ValueError("TrendingStudy requires --kind (artifact kind)")
        if not metric:
            raise ValueError("TrendingStudy requires --metric (QA metric name)")

        rows = list_artifacts(svc=self.svc, kind=kind, zipcode=params.zipcode, limit=params.limit)
        if not rows:
            return {"selected": 0, "produced": 0}

        # Optional date window filter by created_at
        t0 = self._parse_time(params.since)
        t1 = self._parse_time(params.until)

        def _ok_date(r):
            if not (t0 or t1):
                return True
            ca = r.get("created_at")
            try:
                from datetime import datetime as _dt
                if isinstance(ca, str):
                    dt = _dt.fromisoformat(ca)
                else:
                    dt = ca
            except Exception:
                return True
            if t0 and dt and dt < t0:
                return False
            if t1 and dt and dt > t1:
                return False
            return True

        rows = [r for r in rows if _ok_date(r)]
        if not rows:
            return {"selected": 0, "produced": 0}

        # Gather (time, value) pairs from diagnostics
        points: List[Tuple[float, float]] = []
        ids: List[int] = []
        for r in rows:
            try:
                art_id = int(r.get("id"))
                qa = self.svc.adapter.get_diagnostics(art_id) or {}
                metrics = qa.get("metrics") or {}
                val = metrics.get(metric)
                if val is None:
                    continue
                # X-axis: artifact created_at (timestamp) or ordinal fallback
                ca = r.get("created_at")
                try:
                    from datetime import datetime as _dt
                    if isinstance(ca, str):
                        dt = _dt.fromisoformat(ca)
                    else:
                        dt = ca
                    x = float(dt.timestamp()) if dt else float(art_id)
                except Exception:
                    x = float(art_id)
                points.append((x, float(val)))
                ids.append(art_id)
            except Exception:
                continue

        if not points:
            return {"selected": len(rows), "produced": 0}

        # Sort by time
        points = sorted(points, key=lambda t: t[0])
        xs = np.array([p[0] for p in points], dtype=float)
        ys = np.array([p[1] for p in points], dtype=float)
        # Normalize x to [0,1] for compact plotting if timestamps vary widely
        if np.isfinite(xs).any():
            xs_plot = (xs - np.nanmin(xs)) / max(1.0, (np.nanmax(xs) - np.nanmin(xs)))
            xlab = "time (normalized)"
        else:
            xs_plot = np.arange(xs.size)
            xlab = "index"

        # Build output path root: <out>/<study>/<zipcode or __any__>/
        zkey = (params.zipcode.key() if isinstance(params.zipcode, ZipCode) else "__any__")
        base_dir = Path(params.out_dir) / "trending" / zkey
        base_dir.mkdir(parents=True, exist_ok=True)

        # Compose title and figure
        title = f"QA metric timeseries: {metric} (kind={kind}, n={len(points)})"
        fig = plt.figure(figsize=(7, 3.2))
        ax = fig.add_subplot(1, 1, 1)
        ax.plot(xs_plot, ys, marker="o", ms=2, lw=0.8)
        ax.set_title(title)
        ax.set_xlabel(xlab)
        ax.set_ylabel(metric)
        saved = save_fig(fig, base_dir, base=f"{self.kind_out}_{kind}_{metric}", formats=("png",))
        plt.close(fig)

        produced = 0
        if saved:
            register_static_file(
                svc=self.svc,
                kind=self.kind_out,
                file_path=saved[0].path,
                parent_ids=(ids[:8] if ids else []),  # limit number of parents recorded
                zipcode=params.zipcode,
                params={
                    "source_kind": kind,
                    "source_artifact_ids": ids,
                    "metric": metric,
                },
                payload_type="image",
                storage_format=saved[0].fmt,
                study_name="trending",
            )
            produced = 1

        return {"selected": len(rows), "produced": produced}
