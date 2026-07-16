from __future__ import annotations

"""Wavelength solution derivation from arc spectra and traces.

This module implements a robust per-fiber arc identification and expands it to
build a full 2D wavelength map:
- _get_wave_single: detect arc peaks, match to a reference line list with
  polynomial modeling and sigma-clipping, and return a per-fiber wavelength fit
  with RMS residuals.
- _get_wave: apply per-row seeds, fit across fibers/rows and along dispersion to
  produce a smooth wavelength solution for all pixels.

Exports: step_wave
"""

import numpy as np
import logging
from typing import Optional, Tuple
from itertools import combinations
import matplotlib.pyplot as plt
from .fiber import find_peaks, get_spectra
from pathlib import Path

__all__ = ["step_wave"]

logger = logging.getLogger(__name__)

# Algorithm version string for this module
ALGO_VERSION = "wave-1.0"

# Default pixel length assumption for VIRUS spectra, used in some helpers
PIXELS = 1032

# Reference arc line list (Hg, Cd, etc.) used for matching
LINE_LIST = [3610.508, 3650.153, 4046.565, 4358.335, 4678.149, 4799.912, 4916.068, 5085.822, 5460.750]



def _plot_identify_arc_summary(
    out_folder: Path,
    ref_profile: Optional[np.ndarray],
    best: Optional[dict],
    filename: str = "identify_arc_summary.png",
) -> Optional[Path]:
    """Diagnostic for arc identification using _identify_arc outputs.

    Panels:
    - Top: spectrum with candidate peaks (blue dashed), matched peaks (green), and unmatched candidates (red x).
    - Bottom: residuals (wave_fit - wave_ref) vs wave_ref for matched peaks, annotated with RMS and nmatch.

    Args:
        out_folder: directory to write the image.
        ref_profile: reference 1D spectrum used to detect peaks (S).
        best: dict returned by _identify_arc containing keys like
              'peak_x_all', 'detected_x', 'matches', 'rms', 'nmatch'.
        filename: output filename.
    """
    if ref_profile is None or best is None:
        return None

    prof = np.asarray(ref_profile, dtype=float).ravel()
    n = int(prof.size)
    if n == 0:
        return None
    x = np.arange(n)

    guesses = np.asarray(best.get("peak_x_all", []), dtype=float).ravel()
    det = np.asarray(best.get("detected_x", []), dtype=float).ravel()

    # Determine unmatched candidate peaks
    tol_pix = 2.5
    if guesses.size == 0:
        guesses = np.full(0, np.nan)
    if det.size == 0:
        det = np.full(0, np.nan)
    if guesses.size > 0 and det.size > 0 and np.isfinite(det).any():
        gfin = np.isfinite(guesses)
        dfin = np.isfinite(det)
        if np.any(gfin) and np.any(dfin):
            dmin = np.full(guesses.shape, np.inf, dtype=float)
            dmin[gfin] = np.min(np.abs(guesses[gfin, None] - det[dfin][None, :]), axis=1)
            matched_mask = dmin <= tol_pix
        else:
            matched_mask = np.zeros(guesses.shape, dtype=bool)
    else:
        matched_mask = np.zeros(guesses.shape, dtype=bool)
    missing = (~matched_mask) & np.isfinite(guesses)

    # Build figure
    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(11, 6.5), gridspec_kw=dict(height_ratios=[2, 1]))

    # Top panel: spectrum and lines
    finite_prof = prof[np.isfinite(prof)]
    if finite_prof.size:
        lo, hi = np.percentile(finite_prof, [1, 99])
        if hi <= lo:
            hi = lo + 1e-6
    else:
        lo, hi = 0.0, 1.0
    ax_top.plot(x, prof, color='0.2', lw=0.8)
    if np.isfinite(guesses).any():
        ax_top.vlines(guesses[np.isfinite(guesses)], lo, hi, colors='tab:blue', linestyles='--', alpha=0.5, lw=1.0, label='candidates')
    if np.isfinite(det).any():
        ax_top.vlines(det[np.isfinite(det)], lo, hi, colors='tab:green', linestyles='-', alpha=0.7, lw=1.2, label='matched')
    if np.any(missing):
        gx = guesses[missing]
        ax_top.plot(gx, np.full_like(gx, hi), 'x', color='tab:red', ms=6, mew=1.2, label='missing')
    ax_top.set_xlim(0, n - 1)
    ax_top.set_ylim(lo, hi)
    ax_top.set_ylabel('Ref profile (arb)')
    ax_top.legend(ncol=3, frameon=False, loc='upper right')
    info = f"nmatch={int(best.get('nmatch', 0))}, rms={float(best.get('rms', np.nan)):.3f}"
    ax_top.text(0.01, 0.95, info, transform=ax_top.transAxes, ha='left', va='top', fontsize=10, bbox=dict(facecolor='white', alpha=0.6, edgecolor='none'))

    # Bottom panel: residuals vs. reference wavelength
    matches = best.get('matches', []) or []
    try:
        wave_ref = np.array([m.get('wave_ref', np.nan) for m in matches], dtype=float)
        wave_resid = np.array([m.get('wave_resid', np.nan) for m in matches], dtype=float)
    except Exception:
        wave_ref = np.array([], dtype=float)
        wave_resid = np.array([], dtype=float)
    mfin = np.isfinite(wave_ref) & np.isfinite(wave_resid)
    if np.any(mfin):
        ax_bot.axhline(0.0, color='0.5', lw=1.0)
        ax_bot.scatter(wave_ref[mfin], wave_resid[mfin], s=18, c='tab:purple', alpha=0.8)
        rms = float(best.get('rms', np.nan))
        ax_bot.text(0.02, 0.90, f"RMS={rms:.3f}", transform=ax_bot.transAxes, ha='left', va='top', fontsize=10)
    else:
        ax_bot.text(0.5, 0.5, 'No matched lines', ha='center', va='center', color='0.5')
    ax_bot.set_xlabel('Reference wavelength')
    ax_bot.set_ylabel('Residual (fit - ref)')
    ax_bot.grid(alpha=0.2)

    fig.tight_layout(rect=(0.02, 0.02, 1, 1))
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    out = out_folder / filename
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out

