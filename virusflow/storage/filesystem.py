from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path, PurePosixPath
from typing import Iterator, List, Literal, Optional, Tuple
import tarfile

from astropy.io import fits


@dataclass
class RawSource:
    path: Path
    tar_member: Optional[str] = None
    backend: Literal["filesystem", "tar", "date_tar"] = "filesystem"
    outer_tar_member: Optional[str] = None
    # Ephemeral evidence acquired while a recognized production tar is being
    # traversed.  Storage owns acquisition; registration owns interpretation.
    primary_header: Optional[fits.Header] = field(default=None, repr=False, compare=False)


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


def _read_primary_header_at_current_position(fileobj) -> Optional[fits.Header]:
    """Read only a primary FITS header from the current tar payload position."""
    try:
        return fits.Header.fromfile(
            fileobj,
            sep="",
            endcard=True,
            padding=True,
        )
    except Exception:
        return None


def _skip_failed_header_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> None:
    """Restore tar traversal position after an unsuccessful header parse."""
    if member.offset_data is not None and member.size is not None:
        archive.fileobj.seek(int(member.offset_data) + int(member.size))


def _iter_strategy_b_tar_sources(tar_path: Path) -> Iterator[RawSource]:
    """Yield direct VIRUS-tar FITS sources with their headers acquired in order."""
    with tarfile.open(tar_path, mode="r:") as archive:
        while True:
            member = archive.next()
            if member is None:
                break
            if not member.isfile() or not member.name.endswith(".fits"):
                continue
            header = _read_primary_header_at_current_position(archive.fileobj)
            if header is None:
                _skip_failed_header_member(archive, member)
            yield RawSource(
                path=tar_path,
                tar_member=member.name,
                backend="tar",
                primary_header=header,
            )


def _iter_strategy_b_date_tar_sources(
    tar_path: Path,
) -> Iterator[RawSource]:
    """Yield nested Corral VIRUS sources with ordered inner-header acquisition."""
    with tarfile.open(tar_path, mode="r:") as outer:
        for nested_tar in outer:
            if not nested_tar.isfile() or not FileSystemStorage._is_virus_archive(nested_tar.name):
                continue
            if FileSystemStorage._is_test_archive(nested_tar.name):
                continue
            stream = outer.extractfile(nested_tar)
            if stream is None:
                continue
            try:
                try:
                    with tarfile.open(fileobj=stream, mode="r") as inner:
                        while True:
                            member = inner.next()
                            if member is None:
                                break
                            if not member.isfile() or not member.name.endswith(".fits"):
                                continue
                            header = _read_primary_header_at_current_position(inner.fileobj)
                            if header is None:
                                _skip_failed_header_member(inner, member)
                            yield RawSource(
                                path=tar_path,
                                tar_member=member.name,
                                backend="date_tar",
                                outer_tar_member=nested_tar.name,
                                primary_header=header,
                            )
                except (tarfile.TarError, OSError):
                    continue
            finally:
                stream.close()


def _night_token(value: str) -> Optional[str]:
    """Return a YYYYMMDD token from a night directory or date-tar name."""
    name = Path(value).name
    if name.lower().endswith(".tar"):
        name = name[:-4]
    return name if len(name) == 8 and name.isdigit() else None


