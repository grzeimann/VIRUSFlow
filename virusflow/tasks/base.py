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
        self._require_target()
        if not self.artifact_name:
            raise ValueError(f"{self.__class__.__name__}.artifact_name is not set")
        os.makedirs(self.ctx.workdir, exist_ok=True)
        zkey = self.target.zipcode.key() if getattr(self.target, "zipcode", None) else "UNKNOWN"
        start_s, end_s = self.target.start_date, self.target.end_date
        fname = f"{self.artifact_name}_{zkey}_{start_s}_{end_s}.fits"
        return os.path.join(self.ctx.workdir, fname)

    def make_artifact(self, out_path: str) -> Artifact:
        from ..core.artifacts import CalibrationProduct
        zipcode = getattr(self.target, "zipcode", None)
        vstart = self._parse_date(getattr(self.target, "start_date", None))
        vend = self._parse_date(getattr(self.target, "end_date", None))
        return CalibrationProduct(
            id=None,
            kind="calibration",
            name=self.artifact_name or "calibration",
            path=out_path,
            zipcode=zipcode,
            validity_start=vstart,
            validity_end=vend,
        )

    def run(self, inputs: Dict[str, Artifact]):
        if not callable(self.algorithm):
            raise ValueError(f"{self.__class__.__name__}.algorithm is not set to a callable")
        raw_inputs, parent_ids = self.query_inputs()
        out = self.output_path()
        # Call the algorithm; support both raw_inputs and legacy raw_bias_inputs kw
        meta = self.algorithm(raw_inputs=raw_inputs, output_path=out, params=self.params)
        artifact = self.make_artifact(out)
        self.save_artifact(artifact, parent_ids=parent_ids)
        return {self.artifact_name or "calibration": artifact}