def _suppress_close_peaks(peak_x, peak_h, min_sep=10, max_keep=25):
    peak_x = np.asarray(peak_x, dtype=float)
    peak_h = np.asarray(peak_h, dtype=float)
    order = np.argsort(peak_h)[::-1]
    keep_x = []
    keep_h = []
    for idx in order:
        x = peak_x[idx]
        h = peak_h[idx]
        if not any(np.abs(x - xk) <= min_sep for xk in keep_x):
            keep_x.append(x)
            keep_h.append(h)
        if len(keep_x) >= max_keep:
            break
    keep_x = np.array(keep_x)
    keep_h = np.array(keep_h)
    s = np.argsort(keep_x)
    return keep_x[s], keep_h[s]


def _match_with_model(peak_x, line_list, coeff, x_tol=8.0):
    peak_x = np.asarray(peak_x, dtype=float)
    line_list = np.asarray(line_list, dtype=float)
    wave_at_peaks = np.polyval(coeff, peak_x)
    used = set()
    matches = []
    for w in line_list:
        dw = np.abs(wave_at_peaks - w)
        p = np.argmin(dw)
        # local slope of wavelength solution
        if len(coeff) >= 2:
            local_slope = np.polyval(np.polyder(coeff), peak_x[p])
        else:
            local_slope = coeff[0]
        local_slope = np.abs(local_slope) if np.isfinite(local_slope) else 0.0
        if local_slope < 1e-6:
            local_slope = 1.95
        x_resid = dw[p] / local_slope
        if x_resid <= x_tol and p not in used:
            matches.append({
                "x_obs": peak_x[p],
                "wave_ref": w,
                "wave_fit": wave_at_peaks[p],
                "wave_resid": wave_at_peaks[p] - w,
                "x_resid": x_resid,
                "peak_index": p,
            })
            used.add(p)
    return matches


def _sigma_clip_matches(matches, nsig=3.0):
    if not matches:
        return matches
    import numpy as _np
    resid = _np.array([m["wave_resid"] for m in matches], dtype=float)
    med = _np.median(resid)
    mad = _np.median(_np.abs(resid - med))
    s = 1.4826 * mad if mad > 0 else _np.std(resid) if resid.size > 1 else 1.0
    if not _np.isfinite(s) or s <= 0:
        return matches
    good = _np.abs(resid - med) <= nsig * s
    return [m for m, g in zip(matches, good) if g]


