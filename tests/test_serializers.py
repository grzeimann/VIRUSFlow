from __future__ import annotations

import json
from pathlib import Path
import numpy as np

from virusflow.artifacts.io_fits import write_array_fits
from virusflow.artifacts.serializers import array_fits


def test_array_fits_describe_and_load(tmp_path: Path):
    arr = np.arange(100, dtype=float).reshape(10, 10)
    out = tmp_path / "test.fits"
    write_array_fits(
        str(out),
        data=arr,
        n_inputs=3,
        algo_version="t-1.0",
        extra_primary_cards={"EXTRA": 42.0},
        sidecar={"kind": "unit", "role": "calibration"},
    )

    # Describe should not load full array and must read sidecar/header summary
    d = array_fits.describe(str(out))
    assert d["payload_type"] == "array"
    assert d["storage_format"] == "fits"
    assert d["shape"] == [10, 10]
    assert d.get("role") == "calibration"

    # Load should return data and header
    payload = array_fits.load(str(out))
    data = payload.get("data")
    hdr = payload.get("header")
    assert isinstance(hdr, dict)
    assert data.shape == (10, 10)
    # Ensure header cards were written
    assert int(hdr.get("NINPUTS", -1)) == 3
    assert str(hdr.get("ALGOVER")) == "t-1.0"


def test_array_fits_preserves_float64_astrometric_precision(tmp_path: Path):
    coordinates = np.asarray([202.500357018943, -8.499797222222], dtype=np.float64)
    out = tmp_path / "coordinates.fits"
    write_array_fits(str(out), data=coordinates)
    loaded = array_fits.load(str(out))["data"]
    assert loaded.dtype.itemsize == 8
    np.testing.assert_array_equal(loaded, coordinates)
