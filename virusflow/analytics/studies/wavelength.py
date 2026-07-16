from __future__ import annotations

"""
Wavelength analytics study: post-run plots for wave artifacts.

This study discovers wave artifacts via the registry, loads their array payloads,
produces:
- wave_preview: percentile-stretched image of the wavelength map
- wave_value_hist: histogram of wavelength values (finite pixels)

Outputs are written under <out>/wave/<zipcode>/<artifact_id>/ and registered as
first-class analytics artifacts with provenance linking back to the source wave
artifact. No algorithm/task imports; ArtifactService only.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any
import numpy as np
import matplotlib
matplotlib.use("Agg")  # enforce non-interactive backend
import matplotlib.pyplot as plt  # noqa: E402

from ...artifacts.service import ArtifactService
from ...core.identity import ZipCode
from ..queries import list_artifacts, load_array
from ..outputs import save_fig, register_static_file


@dataclass(frozen=True)
class WavelengthStudyParams:
    out_dir: Path
    zipcode: Optional[ZipCode] = None
    limit: Optional[int] = None
    make_preview: bool = True
    make_value_hist: bool = True


class WavelengthStudy:
    kind_in: str = "wave"
    kind_preview: str = "wave_preview"
    kind_value_hist: str = "wave_value_hist"
    # New outputs for identify-arc parity and residuals
    kind_identify_arc: str = "wave_identify_arc"
    kind_residual_hist: str = "wave_residual_hist"

    def __init__(self, svc: ArtifactService) -> None:
        self.svc = svc

    def run(self, params: WavelengthStudyParams) -> Dict[str, Any]:
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
                zkey = (zc.key() if isinstance(zc, ZipCode) else None) or "__unknown__"
                base_dir = Path(params.out_dir) / self.kind_in / zkey / str(art_id)
                base_dir.mkdir(parents=True, exist_ok=True)
                parents = [art_id]
                # 1) Percentile-stretched preview image
                if params.make_preview:
                    fig = plt.figure(figsize=(6, 4))
                    ax = fig.add_subplot(1, 1, 1)
                    with np.errstate(all="ignore"):
                        fin = arr[np.isfinite(arr)]
                        if fin.size:
                            lo, hi = np.nanpercentile(fin, [5, 99.5])
                        else:
                            lo, hi = 0.0, 1.0
                    im = ax.imshow(arr, aspect="auto", origin="lower", cmap="viridis", vmin=lo, vmax=hi)
                    ax.set_title(f"Wave preview id={art_id}")
                    ax.set_xlabel("x (pix)")
                    ax.set_ylabel("row")
                    fig.colorbar(im, ax=ax, shrink=0.85, label="wavelength")
                    saved = save_fig(fig, base_dir, base="wave_preview", formats=("png",))
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
                            study_name="wavelength",
                        )
                        produced += 1
                # 2) Histogram of wavelength values (finite)
                if params.make_value_hist:
                    with np.errstate(all="ignore"):
                        vals = arr[np.isfinite(arr)].ravel()
                    if vals.size:
                        fig2 = plt.figure(figsize=(6, 3))
                        ax2 = fig2.add_subplot(1, 1, 1)
                        ax2.hist(vals, bins=64, color="#1f77b4", alpha=0.85)
                        ax2.set_title(f"Wave value histogram id={art_id}")
                        ax2.set_xlabel("wavelength")
                        ax2.set_ylabel("count")
                        saved2 = save_fig(fig2, base_dir, base="wave_value_hist", formats=("png",))
                        plt.close(fig2)
                        if saved2:
                            register_static_file(
                                svc=self.svc,
                                kind=self.kind_value_hist,
                                file_path=saved2[0].path,
                                parent_ids=parents,
                                zipcode=zc,
                                params={"source_kind": self.kind_in, "source_artifact_id": art_id},
                                payload_type="image",
                                storage_format=saved2[0].fmt,
                                study_name="wavelength",
                            )
                            produced += 1
                # 3) Identify-arc parity plot using compact extras from sidecar (if present)
                try:
                    desc = self.svc.describe(r)
                    summ = desc.get("summary") if isinstance(desc, dict) else None
                except Exception:
                    summ = None
                if isinstance(summ, dict):
                    ref_ds = summ.get("ref_profile_ds")
                    best_nmatch = summ.get("best_nmatch")
                    best_rms = summ.get("best_rms")
                    if isinstance(ref_ds, (list, tuple)) and len(ref_ds) > 0:
                        import numpy as _np
                        rp = _np.asarray(ref_ds, dtype=float)
                        fig3 = plt.figure(figsize=(6, 2.5))
                        ax3 = fig3.add_subplot(1, 1, 1)
                        ax3.plot(rp, lw=0.8)
                        title = f"Identify-arc ref profile id={art_id}"
                        try:
                            if best_nmatch is not None or best_rms is not None:
                                title += f" (nmatch={best_nmatch}, rms={best_rms})"
                        except Exception:
                            pass
                        ax3.set_title(title)
                        ax3.set_xlabel("pixel")
                        ax3.set_ylabel("flux (a.u.)")
                        saved3 = save_fig(fig3, base_dir, base="wave_identify_arc", formats=("png",))
                        plt.close(fig3)
                        if saved3:
                            register_static_file(
                                svc=self.svc,
                                kind=self.kind_identify_arc,
                                file_path=saved3[0].path,
                                parent_ids=parents,
                                zipcode=zc,
                                params={"source_kind": self.kind_in, "source_artifact_id": art_id},
                                payload_type="image",
                                storage_format=saved3[0].fmt,
                                study_name="wavelength",
                            )
                            produced += 1
                    # 4) Residual histogram from per_fiber_wavelength_residual_rms_ds if available
                    rr = summ.get("per_fiber_wavelength_residual_rms_ds")
                    if isinstance(rr, (list, tuple)) and len(rr) > 0:
                        import numpy as _np
                        valsr = _np.asarray(rr, dtype=float)
                        fin = valsr[_np.isfinite(valsr) & (valsr > 0)]
                        if fin.size:
                            p50 = float(_np.nanmedian(fin))
                            p95 = float(_np.nanpercentile(fin, 95))
                            fig4 = plt.figure(figsize=(6, 3))
                            ax4 = fig4.add_subplot(1, 1, 1)
                            ax4.hist(fin, bins=48, color="#2ca02c", alpha=0.85)
                            ax4.axvline(p50, color="r", lw=1.0, label=f"median={p50:.3f}")
                            ax4.axvline(p95, color="k", lw=1.0, ls="--", label=f"p95={p95:.3f}")
                            ax4.legend(loc="best", fontsize=8)
                            ax4.set_title(f"Wave residual RMS (id={art_id})")
                            ax4.set_xlabel("RMS (wavelength units)")
                            ax4.set_ylabel("count")
                            saved4 = save_fig(fig4, base_dir, base="wave_residual_hist", formats=("png",))
                            plt.close(fig4)
                            if saved4:
                                register_static_file(
                                    svc=self.svc,
                                    kind=self.kind_residual_hist,
                                    file_path=saved4[0].path,
                                    parent_ids=parents,
                                    zipcode=zc,
                                    params={"source_kind": self.kind_in, "source_artifact_id": art_id},
                                    payload_type="image",
                                    storage_format=saved4[0].fmt,
                                    study_name="wavelength",
                                )
                                produced += 1
            except Exception:
                continue
        return {"selected": len(rows), "produced": produced}
