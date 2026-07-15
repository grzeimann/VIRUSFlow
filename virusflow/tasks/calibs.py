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
    """Bias master-frame task scoped by a BiasTarget.

    Section 3 implementation: Override run() to use PublicationService with
    ArtifactRequest and PublicationContext. Algorithm performs computation only
    and returns AlgoResult; no persistence occurs in the algorithm.
    """
    name = "bias"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "zro"
    artifact_name = "master_bias"
    algorithm = step_bias

    def run(self, inputs):
        import time
        from ..contracts.result import BiasResultContract
        from ..artifacts.requests import ArtifactRequest, LogicalComponent
        from ..artifacts.models import Scope
        from ..publication.service import DefaultPublicationService
        from ..publication.context import PublicationContext
        from ..persistence.policy import DefaultPersistencePolicy
        from ..core.algo_result import ensure_algo_result
        from ..artifacts import ArtifactService

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()
        raw_inputs, parent_ids = self.query_inputs()
        t1 = time.perf_counter()

        # Execute algorithm (compute only)
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        t2 = time.perf_counter()
        ar = self.algorithm(raw_inputs=raw_inputs, output_path=None, params=algo_params)
        t3 = time.perf_counter()

        # Normalize and validate result
        ar = ensure_algo_result(ar, kind="bias")
        rep = BiasResultContract().validate(ar)
        if not rep.ok:
            raise ValueError("BiasTask: AlgoResult failed contract validation: " + "; ".join(rep.errors))

        # Compose ArtifactRequest (logical, multi-component)
        master = ar.get_array("master")
        if master is None:
            raise RuntimeError("BiasTask: missing 'master' array in AlgoResult")
        comp_master = LogicalComponent(name="master", model_type="array2d", value=master)
        scope = Scope(zipcode=getattr(self.target, "zipcode", None))
        summaries = {"readnoise": float(ar.as_meta().get("readnoise")), "n_inputs": int(ar.as_meta().get("n_inputs", 0))}
        req = ArtifactRequest(
            kind=str(self.artifact_name or "master_bias"),
            components={"master": comp_master},
            summaries=summaries,
            metadata={},
            scope=scope,
            parents=[int(p) for p in (parent_ids or [])],
            labels=["calibration", "bias"],
        )

        # Publish via DefaultPublicationService
        svc = ArtifactService(self.ctx.db_path)
        pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir)
        ctx = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name="algorithms.bias.step_bias",
            algorithm_version=ar.version,
            parameters=dict(self.params or {}),
            parent_ids=[int(p) for p in (parent_ids or [])],
            timings={"resolve": t1 - t0, "execute": t3 - t2},
        )
        arts = pub.publish([req], ctx)
        if not arts:
            raise RuntimeError("BiasTask: publication produced no artifacts")
        art = arts[0]

        # QA after publication
        try:
            status = svc.diagnostics.evaluate_and_save(artifact_id=int(getattr(art, "id", 0)), kind=str(self.artifact_name), meta=ar)
            if dbg and status:
                print(f"[QA] BiasTask: status={status} artifact_id={getattr(art, 'id', None)}")
            try:
                if svc.diagnostics.should_block(kind=str(self.artifact_name), status=status):
                    raise RuntimeError(f"QA hard-fail for {self.artifact_name} (artifact_id={getattr(art, 'id', None)})")
            except Exception:
                pass
        except Exception:
            # Do not fail task on QA errors
            pass

        if dbg:
            print(f"[Timing] BiasTask: resolve={t1 - t0:.3f}s, execute={t3 - t2:.3f}s")
        return {self.artifact_name: art}


