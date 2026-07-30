from __future__ import annotations

"""Fiber trace detection and polynomial modeling.

This module assembles utilities to estimate fiber traces from flat fields and
fit smooth polynomials suitable for building a 2D trace map:
- _simple_trace_from_flat: quick proxy trace using per-column maxima.
- preprocess_flat_for_detection: background-remove and smooth 1D profiles to
  aid robust peak finding.
- robust_polyfit_predict: stable polynomial fit/predict with robust fallback.
- fit_fiber_traces: end-to-end routine to produce and persist a trace solution
  artifact for downstream extraction/wavelength steps.

Exports: fit_fiber_traces
"""

from typing import Iterable, Optional, Dict, Any, List

import numpy as np
from datetime import datetime
import glob
from astropy.convolution import convolve, Gaussian1DKernel
from astropy.stats import mad_std
from scipy.ndimage import percentile_filter
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import HuberRegressor

from ..artifacts.io_fits import read_array_fits

# Algorithm version string for this module
ALGO_VERSION = "trace-1.0"

# Input item type accepted by fit_fiber_traces (kept for interface symmetry)
TraceInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)




def robust_polyfit_predict(x_obs: np.ndarray | List[float], y_obs: np.ndarray | List[float], x_pred: np.ndarray | List[float], degree: int = 4) -> np.ndarray:
    """
    Robust polynomial fit y(x) using a low-order polynomial and predict on x_pred.

    To avoid numerical blow-up when x spans large pixel ranges (e.g., 0..1031),
    we normalize x to [-1, 1] for both sklearn and numpy backends.

    Preference order:
    1) sklearn.linear_model.HuberRegressor with PolynomialFeatures (degree<=4)
    2) Fallback to numpy.polyfit/polyval with the same degree (reduced if needed)

    Returns a float array of shape x_pred with NaNs if insufficient data.
    """
    # Coerce inputs
    x_obs = np.asarray(x_obs, dtype=float).ravel()
    y_obs = np.asarray(y_obs, dtype=float).ravel()
    x_pred = np.asarray(x_pred, dtype=float).ravel()

    m = np.isfinite(x_obs) & np.isfinite(y_obs)
    if m.sum() < 2:
        return np.full(x_pred.shape, np.nan, dtype=float)

    x = x_obs[m]
    y = y_obs[m]
    # Choose feasible degree
    deg = int(max(1, min(int(degree), len(x) - 1)))

    # Normalize x to [-1, 1] for stability
    xmin = np.nanmin(x)
    xmax = np.nanmax(x)
    span = float(xmax - xmin)
    if not np.isfinite(span) or span <= 0:
        # Degenerate x: predict constant = median(y)
        return np.full(x_pred.shape, float(np.nanmedian(y)), dtype=float)

    def _scale(u):
        return 2.0 * (u - xmin) / span - 1.0

    xs = _scale(x)
    xps = _scale(x_pred)

    # Try robust sklearn fit
    try:
        PF = PolynomialFeatures(degree=deg, include_bias=False)
        X = PF.fit_transform(xs.reshape(-1, 1))
        Xp = PF.transform(xps.reshape(-1, 1))
        model = HuberRegressor(epsilon=1.35, alpha=0.0, fit_intercept=True)
        model.fit(X, y)
        yhat = model.predict(Xp)
        return yhat.astype(float)
    except Exception:
        # Numpy fallback on scaled coordinates
        try:
            coeff = np.polyfit(xs, y, deg=deg)
            yhat = np.polyval(coeff, xps)
            return yhat.astype(float)
        except Exception:
            return np.full(x_pred.shape, np.nan, dtype=float)