def _refit_from_matches(matches, degree=4):
    if not matches:
        # fallback linear
        return np.array([2.0, 0.0]), 0
    x_obs = np.array([m["x_obs"] for m in matches], dtype=float)
    wave_ref = np.array([m["wave_ref"] for m in matches], dtype=float)
    coeff = np.polyfit(x_obs, wave_ref, degree)
    return coeff, len(matches)


def _identify_arc(
    peak_x,
    peak_h,
    line_list,
    min_sep=10,
    max_keep=12,
    anchor_tol_pix=12.0,
    Nbrightest=6,
    final_order=4,
):
    peak_x, peak_h = _suppress_close_peaks(peak_x, peak_h, min_sep=min_sep, max_keep=max_keep)
    line_list = np.sort(np.asarray(line_list, dtype=float))
    top_peaks = np.argsort(peak_h)[::-1][:Nbrightest]
    top_peaks_x = np.sort(peak_x[top_peaks])
    best_rms = np.inf
    best_coeff = None
    for combo in combinations(line_list, Nbrightest):
        combo = np.sort(combo)
        coeff = np.polyfit(top_peaks_x, combo, 2)
        wave_pred = np.polyval(coeff, top_peaks_x)
        resid = np.empty_like(wave_pred)
        for i, w in enumerate(wave_pred):
            j = np.argmin(np.abs(line_list - w))
            resid[i] = line_list[j] - w
        rms = np.sqrt(np.mean(resid ** 2))
        if rms < best_rms:
            best_coeff = coeff
            best_rms = rms
    coeff = best_coeff
    matches = _match_with_model(peak_x, line_list, coeff, x_tol=anchor_tol_pix)
    coeff_fit, _ = _refit_from_matches(matches, degree=final_order)
    matches_final = _match_with_model(peak_x, line_list, coeff_fit, x_tol=anchor_tol_pix)
    resid = np.array([m["wave_resid"] for m in matches_final], dtype=float)
    rms = np.sqrt(np.mean(resid ** 2))
    detected_x = np.array([m["x_obs"] for m in matches_final], dtype=float)
    best = {
        "coeff": coeff_fit,
        "matches": matches_final,
        "nmatch": len(matches_final),
        "rms": rms,
        "top_peaks_x": top_peaks_x,
        "peak_x_all": np.array(peak_x, dtype=float),
        "detected_x": detected_x,
    }
    return best, len(matches)


def _get_wave_single(fiber_arc_spectrum, final_order=4, qa: Optional[dict] = None):
    """
    Derive a per-fiber wavelength solution from an arc spectrum.

    Detected arc peaks are matched to a fixed reference line list (``LINE_LIST``)
    and a polynomial wavelength model is fit in pixel space.

    Args:
        fiber_arc_spectrum (array-like): 1D arc spectrum for a single fiber
            with length equal to the number of spectral pixels (e.g., 1032).
        final_order (int, optional): Final polynomial degree to fit for the
            wavelength model. Defaults to 4.

    Returns:
        tuple[np.ndarray, float, dict]:
            - ``wave``: 1D array giving the wavelength (same units as
              ``LINE_LIST``) for each pixel index.
            - ``rms``: RMS of residuals (in wavelength units) across matched
              lines.
            - ``best``: dictionary with arc-identification details for QA plotting
              (keys include 'coeff', 'matches', 'nmatch', 'rms', 'peak_x_all', 'detected_x').
    """
    x_indices = np.arange(len(fiber_arc_spectrum))
    fiber_arc_spectrum_clean = np.array(fiber_arc_spectrum, dtype=float).copy()
    fiber_arc_spectrum_clean[~np.isfinite(fiber_arc_spectrum_clean)] = 0.0
    peak_loc, peaks = find_peaks(fiber_arc_spectrum_clean, thresh=1)
    best, _ = _identify_arc(
        peak_loc,
        peaks,
        LINE_LIST,
        min_sep=10,
        max_keep=15,
        anchor_tol_pix=12.0,
        final_order=final_order,
    )
    wave = np.polyval(best["coeff"], x_indices)
    return wave, best["rms"], best

