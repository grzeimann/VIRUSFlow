from __future__ import annotations

"""CCD-level utilities for VIRUS/LRS2 reductions.

This module contains low-level image operations shared across algorithms:
- orient_amplifier_image: normalize amplifier images to a common orientation (blue→red left-to-right)
  and consistent fiber order based on header metadata.
- reduce_raw_amplifier_frame: perform overscan subtraction, trimming, orientation, gain application,
  and error propagation, returning (image, error[, header]).
- interpolate_masked_detector_pixels: fill masked/bad pixels in arc/continuum images via row-wise
  interpolation and gentle Gaussian smoothing to avoid artifacts.

Exports: orient_amplifier_image, reduce_raw_amplifier_frame, interpolate_masked_detector_pixels
"""

from typing import Optional, Tuple

import logging
import numpy as np
from astropy.stats import biweight_location
from scipy.ndimage import gaussian_filter1d

from .io import read_fits

__all__ = ["orient_amplifier_image", "reduce_raw_amplifier_frame", "repair_masked_columns"]
logger = logging.getLogger(__name__)


def orient_amplifier_image(image: np.ndarray, amp: str, ampname: Optional[str]) -> np.ndarray:
    """Orient the detector image from blue->red (left-to-right) and proper fiber order.

    This mirrors the behavior in reference/fiber_utils.py::orient_amplifier_image for VIRUS/LRS2.

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


def reduce_raw_amplifier_frame(
    path: str,
    tar_member: Optional[str] = None,
    return_header: bool = False,
) -> Tuple[np.ndarray, np.ndarray] | Tuple[np.ndarray, np.ndarray, dict]:
    """Basic CCD reduction for a single amplifier image.

    Steps (following reference fiber_utils.reduce_raw_amplifier_frame):
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

    oriented = orient_amplifier_image(img, amp, ampname)
    image = (oriented * gain).astype(np.float32, copy=False)

    # 5) Error propagation
    # E = sqrt(rdnoise^2 + max(signal,0))
    pos = np.where(image > 0.0, image, 0.0)
    error = np.sqrt(rdnoise ** 2 + pos).astype(np.float32, copy=False)

    if return_header:
        # Return a plain dict to avoid astropy object mutation surprises
        return image, error, dict(hdr)
    return image, error


def repair_masked_columns(image: np.ndarray, mask: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """Fill masked pixels in an IFU arc image using row-wise interpolation and Gaussian smoothing.

    - Operates along spectral columns (x) within each detector row (y).
    - For rows with masked values, do a nearest-edge linear interpolation between valid
      pixels, then blend masked spans with a Gaussian-filtered version to avoid sharp edges.
    - Ensures no NaNs in the result; uses nearest/median fallbacks when needed.
    """
    img = np.asarray(image, dtype=float)
    m = np.asarray(mask).astype(bool)
    out = img.copy()
    ny, nx = out.shape
    # Replace NaNs in the working copy to keep filters stable; keep a finite map
    finite = np.isfinite(out)
    # Process each detector row independently
    for y in range(ny):
        row = out[y, :]
        mrow = m[y, :]
        if not mrow.any():
            continue
        # valid points are finite and unmasked
        good = (~mrow) & np.isfinite(row)
        if good.sum() >= 2:
            x = np.arange(nx)
            # Nearest extrapolation behavior from np.interp is fine for edges
            interp_vals = np.interp(x, x[good], row[good])
            # Blend masked spans with a lightly smoothed version to reduce discontinuities
            smooth = gaussian_filter1d(interp_vals, sigma=max(0.5, float(sigma)), mode='nearest')
            filled = row.copy()
            filled[mrow] = smooth[mrow]
            out[y, :] = filled
        elif good.sum() == 1:
            v = float(row[good][0]) if np.isfinite(row[good][0]) else 0.0
            filled = row.copy()
            filled[mrow] = v
            # light smooth to avoid flat plateaus at transitions
            out[y, :] = gaussian_filter1d(filled, sigma=max(0.5, float(sigma)), mode='nearest')
        else:
            # No good samples in this row: use row median (fallback) then smooth
            med = float(np.nanmedian(row)) if np.isfinite(row).any() else 0.0
            filled = np.full_like(row, med)
            out[y, :] = gaussian_filter1d(filled, sigma=max(0.5, float(sigma)), mode='nearest')
    # Ensure no NaNs
    bad = ~np.isfinite(out)
    if bad.any():
        out[bad] = 0.0
    return out