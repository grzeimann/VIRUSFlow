from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple, Literal
import tarfile
from dataclasses import dataclass


@dataclass
class RawSource:
    path: Path
    tar_member: Optional[str] = None
    backend: Literal["filesystem", "tar", "date_tar"] = "filesystem"
    outer_tar_member: Optional[str] = None


def read_member_bytes(source: RawSource) -> bytes:
    """Read the raw bytes for a RawSource regardless of storage backend."""
    if source.backend == "filesystem":
        return Path(source.path).read_bytes()
    if source.backend == "tar":
        with tarfile.open(source.path, mode="r") as tf:
            member = tf.getmember(source.tar_member)
            ef = tf.extractfile(member)
            if ef is None:
                raise FileNotFoundError(f"Cannot extract {source.tar_member} from {source.path}")
            return ef.read()
    if source.backend == "date_tar":
        with tarfile.open(source.path, mode="r") as outer:
            outer_member = outer.getmember(source.outer_tar_member)
            outer_stream = outer.extractfile(outer_member)
            if outer_stream is None:
                raise FileNotFoundError(
                    f"Cannot extract {source.outer_tar_member} from {source.path}"
                )
            with tarfile.open(fileobj=outer_stream, mode="r") as inner:
                member = inner.getmember(source.tar_member)
                ef = inner.extractfile(member)
                if ef is None:
                    raise FileNotFoundError(
                        f"Cannot extract {source.tar_member} from {source.outer_tar_member}"
                    )
                return ef.read()
    raise ValueError(f"Unknown storage backend: {source.backend!r}")


