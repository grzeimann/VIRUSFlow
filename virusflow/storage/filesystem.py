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
    backend: Literal["filesystem", "tar"] = "filesystem"


class FileSystemStorage:
    """Simple filesystem storage listing raw frames by extension or inside tar archives."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)

    def exists(self, relative: str | os.PathLike[str]) -> bool:
        return (self.root / relative).exists()

    def list_fits(self, subdir: Optional[str] = None) -> Iterator[Path]:
        base = self.root if subdir is None else (self.root / subdir)
        if not base.exists():
            return iter(())
        for p in base.rglob("*.fits"):
            if p.is_file():
                yield p

    def list_tar_fits(self, subdir: Optional[str] = None) -> Iterator[Tuple[Path, str]]:
        """Yield (tar_path, member_name) for FITS files stored inside .tar archives under root.

        Note: member_name is the path within the tar archive.
        """
        base = self.root if subdir is None else (self.root / subdir)
        if not base.exists():
            return iter(())
        for tar_path in base.rglob("*.tar"):
            if not tar_path.is_file():
                continue
            try:
                with tarfile.open(tar_path, "r") as tf:
                    for member in tf:
                        if member.isfile() and member.name.endswith(".fits"):
                            yield tar_path, member.name
            except (tarfile.TarError, OSError):
                # Skip unreadable/corrupt tar files silently for now
                continue

    def iter_raw_sources(self, subdir: Optional[str] = None) -> Iterator[RawSource]:
        # filesystem files
        for p in self.list_fits(subdir=subdir):
            yield RawSource(path=p, tar_member=None, backend="filesystem")
        # tar members
        for tar_path, member in self.list_tar_fits(subdir=subdir):
            yield RawSource(path=tar_path, tar_member=member, backend="tar")