def _get_wave(spec: np.ndarray,
              trace: np.ndarray,
              T_array: Optional[np.ndarray] = None,
              res_lim: float = 1.0,
              order: int = 4,
              qa: Optional[dict] = None) -> Tuple[Optional[np.ndarray], Optional[Path], Optional[np.ndarray], Optional[dict]]:
    """
    Build a 2D wavelength solution using robust per-row arc fitting.

    Args:
        spec: 2D array (Nrows x Npix) of extracted arc spectra.
        trace: 2D array (Nrows x Npix) of trace positions (same shape as spec).
        T_array: Unused legacy argument kept for compatibility. Ignored.
        res_lim: Maximum acceptable RMS (wavelength units) for a row to be
            considered a reliable solution seed.
        order: Polynomial order used for cross-row and along-dispersion fits.

    Returns:
        Tuple (wave, ref_img, rms_rows, qa_bundle):
        - wave: 2D array (Nrows x Npix) with wavelength at each pixel, or None if insufficient good rows are found.
        - ref_img: Path to the QA ref_profile_quarters image produced (if any), otherwise None.
        - rms_rows: 1D array (Nrows,) with per-row RMS residuals from seed fits (0 for rows not attempted), or None if unavailable.
        - qa_bundle: Optional dict with keys 'ref_profile' (1D array) and 'best' (dict from _identify_arc) from the best attempted row, for plotting on failures.
    """
    if spec is None or trace is None:
        return None, None, None, None

    nrows, npix = spec.shape
    w_seed = np.zeros_like(spec, dtype=float)
    rms = np.zeros((nrows,), dtype=float)

    # Choose a sparse set of rows to attempt per-fiber solutions (similar to prior code)
    try_rows = np.hstack([np.arange(2, nrows, 8), nrows - 3])
    try_rows = np.unique(np.clip(try_rows, 0, nrows - 1))

    # Solve per selected row using robust single-fiber routine
    qa_plotted = False
    ref_img: Optional[Path] = None
    qa_best: Optional[dict] = None
    for j in try_rows:
        try:
            # Robustify by median-combining a small window of rows, as before
            j0 = max(0, j - 2)
            j1 = min(nrows, j + 3)
            S = np.nanmedian(spec[j0:j1], axis=0)
            wave_j, res, best = _get_wave_single(S, final_order=order)
            w_seed[j] = wave_j
            rms[j] = res
            # Track best identification for QA plotting on failure
            try:
                nmatch = int(best.get("nmatch", 0)) if isinstance(best, dict) else -1
            except Exception:
                nmatch = -1
            if qa_best is None or (nmatch > int(((qa_best or {}).get('best') or {}).get('nmatch', -1))):
                qa_best = {"ref_profile": np.asarray(S, dtype=float), "best": best}
        except Exception:
            # Leave defaults (zeros), continue
            pass

    good = (rms > 0) & (rms < res_lim)
    if np.count_nonzero(good) < 7:
        return None, ref_img, rms, qa_best

    # Initialize final wavelength array
    wave = np.zeros_like(spec, dtype=float)

    # Fit across rows at a sparse set of columns, then evaluate on all rows
    # This mirrors the strategy used in fiber_utils._get_wave
    xi = np.hstack([np.arange(0, npix, 24), npix - 1])
    xi = np.unique(np.clip(xi, 0, npix - 1))

    for i in xi:
        x = trace[:, i]
        y = w_seed[:, i]
        # Use only good rows for cross-row fit
        coeff = np.polyfit(x[good], y[good], order)
        wave[:, i] = np.polyval(coeff, x)

    # Smooth along dispersion for each row by fitting through valid columns
    xpix = np.arange(npix)
    for j in range(nrows):
        sel = wave[j] > 0
        if np.count_nonzero(sel) >= (order + 1):
            coeff = np.polyfit(xpix[sel], wave[j][sel], order)
            wave[j] = np.polyval(coeff, xpix)
        else:
            # Fallback: copy nearest good neighbor row if available
            # Find nearest row with a solution
            if np.count_nonzero(good) > 0:
                jj = int(np.argmin(np.abs(np.where(good)[0] - j)))
                wave[j] = wave[np.where(good)[0][jj]]
            else:
                return None, ref_img, rms, qa_best

    return wave, ref_img, rms, qa_best



from ..core.algo_result import AlgoResult

