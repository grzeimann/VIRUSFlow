from __future__ import annotations

from typing import Dict
from pathlib import Path
from astropy.io import fits
import numpy as np

from ..io_fits import write_array_fits


def _read_header_only(path: Path) -> Dict:
    with fits.open(str(path), memmap=True) as hdul:
        hdr = dict(hdul[0].header)
        shape = list(hdul[0].data.shape) if hdul[0].data is not None else hdr.get('NAXIS', 0)
    return {"header": hdr, "shape": shape}


def describe(path_str: str) -> Dict:
    p = Path(path_str)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    # Prefer JSON sidecar when present for speed; else read FITS header only
    side = p.with_suffix(p.suffix + ".json")
    if side.exists():
        try:
            import json
            return json.loads(side.read_text())
        except Exception:
            pass
    info = _read_header_only(p)
    # Normalize to a compact summary schema
    out = {
        "payload_type": "array",
        "storage_format": "fits",
    }
    out.update(info)
    return out


def load(path_str: str) -> Dict:
    p = Path(path_str)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    # Unsigned FITS images use BZERO/BSCALE and cannot be materialized with
    # strict memory mapping. Named components are checksum-verified and loaded
    # as complete arrays at this boundary, so use the general scaled reader.
    with fits.open(str(p), memmap=False) as hdul:
        stored = hdul[0].data
        hdr = dict(hdul[0].header)
    scale = hdr.get("VFSCAL")
    if scale is None:
        return {"data": stored, "header": hdr}
    physical = np.asarray(stored, dtype=np.float32) * np.float32(scale)
    return {"data": physical, "stored_data": stored, "header": hdr, "physical_scale": float(scale)}


def save(path_str: str, value, *, metadata: Dict | None = None) -> None:
    meta = dict(metadata or {})
    write_array_fits(
        path_str,
        data=value,
        n_inputs=int(meta.get("n_inputs", 0) or 0),
        algo_version=str(meta.get("algo_version") or "unknown"),
        sidecar=meta,
    )
