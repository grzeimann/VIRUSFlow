from __future__ import annotations

from pathlib import Path
import types
import numpy as np
import json

from virusflow.algorithms import bias as alg_bias
from virusflow.algorithms import dark as alg_dark
from virusflow.algorithms import flat as alg_flat


def _mk_inputs(n: int):
    # paths are not used when we monkeypatch base_reduction
    return [{"path": f"/dev/null/{i}", "tar_member": None} for i in range(n)]


def test_step_bias_persists_and_reports(tmp_path: Path, monkeypatch):
    # Monkeypatch base_reduction to return deterministic arrays
    def fake_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        arr = np.full((4, 6), float(i), dtype=float)
        return (arr, {}) if return_header else (arr, {})

    monkeypatch.setattr(alg_bias, "base_reduction", fake_reduce)

    out = tmp_path / "mbias.fits"
    meta = alg_bias.step_bias(raw_inputs=_mk_inputs(3), output_path=str(out), params={})
    assert out.exists()
    # Sidecar exists and includes keys
    side = Path(str(out) + ".json")
    assert side.exists()
    sc = json.loads(side.read_text())
    assert sc["payload_type"] == "array"
    assert sc["storage_format"] == "fits"
    assert sc["kind"] == "master_bias"
    assert int(meta["n_inputs"]) == 3
    assert meta["output_path"] == str(out)


def test_step_dark_writes_mask_and_sidecar(tmp_path: Path, monkeypatch):
    def fake_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        # pattern ensures some mask
        base = np.indices((4, 6))[1].astype(float) + i
        return (base, {}) if return_header else (base, {})

    monkeypatch.setattr(alg_dark, "base_reduction", fake_reduce)

    out = tmp_path / "mdark.fits"
    meta = alg_dark.step_dark(raw_inputs=_mk_inputs(4), output_path=str(out), params={})
    assert out.exists()
    # Sidecar
    sc = json.loads(Path(str(out) + ".json").read_text())
    assert sc["kind"] == "master_dark"
    # Header contains BADFRAC via describe; here just check JSON has it
    assert "bad_fraction" in sc
    assert int(meta["n_inputs"]) == 4


def test_step_flat_writes_mask_and_sidecar(tmp_path: Path, monkeypatch):
    def fake_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        # flat-like with variation
        yy, xx = np.indices((6, 8))
        arr = (xx + yy + i).astype(float)
        return (arr, {}) if return_header else (arr, {})

    monkeypatch.setattr(alg_flat, "base_reduction", fake_reduce)

    out = tmp_path / "mflat.fits"
    meta = alg_flat.step_flt(raw_inputs=_mk_inputs(5), output_path=str(out), params={})
    assert out.exists()
    sc = json.loads(Path(str(out) + ".json").read_text())
    assert sc["kind"] == "master_flat"
    assert "bad_fraction" in sc
    assert int(meta["n_inputs"]) == 5