def step_wave(*,
              cmp_spec: Optional[np.ndarray] = None,
              master_cmp: Optional[np.ndarray] = None,
              trace: Optional[np.ndarray] = None,
              npix_extract: int = 5,
              res_lim: float = 1.0,
              order: int = 4,
              params: Optional[dict] = None) -> AlgoResult:
    """
    Build the wavelength solution from spectra of the master comparison (arc) frame.

    This is the public entry point for wavelength calibration. It expects either:
    - cmp_spec: 2D array of extracted arc spectra with shape (Nrows, Npix), or
    - master_cmp: 2D master comparison image and a corresponding 2D trace array
      (same shape as master_cmp) to extract spectra via fiber.get_spectra.

    The routine delegates the core modeling to the internal _get_wave function
    and returns a dictionary with results and metadata. Artifact persistence is
    handled by tasks via the generic artifacts materializer.

    Parameters
    ----------
    cmp_spec : Optional[np.ndarray]
        Pre-extracted arc spectra (Nrows x Npix). If provided, master_cmp is ignored.
    master_cmp : Optional[np.ndarray]
        Master comparison image used to extract spectra when cmp_spec is None.
    trace : Optional[np.ndarray]
        2D trace map (Nrows x Npix). Required to build the 2D wavelength model.
    npix_extract : int
        Extraction aperture (rows integrated around the trace) passed to get_spectra.
    res_lim : float
        Maximum acceptable RMS for a row to be used as a seed in _get_wave.
    order : int
        Polynomial order for cross-row and along-dispersion fits in _get_wave.
    params : Optional[dict]
        Reserved for future use.

    Returns
    -------
    AlgoResult
        Storage-neutral result with arrays and scalars:
        - arrays: {"wave": 2D wavelength array or None on failure, "rms_rows": 1D RMS per seed row or None}
        - scalars: may include compact best-match metrics (e.g., best_nmatch, best_rms)
        - meta: may include failure_reason and shape
    """
    params = params or {}

    if trace is None:
        raise ValueError("step_wave requires a trace array (Nrows x Npix)")

    if cmp_spec is None:
        if master_cmp is None:
            raise ValueError("step_wave requires either cmp_spec, or (master_cmp and trace)")
        # Extract per-row spectra from the master CMP image using the provided trace
        try:
            cmp_spec = get_spectra(master_cmp, trace, npix=npix_extract)
        except Exception as e:
            raise RuntimeError(f"Failed to extract spectra from master_cmp/trace: {e}")

    # Delegate to internal _get_wave to compute the wavelength map
    # Compute wavelength solution (collect QA bundle for failure diagnostics)
    wave, ref_img, rms_rows, qa_bundle = _get_wave(cmp_spec, trace, T_array=None, res_lim=res_lim, order=order, qa=None)

    # Build storage-neutral AlgoResult
    scalars = {}
    # Extract compact best-match metrics if available
    try:
        if isinstance(qa_bundle, dict):
            best = qa_bundle.get("best") or {}
            if "nmatch" in best:
                try:
                    scalars["best_nmatch"] = int(best.get("nmatch"))
                except Exception:
                    pass
            if "rms" in best:
                try:
                    scalars["best_rms"] = float(best.get("rms"))
                except Exception:
                    pass
    except Exception:
        pass

    # Include a failure_reason in meta if modeling failed (wave is None)
    meta = {}
    if wave is None:
        failure_reason = "wavelength modeling failed"
        try:
            if rms_rows is not None:
                arr = np.asarray(rms_rows, dtype=float).ravel()
                fin = arr[np.isfinite(arr) & (arr > 0)]
                tried = int(np.count_nonzero(np.isfinite(arr)))
                good = int(np.count_nonzero((arr > 0) & (arr < float(res_lim))))
                med = float(np.nanmedian(fin)) if fin.size else np.nan
                failure_reason = (f"insufficient good seed rows: {good} < 7 (res_lim={float(res_lim)}) ; "
                                   f"tried_rows={tried}/{arr.size}; median_rms={med if (med==med) else 'nan'}")
        except Exception:
            pass
        meta["failure_reason"] = failure_reason

    # Determine shape summary if available
    try:
        if trace is not None:
            meta.setdefault("shape", list(np.asarray(trace).shape))
        elif cmp_spec is not None:
            meta.setdefault("shape", list(np.asarray(cmp_spec).shape))
    except Exception:
        pass

    return AlgoResult(
        kind="wave",
        version=ALGO_VERSION,
        meta=meta,
        scalars=scalars,
        arrays={
            "wave": wave,
            "rms_rows": rms_rows,
        },
    )