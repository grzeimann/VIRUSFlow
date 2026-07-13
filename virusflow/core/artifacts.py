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


def _save_fits_artifact(
    *,
    kind: str,
    output_path: str,
    primary: np.ndarray,
    n_inputs: int,
    algo_version: str,
    extra_primary_cards: Optional[Dict[str, object]] = None,
    extra_header: Optional[Dict[str, object]] = None,
    mask: Optional[np.ndarray] = None,
    mask_name: Optional[str] = None,
    sidecar_extra: Optional[Dict[str, object]] = None,
) -> None:
    """Generic FITS artifact writer with atomic write and JSON sidecar.

    - primary is written as float32 in the primary HDU.
    - If mask is provided, it is written as uint8 ImageHDU named mask_name.
    - Standard header cards NINPUTS and ALGOVER are added to primary.
    - extra_primary_cards and extra_header are merged into the primary header.
    - Writes a compact sidecar JSON including kind, n_inputs, algo_version, shape, and sidecar_extra.
    """
    from .pathutils import ensure_dir

    arr = np.asarray(primary)
    phdu = fits.PrimaryHDU(arr.astype(np.float32))
    hdr = phdu.header
    hdr["NINPUTS"] = (int(n_inputs), "number of inputs contributing to artifact")
    hdr["ALGOVER"] = (str(algo_version), "algorithm version")
    # Specific cards first (e.g., BADFRAC/BIASRN)
    if extra_primary_cards:
        for k, v in extra_primary_cards.items():
            try:
                hdr[str(k)] = v
            except Exception:
                pass
    # Arbitrary additional cards
    if extra_header:
        for k, v in extra_header.items():
            try:
                hdr[str(k)] = v
            except Exception:
                pass

    hdus: List[fits.hdu.base.ExtensionHDU] = [phdu]
    if mask is not None:
        name = str(mask_name) if mask_name else "MASK"
        mhdu = fits.ImageHDU(np.asarray(mask, dtype=np.uint8), name=name)
        hdus.append(mhdu)

    # Atomic write
    outp = Path(output_path)
    ensure_dir(outp.parent)
    tmp = str(outp.with_suffix(outp.suffix + ".tmp"))
    fits.HDUList(hdus).writeto(tmp, overwrite=True)
    Path(tmp).replace(outp)

    # Sidecar
    side = {
        "kind": str(kind),
        "n_inputs": int(n_inputs),
        "algo_version": str(algo_version),
        "shape": list(arr.shape),
    }
    if sidecar_extra:
        try:
            side.update({k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in sidecar_extra.items()})
        except Exception:
            side.update(sidecar_extra)
    _write_sidecar_json(str(outp), side)


essential_mask_names = {
    "master_dark": "DARKMASK",
    "master_flat": "FLATMASK",
}


def _load_fits_artifact(path: str, *, mask_name: Optional[str] = None):
    """Generic FITS artifact loader.

    Returns (data, header) if mask_name is None, else (data, mask, header).
    """
    with fits.open(Path(path)) as hdul:
        data = np.asarray(hdul[0].data, dtype=float)
        hdr = dict(hdul[0].header)
        if mask_name is None:
            return data, hdr
        # Try to locate mask extension by name, else fall back to first extension
        m = None
        # Look by exact name first
        for h in hdul[1:]:
            if getattr(h, "name", "").upper() == str(mask_name).upper():
                m = np.asarray(h.data, dtype=np.uint8)
                break
        if m is None and len(hdul) > 1:
            m = np.asarray(hdul[1].data, dtype=np.uint8)
        return data, m, hdr


def save_master_bias(output_path: str, master: np.ndarray, *, n_inputs: int, readnoise: float, algo_version: str = "bias-1.0", extra_header: Optional[Dict[str, str]] = None) -> None:
    """Write a master bias to a FITS file using the generic saver.

    Preserves public contract and header semantics.
    """
    _save_fits_artifact(
        kind="master_bias",
        output_path=output_path,
        primary=master,
        n_inputs=n_inputs,
        algo_version=algo_version,
        extra_primary_cards={"BIASRN": float(readnoise)},
        extra_header=extra_header,
        sidecar_extra={"readnoise": float(readnoise)},
    )


def save_master_dark(output_path: str, master: np.ndarray, dark_mask: np.ndarray, *, n_inputs: int, bad_fraction: float, algo_version: str = "dark-1.0", extra_header: Optional[Dict[str, str]] = None) -> None:
    """Write a master dark and its pixel mask to a FITS file using the generic saver."""
    _save_fits_artifact(
        kind="master_dark",
        output_path=output_path,
        primary=master,
        n_inputs=n_inputs,
        algo_version=algo_version,
        extra_primary_cards={"BADFRAC": float(bad_fraction)},
        extra_header=extra_header,
        mask=dark_mask,
        mask_name="DARKMASK",
        sidecar_extra={"bad_fraction": float(bad_fraction)},
    )


def save_master_flat(output_path: str, master: np.ndarray, flat_mask: np.ndarray, *, n_inputs: int, bad_fraction: float, algo_version: str = "flat-1.0", extra_header: Optional[Dict[str, str]] = None) -> None:
    """Write a master flat and its pixel mask to a FITS file using the generic saver."""
    _save_fits_artifact(
        kind="master_flat",
        output_path=output_path,
        primary=master,
        n_inputs=n_inputs,
        algo_version=algo_version,
        extra_primary_cards={"BADFRAC": float(bad_fraction)},
        extra_header=extra_header,
        mask=flat_mask,
        mask_name="FLATMASK",
        sidecar_extra={"bad_fraction": float(bad_fraction)},
    )


def load_master_bias(path: str):
    """Load master bias array and header from a FITS artifact file.

    Returns (array, header_dict).
    """
    return _load_fits_artifact(path)


def load_master_dark(path: str):
    """Load master dark array, dark mask, and header from a FITS artifact file.

    Returns (array, mask_uint8, header_dict).
    """
    return _load_fits_artifact(path, mask_name="DARKMASK")


def load_master_flat(path: str):
    """Load master flat array, flat mask, and header from a FITS artifact file.

    Returns (array, mask_uint8, header_dict).
    """
    return _load_fits_artifact(path, mask_name="FLATMASK")


def save_trace_solution(output_path: str, *, trace_2d: np.ndarray, n_inputs: int = 0, algo_version: str = "trace-1.0", extra_header: Optional[Dict[str, str]] = None) -> None:
    """Persist a trace solution to FITS using the generic saver.

    - Primary HDU: 2D trace array (float32) with NINPUTS and ALGOVER.
    - Sidecar JSON includes kind="trace", n_inputs, algo_version, shape, and trace_len (total elements).
    """
    tr = np.asarray(trace_2d)
    _save_fits_artifact(
        kind="trace",
        output_path=output_path,
        primary=tr,
        n_inputs=int(n_inputs),
        algo_version=str(algo_version),
        extra_primary_cards=None,
        extra_header=extra_header,
        mask=None,
        mask_name=None,
        sidecar_extra={"trace_len": int(tr.size)},
    )


def load_trace_solution(path: str):
    """Load the trace array and header from a trace artifact file using the generic loader.

    Returns (trace_array, header_dict).
    """
    return _load_fits_artifact(path)