def validate_night_range(
    first_night: Optional[str], last_night: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Validate and normalize inclusive HET observing-night labels."""
    normalized = []
    for option, value in (("--first-night", first_night), ("--last-night", last_night)):
        if value is None:
            normalized.append(None)
            continue
        try:
            text = str(value)
            if len(text) != 8 or not text.isdigit():
                raise ValueError
            parsed = datetime.strptime(text, "%Y%m%d")
        except ValueError as exc:
            raise ValueError(f"{option} must use YYYYMMDD") from exc
        normalized.append(parsed.strftime("%Y%m%d"))
    first, last = normalized
    if first and last and first > last:
        raise ValueError("--first-night must be on or before --last-night")
    return first, last


class FileSystemStorage:
    """Discover filesystem and tar-backed VIRUS raw inputs."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)

    def exists(self, relative: str | os.PathLike[str]) -> bool:
        return (self.root / relative).exists()

    @staticmethod
    def _is_virus_archive(name: str) -> bool:
        parts = PurePosixPath(name).parts
        return (
            any(part.lower() == "virus" for part in parts[:-1])
            and parts[-1].lower().startswith("virus")
            and parts[-1].lower().endswith(".tar")
        )

    @staticmethod
    def _is_test_archive(path: Path | str) -> bool:
        # Keep source discovery tied to the same established invariant used by
        # register_raw_file(), without duplicating its filename rule here.
        from ..registry.database import is_test_observation_path

        return is_test_observation_path(str(path))

    def _night_containers(
        self, first_night: str, last_night: str, subdir: Optional[str] = None,
    ) -> Optional[tuple[str, list[tuple[str, Path]]]]:
        """Recognize HET roots and select their inclusive night containers.

        ``None`` means the root is unfamiliar and should use the historical
        recursive fallback.  An empty list means a recognized root with no
        containers in the requested range.
        """
        base = self.root if subdir is None else self.root / subdir
        try:
            entries = list(base.iterdir())
        except OSError:
            return None

        work = [
            (night, entry) for entry in entries
            if entry.is_dir()
            and (night := _night_token(entry.name)) is not None
            and (entry / "virus").is_dir()
        ]
        if work:
            return "work", sorted(
                (night, entry) for night, entry in work
                if first_night <= night <= last_night
            )

        corral = [
            (night, entry) for entry in entries
            if entry.is_file()
            and (night := _night_token(entry.name)) is not None
            and entry.name.lower().endswith(".tar")
        ]
        if corral:
            return "corral", sorted(
                (night, entry) for night, entry in corral
                if first_night <= night <= last_night
            )
        return None

    @staticmethod
    def _fits_in_tar(
        tar_path: Path, members: List[tarfile.TarInfo], *, virus_only: bool = False,
        skip_test_archives: bool = False,
    ) -> Iterator[RawSource]:
        fits_members = [
            member for member in members
            if member.isfile() and member.name.endswith(".fits")
        ]
        if fits_members:
            for member in fits_members:
                yield RawSource(path=tar_path, tar_member=member.name, backend="tar")
            return
        nested = [
            member for member in members
            if member.isfile()
            and member.name.lower().endswith(".tar")
            and (not virus_only or FileSystemStorage._is_virus_archive(member.name))
            and (not skip_test_archives or not FileSystemStorage._is_test_archive(member.name))
        ]
        if not nested:
            return
        try:
            with tarfile.open(tar_path, "r") as outer:
                for nested_tar in nested:
                    stream = outer.extractfile(nested_tar)
                    if stream is None:
                        continue
                    try:
                        with tarfile.open(fileobj=stream, mode="r") as inner:
                            for member in inner:
                                if member.isfile() and member.name.endswith(".fits"):
                                    yield RawSource(
                                        path=tar_path,
                                        tar_member=member.name,
                                        backend="date_tar",
                                        outer_tar_member=nested_tar.name,
                                    )
                    except (tarfile.TarError, OSError):
                        continue
        except (tarfile.TarError, OSError):
            return

    def _iter_selected_work(self, nights: list[tuple[str, Path]]) -> Iterator[RawSource]:
        for night, container in nights:
            virus_root = container / "virus"
            print(f"Scanning night {night}: {virus_root}", flush=True)
            source_count = 0
            try:
                for path in sorted(p for p in virus_root.rglob("*.fits") if p.is_file()):
                    source_count += 1
                    yield RawSource(path=path)
                for tar_path in sorted(p for p in virus_root.rglob("virus*.tar") if p.is_file()):
                    if self._is_test_archive(tar_path):
                        continue
                    try:
                        for source in _iter_strategy_b_tar_sources(tar_path):
                            source_count += 1
                            yield source
                    except (tarfile.TarError, OSError):
                        continue
            finally:
                print(f"Finished night {night}: {source_count} raw sources", flush=True)

    def _iter_selected_corral(self, nights: list[tuple[str, Path]]) -> Iterator[RawSource]:
        for night, archive in nights:
            print(f"Scanning night archive {night}: {archive}", flush=True)
            source_count = 0
            try:
                try:
                    sources = _iter_strategy_b_date_tar_sources(archive)
                    for source in sources:
                        source_count += 1
                        yield source
                except (tarfile.TarError, OSError):
                    continue
            finally:
                print(f"Finished night {night}: {source_count} raw sources", flush=True)

    def list_fits(self, subdir: Optional[str] = None) -> Iterator[Path]:
        base = self.root if subdir is None else self.root / subdir
        if not base.exists():
            return
        for path in base.rglob("*.fits"):
            if path.is_file():
                yield path

    def _iter_top_level_tar_paths(self, subdir: Optional[str] = None) -> Iterator[Path]:
        base = self.root if subdir is None else self.root / subdir
        if not base.exists():
            return
        yield from (path for path in base.rglob("*.tar") if path.is_file())

    def _iter_top_level_tars(
        self, subdir: Optional[str] = None,
    ) -> Iterator[Tuple[Path, List[tarfile.TarInfo]]]:
        for tar_path in self._iter_top_level_tar_paths(subdir=subdir):
            try:
                with tarfile.open(tar_path, "r") as tf:
                    yield tar_path, tf.getmembers()
            except (tarfile.TarError, OSError):
                continue

    def list_tar_fits(self, subdir: Optional[str] = None) -> Iterator[Tuple[Path, str]]:
        """Yield direct FITS members from ordinary tar archives."""
        for tar_path, members in self._iter_top_level_tars(subdir=subdir):
            for member in members:
                if member.isfile() and member.name.endswith(".fits"):
                    yield tar_path, member.name

    def list_date_tar_fits(
        self, subdir: Optional[str] = None,
    ) -> Iterator[Tuple[Path, str, str]]:
        """Yield FITS members from nested date-tar archives."""
        for tar_path, members in self._iter_top_level_tars(subdir=subdir):
            if any(member.isfile() and member.name.endswith(".fits") for member in members):
                continue
            yield from (
                (source.path, source.outer_tar_member, source.tar_member)
                for source in self._fits_in_tar(tar_path, members)
            )

    def iter_raw_sources(
        self,
        subdir: Optional[str] = None,
        *,
        first_night: Optional[str] = None,
        last_night: Optional[str] = None,
    ) -> Iterator[RawSource]:
        first_night, last_night = validate_night_range(first_night, last_night)
        first = first_night or "00000000"
        last = last_night or "99999999"
        recognized = self._night_containers(first, last, subdir=subdir)
        if recognized is not None:
            kind, nights = recognized
            if kind == "work":
                yield from self._iter_selected_work(nights)
            else:
                yield from self._iter_selected_corral(nights)
            return

        for path in self.list_fits(subdir=subdir):
            yield RawSource(path=path)
        for tar_path, members in self._iter_top_level_tars(subdir=subdir):
            yield from self._fits_in_tar(tar_path, members)
