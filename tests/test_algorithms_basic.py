from __future__ import annotations

from pathlib import Path
import types
import numpy as np
import json

from virusflow.algorithms import bias as alg_bias
from virusflow.algorithms import dark as alg_dark
from virusflow.algorithms import flat as alg_flat
from virusflow.algorithms import cmp as alg_cmp
def _mk_inputs(n: int, shape=(6, 8)):
    yy, xx = np.indices(shape)
    return [{"data": (xx + yy + i).astype(float)} for i in range(n)]


def test_step_bias_returns_algo_result():
    ar = alg_bias.step_bias(raw_inputs=_mk_inputs(3), params={})
    # Expect an AlgoResult-like object with arrays and scalars accessible
    from virusflow.core.algo_result import AlgoResult, ensure_algo_result
    ar2 = ensure_algo_result(ar, kind="bias")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 3
    assert "read_noise" in m


def test_step_dark_returns_algo_result_with_mask():
    ar = alg_dark.step_dark(raw_inputs=_mk_inputs(4), params={})
    from virusflow.core.algo_result import ensure_algo_result, AlgoResult
    ar2 = ensure_algo_result(ar, kind="dark")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master_dark") is not None
    assert ar2.get_array("dark_pixel_mask") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 4
    assert "bad_fraction" in m


def test_step_flat_returns_algo_result_with_mask():
    ar = alg_flat.step_flt(raw_inputs=_mk_inputs(5), params={})
    from virusflow.core.algo_result import ensure_algo_result, AlgoResult
    ar2 = ensure_algo_result(ar, kind="flat")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master_flat") is not None
    assert ar2.get_array("flat_response_mask") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 5
    assert "bad_fraction" in m



def test_step_cmp_returns_algo_result():
    ar = alg_cmp.step_cmp(raw_inputs=_mk_inputs(3), params={})
    from virusflow.core.algo_result import ensure_algo_result, AlgoResult
    ar2 = ensure_algo_result(ar, kind="cmp")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master_comparison_lamp") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 3
