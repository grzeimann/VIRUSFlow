from __future__ import annotations

from .base import CalibrationTask
from ..algorithms.bias import step_bias
from ..algorithms.dark import step_dark
from ..algorithms.flat import step_flt
from ..algorithms.trace import step_trace
from ..algorithms.twi import step_twi
import numpy as np
from ..algorithms.wave import step_wave
from ..core.artifacts import load_master_cmp, load_trace_solution, save_wave_solution


class BiasTask(CalibrationTask):
    """Bias master-frame task scoped by a BiasTarget, using the generic CalibrationTask template."""
    name = "bias"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "zro"
    artifact_name = "master_bias"
    algorithm = step_bias


class DarkTask(CalibrationTask):
    """Dark master-frame task scoped by a target, using the generic CalibrationTask template.

    Differences from BiasTask:
    - Uses dark frames (frame_type='drk')
    - Produces 'master_dark' artifact via algorithms.dark.step_dark
    """
    name = "dark"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "drk"
    artifact_name = "master_dark"
    algorithm = step_dark


class FlatTask(CalibrationTask):
    """Flat master-frame task using generic CalibrationTask.

    - Uses flat frames (frame_type='flt')
    - Produces 'master_flat' artifact via algorithms.flat.step_flt
    """
    name = "flat"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "flt"
    artifact_name = "master_flat"
    algorithm = step_flt


class TwiTask(CalibrationTask):
    """Twilight master-frame task using generic CalibrationTask.

    - Uses twilight frames (frame_type='twi')
    - Produces 'master_twi' artifact via algorithms.twi.step_twi
    """
    name = "twi"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "twi"
    artifact_name = "master_twi"
    algorithm = step_twi


class TraceTask(CalibrationTask):
    """Trace calibration task implemented as a CalibrationTask.

    - Depends on an existing 'master_flat' artifact for the same zipcode/window.
    - Produces a 'trace' artifact using algorithms.trace.step_trace.
    - Registers provenance with the master_flat as parent and runs QA(kind='trace').
    """
    name = "trace"
    version = "v1"

    # CalibrationTask configuration (no raw inputs gathered via frame_type)
    frame_type = None  # override query_inputs to supply parent master_flat only
    artifact_name = "trace"
    algorithm = step_trace

    def _require_target(self) -> None:
        return super()._require_target()

    def _parse_date(self, s: str):
        return super()._parse_date(s)

    def _resolve_master_flat(self) -> dict:
        from ..registry import database as _db
        zipcode = getattr(self.target, "zipcode", None)
        vstart = self._parse_date(getattr(self.target, "start_date", None))
        vend = self._parse_date(getattr(self.target, "end_date", None))
        at_time = None
        if vstart and vend:
            at_time = vstart + (vend - vstart) / 2
        else:
            at_time = vstart or vend
        flats = _db.find_artifacts(kind="master_flat", zipcode=zipcode, at_time=at_time, db_path=self.ctx.db_path, limit=1)
        if not flats:
            flats = _db.find_artifacts(kind="master_flat", zipcode=zipcode, db_path=self.ctx.db_path, limit=1)
        if not flats:
            zkey = zipcode.key() if zipcode else "UNKNOWN"
            raise RuntimeError(f"TraceTask requires an existing master_flat for zipcode={zkey} in the given date window")
        return flats[0]

    def query_inputs(self):
        # Provide no raw inputs to the algorithm, but return parent_ids for provenance
        self._require_target()
        mf = self._resolve_master_flat()
        parent_ids = [pid for pid in [mf.get("id")] if pid is not None]
        return [], parent_ids

    def output_path(self) -> str:
        # Reuse CalibrationTask output_path which uses artifact_name
        return super().output_path()

    def make_artifact(self, out_path: str):
        # Reuse CalibrationTask make_artifact but ensure kind/name = 'trace'
        return super().make_artifact(out_path)

    def run(self, inputs):
        import time
        from ..qa import diagnostics as qa_diag

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()

        # Resolve dependency master_flat and parent provenance
        mf = self._resolve_master_flat()
        parent_ids = [str(mf.get("id"))] if mf.get("id") is not None else []

        out_path = self.output_path()

        # Build algorithm params: merge self.params, ctx.config and required identifiers
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        # Inject required fields for step_trace
        algo_params.setdefault("master_flat_path", mf.get("path"))
        zc = getattr(self.target, "zipcode", None)
        if zc is not None:
            try:
                algo_params.setdefault("specid", str(getattr(zc, "specid", None)))
                algo_params.setdefault("ifuslot", str(getattr(zc, "ifuslot", None)))
                algo_params.setdefault("ifuid", str(getattr(zc, "ifuid", None)))
                algo_params.setdefault("amp", str(getattr(zc, "amp", None)))
            except Exception:
                pass
        if getattr(self.target, "start_date", None) is not None:
            algo_params.setdefault("obsdate", str(self.target.start_date))
        # virusconfig root if available in config
        try:
            vc = self.ctx.config.get("virusconfig") if isinstance(self.ctx.config, dict) else None
            vc = vc or (self.ctx.config.get("trace_config") if isinstance(self.ctx.config, dict) else None)
            vc = vc or (self.ctx.config.get("tr_folder") if isinstance(self.ctx.config, dict) else None)
        except Exception:
            vc = None
        if vc:
            algo_params.setdefault("virusconfig", str(vc))

        t1 = time.perf_counter()
        meta = self.algorithm(raw_inputs=None, output_path=out_path, params=algo_params)
        t2 = time.perf_counter()

        artifact = self.make_artifact(out_path)
        art_id = self.save_artifact(artifact, parent_ids=parent_ids)
        try:
            setattr(artifact, "id", int(art_id))
        except Exception:
            pass

        # QA hook for 'trace'
        try:
            status = qa_diag.evaluate_and_save(artifact_id=art_id, kind="trace", meta=dict(meta or {}), db_path=self.ctx.db_path)
            if dbg and status:
                print(f"[QA] TraceTask: auto-qa status={status} artifact_id={art_id}")
        except Exception:
            pass

        if dbg:
            print(f"[Timing] TraceTask: dependency+io={t1 - t0:.3f}s, algo={t2 - t1:.3f}s, total={time.perf_counter() - t0:.3f}s")
        return {self.artifact_name or "trace": artifact}


