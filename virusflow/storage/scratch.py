from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..executors.execution_context import current_worker_id


_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _token(value: str) -> str:
    return _SAFE.sub("_", str(value)).strip("._") or "unknown"


@dataclass
class ScratchSpace:
    """Worker-local scratch with explicit success/failure retention policy."""

    workdir: str | Path
    run_id: str | None = None
    worker_id: str | None = None
    preserve_failed: bool = False

    def __post_init__(self) -> None:
        run = _token(self.run_id or uuid.uuid4().hex)
        worker = _token(self.worker_id or current_worker_id())
        root = Path(self.workdir).resolve() / ".scratch"
        self.path = root / run / worker
        self.path.mkdir(parents=True, exist_ok=False)
        self._root = root

    def child(self, name: str) -> Path:
        path = self.path / _token(name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup(self) -> None:
        from ..performance import current_task_timing, phase
        resolved = self.path.resolve()
        root = self._root.resolve()
        if root not in resolved.parents or resolved == root:
            raise RuntimeError(f"refusing unsafe scratch cleanup: {resolved}")
        try:
            scratch_bytes = sum(path.stat().st_size for path in resolved.rglob("*") if path.is_file())
        except OSError:
            scratch_bytes = 0
        with phase("scratch_cleanup"):
            shutil.rmtree(resolved)
        timing = current_task_timing()
        if timing is not None:
            timing.increment("scratch_bytes_written", scratch_bytes)
        parent = resolved.parent
        while parent != root and root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def __enter__(self) -> "ScratchSpace":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is None or not self.preserve_failed:
            self.cleanup()
        return False
