from __future__ import annotations

from typing import Optional
import numpy as np
from scipy.ndimage import gaussian_filter1d

def interpolate_masked_detector_pixels(image: np.ndarray, mask: np.ndarray, sigma: float = 1.0) -> np.ndarray:
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

# Note: legacy _load_mask and build_union_pixelmask have been removed to preserve
# the architecture (algorithms remain storage-neutral; tasks use ArtifactService
# to materialize any needed components). Keep only interpolation utility here.
