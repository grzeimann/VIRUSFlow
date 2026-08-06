from __future__ import annotations

"""
Analytics service: study execution API independent of any CLI.

Exposes a small facade to run named studies with typed params and to ensure
isolation from algorithms/tasks/planning. Uses ArtifactService for all I/O.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

from ..artifacts.service import ArtifactService
from .studies.trace import TraceStudy, TraceStudyParams
from .studies.wavelength import WavelengthStudy, WavelengthStudyParams
from .studies.calibration import CalibrationStudy, CalibrationStudyParams
from .studies.instrument_health import InstrumentHealthStudy, InstrumentHealthParams
from .studies.trending import TrendingStudy, TrendingStudyParams
from .studies.reports import ReportsStudy, ReportsStudyParams
from .studies.bias import BiasStabilityParams, BiasStabilityStudy


@dataclass(frozen=True)
class AnalyticsRequest:
    name: str  # e.g., "trace"
    params: Dict[str, Any]


class AnalyticsService:
    def __init__(self, db_path: str) -> None:
        self.svc = ArtifactService(db_path)

    def run(self, req: AnalyticsRequest) -> Dict[str, Any]:
        name = (req.name or "").strip().lower()
        if name == "trace":
            p = self._parse_trace_params(req.params)
            study = TraceStudy(self.svc)
            return study.run(p)
        if name == "wavelength":
            p = self._parse_wavelength_params(req.params)
            study = WavelengthStudy(self.svc)
            return study.run(p)
        if name == "calibration":
            p = self._parse_calibration_params(req.params)
            study = CalibrationStudy(self.svc)
            return study.run(p)
        if name == "instrument_health":
            p = self._parse_instrument_health_params(req.params)
            study = InstrumentHealthStudy(self.svc)
            return study.run(p)
        if name == "trending":
            p = self._parse_trending_params(req.params)
            study = TrendingStudy(self.svc)
            return study.run(p)
        if name == "reports":
            p = self._parse_reports_params(req.params)
            study = ReportsStudy(self.svc)
            return study.run(p)
        if name == "bias_stability":
            zipcode = req.params.get("zipcode")
            if isinstance(zipcode, str):
                from ..core.identity import parse_zipcode_key

                zipcode = parse_zipcode_key(zipcode)
            if zipcode is None:
                raise ValueError("bias_stability requires zipcode")
            limit = req.params.get("limit")
            study = BiasStabilityStudy(self.svc)
            return study.run(
                BiasStabilityParams(
                    out_dir=Path(req.params.get("out") or req.params.get("out_dir") or "./qa_plots"),
                    zipcode=zipcode,
                    limit=int(limit) if limit is not None else None,
                )
            )
        raise ValueError(f"Unknown analytics study: {req.name}")

    def _parse_trace_params(self, p: Dict[str, Any]) -> TraceStudyParams:
        out_dir = Path(p.get("out") or p.get("out_dir") or "./qa_plots")
        zipcode = p.get("zipcode")
        # Allow either ZipCode or string key
        if isinstance(zipcode, str) and zipcode:
            from ..core.identity import parse_zipcode_key
            zipcode = parse_zipcode_key(zipcode)
        limit = p.get("limit")
        try:
            limit = int(limit) if limit is not None else None
        except Exception:
            limit = None
        make_preview = bool(p.get("make_preview", True))
        make_row_dispersion = bool(p.get("make_row_dispersion", True))
        return TraceStudyParams(
            out_dir=out_dir,
            zipcode=zipcode,
            limit=limit,
            make_preview=make_preview,
            make_row_dispersion=make_row_dispersion,
        )

    def _parse_wavelength_params(self, p: Dict[str, Any]) -> WavelengthStudyParams:
        out_dir = Path(p.get("out") or p.get("out_dir") or "./qa_plots")
        zipcode = p.get("zipcode")
        if isinstance(zipcode, str) and zipcode:
            from ..core.identity import parse_zipcode_key
            zipcode = parse_zipcode_key(zipcode)
        limit = p.get("limit")
        try:
            limit = int(limit) if limit is not None else None
        except Exception:
            limit = None
        make_preview = bool(p.get("make_preview", True))
        make_value_hist = bool(p.get("make_value_hist", True))
        return WavelengthStudyParams(
            out_dir=out_dir,
            zipcode=zipcode,
            limit=limit,
            make_preview=make_preview,
            make_value_hist=make_value_hist,
        )

    def _parse_calibration_params(self, p: Dict[str, Any]) -> CalibrationStudyParams:
        out_dir = Path(p.get("out") or p.get("out_dir") or "./qa_plots")
        zipcode = p.get("zipcode")
        if isinstance(zipcode, str) and zipcode:
            from ..core.identity import parse_zipcode_key
            zipcode = parse_zipcode_key(zipcode)
        limit = p.get("limit")
        try:
            limit = int(limit) if limit is not None else None
        except Exception:
            limit = None
        kinds_raw = p.get("kinds")
        kinds = ("master_flat", "master_cmp")
        if isinstance(kinds_raw, str) and kinds_raw.strip():
            kinds = tuple(s.strip() for s in kinds_raw.split(",") if s.strip()) or kinds
        make_p95_hist = bool(p.get("make_p95_hist", True))
        make_badfrac_trend = bool(p.get("make_badfrac_trend", True))
        make_zero_map = bool(p.get("make_zero_map", True))
        return CalibrationStudyParams(
            out_dir=out_dir,
            zipcode=zipcode,
            limit=limit,
            kinds=kinds,
            make_p95_hist=make_p95_hist,
            make_badfrac_trend=make_badfrac_trend,
            make_zero_map=make_zero_map,
        )

    def _parse_instrument_health_params(self, p: Dict[str, Any]) -> InstrumentHealthParams:
        out_dir = Path(p.get("out") or p.get("out_dir") or "./qa_plots")
        zipcode = p.get("zipcode")
        if isinstance(zipcode, str) and zipcode:
            from ..core.identity import parse_zipcode_key
            zipcode = parse_zipcode_key(zipcode)
        limit = p.get("limit")
        try:
            limit = int(limit) if limit is not None else None
        except Exception:
            limit = None
        make_throughput_trend = bool(p.get("make_throughput_trend", True))
        make_bad_fiber_map = bool(p.get("make_bad_fiber_map", True))
        return InstrumentHealthParams(
            out_dir=out_dir,
            zipcode=zipcode,
            limit=limit,
            make_throughput_trend=make_throughput_trend,
            make_bad_fiber_map=make_bad_fiber_map,
        )

    def _parse_trending_params(self, p: Dict[str, Any]) -> TrendingStudyParams:
        out_dir = Path(p.get("out") or p.get("out_dir") or "./qa_plots")
        zipcode = p.get("zipcode")
        if isinstance(zipcode, str) and zipcode:
            from ..core.identity import parse_zipcode_key
            zipcode = parse_zipcode_key(zipcode)
        limit = p.get("limit")
        try:
            limit = int(limit) if limit is not None else None
        except Exception:
            limit = None
        kind = str(p.get("kind") or p.get("trend_kind") or "").strip()
        metric = str(p.get("metric") or "").strip()
        since = p.get("since")
        until = p.get("until")
        return TrendingStudyParams(
            out_dir=out_dir,
            kind=kind,
            metric=metric,
            zipcode=zipcode,
            limit=limit,
            since=since,
            until=until,
        )

    def _parse_reports_params(self, p: Dict[str, Any]) -> ReportsStudyParams:
        out_dir = Path(p.get("out") or p.get("out_dir") or "./qa_plots")
        report_kind = str(p.get("report_kind") or p.get("report") or "").strip() or "daily_calib"
        zipcode = p.get("zipcode")
        if isinstance(zipcode, str) and zipcode:
            from ..core.identity import parse_zipcode_key
            zipcode = parse_zipcode_key(zipcode)
        limit = p.get("limit")
        try:
            limit = int(limit) if limit is not None else None
        except Exception:
            limit = None
        return ReportsStudyParams(
            out_dir=out_dir,
            report_kind=report_kind,
            zipcode=zipcode,
            limit=limit,
        )
