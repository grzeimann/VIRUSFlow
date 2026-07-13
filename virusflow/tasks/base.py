from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from ..core.artifacts import Artifact
from ..core.provenance import build_provenance
from ..registry import database as db


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

    def run(self, inputs: Dict[str, Artifact]) -> Dict[str, Artifact]:
        raise NotImplementedError

    def save_artifact(self, art: Artifact, parent_ids: List[int] | None = None) -> int:
        # Merge target into provenance parameters for traceability
        params = dict(self.params)
        if getattr(self, "target", None) is not None:
            try:
                # Try to serialize target to a dict
                t = self.target
                if hasattr(t, "to_dict"):
                    params["target"] = t.to_dict()
                else:
                    params["target"] = str(t)
            except Exception:
                params["target"] = str(self.target)
        prov = build_provenance(
            algorithm=f"{self.name}:{self.version}",
            params=params,
            parents=[str(x) for x in (parent_ids or [])],
        )
        return db.save_artifact(art, prov, db_path=self.ctx.db_path)


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
        midpoint of the target window. Falls back to the latest by zipcode if a
        time-qualified match is not found.
        """
        from ..registry import database as _db
        self._require_target()
        zipcode = getattr(self.target, "zipcode", None)
        at_time = self._target_mid_time()
        rows = _db.find_artifacts(kind=kind, zipcode=zipcode, at_time=at_time, db_path=self.ctx.db_path, limit=1)
        if not rows:
            rows = _db.find_artifacts(kind=kind, zipcode=zipcode, db_path=self.ctx.db_path, limit=1)
        if not rows:
            if required:
                zkey = zipcode.key() if zipcode else "UNKNOWN"
                raise RuntimeError(f"{self.__class__.__name__} requires an existing {kind} for zipcode={zkey} in the given date window")
            return None
        return rows[0]

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
        raw_inputs = [{"path": r.path, "tar_member": r.tar_member} for (_id, r) in scoped]
        parent_ids = [pid for (pid, _r) in scoped if pid is not None]
        if not raw_inputs:
            zkey = getattr(self.target, "zipcode", None)
            zkey = zkey.key() if zkey else "UNKNOWN"
            raise RuntimeError(
                f"No raw '{self.frame_type}' frames found for zipcode={zkey} in date window {self.target.start_date}..{self.target.end_date}"
            )
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

    def make_artifact(self, out_path: str) -> Artifact:
        from ..core.artifacts import CalibrationProduct
        zipcode = getattr(self.target, "zipcode", None)
        vstart = self._parse_date(getattr(self.target, "start_date", None))
        vend = self._parse_date(getattr(self.target, "end_date", None))
        # Use the specific artifact name as the artifact kind for discoverability (e.g., 'master_bias')
        art_kind = self.artifact_name or "calibration"
        return CalibrationProduct(
            id=None,
            kind=art_kind,
            name=self.artifact_name or "calibration",
            path=out_path,
            zipcode=zipcode,
            validity_start=vstart,
            validity_end=vend,
        )

    def run(self, inputs: Dict[str, Artifact]):
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
            from ..qa import diagnostics as qa_diag
            qa_kind = (self.artifact_name or "").strip().lower()
            status = qa_diag.evaluate_and_save(artifact_id=art_id, kind=qa_kind, meta=dict(meta or {}), db_path=self.ctx.db_path)
            if dbg and status:
                print(f"[QA] {self.__class__.__name__}: auto-qa status={status} kind={qa_kind} artifact_id={art_id}")
        except Exception:
            # Never fail the task on QA evaluation errors
            pass
        if dbg:
            print(f"[Timing] {self.__class__.__name__}: algorithm={t3 - t2:.3f}s, save_artifact={t4 - t3:.3f}s, total={t4 - t0:.3f}s")
        return {self.artifact_name or "calibration": artifact}
