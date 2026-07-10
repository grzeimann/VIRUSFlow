from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from astropy.io import fits
import numpy as np

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
    fits.HDUList([hdu]).writeto(output_path, overwrite=True)


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
    fits.HDUList([phdu, mhdu]).writeto(output_path, overwrite=True)
