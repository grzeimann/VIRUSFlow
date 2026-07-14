from __future__ import annotations

from .base import CalibrationTask
from ..algorithms.bias import step_bias
from ..algorithms.dark import step_dark
from ..algorithms.flat import step_flt
from ..algorithms.trace import step_trace
from ..algorithms.twi import step_twi
import numpy as np
from ..algorithms.wave import step_wave
from ..algorithms.cmp import step_cmp
from ..artifacts import ArtifactService, Scope
from ..artifacts.materialize import ArtifactMaterializer


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


class CmpTask(CalibrationTask):
    """Comparison-lamp master-frame task using generic CalibrationTask.

    - Uses comparison frames (frame_type='cmp')
    - Produces 'master_cmp' artifact via algorithms.cmp.step_cmp
    """
    name = "cmp"
    version = "v1"

    frame_type = "cmp"
    artifact_name = "master_cmp"
    algorithm = step_cmp


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

    # Declarative dependency: requires a 'flat' task for the same target
    requires = ["flat"]

    # CalibrationTask configuration (no raw inputs gathered via frame_type)
    frame_type = None  # override query_inputs to supply parent master_flat only
    artifact_name = "trace"
    algorithm = step_trace

    def _require_target(self) -> None:
        return super()._require_target()

    def _parse_date(self, s: str):
        return super()._parse_date(s)


    def query_inputs(self):
        # Provide no raw inputs to the algorithm, but return parent_ids for provenance
        self._require_target()
        mf = self._resolve_artifact("master_flat", required=True)
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

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()

        # Resolve dependency master_flat and parent provenance
        mf = self._resolve_artifact("master_flat", required=True)
        parent_ids = [str(mf.get("id"))] if mf.get("id") is not None else []

        out_path = self.output_path()

        # Build algorithm params: merge self.params, ctx.config and required identifiers
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        # Inject registry context for artifact-centric loading
        try:
            algo_params.setdefault("db_path", self.ctx.db_path)
        except Exception:
            pass
        if getattr(self.target, "zipcode", None) is not None:
            algo_params.setdefault("zipcode", getattr(self.target, "zipcode"))
        # Inject required fields for step_trace (direct path fallback)
        algo_params.setdefault("master_flat_path", mf.get("path"))
        zc = getattr(self.target, "zipcode", None)
        if zc is not None:
            try:
                # Normalize numeric IDs to 3-digit zero-padded strings for reference lookup consistency
                def _pad3(v: object) -> str:
                    s = str(v).strip()
                    return s.zfill(3) if s.isdigit() and len(s) < 3 else s
                algo_params.setdefault("specid", _pad3(getattr(zc, "specid", None)))
                algo_params.setdefault("ifuslot", _pad3(getattr(zc, "ifuslot", None)))
                algo_params.setdefault("ifuid", _pad3(getattr(zc, "ifuid", None)))
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
        else:
            # Default to repository root (containing Fiber_Locations) when available
            try:
                from ..algorithms.trace import default_virusconfig_root as _vf_default_root
                algo_params.setdefault("virusconfig", _vf_default_root())
            except Exception:
                # Last-resort historical path
                algo_params.setdefault("virusconfig", "/work/03946/hetdex/maverick/virus_config")

        t1 = time.perf_counter()
        meta = self.algorithm(output_path=out_path, params=algo_params)
        t2 = time.perf_counter()

        artifact = self.make_artifact(out_path)
        art_id = self.save_artifact(artifact, parent_ids=parent_ids)
        try:
            setattr(artifact, "id", int(art_id))
        except Exception:
            pass

        # QA hook for 'trace'
        try:
            svc = ArtifactService(self.ctx.db_path)
            status = svc.diagnostics.evaluate_and_save(artifact_id=art_id, kind="trace", meta=dict(meta or {}))
            if dbg and status:
                print(f"[QA] TraceTask: auto-qa status={status} artifact_id={art_id}")
            try:
                if svc.diagnostics.should_block(kind="trace", status=status):
                    raise RuntimeError(f"QA hard-fail for trace (artifact_id={art_id})")
            except Exception:
                pass
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

    # Declarative dependency: requires 'trace' and 'cmp' tasks for the same target
    requires = ["trace", "cmp"]

    # No raw inputs; depends on prior artifacts
    frame_type = None
    artifact_name = "wave"
    algorithm = None  # not used; run() is overridden


    def query_inputs(self):
        # No raw inputs; establish parents from existing artifacts
        self._require_target()
        mc = self._resolve_artifact("master_cmp", required=True)
        tr = self._resolve_artifact("trace", required=True)
        parent_ids = [pid for pid in [mc.get("id"), tr.get("id")] if pid is not None]
        return [], parent_ids

    def run(self, inputs):
        import time
        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()

        mc = self._resolve_artifact("master_cmp", required=True)
        tr = self._resolve_artifact("trace", required=True)
        parent_ids = [str(x) for x in (mc.get("id"), tr.get("id")) if x is not None]

        out_path = self.output_path()

        # Load required arrays using artifact-centric materializer
        svc = ArtifactService(self.ctx.db_path)
        mat = ArtifactMaterializer(svc)
        def _load_with_fallback(row: dict, kind: str):
            from pathlib import Path as _Path
            try:
                return mat.load_by_id(int(row.get("id"))), row
            except FileNotFoundError:
                # Attempt to reselect a better candidate that actually exists on disk
                scope = Scope(getattr(self.target, "zipcode", None))
                at_time = self._target_mid_time()
                # Prefer latest_valid at target time
                cand = svc.select_best(kind=kind, scope=scope, at_time=at_time, policy="latest_valid")
                if not cand or not cand.get("path") or not _Path(str(cand.get("path"))).exists():
                    # Fallback to latest regardless of time
                    cand = svc.select_best(kind=kind, scope=scope, at_time=None, policy="latest")
                if cand and cand.get("id") and cand.get("path") and _Path(str(cand.get("path"))).exists():
                    try:
                        return mat.load_by_id(int(cand.get("id"))), cand
                    except Exception:
                        pass
                # Re-raise original if no viable candidate found
                raise

        lr_cmp_obj, mc = _load_with_fallback(mc, "master_cmp")
        lr_tr_obj, tr = _load_with_fallback(tr, "trace")
        master_cmp, hdr_cmp = lr_cmp_obj.data, lr_cmp_obj.header
        trace_2d, hdr_tr = lr_tr_obj.data, lr_tr_obj.header

        t1 = time.perf_counter()
        # Build algorithm params: merge task params with context config so CLI-provided qa_out_dir flows through
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        # Run wavelength algorithm
        meta = step_wave(master_cmp=master_cmp, trace=trace_2d, output_path=None, params=algo_params)
        t2 = time.perf_counter()

        wave = meta.get("wave") if isinstance(meta, dict) else None
        if wave is None:
            raise RuntimeError("WaveTask: step_wave returned no wavelength solution")

        # Persist wavelength solution via materializer
        rms_rows = meta.get("rms_rows") if isinstance(meta, dict) else None
        rms_med = float(np.nanmedian(np.asarray(rms_rows))) if (rms_rows is not None and np.asarray(rms_rows).size) else None
        sidecar = {
            "kind": "wave",
            "role": "calibration",
            "payload_type": "array",
            "storage_format": "fits",
        }
        if rms_med is not None:
            sidecar["rms_median"] = float(rms_med)
        mat.persist_array(
            out_path,
            data=wave,
            n_inputs=int(hdr_cmp.get("NINPUTS", 0)),
            algo_version=meta.get("version", "wave-1.0"),
            extra_header={
                "SRCCMP": str(mc.get("path")),
                "SRCTRACE": str(tr.get("path")),
            },
            sidecar=sidecar,
        )

        artifact = self.make_artifact(out_path)
        art_id = self.save_artifact(artifact, parent_ids=parent_ids)
        try:
            setattr(artifact, "id", int(art_id))
        except Exception:
            pass

        # QA hook for 'wave'
        try:
            svc = ArtifactService(self.ctx.db_path)
            status = svc.diagnostics.evaluate_and_save(artifact_id=art_id, kind="wave", meta=dict(meta or {}))
            if dbg and status:
                print(f"[QA] WaveTask: auto-qa status={status} artifact_id={art_id}")
            try:
                if svc.diagnostics.should_block(kind="wave", status=status):
                    raise RuntimeError(f"QA hard-fail for wave (artifact_id={art_id})")
            except Exception:
                pass
        except Exception:
            pass

        if dbg:
            print(f"[Timing] WaveTask: dependency+io={t1 - t0:.3f}s, algo={t2 - t1:.3f}s, total={time.perf_counter() - t0:.3f}s")
        return {self.artifact_name or "wave": artifact}
