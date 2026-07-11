from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from astropy.io import fits
import numpy as np
from pathlib import Path

from .identity import ZipCode


@dataclass
class ProvenanceInfo:
    software_version: str
    git_commit: Optional[str]
    algorithm: str
    parameters_hash: str
    parents: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Artifact:
    """Generic artifact description recorded in the registry.

    Note: This dataclass is a light-weight descriptor. Persist to the registry
    to make it durable.
    """

    id: Optional[int]
    kind: str
    name: str
    path: Optional[str]
    zipcode: Optional[ZipCode]
    validity_start: Optional[datetime] = None
    validity_end: Optional[datetime] = None
    provenance: Optional[ProvenanceInfo] = None
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class CalibrationProduct(Artifact):
    pass


@dataclass
class ReductionProduct(Artifact):
    pass


# -------- Artifact storage helpers (FITS shim) --------
# These helpers centralize how algorithm outputs are materialized on disk.
# They intentionally live in core so that we can later switch to alternate
# backends (e.g., HDF5, object storage) without touching algorithm code.

def _write_sidecar_json(base_path: str, payload: Dict[str, object]) -> None:
    """Write a small JSON sidecar next to an artifact path (same stem + .json).

    The sidecar is intended for quick inspection and selection without opening
    large FITS files. Failures are silent to avoid interrupting core writes.
    """
    try:
        import json as _json
        p = Path(base_path)
        side = p.with_suffix(p.suffix + ".json")
        # Compact, stable JSON
        data = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
        side.write_text(data)
    except Exception:
        # Sidecar is optional; do not raise
        pass


def save_master_bias(output_path: str, master: np.ndarray, *, n_inputs: int, readnoise: float, algo_version: str = "bias-1.0", extra_header: Optional[Dict[str, str]] = None) -> None:
    """Write a master bias to a FITS file.

    - Primary HDU contains the master array as float32.
    - Header keywords: NINPUTS, BIASRN, ALGOVER.
    - extra_header (optional) allows callers to add more cards.
    """
    from pathlib import Path
    from .pathutils import ensure_dir
    ensure_dir(Path(output_path).parent)
    hdu = fits.PrimaryHDU(master.astype(np.float32))
    hdr = hdu.header
    hdr["NINPUTS"] = (int(n_inputs), "number of input bias frames")
    hdr["BIASRN"] = (float(readnoise), "median per-pixel MAD (scaled)")
    hdr["ALGOVER"] = (str(algo_version), "algorithm version")
    if extra_header:
        for k, v in extra_header.items():
            try:
                hdr[str(k)] = v
            except Exception:
                # ignore invalid cards for now
                pass
    # Atomic write to avoid torn files
    from .pathutils import ensure_dir
    ensure_dir(Path(output_path).parent)
    _tmp = str(Path(output_path).with_suffix(Path(output_path).suffix + ".tmp"))
    fits.HDUList([hdu]).writeto(_tmp, overwrite=True)
    Path(_tmp).replace(output_path)
    # Write interpolation-ready sidecar summary
    _write_sidecar_json(output_path, {
        "kind": "master_bias",
        "n_inputs": int(n_inputs),
        "readnoise": float(readnoise),
        "shape": list(master.shape),
        "algo_version": str(algo_version),
    })


def save_master_dark(output_path: str, master: np.ndarray, dark_mask: np.ndarray, *, n_inputs: int, bad_fraction: float, algo_version: str = "dark-1.0", extra_header: Optional[Dict[str, str]] = None) -> None:
    """Write a master dark and its pixel mask to a FITS file.

    - Primary HDU: master as float32 with NINPUTS, BADFRAC, ALGOVER
    - ImageHDU 'DARKMASK': uint8 mask
    - extra_header (optional) allows callers to add more cards to primary
    """
    from pathlib import Path
    from .pathutils import ensure_dir
    ensure_dir(Path(output_path).parent)
    phdu = fits.PrimaryHDU(master.astype(np.float32))
    phdr = phdu.header
    phdr["NINPUTS"] = (int(n_inputs), "number of input dark frames")
    phdr["BADFRAC"] = (float(bad_fraction), "fraction of pixels flagged in dark mask")
    phdr["ALGOVER"] = (str(algo_version), "algorithm version")
    if extra_header:
        for k, v in extra_header.items():
            try:
                phdr[str(k)] = v
            except Exception:
                pass
    mhdu = fits.ImageHDU(dark_mask.astype(np.uint8), name="DARKMASK")
    # Atomic write to avoid torn files
    from .pathutils import ensure_dir
    ensure_dir(Path(output_path).parent)
    _tmp = str(Path(output_path).with_suffix(Path(output_path).suffix + ".tmp"))
    fits.HDUList([phdu, mhdu]).writeto(_tmp, overwrite=True)
    Path(_tmp).replace(output_path)
    # Write interpolation-ready sidecar summary
    _write_sidecar_json(output_path, {
        "kind": "master_dark",
        "n_inputs": int(n_inputs),
        "bad_fraction": float(bad_fraction),
        "shape": list(master.shape),
        "algo_version": str(algo_version),
    })


