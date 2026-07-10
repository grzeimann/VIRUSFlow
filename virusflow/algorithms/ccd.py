from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from astropy.stats import biweight_location

from .io import read_fits


def orient_image(image: np.ndarray, amp: str, ampname: Optional[str]) -> np.ndarray:
    """Orient the detector image from blue->red (left-to-right) and proper fiber order.

    This mirrors the behavior in reference/fiber_utils.py::orient_image for VIRUS/LRS2.

    Flips are amplifier-dependent:
    - For LU and RL, flip both axes.
    - If ampname is 'LR' or 'UL', additionally flip columns.
    """
    img = np.array(image, copy=True)
    if amp == "LU":
        img[:] = img[::-1, ::-1]
    if amp == "RL":
        img[:] = img[::-1, ::-1]
    if ampname is not None:
        if ampname in ("LR", "UL"):
            img[:] = img[:, ::-1]
    return img


def base_reduction(
    path: str,
    tar_member: Optional[str] = None,
    return_header: bool = False,
) -> Tuple[np.ndarray, np.ndarray] | Tuple[np.ndarray, np.ndarray, dict]:
    """Basic CCD reduction for a single amplifier image.

    Steps (following reference fiber_utils.base_reduction):
      1) Overscan subtraction (robust per-row estimate)
      2) Trim overscan region
      3) Orientation (blue->red left-to-right, proper fiber order)
      4) Gain multiplication
      5) Error propagation (E = sqrt(RN^2 + max(signal,0)))

    Parameters
    ----------
    path : str
        Path to a FITS file or .tar archive containing the FITS as tar_member.
    tar_member : Optional[str]
        Member path inside tar archive.
    return_header : bool
        If True, also return the FITS primary header (as a copy/dict).

    Returns
    -------
    image : np.ndarray
        Reduced, oriented, and gain-multiplied image (float32).
    error : np.ndarray
        Propagated error image (float32).
    header : dict (optional)
        Primary header (for metadata propagation), returned if return_header=True.
    """
    data, hdr = read_fits(path, tar_member)

    # Defensive copies and dtype
    img = np.asarray(data, dtype=float).copy()

    # 1) Overscan subtraction
    # Reference computes overscan_length = int(32 * (nx / 1064)) with biweight along rows.
    nx = img.shape[1]
    overscan_length = int(32 * (nx / 1064.0))
    overscan_length = max(0, min(nx // 4, overscan_length))  # reasonable bounds
    if overscan_length > 0:
        # robust per-row estimate: use biweight_location to reduce digitization bias
        # Use all but the last 2 columns in the overscan, like reference code
        osc_region = img[:, -(overscan_length - 2) :] if overscan_length >= 3 else img[:, -overscan_length:]
        # biweight location per row (robust central tendency)
        O = biweight_location(osc_region, axis=1, ignore_nan=True)
        img -= O[:, np.newaxis]

    # 2) Trim image (drop overscan columns)
    if overscan_length > 0:
        img = img[:, : nx - overscan_length]

    # 3) Orientation and 4) Gain multiplication
    # Extract metadata with safe defaults
    gain = float(hdr.get("GAIN", 0.85))
    if not np.isfinite(gain) or gain <= 0:
        gain = 0.85
    rdnoise = float(hdr.get("RDNOISE", 3.0))
    if not np.isfinite(rdnoise) or rdnoise <= 0:
        rdnoise = 3.0
    ccdpos = str(hdr.get("CCDPOS", "")).replace(" ", "")
    ccdhalf = str(hdr.get("CCDHALF", "")).replace(" ", "")
    amp = ccdpos + ccdhalf
    ampname = hdr.get("AMPNAME")
    ampname = str(ampname) if ampname is not None else None

    oriented = orient_image(img, amp, ampname)
    image = (oriented * gain).astype(np.float32, copy=False)

    # 5) Error propagation
    # E = sqrt(rdnoise^2 + max(signal,0))
    pos = np.where(image > 0.0, image, 0.0)
    error = np.sqrt(rdnoise ** 2 + pos).astype(np.float32, copy=False)

    if return_header:
        # Return a plain dict to avoid astropy object mutation surprises
        return image, error, dict(hdr)
    return image, error
