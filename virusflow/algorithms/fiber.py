from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["get_spectra"]


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