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
    from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
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
    workers = int(params.get("workers", 0) or 0)
    parallel_mode = str(params.get("parallel_mode", "thread")).lower()

    def _reduce_one(idx_item):
        i, it = idx_item
        p = it.get("path")
        tm = it.get("tar_member")
        if not p:
            return None, i, "no-path"
        try:
            img, _err = base_reduction(p, tm, return_header=False)
            return img, i, None
        except Exception as e:
            return None, i, str(e)

    frames: List[np.ndarray] = []
    errors: List[str] = []

    if workers and workers > 0 and n_inputs > 1:
        if parallel_mode == "process":
            # ProcessPool can be heavy due to large array returns; default to thread unless explicitly requested
            Executor = ProcessPoolExecutor
        else:
            Executor = ThreadPoolExecutor
        with Executor(max_workers=workers) as ex:
            futs = [ex.submit(_reduce_one, (i, it)) for i, it in enumerate(inputs)]
            for fut in as_completed(futs):
                img, idx, err = fut.result()
                if img is not None:
                    frames.append(img)
                elif err:
                    errors.append(f"[{idx}] {err}")
    else:
        for i, it in enumerate(inputs):
            img, idx, err = _reduce_one((i, it))
            if img is not None:
                frames.append(img)
            elif err:
                errors.append(f"[{idx}] {err}")

    if not frames:
        raise RuntimeError("No readable bias frames provided to step_bias")

    # Align shapes (ensure all equal); if not, raise
    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"Input bias frames have differing shapes: {sorted(shapes)}")

    stack = np.stack(frames, axis=0)
    # Use biweight location for stack combination to avoid digitization bias
    master = biweight_location(stack, axis=0, ignore_nan=True)
    # Robust per-pixel scatter via MAD (kept as median-of-abs-dev for now)
    mad = np.median(np.abs(stack - master[None, :, :]), axis=0) * 1.4826
    # Scalar readnoise estimate
    readnoise = float(np.nanmedian(mad))

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
