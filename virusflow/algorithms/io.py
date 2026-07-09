from __future__ import annotations

import io as _io
import tarfile
from typing import Optional, Tuple

import numpy as np
from astropy.io import fits


def read_fits_data(path: str, tar_member: Optional[str] = None) -> np.ndarray:
    """Read FITS image data from either a filesystem path or a tar archive member.

    Parameters
    ----------
    path : str
        Path to a FITS file, or to a .tar containing the member.
    tar_member : Optional[str]
        Member path inside the tar archive when reading from a tarball.
    """
    if tar_member:
        with tarfile.open(path, "r") as tf:
            m = tf.getmember(tar_member)
            with tf.extractfile(m) as fh:  # type: ignore[arg-type]
                if fh is None:
                    raise FileNotFoundError(f"Member {tar_member} not found in {path}")
                data = fits.getdata(_io.BytesIO(fh.read()), memmap=False)
                return np.asarray(data, dtype=float)
    data = fits.getdata(path, memmap=False)
    return np.asarray(data, dtype=float)


def read_fits(path: str, tar_member: Optional[str] = None) -> Tuple[np.ndarray, fits.Header]:
    """Read FITS primary image and header from file or tar member.

    Returns
    -------
    data : np.ndarray (float)
        Primary HDU image as float array.
    header : fits.Header
        Primary HDU header.
    """
    if tar_member:
        with tarfile.open(path, "r") as tf:
            m = tf.getmember(tar_member)
            with tf.extractfile(m) as fh:  # type: ignore[arg-type]
                if fh is None:
                    raise FileNotFoundError(f"Member {tar_member} not found in {path}")
                with fits.open(_io.BytesIO(fh.read()), memmap=False) as hdul:
                    data = hdul[0].data
                    header = hdul[0].header.copy()
    else:
        with fits.open(path, memmap=False) as hdul:
            data = hdul[0].data
            header = hdul[0].header.copy()
    return np.asarray(data, dtype=float, order="C"), header
