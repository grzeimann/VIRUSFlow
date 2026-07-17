from __future__ import annotations

import numpy as np
import pytest

from virusflow.algorithms import sci as alg_sci
from virusflow.algorithms import ccd as alg_ccd
from virusflow.core.algo_result import ensure_algo_result, AlgoResult
from virusflow.contracts.result import SciResultContract
from virusflow.contracts.artifact import MasterSciContract
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.artifacts.models import Scope
from virusflow.artifacts.service import ArtifactService
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.registry.database import init_db


def _mk_inputs(n: int):
    return [{"path": f"/dev/null/{i}", "tar_member": None} for i in range(n)]


def test_build_master_science_basic(monkeypatch):
    # Patch CCD reduction used inside algorithms.sci
    def fake_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        yy, xx = np.indices((6, 8))
        arr = (xx + 0.5 * i).astype(float)
        return (arr, {}) if return_header else (arr, {})

    monkeypatch.setattr(alg_ccd, "reduce_raw_amplifier_frame", fake_reduce)

    ar = alg_sci.build_master_science(raw_inputs=_mk_inputs(3), params={})
    ar2 = ensure_algo_result(ar, kind="sci")
    assert isinstance(ar2, AlgoResult)
    assert ar2.get_array("master_science") is not None
    m = ar2.as_meta()
    assert int(m.get("n_inputs", 0)) == 3
    # Algorithm must not compute generic QA like p95
    assert "p95" not in m
    # Contract validation passes
    rep = SciResultContract().validate(ar2)
    assert rep.ok, f"Unexpected contract errors: {rep.errors}"


def test_build_master_science_errors(monkeypatch):
    # 1) No inputs
    with pytest.raises(ValueError):
        alg_sci.build_master_science(raw_inputs=[], params={})

    # 2) All inputs unreadable
    def bad_reduce(path, tar_member, return_header=False):
        raise IOError("cannot read")

    monkeypatch.setattr(alg_ccd, "reduce_raw_amplifier_frame", bad_reduce)
    with pytest.raises(RuntimeError):
        alg_sci.build_master_science(raw_inputs=_mk_inputs(2), params={})

    # 3) Inconsistent shapes
    def varying_reduce(path, tar_member, return_header=False):
        i = int(str(path).split("/")[-1]) if str(path).split("/")[-1].isdigit() else 0
        arr = np.zeros((5 + i, 7), dtype=float)
        return (arr, {}) if return_header else (arr, {})

    monkeypatch.setattr(alg_ccd, "reduce_raw_amplifier_frame", varying_reduce)
    with pytest.raises(ValueError):
        alg_sci.build_master_science(raw_inputs=_mk_inputs(2), params={})


def test_master_sci_publication_and_qa_from_component(tmp_path, monkeypatch):
    # Arrange DB and services
    db_path = str(tmp_path / "vf.sqlite")
    init_db(db_path)
    svc = ArtifactService(db_path)
    policy = DefaultPersistencePolicy()
    pub = DefaultPublicationService(svc=svc, policy=policy, base_dir=str(tmp_path))

    # All-zero array should lead to p95 == 0 from component and QA fail for master_sci
    arr = np.zeros((4, 6), dtype=float)
    comp = LogicalComponent(name="master_science", model_type="array2d", value=arr)
    req = ArtifactRequest(
        kind="master_sci",
        components={"master_science": comp},
        summaries={"n_inputs": 2},
        metadata={},
        scope=Scope(zipcode=None),
        parents=[],
    )

    ctx = PublicationContext(
        task_name="sci",
        task_version="v1",
        algorithm_name="algorithms.sci.build_master_science",
        algorithm_version="sci-1.0",
        parameters={},
        parent_ids=[],
        timings={},
    )

    arts = pub.publish([req], ctx)
    assert arts and arts[0].id is not None
    art = arts[0]

    # Evaluate QA; DiagnosticsFacade should compute p95 from component payload via meta.component.data
    status = svc.diagnostics.evaluate_and_save(artifact_id=int(art.id), kind="master_sci", meta={})
    # With all zeros, p95 == 0 -> fail
    assert status == "fail"


def test_artifact_contract_specs_include_master_sci():
    spec = MasterSciContract().spec()
    assert spec.kind == "master_sci"
    names = [c.name for c in spec.components]
    assert "master_science" in names
