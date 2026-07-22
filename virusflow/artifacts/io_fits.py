from __future__ import annotations

from typing import Dict, Optional, Tuple
from pathlib import Path
import numpy as np
from astropy.io import fits

# Generic FITS array I/O with compact JSON sidecar next to the FITS file.
# This module replaces legacy product-specific helpers with
# clean, generic read/write utilities and a few thin wrappers used by
# algorithms and tasks today.


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_sidecar_json(base_path: Path, payload: Dict[str, object]) -> None:
    try:
        import json
        side = base_path.with_suffix(base_path.suffix + ".json")
        side.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception:
        # Sidecar is optional; never fail writes
        pass


def write_array_fits(
    output_path: str,
    *,
    data: np.ndarray,
    n_inputs: int = 0,
    algo_version: str = "unknown",
    extra_primary_cards: Optional[Dict[str, object]] = None,
    extra_header: Optional[Dict[str, object]] = None,
    mask: Optional[np.ndarray] = None,
    mask_name: Optional[str] = None,
    sidecar: Optional[Dict[str, object]] = None,
) -> None:
    """Write a 1D/2D array as a FITS artifact with a compact sidecar JSON.

    - Primary HDU preserves numeric precision (boolean arrays use uint8)
    - Adds NINPUTS and ALGOVER cards to header
    - Optional mask written as uint8 ImageHDU with provided name
    - Writes a small JSON sidecar with generic fields and any extra keys in 'sidecar'
    """
    arr = np.asarray(data)
    storage_array = arr.astype(np.uint8) if arr.dtype.kind == "b" else arr
    phdu = fits.PrimaryHDU(storage_array)
    hdr = phdu.header
    hdr["NINPUTS"] = (int(n_inputs), "number of inputs contributing to artifact")
    hdr["ALGOVER"] = (str(algo_version), "algorithm version")
    if extra_primary_cards:
        for k, v in extra_primary_cards.items():
            try:
                hdr[str(k)] = v
            except Exception:
                pass
    if extra_header:
        for k, v in extra_header.items():
            try:
                hdr[str(k)] = v
            except Exception:
                pass

    hdus = [phdu]
    if mask is not None:
        name = str(mask_name) if mask_name else "MASK"
        mhdu = fits.ImageHDU(np.asarray(mask, dtype=np.uint8), name=name)
        hdus.append(mhdu)

    outp = Path(output_path)
    _ensure_dir(outp)
    # Robust atomic write: write to a NamedTemporaryFile in the same directory,
    # fsync, then atomically replace the final path. This avoids races where a
    # previously chosen temp name no longer exists at rename time.
    import os as _os
    import tempfile as _tempfile
    import time as _time

    hdul = fits.HDUList(hdus)

    # Create a temp file in the destination directory (delete=False so we can replace)
    with _tempfile.NamedTemporaryFile(prefix=outp.name + ".", suffix=".tmp", dir=str(outp.parent), delete=False) as tf:
        tmp_path = Path(tf.name)
    # Write to temp path
    hdul.writeto(str(tmp_path), overwrite=True)
    # Ensure data is on disk before rename
    try:
        with open(tmp_path, 'rb') as _f:
            try:
                _os.fsync(_f.fileno())
            except Exception:
                pass
    except Exception:
        pass

    # Attempt atomic replace with a short bounded retry for transient issues
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            tmp_path.replace(outp)
            last_err = None
            break
        except Exception as e:
            last_err = e
            # If the final file already exists and is recent, treat as success (another worker won the race)
            try:
                if outp.exists() and (time := _time.time()) and (time - outp.stat().st_mtime) < 5.0:
                    last_err = None
                    break
            except Exception:
                pass
            _time.sleep(0.05 * (attempt + 1))
    # Cleanup temp if it still exists and we succeeded or are giving up
    try:
        if tmp_path.exists() and (last_err is None or not outp.exists()):
            # If replace failed and final doesn't exist, keep temp for debugging
            if last_err is None:
                tmp_path.unlink(missing_ok=True)
    except Exception:
        pass
    if last_err is not None:
        # Surface a clear error including paths for debugging
        raise RuntimeError(f"Atomic write failed: {tmp_path} -> {outp}: {last_err}")

    # Sidecar JSON
    side = {
        "payload_type": "array",
        "storage_format": "fits",
        "n_inputs": int(n_inputs),
        "algo_version": str(algo_version),
        "shape": list(arr.shape),
    }
    if isinstance(sidecar, dict):
        try:
            # normalize numeric scalars for stability
            import numpy as _np
            for k, v in list(sidecar.items()):
                if isinstance(v, (int, float, _np.floating)):
                    side[k] = float(v)
                else:
                    side[k] = v
        except Exception:
            side.update(sidecar)
    _write_sidecar_json(outp, side)


def read_array_fits(path: str) -> Dict:
    """Read the primary HDU array and header from a FITS artifact file.

    Returns a dict with keys: {"data": np.ndarray, "header": dict}
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    with fits.open(str(p), memmap=True) as hdul:
        data = hdul[0].data
        hdr = dict(hdul[0].header)
    return {"data": data, "header": hdr}

