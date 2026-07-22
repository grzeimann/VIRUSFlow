from __future__ import annotations

import io
import tarfile
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

    def load(self, path: str, tar_member: Optional[str] = None) -> RawFrameData:
        if tar_member:
            with tarfile.open(path, mode="r:*") as archive:
                member = archive.getmember(tar_member)
                stream = archive.extractfile(member)
                if stream is None:
                    raise FileNotFoundError(f"Cannot extract {tar_member} from {path}")
                blob = stream.read()
            with fits.open(io.BytesIO(blob), memmap=False) as hdul:
                data = np.asarray(hdul[0].data)
                header = dict(hdul[0].header)
        else:
            with fits.open(str(Path(path)), memmap=False) as hdul:
                data = np.asarray(hdul[0].data)
                header = dict(hdul[0].header)
        return RawFrameData(data=data, header=header, path=str(path), tar_member=tar_member)