class FileSystemStorage:
    """Simple filesystem storage listing raw frames by extension or inside tar archives."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)

    def exists(self, relative: str | os.PathLike[str]) -> bool:
        return (self.root / relative).exists()

    def list_fits(
        self,
        subdir: Optional[str] = None,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Iterator[Path]:
        base = self.root if subdir is None else (self.root / subdir)
        if not base.exists():
            return iter(())
        date_roots = self._bounded_date_roots(
            subdir=subdir, start_date=start_date, end_date=end_date,
        )
        if date_roots is not None:
            for root in date_roots:
                if root.is_dir():
                    yield from (p for p in root.rglob("*.fits") if p.is_file())
            return
        for p in base.rglob("*.fits"):
            if p.is_file():
                yield p

    @staticmethod
    def _date_token(value: str) -> Optional[str]:
        """Return a YYYYMMDD token when *value* is a date archive/directory name."""
        name = Path(value).name
        if name.lower().endswith(".tar"):
            name = name[:-4]
        if len(name) == 8 and name.isdigit():
            return name
        return None

    def _bounded_date_roots(
        self, *, subdir: Optional[str], start_date: Optional[str], end_date: Optional[str],
    ) -> Optional[List[Path]]:
        """Return selected date roots for a homogeneous TACC date-archive root.

        A Corral raw root commonly contains only ``YYYYMMDD.tar`` files (or
        date-named directories).  In that layout, scanning a bounded interval
        must not recursively walk and open every archive in a multi-year tree.
        We deliberately require the root to be homogeneous before using this
        optimization; mixed or unfamiliar layouts retain the historical full
        traversal so no in-window source can be silently omitted.
        """
        if not (start_date or end_date):
            return None
        base = self.root if subdir is None else (self.root / subdir)
        try:
            entries = list(base.iterdir())
        except OSError:
            return None
        visible = [entry for entry in entries if not entry.name.startswith(".")]
        if not visible:
            return None
        dated = [entry for entry in visible if self._date_token(entry.name) is not None]
        if len(dated) != len(visible):
            return None
        selected = []
        for entry in dated:
            date = self._date_token(entry.name)
            assert date is not None
            if ((start_date and date < start_date)
                    or (end_date and date > end_date)):
                continue
            selected.append(entry)
        return sorted(selected)

    def _iter_top_level_tar_paths(
        self,
        subdir: Optional[str] = None,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Iterator[Path]:
        """Yield tar paths, pruning a homogeneous date-archive root when bounded."""
        date_roots = self._bounded_date_roots(
            subdir=subdir, start_date=start_date, end_date=end_date,
        )
        if date_roots is not None:
            for root in date_roots:
                if root.is_file() and root.name.lower().endswith(".tar"):
                    yield root
                elif root.is_dir():
                    yield from root.rglob("*.tar")
            return
        base = self.root if subdir is None else (self.root / subdir)
        if not base.exists():
            return
        yield from base.rglob("*.tar")

    def list_tar_fits(self, subdir: Optional[str] = None) -> Iterator[Tuple[Path, str]]:
        """Yield (tar_path, member_name) for FITS files stored directly inside .tar archives.

        Note: member_name is the path within the tar archive. Tars whose members are
        themselves nested tars (Corral date-tar layout) are handled by
        list_date_tar_fits() instead.
        """
        for tar_path, members in self._iter_top_level_tars(subdir=subdir):
            fits_members = [m for m in members if m.isfile() and m.name.endswith(".fits")]
            if fits_members:
                for member in fits_members:
                    yield tar_path, member.name

    def list_date_tar_fits(
        self, subdir: Optional[str] = None
    ) -> Iterator[Tuple[Path, str, str]]:
        """Yield (date_tar_path, nested_tar_member, fits_member) for Corral date-tars.

        A date-tar (e.g. 20260501.tar) contains nested VIRUS tars (e.g. virus/virus...tar),
        each of which contains FITS files as usual.
        """
        for tar_path, members in self._iter_top_level_tars(subdir=subdir):
            fits_members = [m for m in members if m.isfile() and m.name.endswith(".fits")]
            if fits_members:
                continue
            nested_tar_members = [m for m in members if m.isfile() and m.name.endswith(".tar")]
            if not nested_tar_members:
                continue
            try:
                with tarfile.open(tar_path, "r") as outer:
                    for nested in nested_tar_members:
                        stream = outer.extractfile(nested)
                        if stream is None:
                            continue
                        try:
                            with tarfile.open(fileobj=stream, mode="r") as inner:
                                for inner_member in inner:
                                    if inner_member.isfile() and inner_member.name.endswith(".fits"):
                                        yield tar_path, nested.name, inner_member.name
                        except (tarfile.TarError, OSError):
                            continue
            except (tarfile.TarError, OSError):
                continue

    def _iter_top_level_tars(
        self,
        subdir: Optional[str] = None,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Iterator[Tuple[Path, List[tarfile.TarInfo]]]:
        base = self.root if subdir is None else (self.root / subdir)
        if not base.exists():
            return iter(())
        for tar_path in self._iter_top_level_tar_paths(
            subdir=subdir, start_date=start_date, end_date=end_date,
        ):
            if not tar_path.is_file():
                continue
            try:
                with tarfile.open(tar_path, "r") as tf:
                    members = list(tf.getmembers())
            except (tarfile.TarError, OSError):
                # Skip unreadable/corrupt tar files silently for now
                continue
            yield tar_path, members

    def iter_raw_sources(
        self,
        subdir: Optional[str] = None,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Iterator[RawSource]:
        # filesystem files
        for p in self.list_fits(
            subdir=subdir, start_date=start_date, end_date=end_date,
        ):
            yield RawSource(path=p, tar_member=None, backend="filesystem")
        # tar members (direct FITS members) and date-tar members (nested tars) are
        # mutually exclusive per top-level tar, detected by inspecting its contents once.
        for tar_path, members in self._iter_top_level_tars(
            subdir=subdir, start_date=start_date, end_date=end_date,
        ):
            fits_members = [m for m in members if m.isfile() and m.name.endswith(".fits")]
            if fits_members:
                for member in fits_members:
                    yield RawSource(path=tar_path, tar_member=member.name, backend="tar")
                continue
            nested_tar_members = [m for m in members if m.isfile() and m.name.endswith(".tar")]
            if not nested_tar_members:
                continue
            try:
                with tarfile.open(tar_path, "r") as outer:
                    for nested in nested_tar_members:
                        stream = outer.extractfile(nested)
                        if stream is None:
                            continue
                        try:
                            with tarfile.open(fileobj=stream, mode="r") as inner:
                                for inner_member in inner:
                                    if inner_member.isfile() and inner_member.name.endswith(".fits"):
                                        yield RawSource(
                                            path=tar_path,
                                            tar_member=inner_member.name,
                                            backend="date_tar",
                                            outer_tar_member=nested.name,
                                        )
                        except (tarfile.TarError, OSError):
                            continue
            except (tarfile.TarError, OSError):
                continue
