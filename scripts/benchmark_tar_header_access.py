#!/usr/bin/env python3
"""Compare two physical access patterns for primary FITS headers in VIRUS tars.

This is intentionally a standalone experiment.  It does not use the VIRUSFlow
database or production storage classes.

Strategy A models the indexed production path:

* traverse an ordinary, uncompressed tar once and retain TarInfo offsets;
* close that traversal;
* for every relevant member, open the tar as a binary file, seek to
  ``TarInfo.offset_data``, and parse only the primary FITS header.

Strategy B traverses a fresh tar once.  Immediately after ``TarFile.next()``
returns a relevant member, Python's seekable tarfile implementation has its
file object positioned at that member's ``offset_data``.  The benchmark reads
the primary FITS header directly from that existing file object, then lets the
next ``TarFile.next()`` call skip the unconsumed FITS payload.

Strategy C opens an ordinary tar with ``mode="r|"``.  It reads the primary
header from the current stream position, consumes the rest of each FITS
member in bounded chunks, and continues to EOF.  In stream mode tarfile's
forward ``seek()`` operation is implemented by reads, so it does not issue
filesystem seeks.

The benchmark deliberately does not use ``TarFile.extractfile()``.  That API
uses a _FileInFile wrapper whose reads seek to the member offset, which would
add an explicit seek for every header and obscure the physical comparison.
The benchmark also does not call ``getmembers()`` or ``getmember()`` after the
initial traversal, so there are no hidden member-list scans.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import sys
import tarfile
import time
from typing import Any, BinaryIO, Iterator, Optional

from astropy.io import fits


FITS_BLOCK_BYTES = 2880
MAX_PRIMARY_HEADER_BYTES = 1024 * 1024
REPRESENTATIVE_CARDS = (
    "DATE",
    "EXPTIME",
    "PEXPTIME",
    "QPROG",
    "OBJECT",
    "QOBJECT",
    "IFUSLOT",
    "IFUID",
    "SPECID",
    "CONTID",
    "CONTROLLER",
    "AMPNAME",
    "CCDPOS",
    "CCDHALF",
)
TEST_ARCHIVE_RE = re.compile(r"^virus(\d{7})(?:\.tar)?$", re.IGNORECASE)


class BenchmarkError(RuntimeError):
    """Raised when the experiment cannot establish equivalent work."""


@dataclass(frozen=True)
class MemberRecord:
    name: str
    offset_data: int
    size: int


@dataclass
class IOStats:
    archive_opens: int = 0
    header_file_opens: int = 0
    member_scans: int = 0
    member_list_scan_passes: int = 0
    seek_calls: int = 0
    traversal_seek_calls: int = 0
    header_seek_calls: int = 0
    backward_seek_calls: int = 0
    forward_seek_calls: int = 0
    stream_forward_seek_calls: int = 0
    header_reads: int = 0
    header_bytes: int = 0
    archive_bytes_read: int = 0
    payload_bytes_discarded: int = 0
    _phase: str = field(default="other", repr=False)
    _header_bytes_current: int = field(default=0, repr=False)


class CountingFile:
    """Small file proxy that records seeks and logical FITS-header reads."""

    def __init__(self, raw: BinaryIO, stats: IOStats):
        self._raw = raw
        self._stats = stats

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        previous = self._stats._phase
        self._stats._phase = name
        try:
            yield
        finally:
            self._stats._phase = previous

    def begin_header(self) -> None:
        self._stats._header_bytes_current = 0

    def end_header(self, start: int, end: int) -> int:
        consumed = end - start
        if consumed <= 0:
            raise BenchmarkError("primary FITS header read consumed no bytes")
        if consumed > MAX_PRIMARY_HEADER_BYTES:
            raise BenchmarkError(
                "primary FITS header read exceeded the 1 MiB safety limit; "
                "refusing to risk reading an image payload"
            )
        self._stats.header_bytes += consumed
        self._stats.header_reads += 1
        return consumed

    def read(self, size: int = -1) -> bytes:
        if self._stats._phase in {"a-header", "b-header"} and size >= 0:
            if self._stats._header_bytes_current + size > MAX_PRIMARY_HEADER_BYTES:
                raise BenchmarkError(
                    "primary FITS header read exceeded the 1 MiB safety limit; "
                    "refusing to risk reading an image payload"
                )
        data = self._raw.read(size)
        self._stats.archive_bytes_read += len(data)
        if self._stats._phase in {"a-header", "b-header"}:
            self._stats._header_bytes_current += len(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        self._stats.seek_calls += 1
        before = self._raw.tell()
        result = self._raw.seek(offset, whence)
        if result < before:
            self._stats.backward_seek_calls += 1
        elif result > before:
            self._stats.forward_seek_calls += 1
        if self._stats._phase in {"a-discovery", "b-traversal"}:
            self._stats.traversal_seek_calls += 1
        elif self._stats._phase in {"a-header", "b-header"}:
            self._stats.header_seek_calls += 1
        return result

    def tell(self) -> int:
        return self._raw.tell()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class StreamHeaderFile:
    """Header-only view over tarfile's current non-seekable stream position."""

    binary = True

    def __init__(self, stream: Any, stats: IOStats):
        self._stream = stream
        self._stats = stats

    def begin_header(self) -> None:
        self._stats._header_bytes_current = 0

    def end_header(self, start: int, end: int) -> int:
        consumed = end - start
        if consumed <= 0:
            raise BenchmarkError("primary FITS header read consumed no bytes")
        if consumed > MAX_PRIMARY_HEADER_BYTES:
            raise BenchmarkError(
                "primary FITS header read exceeded the 1 MiB safety limit; "
                "refusing to risk reading an image payload"
            )
        self._stats.header_bytes += consumed
        self._stats.header_reads += 1
        return consumed

    def read(self, size: int = -1) -> bytes:
        if size >= 0 and self._stats._header_bytes_current + size > MAX_PRIMARY_HEADER_BYTES:
            raise BenchmarkError(
                "primary FITS header read exceeded the 1 MiB safety limit; "
                "refusing to risk reading an image payload"
            )
        data = self._stream.read(size)
        self._stats._header_bytes_current += len(data)
        return data

    def seek(self, *args: Any, **kwargs: Any) -> int:
        raise BenchmarkError("FITS header parsing attempted a seek in Strategy C")

    def tell(self) -> int:
        return self._stream.tell()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class CountingInnerView:
    """Count logical operations on Corral's seekable ExFileObject view."""

    def __init__(self, raw: Any, stats: IOStats):
        self._raw = raw
        self._stats = stats

    def read(self, size: int = -1) -> bytes:
        data = self._raw.read(size)
        self._stats.archive_bytes_read += len(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        before = self._raw.tell()
        result = self._raw.seek(offset, whence)
        self._stats.seek_calls += 1
        if result < before:
            self._stats.backward_seek_calls += 1
        elif result > before:
            self._stats.forward_seek_calls += 1
        return result

    def tell(self) -> int:
        return self._raw.tell()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


@dataclass
class RunResult:
    strategy: str
    total_seconds: float
    discovery_seconds: Optional[float]
    header_seconds: Optional[float]
    records: list[MemberRecord]
    headers: list[dict[str, Any]]
    stats: IOStats
    logical_stream_bytes: Optional[int] = None


@dataclass
class CorralRunResult:
    outer_path: Path
    inner_name: str
    outer_size: int
    inner_size: int
    outer_location_seconds: float
    inner_setup_seconds: float
    inner_traversal_seconds: float
    total_seconds: float
    outer_members_examined: int
    records: list[MemberRecord]
    headers: list[dict[str, Any]]
    outer_stats: IOStats
    inner_stats: IOStats
    outer_location_seek_calls: int
    outer_location_forward_seeks: int
    outer_location_backward_seeks: int
    outer_location_bytes_read: int
    outer_inner_seek_calls: int
    outer_inner_forward_seeks: int
    outer_inner_backward_seeks: int
    outer_inner_bytes_read: int


def _human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.3f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    return f"{value} B"


def _test_observation_number(path: Path) -> Optional[int]:
    for component in path.parts:
        match = TEST_ARCHIVE_RE.fullmatch(component)
        if match:
            return int(match.group(1))
    return None


def _is_relevant_member(member: tarfile.TarInfo) -> bool:
    """Match FileSystemStorage's direct ordinary-tar FITS predicate."""
    return member.isfile() and member.name.endswith(".fits")


def _record(member: tarfile.TarInfo) -> MemberRecord:
    if member.offset_data is None or member.size is None:
        raise BenchmarkError(f"member has no usable offset/size: {member.name}")
    return MemberRecord(member.name, int(member.offset_data), int(member.size))


def _normalise_header_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("latin1", errors="replace")
    if hasattr(value, "item"):
        try:
            value = value.item()
        except ValueError:
            pass
    if isinstance(value, float) and math.isnan(value):
        return "<NaN>"
    return value


def _header_snapshot(header: fits.Header) -> dict[str, Any]:
    return {
        card: _normalise_header_value(header.get(card))
        for card in REPRESENTATIVE_CARDS
    }


def _read_primary_header(fileobj: Any, member: MemberRecord) -> tuple[dict[str, Any], int]:
    start = fileobj.tell()
    fileobj.begin_header()
    header = fits.Header.fromfile(
        fileobj,
        sep="",
        endcard=True,
        padding=True,
    )
    end = fileobj.tell()
    return _header_snapshot(header), fileobj.end_header(start, end)


def _open_archive(path: Path, stats: IOStats) -> tuple[BinaryIO, CountingFile, tarfile.TarFile]:
    raw = open(path, "rb")
    counted = CountingFile(raw, stats)
    try:
        archive = tarfile.open(fileobj=counted, mode="r:")
    except Exception:
        raw.close()
        raise
    stats.archive_opens += 1
    return raw, counted, archive


def _discover(path: Path, stats: IOStats) -> list[MemberRecord]:
    raw, counted, archive = _open_archive(path, stats)
    try:
        stats.member_list_scan_passes += 1
        records = []
        with counted.phase("a-discovery"):
            for member in archive:
                stats.member_scans += 1
                if _is_relevant_member(member):
                    records.append(_record(member))
        return records
    finally:
        archive.close()
        raw.close()


def _strategy_a(path: Path) -> RunResult:
    stats = IOStats()
    started = time.perf_counter()
    discovery_started = started
    records = _discover(path, stats)
    discovery_seconds = time.perf_counter() - discovery_started

    headers: list[dict[str, Any]] = []
    header_started = time.perf_counter()
    for member in records:
        raw = open(path, "rb")
        stats.header_file_opens += 1
        counted = CountingFile(raw, stats)
        try:
            with counted.phase("a-header"):
                counted.seek(member.offset_data)
                snapshot, _ = _read_primary_header(counted, member)
            headers.append(snapshot)
        finally:
            raw.close()
    header_seconds = time.perf_counter() - header_started
    return RunResult(
        strategy="A",
        total_seconds=time.perf_counter() - started,
        discovery_seconds=discovery_seconds,
        header_seconds=header_seconds,
        records=records,
        headers=headers,
        stats=stats,
    )


def _strategy_b(path: Path) -> RunResult:
    stats = IOStats()
    started = time.perf_counter()
    raw, counted, archive = _open_archive(path, stats)
    records: list[MemberRecord] = []
    headers: list[dict[str, Any]] = []
    try:
        stats.member_list_scan_passes += 1
        while True:
            with counted.phase("b-traversal"):
                member = archive.next()
            if member is None:
                break
            stats.member_scans += 1
            if not _is_relevant_member(member):
                continue
            record = _record(member)
            current = counted.tell()
            if current != record.offset_data:
                raise BenchmarkError(
                    "Strategy B did not encounter the member payload at the "
                    f"expected offset for {record.name}: current={current}, "
                    f"offset_data={record.offset_data}"
                )
            with counted.phase("b-header"):
                snapshot, _ = _read_primary_header(counted, record)
            records.append(record)
            headers.append(snapshot)
    finally:
        archive.close()
        raw.close()
    total_seconds = time.perf_counter() - started
    return RunResult(
        strategy="B",
        total_seconds=total_seconds,
        discovery_seconds=None,
        header_seconds=None,
        records=records,
        headers=headers,
        stats=stats,
    )


def _instrument_stream_seek(stream: Any, stats: IOStats) -> None:
    """Count tarfile's logical forward seeks; _Stream implements them as reads."""
    original_seek = stream.seek

    def counted_seek(position: int = 0) -> int:
        before = stream.tell()
        result = original_seek(position)
        after = stream.tell()
        if after < before:
            raise BenchmarkError("Strategy C performed a backwards stream seek")
        if after > before:
            stats.stream_forward_seek_calls += 1
        return result

    stream.seek = counted_seek


def _discard_stream_member_payload(stream: Any, member: MemberRecord, header_bytes: int, stats: IOStats) -> None:
    remaining = member.size - header_bytes
    if remaining < 0:
        raise BenchmarkError(
            f"primary header exceeds FITS member size for {member.name}: "
            f"header={header_bytes}, member={member.size}"
        )
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            raise BenchmarkError(
                f"unexpected EOF while consuming FITS member payload: {member.name}"
            )
        remaining -= len(chunk)
        stats.payload_bytes_discarded += len(chunk)


def _strategy_c(path: Path) -> RunResult:
    """Read an ordinary tar as a true sequential byte stream."""
    stats = IOStats()
    started = time.perf_counter()
    raw = open(path, "rb", buffering=0)
    counted = CountingFile(raw, stats)
    try:
        archive = tarfile.open(
            fileobj=counted,
            mode="r|",
            bufsize=tarfile.RECORDSIZE,
        )
        stats.archive_opens += 1
    except Exception:
        raw.close()
        raise

    records: list[MemberRecord] = []
    headers: list[dict[str, Any]] = []
    logical_stream_bytes: Optional[int] = None
    try:
        _instrument_stream_seek(archive.fileobj, stats)
        stats.member_list_scan_passes += 1
        while True:
            member = archive.next()
            if member is None:
                break
            stats.member_scans += 1
            if not _is_relevant_member(member):
                continue
            record = _record(member)
            current = archive.fileobj.tell()
            if current != record.offset_data:
                raise BenchmarkError(
                    "Strategy C did not encounter the member payload at the "
                    f"expected stream offset for {record.name}: current={current}, "
                    f"offset_data={record.offset_data}"
                )
            header_file = StreamHeaderFile(archive.fileobj, stats)
            snapshot, header_bytes = _read_primary_header(header_file, record)
            _discard_stream_member_payload(
                archive.fileobj, record, header_bytes, stats,
            )
            records.append(record)
            headers.append(snapshot)
        logical_stream_bytes = archive.fileobj.tell()
    finally:
        archive.close()
        raw.close()

    return RunResult(
        strategy="C",
        total_seconds=time.perf_counter() - started,
        discovery_seconds=None,
        header_seconds=None,
        records=records,
        headers=headers,
        stats=stats,
        logical_stream_bytes=logical_stream_bytes,
    )


def _is_production_inner_tar_name(name: str) -> bool:
    path = Path(name)
    return (
        path.name.lower().startswith("virus")
        and path.name.lower().endswith(".tar")
        and any(part.lower() == "virus" for part in path.parts[:-1])
    )


def _strategy_b_corral(outer_path: Path, inner_name: str) -> CorralRunResult:
    """Benchmark B-style access through one selected Corral nested-tar view."""
    if not _is_production_inner_tar_name(inner_name):
        raise BenchmarkError(
            "--corral-inner must name a nested production virus*.tar under a virus directory"
        )
    test_number = _test_observation_number(Path(inner_name))
    if test_number is not None and test_number >= 999:
        raise BenchmarkError(
            f"refusing test observation inner archive {inner_name} (observation {test_number} >= 999)"
        )

    total_started = time.perf_counter()
    outer_stats = IOStats()
    outer_open_started = time.perf_counter()
    raw = open(outer_path, "rb", buffering=0)
    counted_outer = CountingFile(raw, outer_stats)
    try:
        outer = tarfile.open(fileobj=counted_outer, mode="r:")
        outer_stats.archive_opens += 1
    except Exception:
        raw.close()
        raise

    nested_info: Optional[tarfile.TarInfo] = None
    try:
        outer_stats.member_list_scan_passes += 1
        for member in outer:
            outer_stats.member_scans += 1
            if member.name == inner_name:
                if not member.isfile() or not _is_production_inner_tar_name(member.name):
                    raise BenchmarkError(f"requested member is not a regular production inner tar: {inner_name}")
                nested_info = member
                break
        if nested_info is None:
            raise BenchmarkError(f"nested tar member not found in outer archive: {inner_name}")
        outer_location_seconds = time.perf_counter() - outer_open_started

        outer_location_seek_calls = outer_stats.seek_calls
        outer_location_forward_seeks = outer_stats.forward_seek_calls
        outer_location_backward_seeks = outer_stats.backward_seek_calls
        outer_location_bytes_read = outer_stats.archive_bytes_read

        inner_setup_started = time.perf_counter()
        inner_stream = outer.extractfile(nested_info)
        if inner_stream is None:
            raise BenchmarkError(f"could not open nested tar member: {inner_name}")
        inner_stats = IOStats()
        counted_inner = CountingInnerView(inner_stream, inner_stats)
        try:
            inner = tarfile.open(fileobj=counted_inner, mode="r:")
            inner_stats.archive_opens += 1
        except Exception:
            inner_stream.close()
            raise
        inner_setup_seconds = time.perf_counter() - inner_setup_started

        records: list[MemberRecord] = []
        headers: list[dict[str, Any]] = []
        inner_traversal_started = time.perf_counter()
        try:
            inner_stats.member_list_scan_passes += 1
            for member in inner:
                inner_stats.member_scans += 1
                if not _is_relevant_member(member):
                    continue
                record = _record(member)
                current = counted_inner.tell()
                if current != record.offset_data:
                    raise BenchmarkError(
                        "Corral inner Strategy B did not encounter the member payload at "
                        f"the expected offset for {record.name}: current={current}, "
                        f"offset_data={record.offset_data}"
                    )
                header_file = StreamHeaderFile(counted_inner, inner_stats)
                snapshot, header_bytes = _read_primary_header(header_file, record)
                _discard_stream_member_payload(
                    counted_inner, record, header_bytes, inner_stats,
                )
                records.append(record)
                headers.append(snapshot)
        finally:
            inner.close()
            inner_stream.close()
        inner_traversal_seconds = time.perf_counter() - inner_traversal_started

        outer_inner_seek_calls = outer_stats.seek_calls - outer_location_seek_calls
        outer_inner_forward_seeks = outer_stats.forward_seek_calls - outer_location_forward_seeks
        outer_inner_backward_seeks = outer_stats.backward_seek_calls - outer_location_backward_seeks
        outer_inner_bytes_read = outer_stats.archive_bytes_read - outer_location_bytes_read
        return CorralRunResult(
            outer_path=outer_path,
            inner_name=inner_name,
            outer_size=outer_path.stat().st_size,
            inner_size=int(nested_info.size),
            outer_location_seconds=outer_location_seconds,
            inner_setup_seconds=inner_setup_seconds,
            inner_traversal_seconds=inner_traversal_seconds,
            total_seconds=time.perf_counter() - total_started,
            outer_members_examined=outer_stats.member_scans,
            records=records,
            headers=headers,
            outer_stats=outer_stats,
            inner_stats=inner_stats,
            outer_location_seek_calls=outer_location_seek_calls,
            outer_location_forward_seeks=outer_location_forward_seeks,
            outer_location_backward_seeks=outer_location_backward_seeks,
            outer_location_bytes_read=outer_location_bytes_read,
            outer_inner_seek_calls=outer_inner_seek_calls,
            outer_inner_forward_seeks=outer_inner_forward_seeks,
            outer_inner_backward_seeks=outer_inner_backward_seeks,
            outer_inner_bytes_read=outer_inner_bytes_read,
        )
    finally:
        outer.close()
        raw.close()


def _compare_results(a: RunResult, b: RunResult) -> None:
    if a.records != b.records:
        for index, (a_record, b_record) in enumerate(zip(a.records, b.records)):
            if a_record != b_record:
                raise BenchmarkError(
                    "member identities differ at index "
                    f"{index}: A={a_record!r}, B={b_record!r}"
                )
        raise BenchmarkError(
            f"member count differs: A={len(a.records)}, B={len(b.records)}"
        )
    if len(a.headers) != len(b.headers):
        raise BenchmarkError(
            f"header result count differs: A={len(a.headers)}, B={len(b.headers)}"
        )
    for index, (a_header, b_header) in enumerate(zip(a.headers, b.headers)):
        for card in REPRESENTATIVE_CARDS:
            if a_header[card] != b_header[card]:
                raise BenchmarkError(
                    "representative header values differ for "
                    f"{a.records[index].name}, card {card}: "
                    f"A={a_header[card]!r}, B={b_header[card]!r}"
                )


def _format_value(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _print_stats(stats: IOStats) -> None:
    print(f"      archive/tar opens:        {stats.archive_opens}")
    print(f"      header file opens:        {stats.header_file_opens}")
    print(f"      total underlying opens:   {stats.archive_opens + stats.header_file_opens}")
    print(f"      member-list scan passes:  {stats.member_list_scan_passes}")
    print(f"      members examined:         {stats.member_scans}")
    print("      hidden getmember scans:   0")
    print(f"      seek calls:               {stats.seek_calls}")
    print(f"        traversal skip seeks:  {stats.traversal_seek_calls}")
    print(f"        header-phase seeks:     {stats.header_seek_calls}")
    print(f"        backward seeks:         {stats.backward_seek_calls}")
    print(f"      FITS header reads:        {stats.header_reads}")
    print(f"      FITS header bytes read:   {stats.header_bytes:,} ({_human_bytes(stats.header_bytes)})")


def _print_header_samples(
    result: RunResult, title: str = "Header comparison sample (first 3 members; missing cards are None):",
) -> None:
    print(f"    {title}")
    for record, header in zip(result.records[:3], result.headers[:3]):
        values = ", ".join(
            f"{card}={_format_value(header[card])}"
            for card in REPRESENTATIVE_CARDS
            if header[card] is not None
        )
        print(f"      {record.name}: {values or '(no representative cards present)'}")


def _print_strategy_c_stats(path: Path, result: RunResult) -> None:
    size = path.stat().st_size
    bytes_read = result.stats.archive_bytes_read
    fraction = bytes_read / size if size else float("nan")
    throughput = (
        bytes_read / (1024 * 1024) / result.total_seconds
        if result.total_seconds
        else float("nan")
    )
    print("Strategy C: sequential stream")
    print("  total:")
    print(f"      wall time:       {result.total_seconds:.6f} s")
    print(f"      archive opens:             {result.stats.archive_opens}")
    print("      header file opens:         0")
    print(f"      total underlying opens:    {result.stats.archive_opens}")
    print(f"      member-list scan passes:   {result.stats.member_list_scan_passes}")
    print(f"      members examined:          {result.stats.member_scans}")
    print("      hidden getmember scans:    0")
    print(f"      relevant FITS members:     {len(result.records)}")
    print(f"      FITS headers parsed:        {result.stats.header_reads}")
    print(f"      FITS header bytes parsed:   {result.stats.header_bytes:,}")
    print(f"      payload bytes discarded:   {result.stats.payload_bytes_discarded:,}")
    print(f"      total archive bytes read:   {bytes_read:,}")
    print(f"      logical stream bytes:       {result.logical_stream_bytes}")
    print(f"      archive size:               {size:,}")
    print(f"      fraction physically read:  {fraction:.6f}")
    print(f"      effective throughput:       {throughput:.3f} MiB/s")
    print(f"      filesystem seek calls:      {result.stats.seek_calls}")
    print(f"        forward seeks:            {result.stats.forward_seek_calls}")
    print(f"        backward seeks:           {result.stats.backward_seek_calls}")
    print("        direct header seeks:      0")
    print(
        "      tarfile logical forward seeks (implemented by reads): "
        f"{result.stats.stream_forward_seek_calls}"
    )


def _print_corral_report(result: CorralRunResult) -> None:
    inner = result.inner_stats
    outer = result.outer_stats
    print("VIRUSFlow Corral nested-tar Strategy B benchmark")
    print("Execution mode: selected inner Strategy B only")
    print(f"Outer date tar: {result.outer_path}")
    print(f"Nested member: {result.inner_name}")
    print()
    print("Outer tar")
    print(f"  size:                         {result.outer_size:,} bytes ({_human_bytes(result.outer_size)})")
    print(f"  open + member-location time:  {result.outer_location_seconds:.6f} s")
    print(f"  source opens:                 {outer.archive_opens}")
    print(f"  tar members examined:         {result.outer_members_examined}")
    print(f"  bytes read during location:   {result.outer_location_bytes_read:,}")
    print(f"  location seek calls:          {result.outer_location_seek_calls}")
    print(f"    forward seeks:              {result.outer_location_forward_seeks}")
    print(f"    backward seeks:             {result.outer_location_backward_seeks}")
    print()
    print("Inner VIRUS tar")
    print(f"  logical size:                 {result.inner_size:,} bytes ({_human_bytes(result.inner_size)})")
    print(f"  setup + open time:            {result.inner_setup_seconds:.6f} s")
    print(f"  traversal + headers time:     {result.inner_traversal_seconds:.6f} s")
    print(f"  logical tar opens:            {inner.archive_opens}")
    print(f"  relevant FITS members:        {len(result.records)}")
    print(f"  FITS headers parsed:           {inner.header_reads}")
    print(f"  FITS header bytes parsed:      {inner.header_bytes:,}")
    print(f"  logical inner bytes read:      {inner.archive_bytes_read:,}")
    print(f"  payload bytes discarded:       {inner.payload_bytes_discarded:,}")
    print(f"  inner seek calls:              {inner.seek_calls}")
    print(f"    forward seeks:               {inner.forward_seek_calls}")
    print(f"    backward seeks:              {inner.backward_seek_calls}")
    print("    direct header seeks:         0")
    print()
    print("Outer physical access while reading inner tar")
    print(f"  bytes read:                    {result.outer_inner_bytes_read:,}")
    print(f"  seek calls:                    {result.outer_inner_seek_calls}")
    print(f"    forward seeks:               {result.outer_inner_forward_seeks}")
    print(f"    backward seeks:              {result.outer_inner_backward_seeks}")
    print("  inner seek mechanism:          outer-file seeks through ExFileObject/_FileInFile")
    print()
    print("Validation")
    print("  requested inner member:        PASS")
    print("  production FITS members:       PASS")
    print("  primary-header parsing:        PASS")
    print("  FITS image arrays loaded:      NO")
    print("  unrelated inner tars opened:   0")
    print()
    print(f"Total time: {result.total_seconds:.6f} s")
    print("Header sample (first 3 members; missing cards are None):")
    _print_header_samples(
        RunResult(
            strategy="B",
            total_seconds=result.inner_traversal_seconds,
            discovery_seconds=None,
            header_seconds=None,
            records=result.records,
            headers=result.headers,
            stats=inner,
        ),
        "  Primary-header sample (first 3 members; missing cards are None):",
    )


def _print_report(path: Path, a: RunResult, b: RunResult) -> None:
    size = path.stat().st_size
    ratio = b.total_seconds / a.total_seconds if a.total_seconds else float("nan")
    speedup = a.total_seconds / b.total_seconds if b.total_seconds else float("inf")
    saved = a.total_seconds - b.total_seconds
    percent = saved / a.total_seconds * 100 if a.total_seconds else float("nan")
    print("=" * 70)
    print(f"Tar: {path}")
    print(f"Size: {size:,} bytes ({_human_bytes(size)})")
    print(f"Relevant FITS members: {len(a.records)}")
    print()
    print("Strategy A: two-phase")
    print("  discovery:")
    print(f"      wall time:       {a.discovery_seconds:.6f} s")
    print("  header phase:")
    print(f"      wall time:       {a.header_seconds:.6f} s")
    print("  total:")
    print(f"      wall time:       {a.total_seconds:.6f} s")
    _print_stats(a.stats)
    print()
    print("Strategy B: ordered acquisition")
    print("  traversal + headers:")
    print(f"      wall time:       {b.total_seconds:.6f} s")
    print("  total:")
    print(f"      wall time:       {b.total_seconds:.6f} s")
    _print_stats(b.stats)
    print()
    print("Correctness:")
    print("  member identities: PASS")
    print("  header comparison: PASS")
    print(f"  representative cards compared per member: {len(REPRESENTATIVE_CARDS)}")
    _print_header_samples(a)
    print()
    print("Comparison:")
    print(f"  A total:       {a.total_seconds:.6f} s")
    print(f"  B total:       {b.total_seconds:.6f} s")
    print(f"  B/A ratio:     {ratio:.6f}")
    print(f"  speedup A/B:   {speedup:.6f}x")
    print(f"  seconds saved: {saved:.6f} s")
    print(f"  percent saved: {percent:.3f}%")
    print("=" * 70)


def _print_aggregate(results: list[tuple[RunResult, RunResult]]) -> None:
    a_total = sum(a.total_seconds for a, _ in results)
    b_total = sum(b.total_seconds for _, b in results)
    members = sum(len(a.records) for a, _ in results)
    speedup = a_total / b_total if b_total else float("inf")
    reduction = (a_total - b_total) / a_total * 100 if a_total else float("nan")
    print()
    print("Aggregate")
    print(f"  tar count:     {len(results)}")
    print(f"  members:       {members}")
    print()
    print(f"  Strategy A total: {a_total:.6f} s")
    print(f"  Strategy B total: {b_total:.6f} s")
    print(f"  speedup:          {speedup:.6f}x")
    print(f"  time reduction:   {reduction:.3f}%")


def _print_single_report(path: Path, result: RunResult) -> None:
    size = path.stat().st_size
    print("=" * 70)
    print(f"Tar: {path}")
    print(f"Size: {size:,} bytes ({_human_bytes(size)})")
    print(f"Relevant FITS members: {len(result.records)}")
    print()
    if result.strategy == "A":
        print("Strategy A: two-phase")
        print("  discovery:")
        print(f"      wall time:       {result.discovery_seconds:.6f} s")
        print("  header phase:")
        print(f"      wall time:       {result.header_seconds:.6f} s")
        print("  total:")
        print(f"      wall time:       {result.total_seconds:.6f} s")
        _print_stats(result.stats)
    elif result.strategy == "B":
        print("Strategy B: ordered acquisition")
        print("  traversal + headers:")
        print(f"      wall time:       {result.total_seconds:.6f} s")
        print("  total:")
        print(f"      wall time:       {result.total_seconds:.6f} s")
        _print_stats(result.stats)
    else:
        _print_strategy_c_stats(path, result)
    print()
    print("Validation:")
    print("  selected-strategy member/header work: PASS")
    print(f"  primary headers parsed:               {len(result.headers)}")
    print("  A/B equivalence comparison:           NOT RUN (single-strategy mode)")
    _print_header_samples(result, "Primary-header sample (first 3 members; missing cards are None):")
    print("=" * 70)


def _print_single_aggregate(results: list[RunResult]) -> None:
    strategy = results[0].strategy
    total = sum(result.total_seconds for result in results)
    members = sum(len(result.records) for result in results)
    print()
    print("Aggregate")
    print(f"  tar count:     {len(results)}")
    print(f"  members:       {members}")
    print()
    print(f"  Strategy {strategy} total: {total:.6f} s")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two physical primary-FITS-header access patterns in ordinary VIRUS tars."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--order",
        choices=("A-B", "B-A"),
        help="strategy execution order for each tar (default: A-B)",
    )
    mode.add_argument(
        "--strategy",
        choices=("A", "B", "C"),
        help="run only the selected strategy for each tar",
    )
    mode.add_argument(
        "--corral-inner",
        metavar="MEMBER",
        help="benchmark B-style access for one nested Corral virus*.tar member",
    )
    parser.add_argument("tar_paths", nargs="+", type=Path, metavar="TAR")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    paths = [path.expanduser() for path in args.tar_paths]

    if args.corral_inner is not None:
        if len(paths) != 1:
            print("ERROR: --corral-inner requires exactly one outer date tar path", file=sys.stderr)
            return 2
        outer_path = paths[0]
        if not outer_path.is_file():
            print(f"ERROR: outer date tar is not a regular file: {outer_path}", file=sys.stderr)
            return 2
        try:
            result = _strategy_b_corral(outer_path, args.corral_inner)
            _print_corral_report(result)
        except (BenchmarkError, OSError, tarfile.TarError, fits.VerifyError) as exc:
            print(f"ERROR while benchmarking Corral inner tar {args.corral_inner}: {exc}", file=sys.stderr)
            return 1
        return 0

    for path in paths:
        if not path.is_file():
            print(f"ERROR: tar path is not a regular file: {path}", file=sys.stderr)
            return 2
        test_number = _test_observation_number(path)
        if test_number is not None and test_number >= 999:
            print(
                f"ERROR: refusing test observation archive {path} (observation {test_number} >= 999)",
                file=sys.stderr,
            )
            return 2

    execution_order = args.order or "A-B"
    print("VIRUSFlow tar primary-header access benchmark")
    if args.strategy is not None:
        print(f"Execution mode: Strategy {args.strategy} only")
    else:
        print(f"Execution order for each tar: {execution_order}")
    print("Filesystem cache flushing: none")
    print("Tar mode: r: (ordinary uncompressed tar only)")
    print("Physical access notes:")
    print("  Strategy A discovers with TarFile.next() iteration, then does one")
    print("  independent open+seek(offset_data)+primary-header-read per FITS member.")
    print("  Strategy B reads from tar.fileobj at offset_data immediately after")
    print("  TarFile.next(); subsequent TarFile.next() calls seek past payloads.")
    print("  B uses neither extractfile() nor getmember(), and does not read image payloads.")
    print("  B therefore removes the later independent header seeks, but traversal")
    print("  can still issue seeks to skip each unconsumed member payload.")
    print("  Strategy C uses tarfile r|, reads headers at the current stream position,")
    print("  and consumes/discards each remaining FITS member payload sequentially.")
    print("  C uses one unbuffered archive source and does not issue filesystem seeks.")
    print()

    results: list[tuple[RunResult, RunResult]] = []
    single_results: list[RunResult] = []
    for path in paths:
        try:
            if args.strategy == "A":
                selected = _strategy_a(path)
                _print_single_report(path, selected)
                single_results.append(selected)
                continue
            elif args.strategy == "B":
                selected = _strategy_b(path)
                _print_single_report(path, selected)
                single_results.append(selected)
                continue
            elif args.strategy == "C":
                selected = _strategy_c(path)
                _print_single_report(path, selected)
                single_results.append(selected)
                continue
            elif execution_order == "A-B":
                a = _strategy_a(path)
                b = _strategy_b(path)
            else:
                b = _strategy_b(path)
                a = _strategy_a(path)
            _compare_results(a, b)
            _print_report(path, a, b)
            results.append((a, b))
        except (BenchmarkError, OSError, tarfile.TarError, fits.VerifyError) as exc:
            print(f"ERROR while benchmarking {path}: {exc}", file=sys.stderr)
            return 1

    if args.strategy is not None:
        if len(single_results) > 1:
            _print_single_aggregate(single_results)
    elif len(results) > 1:
        _print_aggregate(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
