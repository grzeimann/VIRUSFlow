from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# Legacy artifact dataclasses are no longer used in the new artifacts subsystem
from ..registry import database as db
from ..artifacts import Artifact as NewArtifact, ArtifactService, Scope, StorageRef, Provenance


@dataclass
class TaskContext:
    db_path: str
    workdir: str
    config: Dict[str, Any] = field(default_factory=dict)


class Task:
    kind: str = "task"
    name: str = "base"
    version: str = "v1"
    # Declarative dependencies: names of task types that must run for the same target
    # before this task can execute (e.g., TraceTask.requires = ["flat"]).
    # Semantics: dependencies are resolved at runtime for tasks sharing the same
    # zipcode and date window; plans need not (and should not) carry explicit deps.
    requires: list[str] = []

    def __init__(self, ctx: TaskContext, target: Any | None = None, params: Optional[Dict[str, Any]] = None) -> None:
        self.ctx = ctx
        self.target = target
        self.params = params or {}

    # Declared interface
    @classmethod
    def inputs(cls) -> List[str]:
        return []

    @classmethod
    def outputs(cls) -> List[str]:
        return []

    def run(self, inputs: Dict[str, NewArtifact]) -> Dict[str, NewArtifact]:
        raise NotImplementedError

    def save_artifact(self, art: NewArtifact, parent_ids: List[int] | None = None) -> int:
        """Register an artifact via ArtifactService.register (full delegation).

        This centralizes provenance and registry behavior in the service.
        """
        # Build provenance params (include serialized target for traceability)
        params = dict(self.params)
        if getattr(self, "target", None) is not None:
            try:
                t = self.target
                if hasattr(t, "to_dict"):
                    params["target"] = t.to_dict()
                else:
                    params["target"] = str(t)
            except Exception:
                params["target"] = str(self.target)
            # Also thread validity_start/end (as datetimes) derived from target window when available
            try:
                from datetime import datetime as _dt
                sd = getattr(self.target, "start_date", None)
                ed = getattr(self.target, "end_date", None)
                def _parse_ymd(s):
                    if not s:
                        return None
                    s = str(s)
                    if len(s) >= 8:
                        return _dt.strptime(s[:8], "%Y%m%d")
                    return None
                vstart = _parse_ymd(sd)
                vend = _parse_ymd(ed)
                if vstart is not None:
                    params.setdefault("validity_start", vstart)
                if vend is not None:
                    params.setdefault("validity_end", vend)
            except Exception:
                pass

        # Construct service scope
        from ..artifacts import ArtifactService, Scope, Artifact as NewArtifact, StorageRef, Provenance
        zc = getattr(art, "zipcode", None) or getattr(getattr(self, "target", None), "zipcode", None)
        scope = Scope(zipcode=zc)

        # Build new-model Artifact and register
        svc = ArtifactService(self.ctx.db_path)
        # Prefer the existing storage reference from the provided artifact (has correct URI)
        _existing_storage = getattr(art, "storage", None)
        _sfmt = getattr(_existing_storage, "storage_format", "fits") if _existing_storage else "fits"
        _suri = getattr(_existing_storage, "uri", None)
        if not _suri:
            # Fallback to legacy `.path` attribute if present
            _suri = str(getattr(art, "path", None) or "")
        storage_ref = StorageRef(uri=str(_suri), storage_format=_sfmt, backend=getattr(_existing_storage, "backend", "fs"))
        new_art = NewArtifact(
            id=None,
            kind=str(getattr(art, "kind", self.name)),
            role=getattr(art, "role", "calibration"),
            payload_type=getattr(art, "payload_type", "array"),
            storage_format=_sfmt,  # current savers use FITS primary HDU
            storage=storage_ref,
            scope=scope,
            metadata=dict(getattr(art, "metadata", {}) or {}),
            provenance=Provenance(
                algorithm=f"{self.name}:{self.version}",
                params=params,
                parents=[int(x) for x in (parent_ids or [])],
            ),
        )
        return svc.register(new_art)


