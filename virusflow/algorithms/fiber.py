from __future__ import annotations

"""Fiber-related extraction and 1D profile utilities.

This module provides:
- get_spectra: extract per-fiber spectra from a 2D image given a 2D trace.
- find_peaks: robust 1D peak finder with sub-pixel parabolic refinement.
"""

import numpy as np

__all__ = ["get_spectra", "find_peaks"]


def get_spectra(array_flt: np.ndarray, array_trace: np.ndarray, npix: int = 5) -> np.ndarray:
    """Extract per-fiber spectra from a flat (twilight) image using a 2D trace map.

    This routine integrates a small, symmetric window of rows around each fiber's trace
    center for every detector column and averages the weighted samples to build a
    2D spectrum array of shape (n_fibers, n_x).

    Parameters
    ----------
    array_flt : np.ndarray
        2D twilight/flat image with shape (ny, nx).
    array_trace : np.ndarray
        2D array of shape (n_fibers, nx) giving the sub-pixel row position of each
        fiber's trace center for every column.
    npix : int, optional
        Number of rows to integrate per column (integration aperture), default 5.
        Must be a positive integer. The algorithm internally computes a half-width
        LB = ceil(npix/2) and integrates rows [center-LB+1, ..., center+LB-1].

    Returns
    -------
    np.ndarray
        2D array of shape (n_fibers, nx) with the extracted spectra.

    Notes
    -----
    - The implementation mirrors legacy behavior: it computes linear weights on the
      first and last rows of the aperture to approximate sub-pixel integration
      (parallelogram rule); interior rows get unit weight. The final value is divided
      by ``npix`` to form an average.
    - Fibers whose rounded trace path would sample outside the detector bounds are
      skipped (left as zeros).
    """
    try:
        img = np.asarray(array_flt, dtype=float)
        tr = np.asarray(array_trace, dtype=float)

        if img.ndim != 2 or tr.ndim != 2:
            raise ValueError("get_spectra requires 2D arrays: array_flt(ny,nx), array_trace(nf,nx)")
        ny, nx = img.shape
        nf, nx_tr = tr.shape
        if nx_tr != nx:
            raise ValueError(f"array_trace has nx={nx_tr} but array_flt has nx={nx}")
        if not isinstance(npix, (int, np.integer)) or int(npix) <= 0:
            raise ValueError("npix must be a positive integer")

        npix = int(npix)
        # Half-width in pixels, following legacy definition
        LB = int((npix + 1) // 2)  # e.g., npix=5 -> LB=3
        HB = -LB + npix + 1        # symmetric upper offset exclusive bound in range(-LB, HB)

        spec = np.zeros((nf, nx), dtype=float)
        x = np.arange(nx)

        # Loop fibers; skip fibers whose rounded trace would step out of bounds
        for fiber in range(nf):
            tr_row = tr[fiber]
            tr_round = np.round(tr_row)
            if tr_round.min() < LB:
                continue
            if tr_round.max() >= (ny - LB):
                continue
            indv = tr_round.astype(int)
            # Integrate rows in [-LB, HB)
            for j in range(-LB, HB):
                if j == -LB:
                    w = indv + j + 1 - (tr_row - npix / 2.0)
                elif j == HB - 1:
                    w = (npix / 2.0 + tr_row) - (indv + j)
                else:
                    w = 1.0
                # Safe gather within bounds (guaranteed by checks above)
                spec[fiber] += img[indv + j, x] * w

        return spec / float(npix)
    except Exception as e:
        # Convert unexpected issues into a clear runtime error for callers
        raise RuntimeError(f"get_spectra failed: {e}") from e
    
def find_peaks(y: np.ndarray | list[float], thresh: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """Find 1D local maxima with sub-pixel refinement using parabolic interpolation.

    Parameters
    ----------
    y : 1D array-like
        Input profile. It will be converted to a float NumPy array; NaNs are treated as -inf.
    thresh : float, optional
        Minimum peak height threshold applied on the original profile to select
        candidate maxima (default: 10.0, same units as y).

    Returns
    -------
    (peak_x, peak_h) : tuple of np.ndarray
        - peak_x: sub-pixel x-positions of detected peaks (float)
        - peak_h: peak heights sampled from y at nearest integer to peak_x

    Notes
    -----
    - Candidate peaks are first located where the discrete derivative changes sign
      from positive to negative.
    - Sub-pixel refinement uses a 3-point parabola around each candidate index i
      using y[i-1], y[i], y[i+1]. Division-by-zero is guarded; invalid fits are dropped.
    - Only candidates with y[i] > thresh are returned.
    """
    y_arr = np.asarray(y, dtype=float).ravel()
    n = y_arr.size
    if n < 3:
        return np.array([], dtype=float), np.array([], dtype=float)

    # Replace NaNs to avoid propagating them in differences
    y_safe = np.where(np.isfinite(y_arr), y_arr, -np.inf)
    diff_array = y_safe[1:] - y_safe[:-1]
    # Candidates where slope goes + to -
    cand = np.where((diff_array[:-1] > 0.0) & (diff_array[1:] < 0.0))[0] + 1
    if cand.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    # Apply height threshold on original y (before NaN replacement)
    sel = y_arr[cand] > float(thresh)
    cand = cand[sel]
    if cand.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    # Keep only those with valid neighbors (1..n-2)
    cand = cand[(cand >= 1) & (cand <= n - 2)]
    if cand.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    # Parabolic interpolation for sub-pixel maxima around integer candidates
    x = np.arange(n, dtype=float)
    y0 = y_safe[cand - 1]
    y1 = y_safe[cand]
    y2 = y_safe[cand + 1]
    denom = 2.0 * (y2 - 2.0 * y1 + y0)
    # Avoid divide-by-zero; mark invalids
    with np.errstate(divide='ignore', invalid='ignore'):
        dx = (y2 - y0) / denom
    x_peak = x[cand] - dx

    # Filter invalid/NaN results
    good = np.isfinite(x_peak)
    x_peak = x_peak[good]
    # Heights from nearest integer sample for stability
    peaks_h = y_arr[np.clip(np.round(x_peak).astype(int), 0, n - 1)]

    return x_peak.astype(float), np.asarray(peaks_h, dtype=float)