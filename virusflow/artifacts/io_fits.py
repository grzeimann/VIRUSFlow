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

    - Primary HDU stores data as float32
    - Adds NINPUTS and ALGOVER cards to header
    - Optional mask written as uint8 ImageHDU with provided name
    - Writes a small JSON sidecar with generic fields and any extra keys in 'sidecar'
    """
    arr = np.asarray(data)
    phdu = fits.PrimaryHDU(arr.astype(np.float32))
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
    tmp = outp.with_suffix(outp.suffix + ".tmp")
    fits.HDUList(hdus).writeto(str(tmp), overwrite=True)
    tmp.replace(outp)

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