# Generic calibration task template used by BiasTask and future calibs
class CalibrationTask(Task):
    # To be set by subclasses
    frame_type: Optional[str] = None  # e.g., "zro", "drk", "flt"
    algorithm = None  # callable(raw_inputs=[...], output_path=..., params=...)
    artifact_name: Optional[str] = None  # e.g., "master_bias"

    def _require_target(self) -> None:
        if self.target is None:
            raise TypeError(f"{self.__class__.__name__} requires a target specifying zipcode and date window")
        # Expect attributes: zipcode, start_date, end_date
        for attr in ("zipcode", "start_date", "end_date"):
            if not hasattr(self.target, attr):
                raise TypeError(f"Target for {self.__class__.__name__} is missing attribute '{attr}'")

    def _parse_date(self, s: str):
        from datetime import datetime
        if s in (None, "", "00000000"):
            return None
        return datetime.strptime(str(s), "%Y%m%d")

    # ---- Common artifact resolution helpers for calibration tasks ----
    def _target_mid_time(self):
        """Return the midpoint datetime of the target validity window when available.

        If only one bound is present, return that bound. If neither is present, return None.
        """
        vstart = self._parse_date(getattr(self.target, "start_date", None))
        vend = self._parse_date(getattr(self.target, "end_date", None))
        if vstart and vend:
            return vstart + (vend - vstart) / 2
        return vstart or vend

    def _resolve_artifact(self, kind: str, required: bool = True) -> dict | None:
        """Find the best existing artifact of a given kind for this task's target.

        Searches by zipcode and, when possible, selects the artifact valid at the
        midpoint of the target window via ArtifactService.select_best. Falls back
        to the latest by zipcode if a time-qualified match is not found.

        When the feature flag is enabled (ctx.config.use_mapping_helper or
        environment VF_MAP_HELPER=1), consult the planning.mapping.select_for_edge
        helper to centralize policy+tolerance handling. Optional tolerance can be
        configured via ctx.config.mapping_tolerance_days.
        """
        import os as _os
        self._require_target()
        zipcode = getattr(self.target, "zipcode", None)
        at_time = self._target_mid_time()
        svc = ArtifactService(self.ctx.db_path)
        scope = Scope(zipcode=zipcode)
        use_helper = False
        try:
            use_helper = bool(getattr(self.ctx, "config", {}).get("use_mapping_helper", False))
        except Exception:
            use_helper = False
        if not use_helper:
            use_helper = (_os.environ.get("VF_MAP_HELPER", "0") == "1")
        if use_helper:
            try:
                from ..planning import select_for_edge as _select_for_edge  # type: ignore
            except Exception:
                _select_for_edge = None  # type: ignore
            tol = None
            try:
                tol = getattr(self.ctx, "config", {}).get("mapping_tolerance_days")
            except Exception:
                tol = None
            if _select_for_edge is not None:
                row = _select_for_edge(kind=kind, scope=scope, at_time=at_time, policy="latest_valid", tolerance_days=tol, service=svc)
            else:
                row = svc.select_best(kind=kind, scope=scope, at_time=at_time, policy="latest_valid")
        else:
            row = svc.select_best(kind=kind, scope=scope, at_time=at_time, policy="latest_valid")
        if not row:
            row = svc.select_best(kind=kind, scope=scope, at_time=None, policy="latest")
        if not row:
            if required:
                zkey = zipcode.key() if zipcode else "UNKNOWN"
                raise RuntimeError(f"{self.__class__.__name__} requires an existing {kind} for zipcode={zkey} in the given date window")
            return None
        return row

    def query_inputs(self):
        from ..registry import database as _db
        self._require_target()
        if not self.frame_type:
            raise ValueError(f"{self.__class__.__name__}.frame_type is not set")
        scoped = _db.list_raw_files_scoped(
            frame_type=self.frame_type,
            start_date=self.target.start_date,
            end_date=self.target.end_date,
            zipcode=getattr(self.target, "zipcode", None),
            db_path=self.ctx.db_path,
        )
        raw_inputs = [{"path": r.path, "tar_member": r.tar_member, "storage_backend": r.storage_backend} for (_id, r) in scoped]
        parent_ids = [pid for (pid, _r) in scoped if pid is not None]
        if not raw_inputs:
            zkey = getattr(self.target, "zipcode", None)
            zkey = zkey.key() if zkey else "UNKNOWN"
            raise RuntimeError(
                f"No raw '{self.frame_type}' frames found for zipcode={zkey} in date window {self.target.start_date}..{self.target.end_date}"
            )
        # Optional debug print of a few resolved inputs for visibility during failures
        try:
            dbg_inputs = bool(self.ctx.config.get("debug_inputs", False)) if isinstance(self.ctx.config, dict) else False
        except Exception:
            dbg_inputs = False
        if dbg_inputs:
            try:
                n = len(raw_inputs)
                sample = raw_inputs[:3]
                print(f"[Debug] {self.__class__.__name__}: resolved {n} '{self.frame_type}' inputs. Sample:")
                for s in sample:
                    print(f"  - path={s.get('path')} tar_member={s.get('tar_member')} backend={s.get('storage_backend')}")
            except Exception:
                pass
        return raw_inputs, parent_ids

    def output_path(self) -> str:
        import os
        from ..core.pathutils import ensure_dir, sanitize_for_filename
        self._require_target()
        if not self.artifact_name:
            raise ValueError(f"{self.__class__.__name__}.artifact_name is not set")
        ensure_dir(self.ctx.workdir)
        zkey_raw = self.target.zipcode.key() if getattr(self.target, "zipcode", None) else "UNKNOWN"
        zkey = sanitize_for_filename(zkey_raw)
        start_s, end_s = self.target.start_date, self.target.end_date
        fname = f"{self.artifact_name}_{zkey}_{start_s}_{end_s}.fits"
        return os.path.join(self.ctx.workdir, fname)

    def make_artifact(self, out_path: str) -> NewArtifact:
        # Build a new-model Artifact with explicit representation fields
        zipcode = getattr(self.target, "zipcode", None)
        scope = Scope(zipcode=zipcode)
        art_kind = self.artifact_name or "calibration"
        return NewArtifact(
            id=None,
            kind=art_kind,
            role="calibration",
            payload_type="array",
            storage_format="fits",
            storage=StorageRef(uri=str(out_path), storage_format="fits", backend="fs"),
            scope=scope,
            metadata={},
            provenance=None,
        )

    def run(self, inputs: Dict[str, NewArtifact]):
        import time
        if not callable(self.algorithm):
            raise ValueError(f"{self.__class__.__name__}.algorithm is not set to a callable")
        dbg = bool(self.ctx.config.get("debug_timing", False)) if isinstance(self.ctx.config, dict) else False
        t0 = time.perf_counter()
        raw_inputs, parent_ids = self.query_inputs()
        t1 = time.perf_counter()
        out = self.output_path()
        # Merge context config into params so algorithms can see workers/debug flags
        algo_params = dict(self.params)
        if isinstance(self.ctx.config, dict):
            for k, v in self.ctx.config.items():
                algo_params.setdefault(k, v)
        if dbg:
            print(f"[Timing] {self.__class__.__name__}: query_inputs={t1 - t0:.3f}s, n_inputs={len(raw_inputs)}")
        # Ensure algorithms receive db_path and zipcode for artifact-centric loading
        algo_params.setdefault("db_path", self.ctx.db_path)
        try:
            if getattr(self.target, "zipcode", None) is not None:
                algo_params.setdefault("zipcode", getattr(self.target, "zipcode"))
        except Exception:
            pass
        # Call the algorithm; support both raw_inputs and legacy raw_bias_inputs kw
        t2 = time.perf_counter()
        meta = self.algorithm(raw_inputs=raw_inputs, output_path=out, params=algo_params)
        t3 = time.perf_counter()
        artifact = self.make_artifact(out)
        art_id = self.save_artifact(artifact, parent_ids=parent_ids)
        # Attach the generated artifact id to the Artifact instance for downstream inspection
        try:
            setattr(artifact, "id", int(art_id))
        except Exception:
            pass
        t4 = time.perf_counter()
        # Automatic QA: evaluate and persist based on algorithm metadata and artifact kind
        try:
            svc = ArtifactService(self.ctx.db_path)
            qa_kind = (self.artifact_name or "").strip().lower()
            status = svc.diagnostics.evaluate_and_save(artifact_id=art_id, kind=qa_kind, meta=meta)
            if dbg and status:
                print(f"[QA] {self.__class__.__name__}: auto-qa status={status} kind={qa_kind} artifact_id={art_id}")
            # Enforce policy: hard-fail kinds with status=fail
            try:
                if svc.diagnostics.should_block(kind=qa_kind, status=status):
                    raise RuntimeError(f"QA hard-fail for {qa_kind} (artifact_id={art_id})")
            except Exception:
                # Do not block on errors computing policy
                pass
        except Exception:
            # Never fail the task on QA evaluation errors
            pass
        if dbg:
            print(f"[Timing] {self.__class__.__name__}: algorithm={t3 - t2:.3f}s, save_artifact={t4 - t3:.3f}s, total={t4 - t0:.3f}s")
        return {self.artifact_name or "calibration": artifact}
