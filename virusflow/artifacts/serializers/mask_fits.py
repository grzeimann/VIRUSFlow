from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import numpy as np
from astropy.io import fits

from ..io_fits import write_array_fits
from ..sparse_mask import EncodedMask, decode_mask, encode_mask


def save(path_str: str, value, *, metadata: Dict | None = None) -> None:
    encoded = value if isinstance(value, EncodedMask) else encode_mask(value)
    meta = dict(metadata or {})
    meta.update(
        {
            "mask_encoding": encoded.encoding,
            "mask_shape": list(encoded.shape),
            "mask_dtype": encoded.dtype,
        }
    )
    write_array_fits(
        path_str,
        data=encoded.payload,
        n_inputs=int(meta.get("n_inputs", 0) or 0),
        algo_version=str(meta.get("algo_version") or "unknown"),
        extra_primary_cards={
            "MSKENC": encoded.encoding,
            "MSKSHAP": ",".join(str(x) for x in encoded.shape),
            "MSKDTYP": encoded.dtype,
        },
        sidecar=meta,
    )


def load(path_str: str) -> Dict:
    with fits.open(path_str, memmap=False) as hdul:
        payload = np.asarray(hdul[0].data)
        header = dict(hdul[0].header)
    encoded = EncodedMask(
        tuple(int(x) for x in str(header["MSKSHAP"]).split(",") if x),
        str(header["MSKDTYP"]),
        str(header["MSKENC"]).lower(),
        payload,
    )
    return {"data": decode_mask(encoded), "header": header, "encoding": encoded.encoding}


def describe(path_str: str) -> Dict:
    path = Path(path_str)
    sidecar = path.with_suffix(path.suffix + ".json")
    if sidecar.exists():
        return json.loads(sidecar.read_text())
    with fits.open(path_str, memmap=True) as hdul:
        header = hdul[0].header
        shape = [int(x) for x in str(header["MSKSHAP"]).split(",") if x]
        return {
            "payload_type": "mask",
            "storage_format": "fits",
            "shape": shape,
            "mask_encoding": str(header["MSKENC"]).lower(),
            "mask_dtype": str(header["MSKDTYP"]),
        }