def preprocess_flat_for_detection(flat: np.ndarray | List[float], perc_window: int = 201, perc: float = 5.0, poly_order: int = 2, gauss_sigma: float = 1.5) -> np.ndarray:
    """
    Preprocess a 1D flat (median-collapsed along columns) to aid fiber peak detection.

    Steps:
    - Estimate a smooth background using a low percentile filter (e.g., 5th) with
      a wide window (default 201 pixels).
    - Fit a low-order polynomial (order=2) to the percentile background to model
      large-scale structure, and subtract it from the original profile.
    - Smooth the background-subtracted profile with a 1D Gaussian kernel
      (sigma≈fiber dispersion resolution; default 1.5 pixels) to boost S/N.

    Parameters
    ----------
    flat : 1D numpy array
        Cross-dispersion profile (e.g., median of an image chunk along x).
    perc_window : int, optional
        Window size for percentile filter (odd recommended).
    perc : float, optional
        Percentile to compute in the filter (e.g., 5.0 for 5th percentile).
    poly_order : int, optional
        Polynomial order for background model fit (default: 2).
    gauss_sigma : float, optional
        Sigma of the Gaussian kernel used for smoothing (in pixels).

    Returns
    -------
    prof_smooth : 1D numpy array
        Background-subtracted and smoothed profile for robust peak finding.
        Returns a safe fallback (copy of input or median-filtered) on failure.
    """
    try:
        f = np.asarray(flat, dtype=float).ravel()
        n = f.size
        if n == 0:
            return f
        # Percentile background with wide window
        w = int(max(3, perc_window))
        if w % 2 == 0:
            w += 1  # prefer odd window
        bgp = percentile_filter(f, percentile=perc, size=w, mode='nearest')
        # Poly-2 fit to percentile curve
        x = np.arange(n, dtype=float)
        # Guard against singular fit: need >= (poly_order+1) points
        if n >= (poly_order + 1):
            coeff = np.polyfit(x, bgp, deg=int(poly_order))
            bgfit = np.polyval(coeff, x)
        else:
            bgfit = bgp
        resid = f - bgfit
        # Smooth residuals with Gaussian 1D kernel
        sig = float(max(0.5, gauss_sigma))
        kernel = Gaussian1DKernel(stddev=sig)
        prof_smooth = convolve(resid, kernel, boundary='extend', nan_treatment='interpolate', preserve_nan=False)
        return prof_smooth
    except Exception:
        return np.asarray(flat, dtype=float).ravel()

def default_virusconfig_root() -> str:
    """Resolve the default virusconfig root.

    Preference order:
    1) Environment variable VIRUSCONFIG_ROOT if it points to a directory.
    2) Walk up from this file to find a directory containing 'Fiber_Locations'.
       If found, return that directory path.
    3) Fallback to the historical path '/work/03946/hetdex/maverick/virus_config'.
    """
    from pathlib import Path as _Path
    import os as _os
    # 1) ENV override
    env = _os.environ.get("VIRUSCONFIG_ROOT")
    if env and _Path(env).is_dir():
        return str(_Path(env).resolve())
    # 2) Walk parents looking for Fiber_Locations at repo root
    here = _Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        try:
            # Stop at filesystem root
            if p == p.parent:
                break
            if (p / "Fiber_Locations").is_dir():
                return str(p)
        except Exception:
            pass
    # 3) Historical fallback
    return "/work/03946/hetdex/maverick/virus_config"


def get_trace_reference(specid: str, ifuslot: str, ifuid: str, amp: str, obsdate: str,
                        virusconfig: str | None = None) -> np.ndarray:
    """Locate and load the closest-in-time fiber location reference file.

    The directory layout is expected to be:
      <virusconfig>/Fiber_Locations/<YYYYMMDD>/fiber_loc_<specid>_<ifuslot>_<ifuid>_<amp>.txt

    Notes
    -----
    Historically, specid/ifuslot/ifuid may appear without leading zeros
    (e.g., "27"). Reference filenames are zero-padded to width 3
    (e.g., "027"). This function normalizes IDs by zero-padding to 3
    characters before globbing so callers can pass either form.

    Returns a NumPy array loaded from the selected reference file.
    """
    try:
        from pathlib import Path
        # Normalize IDs to width-3 strings ("27" -> "027"). Non-numeric strings are left as-is.
        def _pad3(v: object) -> str:
            s = str(v).strip()
            return s.zfill(3) if s.isdigit() and len(s) < 3 else s
        specid_n = _pad3(specid)
        ifuslot_n = _pad3(ifuslot)
        ifuid_n = _pad3(ifuid)

        if virusconfig is None:
            virusconfig = default_virusconfig_root()
        base = Path(virusconfig)
        patt = base / 'Fiber_Locations' / '*' / f'fiber_loc_{specid_n}_{ifuslot_n}_{ifuid_n}_{amp}.txt'
        files = sorted(glob.glob(str(patt)))
        if not files:
            raise FileNotFoundError(f"No fiber_loc reference found. Tried pattern: {patt}")
        # Extract date directory names
        dates = [Path(fn).parent.name for fn in files]
        # Normalize observation date
        od = datetime(int(str(obsdate)[:4]), int(str(obsdate)[4:6]), int(str(obsdate)[6:]))
        # Choose file with minimum |obs - ref| days
        diffs = []
        for ds in dates:
            try:
                d = datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
                diffs.append(abs((od - d).days))
            except Exception:
                diffs.append(float('inf'))
        best_idx = int(np.nanargmin(np.asarray(diffs, dtype=float)))
        ref_file = np.loadtxt(files[best_idx])
        return np.asarray(ref_file)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load trace reference for {specid}/{ifuslot}/{ifuid}/{amp} at {virusconfig}: {e}"
        ) from e


