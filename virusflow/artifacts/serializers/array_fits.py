from __future__ import annotations

from typing import Dict
from pathlib import Path
from astropy.io import fits


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
    with fits.open(str(p), memmap=True) as hdul:
        data = hdul[0].data
        hdr = dict(hdul[0].header)
    return {"data": data, "header": hdr}
