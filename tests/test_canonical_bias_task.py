from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest
from virusflow.analytics.studies.bias import BiasStabilityParams, BiasStabilityStudy
from virusflow.artifacts import ArtifactService, Scope
from virusflow.core.algo_result import AlgoResult
from virusflow.core.identity import ZipCode
from virusflow.io import RawFrameData
from virusflow.registry.database import connect, init_db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.calibs import BiasTask


class _Target:
    zipcode = ZipCode(ifuslot="020", ifuid="001", specid="001", amp="LL", controller="A")
    start_date = "20260609"
    end_date = "20260610"
    start_dt = datetime(2026, 6, 9, 1, 2, 3)
    end_dt = datetime(2026, 6, 10, 4, 5, 6)


class _Loader:
    def __init__(self, value=0.0):
        self.value = value

    def load(self, path, tar_member=None):
        data = np.full((8, 8), self.value, dtype=float)
        header = {"GAIN": 1.0, "RDNOISE": 3.0, "CCDPOS": "L", "CCDHALF": "L"}
        return RawFrameData(data, header, path, tar_member)


def _task(tmp_path, task_type=BiasTask, value=0.0):
    database = str(tmp_path / "registry.sqlite3")
    init_db(database)
    context = TaskContext(database, str(tmp_path / "products"), {"raw_frame_loader": _Loader(value)})
    task = task_type(context, target=_Target())
    task.query_inputs = lambda: ([{"path": "virtual.fits", "tar_member": None}], [])
    return task, ArtifactService(database)


def test_bias_task_persists_complete_contract_validity_configuration_and_qa(tmp_path):
    task, service = _task(tmp_path)
    artifact = task.run({})["master_bias"]
    description = service.describe(artifact.id)
    assert {item["name"] for item in description["components"]} == {"master", "per_pixel_bias_scatter"}
    assert description["validity"] == {
        "start": _Target.start_dt,
        "end": _Target.end_dt,
        "policy": "target_window",
    }
    assert description["revision"] and description["checksum"]
    full = service.get(artifact.id)
    assert full.configuration_refs
    assert all(ref.evidence_state in {"verified", "unknown"} for ref in full.configuration_refs)
    assert service.adapter.get_diagnostics(artifact.id)["status"] == "pass"
    with connect(service.db_path) as connection:
        facts = {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                "SELECT name, value_json, units, component FROM qa_facts WHERE artifact_id=?",
                (artifact.id,),
            )
        }
        decision = connection.execute(
            "SELECT status, usability FROM qa_decisions WHERE artifact_id=?", (artifact.id,)
        ).fetchone()
    assert facts["read_noise"][1] == "electron"
    assert facts["per_pixel_bias_scatter_median"][2] == "per_pixel_bias_scatter"
    assert tuple(decision) == ("pass", "usable")


def test_bias_stability_uses_named_components_and_records_all_source_lineage(tmp_path):
    task, service = _task(tmp_path)
    first = task.run({})["master_bias"]
    second = task.run({})["master_bias"]
    output = BiasStabilityStudy(service).run(
        BiasStabilityParams(tmp_path / "analytics", _Target.zipcode)
    )
    # Identical repeated publications resolve to one content-derived canonical
    # revision; analytic lineage records each unique source Product once.
    assert output["source_ids"] == sorted({first.id, second.id})
    desc = service.describe(output["artifact_id"])
    assert {item["name"] for item in desc["components"]} == {
        "source_artifact_id", "median_bias_level", "median_bias_scatter"
    }
    assert {item["parent_id"] for item in desc["relations"]} == {first.id, second.id}


def _bias_result(read_noise: float) -> AlgoResult:
    return AlgoResult(
        kind="bias",
        version="test-read-noise-qa",
        arrays={
            "master": np.zeros((8, 8), dtype=float),
            "per_pixel_bias_scatter": np.full((8, 8), read_noise, dtype=float),
        },
        scalars={"read_noise": read_noise, "n_inputs": 25},
    )


def test_bias_warning_is_retained_as_degraded_without_blocking(tmp_path, monkeypatch):
    task, service = _task(tmp_path)
    monkeypatch.setattr(BiasTask, "algorithm", staticmethod(
        lambda *, raw_inputs, params: _bias_result(5.0)
    ))

    artifact = task.run({})["master_bias"]
    qa = service.adapter.get_qa_bundle(artifact.id)
    assert qa["status"] == "warn"
    assert qa["usability"] == "degraded"
    assert qa["policy_version"] == "2"
    assert "4.5 electron warning ceiling" in qa["rules"][0]["message"]


def test_bias_critical_is_retained_for_diagnostics_and_hard_fails_early(tmp_path, monkeypatch):
    task, service = _task(tmp_path)
    monkeypatch.setattr(BiasTask, "algorithm", staticmethod(
        lambda *, raw_inputs, params: _bias_result(6.5)
    ))

    with pytest.raises(
        RuntimeError,
        match=r"QA hard-fail for master_bias.*read_noise=6.5 electron.*6.0 electron critical",
    ):
        task.run({})

    row = service.select_best(
        kind="master_bias", scope=Scope(zipcode=_Target.zipcode), policy="latest"
    )
    assert row is not None
    qa = service.adapter.get_qa_bundle(int(row["id"]))
    assert qa["status"] == "fail"
    assert qa["usability"] == "unusable"
    assert qa["metrics"]["read_noise"] == 6.5
