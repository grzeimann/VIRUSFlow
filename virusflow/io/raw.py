from __future__ import annotations

import io
import os
import tarfile
import time
from collections import OrderedDict
from threading import Event, Lock
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from astropy.io import fits


@dataclass(frozen=True)
class RawFrameData:
    data: np.ndarray
    header: Dict[str, Any]
    path: str
    tar_member: Optional[str] = None


class RawFrameLoader:
    """Approved raw FITS/tar I/O boundary for Tasks."""

    _seen: dict[str, set[str]] = {}
    _seen_lock = Lock()
    _cache: OrderedDict[tuple[str, str], RawFrameData] = OrderedDict()
    _cache_bytes: dict[str, int] = {}
    _inflight: dict[tuple[str, str], Event] = {}
    _cache_lock = Lock()

    def __init__(self) -> None:
        self._archive_handles: dict[str, Any] = {}

    def close(self) -> None:
        for handle in self._archive_handles.values():
            try:
                handle.close()
            except Exception:
                pass
        self._archive_handles.clear()

    def __del__(self):  # pragma: no cover - deterministic task lifetime normally closes at GC
        self.close()

    def load(
        self,
        path: str,
        tar_member: Optional[str] = None,
        *,
        archive_offset: int | None = None,
        archive_size: int | None = None,
        outer_tar_member: Optional[str] = None,
    ) -> RawFrameData:
        from ..performance import current_task_timing, legacy_baseline_enabled, phase

        timing = current_task_timing()
        identity = f"{Path(path).resolve()}::{outer_tar_member or ''}::{tar_member or ''}"
        started = time.perf_counter()
        cold = True
        if timing is not None:
            timing.increment("raw_frames_requested")
            timing.identity("raw_frames_requested", identity)
            with self._seen_lock:
                seen = self._seen.setdefault(timing.run_id, set())
                cold = identity not in seen
                seen.add(identity)
        def record_access(*, cache_hit: bool, physical: bool) -> None:
            if timing is None:
                return
            timing.raw_accesses.append({
                "identity": identity, "path": str(path), "member": tar_member,
                "seconds": max(0.0, time.perf_counter() - started),
                "cache_hit": bool(cache_hit), "physical": bool(physical),
                "cold": cold, "worker_id": timing.worker_id, "task_kind": timing.kind,
            })
        legacy_baseline = legacy_baseline_enabled()
        cache_key = (
            (timing.run_id, identity)
            if timing is not None and not legacy_baseline
            else None
        )
        owner = True
        wait_event = None
        if cache_key is not None:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    self._cache.move_to_end(cache_key)
                    timing.increment("raw_cache_hits")
                    record_access(cache_hit=True, physical=False)
                    return cached
                wait_event = self._inflight.get(cache_key)
                if wait_event is None:
                    self._inflight[cache_key] = Event()
                else:
                    owner = False
            if not owner and wait_event is not None:
                with phase("raw_cache_wait"):
                    wait_event.wait()
                with self._cache_lock:
                    cached = self._cache.get(cache_key)
                if cached is not None:
                    timing.increment("raw_cache_hits")
                    record_access(cache_hit=True, physical=False)
                    return cached
        try:
            frame, bytes_read = self._read_physical(
                path, tar_member,
                archive_offset=None if legacy_baseline else archive_offset,
                archive_size=None if legacy_baseline else archive_size,
                outer_tar_member=outer_tar_member,
                timing=timing,
            )
        except BaseException:
            if cache_key is not None:
                with self._cache_lock:
                    event = self._inflight.pop(cache_key, None)
                    if event is not None:
                        event.set()
            raise
        if cache_key is not None:
            with self._cache_lock:
                frame_bytes = int(getattr(frame.data, "nbytes", 0))
                limit = max(
                    0, int(os.environ.get("VIRUSFLOW_RAW_CACHE_MAX_BYTES", str(512 * 1024**2)))
                )
                if frame_bytes <= limit:
                    self._cache[cache_key] = frame
                    self._cache.move_to_end(cache_key)
                    self._cache_bytes[timing.run_id] = (
                        self._cache_bytes.get(timing.run_id, 0) + frame_bytes
                    )
                    while self._cache_bytes[timing.run_id] > limit:
                        victim = next(
                            (key for key in self._cache if key[0] == timing.run_id), None
                        )
                        if victim is None:
                            break
                        removed = self._cache.pop(victim)
                        self._cache_bytes[timing.run_id] -= int(
                            getattr(removed.data, "nbytes", 0)
                        )
                        timing.increment("raw_cache_evictions")
                event = self._inflight.pop(cache_key, None)
                if event is not None:
                    event.set()
        if timing is not None:
            elapsed = max(0.0, time.perf_counter() - started)
            timing.increment("raw_frames_read")
            timing.increment("raw_bytes_read", bytes_read)
            timing.increment("raw_cache_misses")
            timing.identity("raw_frames_read", identity)
            timing.raw_reads.append({
                "identity": identity, "path": str(path), "member": tar_member,
                "seconds": elapsed, "bytes": bytes_read, "cold": cold,
                "worker_id": timing.worker_id, "task_kind": timing.kind,
            })
            record_access(cache_hit=False, physical=True)
        return frame

    def _read_physical(
        self, path, tar_member, *, archive_offset, archive_size, outer_tar_member=None, timing
    ):
        from ..performance import phase

        if outer_tar_member:
            from ..storage.filesystem import RawSource, read_member_bytes

            with phase("raw_archive_open"):
                source = RawSource(
                    path=Path(path), tar_member=tar_member,
                    backend="date_tar", outer_tar_member=outer_tar_member,
                )
                if timing is not None:
                    timing.increment("archive_opens")
            with phase("raw_byte_read"):
                blob = read_member_bytes(source)
            with phase("fits_header_parse"):
                hdul = fits.open(io.BytesIO(blob), memmap=False)
                header = dict(hdul[0].header)
            try:
                with phase("pixel_array_load"):
                    data = np.asarray(hdul[0].data)
            finally:
                hdul.close()
            bytes_read = len(blob)
        elif tar_member:
            if archive_offset is not None and archive_size is not None:
                handle = self._archive_handles.get(path)
                if handle is None:
                    with phase("raw_archive_open"):
                        handle = open(path, "rb")
                    self._archive_handles[path] = handle
                    if timing is not None:
                        timing.increment("archive_opens")
                elif timing is not None:
                    timing.increment("archive_handle_reuses")
                with phase("raw_member_lookup"):
                    handle.seek(int(archive_offset))
                with phase("raw_byte_read"):
                    blob = handle.read(int(archive_size))
                if len(blob) != int(archive_size):
                    raise OSError(
                        f"short indexed read for {tar_member}: {len(blob)} != {archive_size}"
                    )
                if timing is not None:
                    timing.increment("resolved_raw_references")
                    timing.increment("archive_index_reuses")
            else:
                with phase("raw_archive_open"):
                    archive = tarfile.open(path, mode="r:*")
                    if timing is not None:
                        timing.increment("archive_opens")
                try:
                    with phase("raw_member_lookup"):
                        member = archive.getmember(tar_member)
                        stream = archive.extractfile(member)
                    if stream is None:
                        raise FileNotFoundError(f"Cannot extract {tar_member} from {path}")
                    with phase("raw_byte_read"):
                        blob = stream.read()
                finally:
                    archive.close()
            with phase("fits_header_parse"):
                hdul = fits.open(io.BytesIO(blob), memmap=False)
                header = dict(hdul[0].header)
            try:
                with phase("pixel_array_load"):
                    data = np.asarray(hdul[0].data)
            finally:
                hdul.close()
            bytes_read = len(blob)
        else:
            with phase("raw_archive_open"):
                hdul = fits.open(str(Path(path)), memmap=False)
                if timing is not None:
                    timing.increment("filesystem_opens")
            try:
                with phase("fits_header_parse"):
                    header = dict(hdul[0].header)
                with phase("pixel_array_load"):
                    data = np.asarray(hdul[0].data)
            finally:
                hdul.close()
            try:
                bytes_read = Path(path).stat().st_size
            except OSError:
                bytes_read = int(getattr(data, "nbytes", 0))
        return (
            RawFrameData(data=data, header=header, path=str(path), tar_member=tar_member),
            bytes_read,
        )

    @classmethod
    def clear_run_cache(cls, run_id: str) -> None:
        with cls._cache_lock:
            for key in [key for key in cls._cache if key[0] == str(run_id)]:
                cls._cache.pop(key, None)
            cls._cache_bytes.pop(str(run_id), None)
        with cls._seen_lock:
            cls._seen.pop(str(run_id), None)

    def load_ref(self, reference) -> RawFrameData:
        """Load a registry-resolved immutable raw reference without rediscovery."""

        return self.load(
            reference.path, reference.tar_member,
            archive_offset=getattr(reference, "archive_offset", None),
            archive_size=getattr(reference, "archive_size", None),
            outer_tar_member=getattr(reference, "outer_tar_member", None),
        )
