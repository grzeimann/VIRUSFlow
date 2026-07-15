from __future__ import annotations

"""
Reports analytics study: compose existing analytics outputs into simple HTML reports.

Implements two report kinds initially:
- report_daily_calib: include latest calibration summaries and trends for a zipcode or all
- report_weekly_health: aggregate instrument health over a recent window (lightweight)

Outputs are written under <out>/reports/<report_kind>/<zipcode or __all__>/index.html
and registered as first-class analytics artifacts with provenance.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List
import shutil

from ...artifacts.service import ArtifactService
from ...core.identity import ZipCode
from ..queries import list_artifacts
from ..outputs import register_static_file


@dataclass(frozen=True)
class ReportsStudyParams:
    out_dir: Path
    report_kind: str  # "daily_calib" | "weekly_health"
    zipcode: Optional[ZipCode] = None
    limit: Optional[int] = None


class ReportsStudy:
    kind_report_daily: str = "report_daily_calib"
    kind_report_weekly: str = "report_weekly_health"

    # Source analytics kinds we may compose
    calib_kinds: tuple[str, ...] = (
        "flat_p95_hist",
        "flat_badfrac_trend",
        "cmp_p95_hist",
        "cmp_zero_map",
    )
    health_kinds: tuple[str, ...] = (
        "fiber_throughput_trend",
        "bad_fiber_map",
    )

    def __init__(self, svc: ArtifactService) -> None:
        self.svc = svc

    def _collect_latest_by_kind(self, kinds: List[str], zipcode: ZipCode | None, limit: Optional[int]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for k in kinds:
            # Prefer a handful of most recent items per kind for the report
            rs = list_artifacts(svc=self.svc, kind=k, zipcode=zipcode, limit=limit or 8)
            # Best effort: sort by created_at descending if available
            try:
                rs = sorted(rs, key=lambda r: (r.get("created_at") or ""), reverse=True)
            except Exception:
                pass
            rows.extend(rs[: (limit or 8)])
        return rows

    def _write_html_report(self, base_dir: Path, title: str, sections: List[tuple[str, List[Dict[str, Any]]]]) -> Path:
        assets_dir = base_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        parts: List[str] = [
            "<html><head>",
            f"<meta charset='utf-8'><title>{title}</title>",
            "<style>body{font-family:Arial,Helvetica,sans-serif;margin:1.2em;}h2{border-bottom:1px solid #ddd;padding-bottom:4px;} .grid{display:flex;flex-wrap:wrap;gap:10px;} .card{border:1px solid #e0e0e0;padding:6px;border-radius:6px;box-shadow:1px 1px 2px rgba(0,0,0,0.06);} .card img{max-width:360px;height:auto;display:block;}</style>",
            "</head><body>",
            f"<h1>{title}</h1>",
        ]
        for sec_title, items in sections:
            parts.append(f"<h2>{sec_title}</h2>")
            parts.append("<div class='grid'>")
            for r in items:
                src_path = r.get("path")
                if not src_path:
                    continue
                try:
                    src = Path(str(src_path))
                    # Copy into assets for portability
                    dst = assets_dir / f"{r.get('kind','artifact')}_{r.get('id','')}_{src.name}"
                    if src.exists():
                        try:
                            shutil.copyfile(src, dst)
                            rel = Path("assets") / dst.name
                        except Exception:
                            rel = src  # fallback to absolute path reference
                    else:
                        rel = src
                    caption = f"{r.get('kind','')} (id={r.get('id','')})"
                    parts.append(f"<div class='card'><img src='{rel}' alt='{caption}'/><div>{caption}</div></div>")
                except Exception:
                    continue
            parts.append("</div>")
        parts.append("</body></html>")
        html_path = base_dir / "index.html"
        with html_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        return html_path

    def run(self, params: ReportsStudyParams) -> Dict[str, Any]:
        rkind = (params.report_kind or "").strip().lower()
        zkey = (params.zipcode.key() if isinstance(params.zipcode, ZipCode) else "__mixed__") if params.zipcode else "__all__"
        out_root = Path(params.out_dir) / "reports" / rkind / zkey
        out_root.mkdir(parents=True, exist_ok=True)

        if rkind == "daily_calib":
            rows = self._collect_latest_by_kind(list(self.calib_kinds), params.zipcode, limit=params.limit or 8)
            html = self._write_html_report(out_root, f"Daily Calibration Summary ({zkey})", [("Calibration artifacts", rows)])
            register_static_file(
                svc=self.svc,
                kind=self.kind_report_daily,
                file_path=html,
                parent_ids=[int(r.get("id")) for r in rows if r.get("id") is not None],
                zipcode=params.zipcode,
                params={"source_kind": "mixed", "source_artifact_id": [int(r.get("id")) for r in rows if r.get("id") is not None]},
                payload_type="html",
                storage_format="html",
                study_name="report",
            )
            return {"produced": 1}
        elif rkind == "weekly_health":
            rows = self._collect_latest_by_kind(list(self.health_kinds), params.zipcode, limit=params.limit or 12)
            html = self._write_html_report(out_root, f"Weekly Instrument Health ({zkey})", [("Instrument health", rows)])
            register_static_file(
                svc=self.svc,
                kind=self.kind_report_weekly,
                file_path=html,
                parent_ids=[int(r.get("id")) for r in rows if r.get("id") is not None],
                zipcode=params.zipcode,
                params={"source_kind": "mixed", "source_artifact_id": [int(r.get("id")) for r in rows if r.get("id") is not None]},
                payload_type="html",
                storage_format="html",
                study_name="report",
            )
            return {"produced": 1}
        else:
            raise ValueError("Unknown report_kind. Use one of: daily_calib, weekly_health")