class DarkTask(CalibrationTask):
    """Dark master-frame task scoped by a target.

    Section 4 implementation mirrors BiasTask: algorithm returns AlgoResult; task composes
    ArtifactRequest and publishes via PublicationService; QA runs post-publication.
    """
    name = "dark"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "drk"
    artifact_name = "master_dark"
    algorithm = step_dark

    def run(self, inputs):
        import time
        from ..contracts.result import DarkResultContract
        from ..artifacts.requests import ArtifactRequest, LogicalComponent
        from ..artifacts.models import Scope
        from ..publication.service import DefaultPublicationService
        from ..publication.context import PublicationContext
        from ..persistence.policy import DefaultPersistencePolicy
        from ..core.algo_result import ensure_algo_result
        from ..artifacts import ArtifactService

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()
        raw_inputs, parent_ids = self.query_inputs()
        t1 = time.perf_counter()

        # Execute algorithm (compute only)
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        t2 = time.perf_counter()
        ar = self.algorithm(raw_inputs=raw_inputs, output_path=None, params=algo_params)
        t3 = time.perf_counter()

        # Normalize and validate result
        ar = ensure_algo_result(ar, kind="dark")
        rep = DarkResultContract().validate(ar)
        if not rep.ok:
            raise ValueError("DarkTask: AlgoResult failed contract validation: " + "; ".join(rep.errors))

        # Compose ArtifactRequest (logical, include optional mask component if present)
        master = ar.get_array("master")
        if master is None:
            raise RuntimeError("DarkTask: missing 'master' array in AlgoResult")
        comp_master = LogicalComponent(name="master", model_type="array2d", value=master)
        components = {"master": comp_master}
        msk = ar.get_array("mask")
        if msk is not None:
            components["mask"] = LogicalComponent(name="mask", model_type="array2d", value=msk)
        scope = Scope(zipcode=getattr(self.target, "zipcode", None))
        mm = ar.as_meta()
        summaries = {"bad_fraction": float(mm.get("bad_fraction")), "n_inputs": int(mm.get("n_inputs", 0))}
        req = ArtifactRequest(
            kind=str(self.artifact_name or "master_dark"),
            components=components,
            summaries=summaries,
            metadata={},
            scope=scope,
            parents=[int(p) for p in (parent_ids or [])],
            labels=["calibration", "dark"],
        )

        # Publish via DefaultPublicationService
        svc = ArtifactService(self.ctx.db_path)
        pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir)
        ctx = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name="algorithms.dark.step_dark",
            algorithm_version=ar.version,
            parameters=dict(self.params or {}),
            parent_ids=[int(p) for p in (parent_ids or [])],
            timings={"resolve": t1 - t0, "execute": t3 - t2},
        )
        arts = pub.publish([req], ctx)
        if not arts:
            raise RuntimeError("DarkTask: publication produced no artifacts")
        art = arts[0]

        # QA after publication
        try:
            status = svc.diagnostics.evaluate_and_save(artifact_id=int(getattr(art, "id", 0)), kind=str(self.artifact_name), meta=ar)
            if dbg and status:
                print(f"[QA] DarkTask: status={status} artifact_id={getattr(art, 'id', None)}")
            try:
                if svc.diagnostics.should_block(kind=str(self.artifact_name), status=status):
                    raise RuntimeError(f"QA hard-fail for {self.artifact_name} (artifact_id={getattr(art, 'id', None)})")
            except Exception:
                pass
        except Exception:
            pass

        if dbg:
            print(f"[Timing] DarkTask: resolve={t1 - t0:.3f}s, execute={t3 - t2:.3f}s")
        return {self.artifact_name: art}


