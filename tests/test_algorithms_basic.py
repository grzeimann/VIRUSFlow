from __future__ import annotations

from pathlib import Path
import types
import numpy as np
import json

from virusflow.algorithms import bias as alg_bias
from virusflow.algorithms import dark as alg_dark
from virusflow.algorithms import flat as alg_flat
from virusflow.algorithms import cmp as alg_cmp
from virusflow.algorithms import ccd as alg_ccd


def _mk_inputs(n: int):
    # paths are not used when we monkeypatch base_reduction
    return [{"path": f"/dev/null/{i}", "tar_member": None} for i in range(n)]


def test_step_bias_returns_algo_result(monkeypatch):
    # Monkeypatch base_reduction to return deterministic arrays
    def fake_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        arr = np.full((4, 6), float(i), dtype=float)
        return (arr, {}) if return_header else (arr, {})

    monkeypatch.setattr(alg_ccd, "base_reduction", fake_reduce)

    ar = alg_bias.step_bias(raw_inputs=_mk_inputs(3), params={})
    # Expect an AlgoResult-like object with arrays and scalars accessible
    from virusflow.core.algo_result import AlgoResult, ensure_algo_result
    ar2 = ensure_algo_result(ar, kind="bias")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 3
    assert "readnoise" in m


def test_step_dark_returns_algo_result_with_mask(monkeypatch):
    def fake_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        base = np.indices((4, 6))[1].astype(float) + i
        return (base, {}) if return_header else (base, {})

    monkeypatch.setattr(alg_ccd, "base_reduction", fake_reduce)

    ar = alg_dark.step_dark(raw_inputs=_mk_inputs(4), params={})
    from virusflow.core.algo_result import ensure_algo_result, AlgoResult
    ar2 = ensure_algo_result(ar, kind="dark")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master") is not None
    assert ar2.get_array("mask") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 4
    assert "bad_fraction" in m


def test_step_flat_returns_algo_result_with_mask(monkeypatch):
    def fake_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        yy, xx = np.indices((6, 8))
        arr = (xx + yy + i).astype(float)
        return (arr, {}) if return_header else (arr, {})

    monkeypatch.setattr(alg_flat, "base_reduction", fake_reduce)

    ar = alg_flat.step_flt(raw_inputs=_mk_inputs(5), params={})
    from virusflow.core.algo_result import ensure_algo_result, AlgoResult
    ar2 = ensure_algo_result(ar, kind="flat")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master") is not None
    assert ar2.get_array("mask") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 5
    assert "bad_fraction" in m



def test_step_cmp_returns_algo_result(monkeypatch):
    def fake_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        yy, xx = np.indices((6, 8))
        arr = (xx + i).astype(float)
        return (arr, {}) if return_header else (arr, {})

    monkeypatch.setattr(alg_ccd, "base_reduction", fake_reduce)

    ar = alg_cmp.step_cmp(raw_inputs=_mk_inputs(3), params={})
    from virusflow.core.algo_result import ensure_algo_result, AlgoResult
    ar2 = ensure_algo_result(ar, kind="cmp")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 3