def _get_trace(
    twilight: np.ndarray,
    specid: str,
    ifuslot: str,
    ifuid: str,
    amp: str,
    reference: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute 2D fiber traces from a flat (twilight) image using reference locations.

    Returns (trace_2d, ref_table, xchunks, Trace_samples), where trace_2d has
    shape (nfiber, nx) and Trace_samples are the per-chunk sampled positions.
    """
    try:
        ref = np.asarray(reference, dtype=float)
        # Number of not dead fibers (aka, good fibers)
        N1 = int((ref[:, 1] == 0.0).sum())
        good = np.where(ref[:, 1] == 0.0)[0]

        def get_trace_chunk(flat: np.ndarray, XN: np.ndarray) -> np.ndarray:
            YM = np.arange(flat.shape[0])
            inds = np.zeros((3, len(XN)))
            inds[0] = XN - 1.0
            inds[1] = XN + 0.0
            inds[2] = XN + 1.0
            inds = np.array(inds, dtype=int)
            # Parabolic interpolation for sub-pixel maxima
            denom = (2.0 * (flat[inds[2]] - 2.0 * flat[inds[1]] + flat[inds[0]]))
            # Avoid divide-by-zero
            denom = np.where(denom == 0, np.nan, denom)
            Trace = (YM[inds[1]] - (flat[inds[2]] - flat[inds[0]]) / denom)
            return Trace

        image = np.asarray(twilight, dtype=float)
        N = 40
        xchunks = np.array([np.mean(x) for x in np.array_split(np.arange(image.shape[1]), N)])
        chunks = np.array_split(image, N, axis=1)
        flats = [np.nanmedian(chunk, axis=1) for chunk in chunks]
        Trace = np.zeros((len(ref), len(chunks)))
        for k, (flat, _x) in enumerate(zip(flats, xchunks)):
            # Preprocess the flat profile to improve S/N for peak detection
            flat_proc = preprocess_flat_for_detection(flat, perc_window=201, perc=5.0, poly_order=2, gauss_sigma=1.5)
            diff_array = flat_proc[1:] - flat_proc[:-1]
            loc = np.where((diff_array[:-1] > 0.0) & (diff_array[1:] < 0.0))[0]
            peaks = flat_proc[loc + 1] if loc.size else np.array([])

            # Dynamically choose a cut that yields N1 peaks (number of good fibers)
            if len(peaks) >= N1 and N1 > 0:
                top_idx = np.argsort(peaks)[::-1][:N1]
                loc = np.sort(loc[top_idx]) + 1
            else:
                loc = np.sort(loc) + 1 if loc.size else np.array([], dtype=int)
            # Use original (unprocessed) flat for sub-pixel peak localization
            trace = get_trace_chunk(np.asarray(flat, dtype=float), loc)
            T = np.zeros((len(ref)))
            if len(trace) == N1:
                T[good] = trace
                for missing in np.where(ref[:, 1] == 1)[0]:
                    gind = int(np.argmin(np.abs(missing - good))) if good.size else 0
                    T[missing] = (T[good[gind]] + ref[missing, 0] - ref[good[gind], 0])
            # Dead Fibers found case
            if len(trace) == len(ref):
                T = trace
            Trace[:, k] = T

        x = np.arange(image.shape[1])
        trace2d = np.zeros((Trace.shape[0], image.shape[1]))
        for i in np.arange(Trace.shape[0]):
            sel = Trace[i, :] > 0.0
            if not np.any(sel):
                continue
            trace2d[i] = robust_polyfit_predict(np.asarray(xchunks)[sel], Trace[i, sel], x, degree=4)
        if (specid == '504') and (ifuid == '018') and (amp == 'RU'):
            return trace2d[:-1, :], ref[:-1], xchunks, Trace[:-1, :]
        return trace2d, ref, xchunks, Trace
    except Exception as e:
        raise RuntimeError(f"_get_trace failed: {e}") from e

from ..core.algo_result import AlgoResult

def fit_fiber_traces(
    raw_inputs: Optional[Iterable[TraceInput]] = None,
    params: Optional[Dict[str, Any]] = None,
    *,
    master_ldls_array: Optional[np.ndarray] = None,
    trace_reference: Optional[np.ndarray] = None,
    zipcode=None,
) -> AlgoResult:
    """Build a trace solution from arrays supplied by Task/configuration boundaries."""
    # Compatibility call shape: legacy callers may still pass (raw_inputs, params),
    # but the params must contain arrays and identity. Path loading is intentionally
    # not restored to this migrated algorithm boundary.
    params = dict(params or {})
    master_ldls_array = master_ldls_array if master_ldls_array is not None else params.get("master_ldls_array", params.get("master_flat_array"))
    trace_reference = trace_reference if trace_reference is not None else params.get("trace_reference")
    if zipcode is None and all(params.get(name) is not None for name in ("ifuslot", "ifuid", "specid", "amp")):
        from ..core.identity import ZipCode

        zipcode = ZipCode(
            str(params["ifuslot"]), str(params["ifuid"]), str(params["specid"]),
            str(params["amp"]), str(params.get("controller") or "unknown"),
        )
    if master_ldls_array is None or trace_reference is None or zipcode is None:
        raise TypeError(
            "fit_fiber_traces requires already-loaded master_ldls_array, explicit "
            "trace_reference, and zipcode; legacy path parameters are not accepted"
        )
    master = np.asarray(master_ldls_array, dtype=float)
    reference = np.asarray(trace_reference, dtype=float)
    if master.ndim != 2 or reference.ndim != 2 or reference.shape[1] < 2:
        raise ValueError("fit_fiber_traces requires a 2D master_ldls_array and Nx2 trace_reference")
    nx = master.shape[1]
    specid = str(zipcode.specid).zfill(3)
    ifuslot = str(zipcode.ifuslot).zfill(3)
    ifuid = str(zipcode.ifuid).zfill(3)
    amp = str(zipcode.amp)

    # Compute full trace using updated algorithm; no fallback
    try:
        trace_2d, ref, xchunks, Trace_samples = _get_trace(
            master, specid, ifuslot, ifuid, amp, reference
        )
    except Exception as e:
        raise RuntimeError(f"fit_fiber_traces failed to compute trace via _get_trace: {e}") from e

    # Compute per-fiber robust dispersion between sampled chunk traces and the modeled trace2d at xchunks
    # Use MAD-based standard deviation (astropy.stats.mad_std) for robustness to outliers.
    try:
        nfib, nch = Trace_samples.shape
        xidx = np.clip(np.round(np.asarray(xchunks, dtype=float)).astype(int), 0, nx - 1)
        rms_fibers = np.full((nfib,), np.nan, dtype=float)
        for i in range(nfib):
            ts = np.asarray(Trace_samples[i, :], dtype=float)
            sel = np.isfinite(ts) & (ts > 0)
            if np.count_nonzero(sel) >= 2:
                model = trace_2d[i, xidx[sel]]
                diff = ts[sel] - model
                # Robust sigma via MAD; ignore NaNs in diff just in case
                val = mad_std(diff, ignore_nan=True)
                try:
                    val = float(val)
                except Exception:
                    val = np.nan
                if np.isfinite(val):
                    rms_fibers[i] = val
    except Exception:
        # If anything goes wrong, leave rms_fibers as None
        rms_fibers = None

    # Build storage-neutral AlgoResult (no persistence here)
    scalars = {"trace_len": int(nx) if nx is not None else int(trace_2d.shape[1])}
    return AlgoResult(
        kind="trace",
        version=ALGO_VERSION,
        meta={
            "trace_map_shape": list(trace_2d.shape),
        },
        scalars=scalars,
        arrays={
            "fiber_trace_map": trace_2d,
            "per_fiber_trace_residual_rms": rms_fibers,
            "trace_sample_columns": np.asarray(xchunks, dtype=float) if xchunks is not None else None,
            "sampled_trace_positions": Trace_samples,
        },
    )
