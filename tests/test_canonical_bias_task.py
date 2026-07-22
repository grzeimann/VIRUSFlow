from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from virusflow.analytics.studies.bias import BiasStabilityParams, BiasStabilityStudy
from virusflow.artifacts import ArtifactService
from virusflow.core.identity import ZipCode
from virusflow.io import RawFrameData
from virusflow.registry.database import init_db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.calibs import BiasTask, SciTask


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


def test_configured_hard_qa_failure_is_not_swallowed(tmp_path, monkeypatch):
    yaml_path = tmp_path / "qa.yml"
    yaml_path.write_text(
        "version: 1\ndefaults: {policy: soft}\nkinds:\n  master_sci:\n    policy: hard\n"
        "    metrics: {p95: {from: 'reduce.percentile(meta.component.data,95)', default: 0.0}}\n"
        "    checks: [{id: signal, where: 'p95 > 0.0', severity: fail_if_false}]\n"
    )
    monkeypatch.setenv("VF_QA_YAML", str(yaml_path))
    task, service = _task(tmp_path, SciTask, value=0.0)
    with pytest.raises(RuntimeError, match="QA hard-fail"):
        task.run({})
    rows = service.adapter.find(kind="master_sci", zipcode=_Target.zipcode, at_time=None, limit=None)
    assert len(rows) == 1
    assert service.adapter.get_diagnostics(int(rows[0]["id"]))["status"] == "fail"


def test_bias_stability_uses_named_components_and_records_all_source_lineage(tmp_path):
    task, service = _task(tmp_path)
    first = task.run({})["master_bias"]
    second = task.run({})["master_bias"]
    output = BiasStabilityStudy(service).run(
        BiasStabilityParams(tmp_path / "analytics", _Target.zipcode)
    )
    assert output["source_ids"] == [first.id, second.id]
    desc = service.describe(output["artifact_id"])
    assert {item["name"] for item in desc["components"]} == {
        "source_artifact_id", "median_bias_level", "median_bias_scatter"
    }
    assert {item["parent_id"] for item in desc["relations"]} == {first.id, second.id}
