from __future__ import annotations

import io as _io
import os
from typing import Optional, Tuple
import sqlite3

import numpy as np
from astropy.io import fits

# Registry DB path for DB-backed tar member lookup (populated during scan).
# DB mode is now the only supported mode for reading FITS from tar archives.
_REG_DB_PATH: Optional[str] = None


def set_registry_db_path(db_path: Optional[str]) -> None:
    global _REG_DB_PATH
    _REG_DB_PATH = db_path


def _lookup_tar_member_from_db(path: str, member: str) -> bytes:
    if not _REG_DB_PATH:
        raise RuntimeError("VIRUSFlow DB mode required: registry DB path not configured. Call set_registry_db_path(db_path) or run via 'virusflow run --db ...'.")
    try:
        with sqlite3.connect(_REG_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT offset, size FROM tar_members WHERE tar_path=? AND member=?", (os.path.abspath(path), member)).fetchone()
            if not r:
                raise RuntimeError(
                    "Tar member not indexed in registry. Please run 'virusflow scan' for this dataset to build the tar index."
                )
            off, size = int(r[0]), int(r[1])
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(size)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to read tar member via DB index: {e}")


def read_fits_data(path: str, tar_member: Optional[str] = None) -> np.ndarray:
    """Read FITS image data from either a filesystem path or a tar archive member.

    Notes
    -----
    - For tar members, a registry DB entry in tar_members is REQUIRED. Run 'virusflow scan' beforehand.
    """
    if tar_member:
        blob = _lookup_tar_member_from_db(path, tar_member)
        data = fits.getdata(_io.BytesIO(blob), memmap=False)
        return np.asarray(data, dtype=float)
    data = fits.getdata(path, memmap=False)
    return np.asarray(data, dtype=float)


def read_fits(path: str, tar_member: Optional[str] = None) -> Tuple[np.ndarray, fits.Header]:
    """Read FITS primary image and header from file or tar member.

    For tar members, DB-backed indexing is mandatory.
    """
    if tar_member:
        blob = _lookup_tar_member_from_db(path, tar_member)
        with fits.open(_io.BytesIO(blob), memmap=False) as hdul:
            data = hdul[0].data
            header = hdul[0].header.copy()
    else:
        with fits.open(path, memmap=False) as hdul:
            data = hdul[0].data
            header = hdul[0].header.copy()
    return np.asarray(data, dtype=float, order="C"), header