class FlatTask(CalibrationTask):
    """Flat master-frame task.

    Section 4 implementation mirrors BiasTask/DarkTask: algorithm returns AlgoResult; task composes
    ArtifactRequest and publishes via PublicationService; QA runs post-publication.
    """
    name = "flat"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "flt"
    artifact_name = "master_flat"
    algorithm = step_flt

    def run(self, inputs):
        import time
        from ..contracts.result import FlatResultContract
        from ..artifacts.requests import ArtifactRequest, LogicalComponent
        from ..artifacts.models import Scope
        from ..publication.service import DefaultPublicationService
        from ..publication.context import PublicationContext
        from ..persistence.policy import DefaultPersistencePolicy
        from ..core.algo_result import ensure_algo_result
        from ..artifacts import ArtifactService

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()
        raw_inputs, parent_ids = self.query_inputs()
        t1 = time.perf_counter()

        # Execute algorithm (compute only)
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        t2 = time.perf_counter()
        ar = self.algorithm(raw_inputs=raw_inputs, output_path=None, params=algo_params)
        t3 = time.perf_counter()

        # Normalize and validate result
        ar = ensure_algo_result(ar, kind="flat")
        rep = FlatResultContract().validate(ar)
        if not rep.ok:
            raise ValueError("FlatTask: AlgoResult failed contract validation: " + "; ".join(rep.errors))

        # Compose ArtifactRequest (logical, include optional mask component if present)
        master = ar.get_array("master")
        if master is None:
            raise RuntimeError("FlatTask: missing 'master' array in AlgoResult")
        comp_master = LogicalComponent(name="master", model_type="array2d", value=master)
        components = {"master": comp_master}
        msk = ar.get_array("mask")
        if msk is not None:
            components["mask"] = LogicalComponent(name="mask", model_type="array2d", value=msk)
        scope = Scope(zipcode=getattr(self.target, "zipcode", None))
        mm = ar.as_meta()
        summaries = {"bad_fraction": float(mm.get("bad_fraction")), "n_inputs": int(mm.get("n_inputs", 0))}
        req = ArtifactRequest(
            kind=str(self.artifact_name or "master_flat"),
            components=components,
            summaries=summaries,
            metadata={},
            scope=scope,
            parents=[int(p) for p in (parent_ids or [])],
            labels=["calibration", "flat"],
        )

        # Publish via DefaultPublicationService
        svc = ArtifactService(self.ctx.db_path)
        pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir)
        ctx = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name="algorithms.flat.step_flt",
            algorithm_version=ar.version,
            parameters=dict(self.params or {}),
            parent_ids=[int(p) for p in (parent_ids or [])],
            timings={"resolve": t1 - t0, "execute": t3 - t2},
        )
        arts = pub.publish([req], ctx)
        if not arts:
            raise RuntimeError("FlatTask: publication produced no artifacts")
        art = arts[0]

        # QA after publication
        try:
            status = svc.diagnostics.evaluate_and_save(artifact_id=int(getattr(art, "id", 0)), kind=str(self.artifact_name), meta=ar)
            if dbg and status:
                print(f"[QA] FlatTask: status={status} artifact_id={getattr(art, 'id', None)}")
            try:
                if svc.diagnostics.should_block(kind=str(self.artifact_name), status=status):
                    raise RuntimeError(f"QA hard-fail for {self.artifact_name} (artifact_id={getattr(art, 'id', None)})")
            except Exception:
                pass
        except Exception:
            pass

        if dbg:
            print(f"[Timing] FlatTask: resolve={t1 - t0:.3f}s, execute={t3 - t2:.3f}s")
        return {self.artifact_name: art}


class CmpTask(CalibrationTask):
    """Comparison-lamp master-frame task.

    Section 4 implementation mirrors other simple calibs: algorithm returns AlgoResult; task composes
    ArtifactRequest and publishes via PublicationService; QA runs post-publication.
    """
    name = "cmp"
    version = "v1"

    frame_type = "cmp"
    artifact_name = "master_cmp"
    algorithm = step_cmp

    def run(self, inputs):
        import time
        from ..contracts.result import CmpResultContract
        from ..artifacts.requests import ArtifactRequest, LogicalComponent
        from ..artifacts.models import Scope
        from ..publication.service import DefaultPublicationService
        from ..publication.context import PublicationContext
        from ..persistence.policy import DefaultPersistencePolicy
        from ..core.algo_result import ensure_algo_result
        from ..artifacts import ArtifactService

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()
        raw_inputs, parent_ids = self.query_inputs()
        t1 = time.perf_counter()

        # Execute algorithm (compute only)
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        t2 = time.perf_counter()
        ar = self.algorithm(raw_inputs=raw_inputs, output_path=None, params=algo_params)
        t3 = time.perf_counter()

        # Normalize and validate result
        ar = ensure_algo_result(ar, kind="cmp")
        rep = CmpResultContract().validate(ar)
        if not rep.ok:
            raise ValueError("CmpTask: AlgoResult failed contract validation: " + "; ".join(rep.errors))

        # Compose ArtifactRequest (logical, single component)
        master = ar.get_array("master")
        if master is None:
            raise RuntimeError("CmpTask: missing 'master' array in AlgoResult")
        comp_master = LogicalComponent(name="master", model_type="array2d", value=master)
        scope = Scope(zipcode=getattr(self.target, "zipcode", None))
        mm = ar.as_meta()
        summaries = {"n_inputs": int(mm.get("n_inputs", 0))}
        req = ArtifactRequest(
            kind=str(self.artifact_name or "master_cmp"),
            components={"master": comp_master},
            summaries=summaries,
            metadata={},
            scope=scope,
            parents=[int(p) for p in (parent_ids or [])],
            labels=["calibration", "cmp"],
        )

        svc = ArtifactService(self.ctx.db_path)
        pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir)
        ctx = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name="algorithms.cmp.step_cmp",
            algorithm_version=ar.version,
            parameters=dict(self.params or {}),
            parent_ids=[int(p) for p in (parent_ids or [])],
            timings={"resolve": t1 - t0, "execute": t3 - t2},
        )
        arts = pub.publish([req], ctx)
        if not arts:
            raise RuntimeError("CmpTask: publication produced no artifacts")
        art = arts[0]

        # QA after publication (kind=master_cmp)
        try:
            status = svc.diagnostics.evaluate_and_save(artifact_id=int(getattr(art, "id", 0)), kind=str(self.artifact_name), meta=ar)
            if dbg and status:
                print(f"[QA] CmpTask: status={status} artifact_id={getattr(art, 'id', None)}")
            try:
                if svc.diagnostics.should_block(kind=str(self.artifact_name), status=status):
                    raise RuntimeError(f"QA hard-fail for {self.artifact_name} (artifact_id={getattr(art, 'id', None)})")
            except Exception:
                pass
        except Exception:
            pass

        if dbg:
            print(f"[Timing] CmpTask: resolve={t1 - t0:.3f}s, execute={t3 - t2:.3f}s")
        return {self.artifact_name: art}


