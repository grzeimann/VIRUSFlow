from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..artifacts import Artifact as NewArtifact, ArtifactService, Scope


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

# Generic calibration task template used by BiasTask and future calibs
class CalibrationTask(Task):
    # To be set by subclasses
    frame_type: Optional[str] = None  # e.g., "zro", "drk", "flt"
    algorithm = None  # callable(raw_inputs=[...], params=...)
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

        Selection is always performed through ArtifactService. Planning edges
        determine execution order; task loading resolves the published Product.
        """
        self._require_target()
        zipcode = getattr(self.target, "zipcode", None)
        at_time = self._target_mid_time()
        svc = ArtifactService(self.ctx.db_path)
        scope = Scope(zipcode=zipcode)
        row = svc.select_best(kind=kind, scope=scope, at_time=at_time, policy="latest_valid")
        if not row:
            row = svc.select_best(kind=kind, scope=scope, at_time=at_time, policy="nearest")
        if not row:
            if required:
                zkey = zipcode.key() if zipcode else "UNKNOWN"
                raise RuntimeError(f"{self.__class__.__name__} requires an existing {kind} for zipcode={zkey} in the given date window")
            return None
        return row

    def query_inputs(self):
        from ..registry import database as _db
        from ..performance import phase
        self._require_target()
        if not self.frame_type:
            raise ValueError(f"{self.__class__.__name__}.frame_type is not set")
        with phase("raw_lookup"):
            planned_ids = tuple(getattr(self.target, "raw_ids", ()) or ())
            if planned_ids:
                scoped = _db.list_raw_files_by_ids(planned_ids, db_path=self.ctx.db_path)
                wrong_scope = [
                    raw.exposure_id for _, raw in scoped
                    if raw.zipcode != getattr(self.target, "zipcode", None)
                ]
                if wrong_scope:
                    raise RuntimeError(f"planned raw membership has wrong ZIP code: {wrong_scope}")
            else:
                scoped = _db.list_raw_files_scoped(
                    frame_type=self.frame_type,
                    start_date=self.target.start_date,
                    end_date=self.target.end_date,
                    zipcode=getattr(self.target, "zipcode", None),
                    db_path=self.ctx.db_path,
                    start_time=getattr(self.target, "start_dt", None),
                    end_time=getattr(self.target, "end_dt", None),
                )
        raw_inputs = [{
            "path": r.path, "tar_member": r.tar_member, "storage_backend": r.storage_backend,
            "archive_offset": r.archive_offset, "archive_size": r.archive_size,
        } for (_id, r) in scoped]
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

    def load_reduced_inputs(self, raw_inputs):
        """Load raw frames through the I/O boundary and return detector arrays only."""
        from ..algorithms.ccd import reduce_amplifier_array
        from ..io import RawFrameLoader
        from ..performance import current_task_timing, phase

        loader = self.ctx.config.get("raw_frame_loader") if isinstance(self.ctx.config, dict) else None
        loader = loader or RawFrameLoader()
        reduced = []
        for item in raw_inputs:
            with phase("load_raw_frames"):
                if item.get("archive_offset") is None:
                    frame = loader.load(str(item["path"]), item.get("tar_member"))
                else:
                    frame = loader.load(
                        str(item["path"]), item.get("tar_member"),
                        archive_offset=item.get("archive_offset"),
                        archive_size=item.get("archive_size"),
                    )
            with phase("base_reduction"):
                detector = reduce_amplifier_array(frame.data, frame.header)
            timing = current_task_timing()
            if timing is not None:
                timing.increment("base_reduction_calls")
            reduced.append(
                {
                    "data": detector.get_array("oriented_detector_image"),
                    "error": detector.get_array("detector_error"),
                    "variance": detector.get_array("detector_variance"),
                    "header": dict(frame.header),
                    "source": {"path": frame.path, "tar_member": frame.tar_member},
                }
            )
        return reduced

    def target_validity(self):
        from ..artifacts.models import Validity

        start = getattr(self.target, "start_dt", None) or self._parse_date(getattr(self.target, "start_date", None))
        end = getattr(self.target, "end_dt", None) or self._parse_date(getattr(self.target, "end_date", None))
        return Validity(start=start, end=end, policy="target_window")

    def configuration_references(self):
        from ..config import ConfigurationService

        root = self.ctx.config.get("configuration_root") if isinstance(self.ctx.config, dict) else None
        return ConfigurationService(root=root).amplifier_references(self.target.zipcode)

    def evaluate_qa(self, service, artifact, result) -> str:
        """Persist canonical QA and propagate configured hard blocking failures."""
        status = service.diagnostics.evaluate_and_save(
            artifact_id=int(artifact.id), kind=str(artifact.kind), meta=result
        )
        if service.diagnostics.should_block(kind=str(artifact.kind), status=status):
            raise RuntimeError(f"QA hard-fail for {artifact.kind} (artifact_id={artifact.id})")
        return status
