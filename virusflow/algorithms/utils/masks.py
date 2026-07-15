from __future__ import annotations

from typing import Optional, Tuple
from pathlib import Path
import numpy as np
from astropy.io import fits
from scipy.ndimage import gaussian_filter1d

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


def _load_mask(path: Optional[str], extname: str) -> Optional[np.ndarray]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        with fits.open(str(p), memmap=True) as hdul:
            for h in hdul[1:]:
                if getattr(h, "name", "").upper() == extname.upper():
                    return np.asarray(h.data, dtype=np.uint8)
            # Fallback: first extension if named ext not present
            if len(hdul) > 1 and hdul[1].data is not None:
                return np.asarray(hdul[1].data, dtype=np.uint8)
    except Exception:
        return None
    return None


def build_union_pixelmask(
    *,
    flat_path: Optional[str] = None,
    dark_path: Optional[str] = None,
    flat_artifact: Optional[dict] = None,
    dark_artifact: Optional[dict] = None,
) -> Tuple[Optional[np.ndarray], float]:
    """Build a union pixel mask from available flat and dark masks.

    Reads masks from FITS artifacts:
      - flat:  extension named 'FLATMASK' when available
      - dark:  extension named 'DARKMASK' when available

    Returns (union_mask, bad_fraction). If neither is present, returns (None, 0.0).
    """
    # Resolve paths from artifact rows if provided
    if flat_artifact and not flat_path:
        try:
            flat_path = flat_artifact.get("path")
        except Exception:
            pass
    if dark_artifact and not dark_path:
        try:
            dark_path = dark_artifact.get("path")
        except Exception:
            pass

    m_flat = _load_mask(flat_path, "FLATMASK")
    m_dark = _load_mask(dark_path, "DARKMASK")

    if m_flat is None and m_dark is None:
        return None, 0.0

    if m_flat is None:
        m = (np.asarray(m_dark, dtype=bool))
    elif m_dark is None:
        m = (np.asarray(m_flat, dtype=bool))
    else:
        if m_flat.shape != m_dark.shape:
            # Shape mismatch: fall back to the larger mask area where possible by broadcasting smaller to larger via crop/pad
            # For simplicity, if shapes differ, choose the smaller common area intersection
            ny = min(m_flat.shape[0], m_dark.shape[0])
            nx = min(m_flat.shape[1], m_dark.shape[1])
            m = (np.asarray(m_flat[:ny, :nx], dtype=bool) | np.asarray(m_dark[:ny, :nx], dtype=bool))
        else:
            m = (np.asarray(m_flat, dtype=bool) | np.asarray(m_dark, dtype=bool))

    frac = float(np.sum(m)) / float(m.size) if m.size else 0.0
    return m.astype(np.uint8), frac