class WaveTask(CalibrationTask):
    """Wavelength calibration task implemented as a CalibrationTask.

    - Depends on existing 'master_cmp' and 'trace' artifacts for the same zipcode/window.
    - Produces a 'wave' artifact using algorithms.wave.step_wave.
    - Registers provenance with both master_cmp and trace as parents and runs QA(kind='wave').
    """
    name = "wave"
    version = "v1"

    # No raw inputs; depends on prior artifacts
    frame_type = None
    artifact_name = "wave"
    algorithm = None  # not used; run() is overridden

    def _resolve_master_cmp(self) -> dict:
        from ..registry import database as _db
        zipcode = getattr(self.target, "zipcode", None)
        vstart = self._parse_date(getattr(self.target, "start_date", None))
        vend = self._parse_date(getattr(self.target, "end_date", None))
        at_time = vstart + (vend - vstart) / 2 if (vstart and vend) else (vstart or vend)
        rows = _db.find_artifacts(kind="master_cmp", zipcode=zipcode, at_time=at_time, db_path=self.ctx.db_path, limit=1)
        if not rows:
            rows = _db.find_artifacts(kind="master_cmp", zipcode=zipcode, db_path=self.ctx.db_path, limit=1)
        if not rows:
            zkey = zipcode.key() if zipcode else "UNKNOWN"
            raise RuntimeError(f"WaveTask requires an existing master_cmp for zipcode={zkey} in the given date window")
        return rows[0]

    def _resolve_trace(self) -> dict:
        from ..registry import database as _db
        zipcode = getattr(self.target, "zipcode", None)
        vstart = self._parse_date(getattr(self.target, "start_date", None))
        vend = self._parse_date(getattr(self.target, "end_date", None))
        at_time = vstart + (vend - vstart) / 2 if (vstart and vend) else (vstart or vend)
        rows = _db.find_artifacts(kind="trace", zipcode=zipcode, at_time=at_time, db_path=self.ctx.db_path, limit=1)
        if not rows:
            rows = _db.find_artifacts(kind="trace", zipcode=zipcode, db_path=self.ctx.db_path, limit=1)
        if not rows:
            zkey = zipcode.key() if zipcode else "UNKNOWN"
            raise RuntimeError(f"WaveTask requires an existing trace for zipcode={zkey} in the given date window")
        return rows[0]

    def query_inputs(self):
        # No raw inputs; establish parents from existing artifacts
        self._require_target()
        mc = self._resolve_master_cmp()
        tr = self._resolve_trace()
        parent_ids = [pid for pid in [mc.get("id"), tr.get("id")] if pid is not None]
        return [], parent_ids

    def run(self, inputs):
        import time
        from ..qa import diagnostics as qa_diag
        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()

        mc = self._resolve_master_cmp()
        tr = self._resolve_trace()
        parent_ids = [str(x) for x in (mc.get("id"), tr.get("id")) if x is not None]

        out_path = self.output_path()

        # Load required arrays
        master_cmp, hdr_cmp = load_master_cmp(str(mc.get("path")))
        trace_2d, hdr_tr = load_trace_solution(str(tr.get("path")))

        t1 = time.perf_counter()
        # Run wavelength algorithm
        meta = step_wave(master_cmp=master_cmp, trace=trace_2d, output_path=None, params=self.params)
        t2 = time.perf_counter()

        wave = meta.get("wave") if isinstance(meta, dict) else None
        if wave is None:
            raise RuntimeError("WaveTask: step_wave returned no wavelength solution")

        # Persist wavelength solution
        rms_rows = meta.get("rms_rows") if isinstance(meta, dict) else None
        rms_med = float(np.nanmedian(np.asarray(rms_rows))) if rms_rows is not None else None
        save_wave_solution(
            out_path,
            wave_2d=wave,
            n_inputs=int(hdr_cmp.get("NINPUTS", 0)),
            algo_version=meta.get("version", "wave-1.0"),
            rms_median=rms_med,
            extra_header={
                "SRCCMP": str(mc.get("path")),
                "SRCTRACE": str(tr.get("path")),
            },
        )

        artifact = self.make_artifact(out_path)
        art_id = self.save_artifact(artifact, parent_ids=parent_ids)
        try:
            setattr(artifact, "id", int(art_id))
        except Exception:
            pass

        # QA hook for 'wave'
        try:
            status = qa_diag.evaluate_and_save(artifact_id=art_id, kind="wave", meta=dict(meta or {}), db_path=self.ctx.db_path)
            if dbg and status:
                print(f"[QA] WaveTask: auto-qa status={status} artifact_id={art_id}")
        except Exception:
            pass

        if dbg:
            print(f"[Timing] WaveTask: dependency+io={t1 - t0:.3f}s, algo={t2 - t1:.3f}s, total={time.perf_counter() - t0:.3f}s")
        return {self.artifact_name or "wave": artifact}
