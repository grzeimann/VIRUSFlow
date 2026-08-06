from __future__ import annotations

import numpy as np

from virusflow.core.algo_result import AlgoResult
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.contracts.result import BiasResultContract
from virusflow.contracts.artifact import (
    MasterBiasContract,
    MasterDarkContract,
    MasterLDLSContract,
    TraceMapContract,
    WavelengthMapContract,
)
from virusflow.publication.context import PublicationContext


def test_algo_result_as_meta_includes_kind_and_version():
    ar = AlgoResult(
        kind="bias",  # computation identity, not artifact kind
        version="bias-1.0",
        meta={"n_inputs": 3},
        scalars={"read_noise": 4.2},
        arrays={"master": np.zeros((2, 2), dtype=float)},
    )
    m = ar.as_meta()
    assert m["_algo_version"] == "bias-1.0"
    assert m["_algo_kind"] == "bias"
    # Scalars merged into meta
    assert m["read_noise"] == 4.2
    # Shapes summary is optional but if present should include key
    if "_arrays_shape" in m:
        assert "master" in m["_arrays_shape"]


essential_arr = np.arange(6, dtype=float).reshape(2, 3)

def test_artifact_request_multi_component():
    comp_master = LogicalComponent(name="master_ldls", model_type="array2d", value=essential_arr)
    comp_mask = LogicalComponent(name="flat_response_mask", model_type="array2d", value=np.ones_like(essential_arr))
    req = ArtifactRequest(
        kind="master_ldls",
        components={
            "master_ldls": comp_master,
            "flat_response_mask": comp_mask,
        },
        summaries={"bad_fraction": 0.12},
        metadata={"role": "calibration"},
        parents=[1, 2],
        labels=["calib", "flat"],
    )
    names = req.component_names()
    assert set(names) == {"master_ldls", "flat_response_mask"}
    assert req.get_component("master_ldls").model_type == "array2d"


def test_result_contract_smoke_validation():
    import numpy as _np
    # For bias, the contract requires 'master' array and 'read_noise' scalar/meta
    ar = AlgoResult(kind="bias", version="bias-1.0", meta={}, scalars={"read_noise": 4.2}, arrays={"master": _np.zeros((2, 2))})
    rep = BiasResultContract().validate(ar)
    assert rep.ok, f"unexpected errors: {rep.errors}"


def test_artifact_contract_specs_present():
    for cls in (
        MasterBiasContract, MasterDarkContract, MasterLDLSContract,
        TraceMapContract, WavelengthMapContract,
    ):
        spec = cls().spec()
        assert isinstance(spec.kind, str) and len(spec.kind) > 0
        # At least one component defined
        assert len(spec.components) >= 1


def test_publication_context_construction():
    ctx = PublicationContext(
        task_name="bias",
        task_version="v1",
        algorithm_name="algorithms.bias.step_bias",
        algorithm_version="bias-1.0",
        parameters={"zipcode": "V01B_002"},
        parent_ids=[123, 456],
        timings={"resolve": 0.01, "execute": 0.5},
    )
    assert ctx.task_name == "bias"
    assert ctx.algorithm_version == "bias-1.0"