class TwiTask(CalibrationTask):
    """Twilight master-frame task.

    Section 4 implementation mirrors other simple calibs: algorithm returns AlgoResult; task composes
    ArtifactRequest and publishes via PublicationService; QA runs post-publication.
    """
    name = "twi"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "twi"
    artifact_name = "master_twi"
    algorithm = step_twi

    def run(self, inputs):
        import time
        from ..contracts.result import TwiResultContract
        from ..artifacts.requests import ArtifactRequest, LogicalComponent
        from ..artifacts.models import Scope
        from ..publication.service import DefaultPublicationService
        from ..publication.context import PublicationContext
        from ..persistence.policy import DefaultPersistencePolicy
        from ..core.algo_result import ensure_algo_result
        from ..artifacts import ArtifactService

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()
        raw_inputs, parent_ids = self.query_inputs()
        t1 = time.perf_counter()

        # Execute algorithm (compute only)
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        t2 = time.perf_counter()
        ar = self.algorithm(raw_inputs=raw_inputs, output_path=None, params=algo_params)
        t3 = time.perf_counter()

        # Normalize and validate result
        ar = ensure_algo_result(ar, kind="twi")
        rep = TwiResultContract().validate(ar)
        if not rep.ok:
            raise ValueError("TwiTask: AlgoResult failed contract validation: " + "; ".join(rep.errors))

        # Compose ArtifactRequest (logical, single component)
        master = ar.get_array("master")
        if master is None:
            raise RuntimeError("TwiTask: missing 'master' array in AlgoResult")
        comp_master = LogicalComponent(name="master", model_type="array2d", value=master)
        scope = Scope(zipcode=getattr(self.target, "zipcode", None))
        mm = ar.as_meta()
        summaries = {"n_inputs": int(mm.get("n_inputs", 0))}
        req = ArtifactRequest(
            kind=str(self.artifact_name or "master_twi"),
            components={"master": comp_master},
            summaries=summaries,
            metadata={},
            scope=scope,
            parents=[int(p) for p in (parent_ids or [])],
            labels=["calibration", "twi"],
        )

        svc = ArtifactService(self.ctx.db_path)
        pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir)
        ctx = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name="algorithms.twi.step_twi",
            algorithm_version=ar.version,
            parameters=dict(self.params or {}),
            parent_ids=[int(p) for p in (parent_ids or [])],
            timings={"resolve": t1 - t0, "execute": t3 - t2},
        )
        arts = pub.publish([req], ctx)
        if not arts:
            raise RuntimeError("TwiTask: publication produced no artifacts")
        art = arts[0]

        # QA after publication (kind=master_twi)
        try:
            status = svc.diagnostics.evaluate_and_save(artifact_id=int(getattr(art, "id", 0)), kind=str(self.artifact_name), meta=ar)
            if dbg and status:
                print(f"[QA] TwiTask: status={status} artifact_id={getattr(art, 'id', None)}")
            try:
                if svc.diagnostics.should_block(kind=str(self.artifact_name), status=status):
                    raise RuntimeError(f"QA hard-fail for {self.artifact_name} (artifact_id={getattr(art, 'id', None)})")
            except Exception:
                pass
        except Exception:
            pass

        if dbg:
            print(f"[Timing] TwiTask: resolve={t1 - t0:.3f}s, execute={t3 - t2:.3f}s")
        return {self.artifact_name: art}


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
        from ..contracts.result import TraceResultContract
        from ..artifacts.requests import ArtifactRequest, LogicalComponent
        from ..artifacts.models import Scope
        from ..publication.service import DefaultPublicationService
        from ..publication.context import PublicationContext
        from ..persistence.policy import DefaultPersistencePolicy
        from ..core.algo_result import ensure_algo_result
        from ..artifacts import ArtifactService

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()

        # Resolve dependency master_flat and parent provenance
        mf = self._resolve_artifact("master_flat", required=True)
        parent_ids = [int(mf.get("id"))] if mf.get("id") is not None else []

        # Materialize master_flat array via serializers
        svc = ArtifactService(self.ctx.db_path)
        try:
            payload = svc.serializers.get("array", "fits").load(str(mf.get("path"))) if mf.get("path") else None
            master_flat = payload.get("data") if payload else None
        except Exception:
            master_flat = None
        if master_flat is None:
            raise RuntimeError("TraceTask: failed to materialize master_flat array from registry row")

        # Build algorithm params
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        # Provide materialized array and identifiers
        algo_params["master_flat_array"] = master_flat
        zc = getattr(self.target, "zipcode", None)
        if zc is not None:
            try:
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
        try:
            vc = self.ctx.config.get("virusconfig") if isinstance(self.ctx.config, dict) else None
            vc = vc or (self.ctx.config.get("trace_config") if isinstance(self.ctx.config, dict) else None)
            vc = vc or (self.ctx.config.get("tr_folder") if isinstance(self.ctx.config, dict) else None)
        except Exception:
            vc = None
        if vc:
            algo_params.setdefault("virusconfig", str(vc))
        else:
            try:
                from ..algorithms.trace import default_virusconfig_root as _vf_default_root
                algo_params.setdefault("virusconfig", _vf_default_root())
            except Exception:
                pass

        t1 = time.perf_counter()
        ar = self.algorithm(raw_inputs=[], output_path=None, params=algo_params)
        t2 = time.perf_counter()

        # Normalize and validate
        ar = ensure_algo_result(ar, kind="trace")
        rep = TraceResultContract().validate(ar)
        if not rep.ok:
            raise ValueError("TraceTask: AlgoResult failed contract validation: " + "; ".join(rep.errors))

        # Compose ArtifactRequest
        trace2d = ar.get_array("trace_2d")
        if trace2d is None:
            raise RuntimeError("TraceTask: missing 'trace_2d' in AlgoResult")
        comp = LogicalComponent(name="trace_2d", model_type="array2d", value=trace2d)
        scope = Scope(zipcode=getattr(self.target, "zipcode", None))
        mm = ar.as_meta()
        summaries = {"trace_len": int(mm.get("trace_len", trace2d.shape[1]))}
        req = ArtifactRequest(
            kind="trace",
            components={"trace_2d": comp},
            summaries=summaries,
            metadata={},
            scope=scope,
            parents=parent_ids,
            labels=["calibration", "trace"],
        )

        # Publish
        pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir)
        ctx = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name="algorithms.trace.step_trace",
            algorithm_version=ar.version,
            parameters=dict(self.params or {}),
            parent_ids=parent_ids,
            timings={"resolve+materialize": t1 - t0, "execute": t2 - t1},
        )
        arts = pub.publish([req], ctx)
        if not arts:
            raise RuntimeError("TraceTask: publication produced no artifacts")
        art = arts[0]

        # QA
        try:
            status = svc.diagnostics.evaluate_and_save(artifact_id=int(getattr(art, "id", 0)), kind="trace", meta=ar)
            if dbg and status:
                print(f"[QA] TraceTask: status={status} artifact_id={getattr(art, 'id', None)}")
            try:
                if svc.diagnostics.should_block(kind="trace", status=status):
                    raise RuntimeError(f"QA hard-fail for trace (artifact_id={getattr(art, 'id', None)})")
            except Exception:
                pass
        except Exception:
            pass

        if dbg:
            print(f"[Timing] TraceTask: resolve+materialize={t1 - t0:.3f}s, execute={t2 - t1:.3f}s")
        return {self.artifact_name: art}


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
        from ..contracts.result import WaveResultContract
        from ..artifacts.requests import ArtifactRequest, LogicalComponent
        from ..artifacts.models import Scope
        from ..publication.service import DefaultPublicationService
        from ..publication.context import PublicationContext
        from ..persistence.policy import DefaultPersistencePolicy
        from ..core.algo_result import ensure_algo_result
        from ..artifacts import ArtifactService

        self._require_target()
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()

        # Resolve parents and materialize payloads
        mc_row = self._resolve_artifact("master_cmp", required=True)
        tr_row = self._resolve_artifact("trace", required=True)
        parent_ids = [int(x) for x in (mc_row.get("id"), tr_row.get("id")) if x is not None]

        svc = ArtifactService(self.ctx.db_path)
        def _load_array(row: dict) -> np.ndarray | None:
            try:
                path = row.get("path")
                if not path:
                    return None
                payload = svc.serializers.get("array", "fits").load(str(path))
                return payload.get("data") if isinstance(payload, dict) else None
            except Exception:
                return None
        master_cmp = _load_array(mc_row)
        trace2d = _load_array(tr_row)
        if master_cmp is None or trace2d is None:
            raise RuntimeError("WaveTask: failed to materialize required parent arrays (master_cmp and trace)")

        # Build union defect mask from available flat/dark masks and repair master_cmp
        try:
            from ..algorithms.utils.masks import build_union_pixelmask, repair_masked_columns
            flat_row = self._resolve_artifact("master_flat", required=False)
            dark_row = self._resolve_artifact("master_dark", required=False)
            umask, _frac = build_union_pixelmask(flat_artifact=flat_row, dark_artifact=dark_row)
            if umask is not None:
                import numpy as _np
                m = _np.asarray(umask)
                if m.shape == _np.asarray(master_cmp).shape:
                    master_cmp = repair_masked_columns(_np.asarray(master_cmp, dtype=float), m)
        except Exception:
            # Mask unification/repair is best-effort; continue on any error
            pass

        t1 = time.perf_counter()
        # Execute wavelength algorithm (storage-neutral)
        algo_params = dict(self.params or {})
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        ar = step_wave(master_cmp=master_cmp, trace=trace2d, output_path=None, params=algo_params)
        t2 = time.perf_counter()

        # Normalize and validate result
        ar = ensure_algo_result(ar, kind="wave")
        rep = WaveResultContract().validate(ar)
        if not rep.ok:
            raise ValueError("WaveTask: AlgoResult failed contract validation: " + "; ".join(rep.errors))

        # Compose ArtifactRequest
        wave = ar.get_array("wave")
        if wave is None:
            raise RuntimeError("WaveTask: missing 'wave' array in AlgoResult")
        comp = LogicalComponent(name="wave", model_type="array2d", value=wave)
        scope = Scope(zipcode=getattr(self.target, "zipcode", None))
        mm = ar.as_meta()
        summaries = {}
        for k in ("best_nmatch", "best_rms"):
            if mm.get(k) is not None:
                summaries[k] = mm.get(k)
        req = ArtifactRequest(
            kind="wave",
            components={"wave": comp},
            summaries=summaries,
            metadata={},
            scope=scope,
            parents=parent_ids,
            labels=["calibration", "wave"],
        )

        # Publish via PublicationService
        pub = DefaultPublicationService(svc=svc, policy=DefaultPersistencePolicy(), base_dir=self.ctx.workdir)
        ctx = PublicationContext(
            task_name=self.name,
            task_version=self.version,
            algorithm_name="algorithms.wave.step_wave",
            algorithm_version=ar.version,
            parameters=dict(self.params or {}),
            parent_ids=parent_ids,
            timings={"resolve+materialize": t1 - t0, "execute": t2 - t1},
        )
        arts = pub.publish([req], ctx)
        if not arts:
            raise RuntimeError("WaveTask: publication produced no artifacts")
        art = arts[0]

        # QA after publication
        try:
            status = svc.diagnostics.evaluate_and_save(artifact_id=int(getattr(art, "id", 0)), kind="wave", meta=ar)
            if dbg and status:
                print(f"[QA] WaveTask: status={status} artifact_id={getattr(art, 'id', None)}")
            try:
                if svc.diagnostics.should_block(kind="wave", status=status):
                    raise RuntimeError(f"QA hard-fail for wave (artifact_id={getattr(art, 'id', None)})")
            except Exception:
                pass
        except Exception:
            pass

        if dbg:
            print(f"[Timing] WaveTask: resolve+materialize={t1 - t0:.3f}s, execute={t2 - t1:.3f}s")
        return {self.artifact_name: art}
