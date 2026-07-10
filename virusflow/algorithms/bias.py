from __future__ import annotations

import os
from typing import Iterable, Optional, Dict, Any, List, Union

import numpy as np
from astropy.io import fits
from astropy.stats import biweight_location
from .ccd import base_reduction

# Input item type accepted by step_bias
BiasInput = Dict[str, Optional[str]]  # keys: 'path' (str), 'tar_member' (str|None)


def step_bias(
    raw_bias_inputs: Optional[Iterable[BiasInput]] = None,
    output_path: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    raw_inputs: Optional[Iterable[BiasInput]] = None,
) -> Dict[str, Any]:
    import time
    from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
    import traceback
    """
    Construct a master bias frame from input zero (bias) frames using numpy/astropy.

    Contract:
    - Inputs: iterable of dicts with keys:
        - 'path': outer container path (FITS file or .tar archive path)
        - 'tar_member': optional member path inside the tar when applicable
    - Output: write a master-bias FITS file to output_path, and return metadata.

    Algorithm (aligned with reference step_zro):
    - For each input frame, run CCD base_reduction (overscan subtraction, trim, orientation, gain, error)
    - Stack the reduced images (converted to float)
    - Master bias = median across the stack (axis=0)
    - Per-pixel robust scatter = 1.4826 * median(|frame - master|) over frames
    - Readnoise scalar = median of the scatter map
    """
    params = params or {}
    # Accept either alias name; prefer explicit raw_inputs if provided
    effective = raw_inputs if raw_inputs is not None else raw_bias_inputs
    inputs: List[BiasInput] = list(effective or [])
    n_inputs = len(inputs)
    if n_inputs == 0:
        # Fail fast per architecture guidance: empty inputs indicate a planning/scoping error
        raise ValueError("step_bias requires at least one raw bias input in raw_bias_inputs")

    # Read all frames (optionally in parallel)
    dbg = bool(params.get("debug_timing", False))
    workers = int(params.get("workers", 0) or 0)
    parallel_mode = str(params.get("parallel_mode", "thread")).lower()

    def _reduce_one(idx_item):
        import time as _t
        i, it = idx_item
        t0 = _t.perf_counter()
        p = it.get("path")
        tm = it.get("tar_member")
        if not p:
            return None, i, 0.0, "no-path", {}
        try:
            tdict = {}
            img, _err = base_reduction(p, tm, return_header=False, timings=tdict)
            dt = _t.perf_counter() - t0
            return img, i, dt, None, tdict
        except Exception as e:
            dt = _t.perf_counter() - t0
            return None, i, dt, str(e), {}

    timings: List[tuple] = []
    frames: List[np.ndarray] = []
    t_read0 = time.perf_counter()
    errors: List[str] = []
    # Aggregate base_reduction sub-step timings over successful frames
    sub_totals: Dict[str, float] = {"read": 0.0, "overscan": 0.0, "trim": 0.0, "orient_gain": 0.0, "error": 0.0, "total": 0.0}
    succ_count = 0

    if workers and workers > 0 and n_inputs > 1:
        if parallel_mode == "process":
            # ProcessPool can be heavy due to large array returns; default to thread unless explicitly requested
            Executor = ProcessPoolExecutor
        else:
            Executor = ThreadPoolExecutor
        with Executor(max_workers=workers) as ex:
            futs = [ex.submit(_reduce_one, (i, it)) for i, it in enumerate(inputs)]
            for fut in as_completed(futs):
                img, idx, dt, err, tdict = fut.result()
                timings.append((idx, dt, err))
                if img is not None:
                    frames.append(img)
                    succ_count += 1
                    for k, v in tdict.items():
                        sub_totals[k] = sub_totals.get(k, 0.0) + float(v)
                elif err:
                    errors.append(f"[{idx}] {err}")
    else:
        for i, it in enumerate(inputs):
            img, idx, dt, err, tdict = _reduce_one((i, it))
            timings.append((idx, dt, err))
            if img is not None:
                frames.append(img)
                succ_count += 1
                for k, v in tdict.items():
                    sub_totals[k] = sub_totals.get(k, 0.0) + float(v)
            elif err:
                errors.append(f"[{idx}] {err}")
    t_read1 = time.perf_counter()

    if not frames:
        raise RuntimeError("No readable bias frames provided to step_bias")

    # Align shapes (ensure all equal); if not, raise
    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"Input bias frames have differing shapes: {sorted(shapes)}")

    t_stack0 = time.perf_counter()
    stack = np.stack(frames, axis=0)
    t_stack1 = time.perf_counter()
    # Use biweight location for stack combination to avoid digitization bias
    t_master0 = time.perf_counter()
    master = biweight_location(stack, axis=0, ignore_nan=True)
    t_master1 = time.perf_counter()
    # Robust per-pixel scatter via MAD (kept as median-of-abs-dev for now)
    t_mad0 = time.perf_counter()
    mad = np.median(np.abs(stack - master[None, :, :]), axis=0) * 1.4826
    t_mad1 = time.perf_counter()
    # Scalar readnoise estimate
    readnoise = float(np.nanmedian(mad))

    if dbg:
        tot_read = t_read1 - t_read0
        print(f"[Timing] step_bias: read/reduce {len(frames)}/{n_inputs} frames in {tot_read:.3f}s (workers={workers}, mode={parallel_mode})")
        # Compute base_reduction totals/averages over successful frames only
        succ_times = [dt for (_idx, dt, err) in timings if not err]
        total_base = float(sum(succ_times)) if succ_times else 0.0
        avg_base = (total_base / len(succ_times)) if succ_times else 0.0
        print(f"[Timing] step_bias: base_reduction total={total_base:.3f}s, avg_per_file={avg_base:.3f}s over {len(succ_times)} files")
        # Detailed base_reduction breakdown across successful frames
        if succ_count > 0:
            def fmt_pair(k):
                return f"{k}={sub_totals.get(k, 0.0):.3f}s ({(sub_totals.get(k, 0.0)/succ_count):.3f}s/file)"
            parts = [fmt_pair(k) for k in ("read", "overscan", "trim", "orient_gain", "error", "total")]
            print("[Timing] step_bias: base_reduction breakdown: " + ", ".join(parts))
        if errors:
            print(f"[Timing] step_bias: {len(errors)} frames failed during reduction")
        # Show 5 slowest items
        slow = sorted(timings, key=lambda x: x[1], reverse=True)[:5]
        for idx, dt, err in slow:
            mark = "ERROR" if err else "ok"
            print(f"  - frame[{idx}]: {dt:.3f}s {mark}")
        # Explicitly label the biweight location combination of the stack
        print(f"[Timing] step_bias: stack={t_stack1 - t_stack0:.3f}s, biweight(master)={t_master1 - t_master0:.3f}s, mad={t_mad1 - t_mad0:.3f}s")

    # Write FITS
    if output_path is not None:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        hdu = fits.PrimaryHDU(master.astype(np.float32))
        hdr = hdu.header
        hdr["NINPUTS"] = (len(frames), "number of input bias frames")
        hdr["BIASRN"] = (readnoise, "median per-pixel MAD (scaled)")
        hdr["ALGOVER"] = ("bias-1.0", "algorithms.bias.step_bias version")
        hdul = fits.HDUList([hdu])
        hdul.writeto(output_path, overwrite=True)

    return {
        "readnoise": readnoise,
        "n_inputs": len(frames),
        "shape": list(master.shape),
        "output_path": output_path,
        "algo": "algorithms.bias.step_bias",
        "version": "1.0",
    }