def save_master_flat(output_path: str, master: np.ndarray, flat_mask: np.ndarray, *, n_inputs: int, bad_fraction: float, algo_version: str = "flat-1.0", extra_header: Optional[Dict[str, str]] = None) -> None:
    """Write a master flat and its pixel mask to a FITS file.

    - Primary HDU: master as float32 with NINPUTS, BADFRAC, ALGOVER
    - ImageHDU 'FLATMASK': uint8 mask
    - extra_header (optional) allows callers to add more cards to primary
    """
    from pathlib import Path
    from .pathutils import ensure_dir
    ensure_dir(Path(output_path).parent)
    phdu = fits.PrimaryHDU(master.astype(np.float32))
    phdr = phdu.header
    phdr["NINPUTS"] = (int(n_inputs), "number of input flat frames")
    phdr["BADFRAC"] = (float(bad_fraction), "fraction of pixels flagged in flat mask")
    phdr["ALGOVER"] = (str(algo_version), "algorithm version")
    if extra_header:
        for k, v in extra_header.items():
            try:
                phdr[str(k)] = v
            except Exception:
                pass
    mhdu = fits.ImageHDU(flat_mask.astype(np.uint8), name="FLATMASK")
    # Atomic write
    ensure_dir(Path(output_path).parent)
    _tmp = str(Path(output_path).with_suffix(Path(output_path).suffix + ".tmp"))
    fits.HDUList([phdu, mhdu]).writeto(_tmp, overwrite=True)
    Path(_tmp).replace(output_path)
    # Sidecar summary
    _write_sidecar_json(output_path, {
        "kind": "master_flat",
        "n_inputs": int(n_inputs),
        "bad_fraction": float(bad_fraction),
        "shape": list(master.shape),
        "algo_version": str(algo_version),
    })


def load_master_bias(path: str):
    """Load master bias array and header from a FITS artifact file.

    Returns (array, header_dict).
    """
    with fits.open(Path(path)) as hdul:
        data = np.asarray(hdul[0].data, dtype=float)
        hdr = dict(hdul[0].header)
    return data, hdr


def load_master_dark(path: str):
    """Load master dark array, dark mask, and header from a FITS artifact file.

    Returns (array, mask_uint8, header_dict).
    """
    with fits.open(Path(path)) as hdul:
        data = np.asarray(hdul[0].data, dtype=float)
        hdr = dict(hdul[0].header)
        mask = None
        # Find DARKMASK extension by name or index 1
        for h in hdul[1:]:
            if getattr(h, "name", "").upper() == "DARKMASK":
                mask = np.asarray(h.data, dtype=np.uint8)
                break
        if mask is None and len(hdul) > 1:
            mask = np.asarray(hdul[1].data, dtype=np.uint8)
    return data, mask, hdr


def load_master_flat(path: str):
    """Load master flat array, flat mask, and header from a FITS artifact file.

    Returns (array, mask_uint8, header_dict).
    """
    with fits.open(Path(path)) as hdul:
        data = np.asarray(hdul[0].data, dtype=float)
        hdr = dict(hdul[0].header)
        mask = None
        for h in hdul[1:]:
            if getattr(h, "name", "").upper() == "FLATMASK":
                mask = np.asarray(h.data, dtype=np.uint8)
                break
        if mask is None and len(hdul) > 1:
            mask = np.asarray(hdul[1].data, dtype=np.uint8)
    return data, mask, hdr
