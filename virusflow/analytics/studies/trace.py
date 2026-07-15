from __future__ import annotations

"""
Trace analytics study: post-run plots and summaries for trace artifacts.

This study discovers trace artifacts via the registry, loads their array payloads,
produces one or more plots per artifact (e.g., a percentile-stretched preview
image and a simple per-row dispersion profile), writes them to an output tree,
and registers each produced file as an analytics artifact with provenance linking
back to the source trace artifact.

No imports from algorithms/tasks/planning; uses ArtifactService only.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any
import numpy as np
import matplotlib
matplotlib.use("Agg")  # enforce non-interactive backend
import matplotlib.pyplot as plt  # noqa: E402

from ...artifacts.service import ArtifactService
from ...core.identity import parse_zipcode_key, ZipCode
from ..queries import list_artifacts, load_array
from ..outputs import save_fig, register_static_file


@dataclass(frozen=True)
class TraceStudyParams:
    out_dir: Path
    zipcode: Optional[ZipCode] = None
    limit: Optional[int] = None
    make_preview: bool = True
    make_row_dispersion: bool = True


class TraceStudy:
    kind_in: str = "trace"
    # Output artifact kinds (use descriptive, scientific names)
    kind_preview: str = "trace_preview"
    kind_row_dispersion: str = "trace_row_dispersion"

    def __init__(self, svc: ArtifactService) -> None:
        self.svc = svc

    def run(self, params: TraceStudyParams) -> Dict[str, Any]:
        rows = list_artifacts(svc=self.svc, kind=self.kind_in, zipcode=params.zipcode, limit=params.limit)
        if not rows:
            return {"selected": 0, "produced": 0}
        produced = 0
        for r in rows:
            try:
                art_id = int(r.get("id"))
                zc = r.get("zipcode") if isinstance(r.get("zipcode"), ZipCode) else params.zipcode
                payload = load_array(svc=self.svc, row=r)
                if payload is None or payload.get("data") is None:
                    continue
                arr = np.asarray(payload.get("data"), dtype=float)
                # Build base output directory: <out>/trace/<zipcode or __unknown__>/<id>
                zkey = (zc.key() if isinstance(zc, ZipCode) else None) or "__unknown__"
                base_dir = Path(params.out_dir) / self.kind_in / zkey / str(art_id)
                base_dir.mkdir(parents=True, exist_ok=True)
                parents = [art_id]
                # 1) Percentile-stretched preview image
                if params.make_preview:
                    fig = plt.figure(figsize=(6, 4))
                    ax = fig.add_subplot(1, 1, 1)
                    # percentile stretch
                    with np.errstate(all="ignore"):
                        lo, hi = np.nanpercentile(arr[np.isfinite(arr)], [5, 99.5]) if np.isfinite(arr).any() else (0.0, 1.0)
                    im = ax.imshow(arr, aspect="auto", origin="lower", cmap="viridis", vmin=lo, vmax=hi)
                    ax.set_title(f"Trace preview id={art_id}")
                    ax.set_xlabel("x (pix)")
                    ax.set_ylabel("fiber/row")
                    fig.colorbar(im, ax=ax, shrink=0.85)
                    saved = save_fig(fig, base_dir, base="trace_preview", formats=("png",))
                    plt.close(fig)
                    if saved:
                        register_static_file(
                            svc=self.svc,
                            kind=self.kind_preview,
                            file_path=saved[0].path,
                            parent_ids=parents,
                            zipcode=zc,
                            params={"source_kind": self.kind_in, "source_artifact_id": art_id},
                            payload_type="image",
                            storage_format=saved[0].fmt,
                            study_name="trace",
                        )
                        produced += 1
                # 2) Simple per-row dispersion preview: std dev across x for each row
                if params.make_row_dispersion:
                    with np.errstate(all="ignore"):
                        row_std = np.nanstd(arr, axis=1)
                    fig2 = plt.figure(figsize=(6, 3))
                    ax2 = fig2.add_subplot(1, 1, 1)
                    ax2.plot(row_std, lw=0.7)
                    ax2.set_title(f"Trace row dispersion id={art_id}")
                    ax2.set_xlabel("row")
                    ax2.set_ylabel("std dev (ADU)")
                    saved2 = save_fig(fig2, base_dir, base="row_dispersion", formats=("png",))
                    plt.close(fig2)
                    if saved2:
                        register_static_file(
                            svc=self.svc,
                            kind=self.kind_row_dispersion,
                            file_path=saved2[0].path,
                            parent_ids=parents,
                            zipcode=zc,
                            params={"source_kind": self.kind_in, "source_artifact_id": art_id},
                            payload_type="image",
                            storage_format=saved2[0].fmt,
                            study_name="trace",
                        )
                        produced += 1
            except Exception:
                # continue on failures to keep batch robust
                continue
        return {"selected": len(rows), "produced": produced}
