from __future__ import annotations

import numpy as np
from pathlib import Path

from virusflow.registry.database import init_db
from virusflow.artifacts.service import ArtifactService
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.artifacts.models import Scope
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.persistence.policy import DefaultPersistencePolicy


def test_publication_records_publication_context_in_provenance(tmp_path: Path, monkeypatch):
    # Arrange: minimal DB and services
    db_path = str(tmp_path / "vf.sqlite")
    init_db(db_path)
    svc = ArtifactService(db_path)
    policy = DefaultPersistencePolicy()
    pub = DefaultPublicationService(svc=svc, policy=policy, base_dir=str(tmp_path))

    # Build a simple multi-component-capable request (single array component used here)
    arr = np.zeros((3, 5), dtype=float)
    comp = LogicalComponent(name="master", model_type="array2d", value=arr)
    req = ArtifactRequest(
        kind="master_bias",
        components={"master": comp},
        summaries={"readnoise": 2.5},
        metadata={"n_inputs": 3, "algo_version": "bias-1.0"},
        scope=Scope(zipcode=None),
        parents=[42],
        labels=["calibration", "bias"],
    )

    ctx = PublicationContext(
        task_name="bias",
        task_version="v1",
        algorithm_name="algorithms.bias.step_bias",
        algorithm_version="bias-1.0",
        parameters={"foo": "bar"},
        parent_ids=[42],
        timings={"resolve": 0.01, "execute": 0.5},
    )

    captured = {}

    def fake_register(artifact):
        # Capture the artifact object passed to registration to inspect provenance fields
        captured["artifact"] = artifact
        # Return a fake id
        return 123

    monkeypatch.setattr(svc, "register", fake_register)

    # Act
    arts = pub.publish([req], ctx)

    # Assert
    assert isinstance(arts, list) and len(arts) == 1
    art = captured.get("artifact")
    assert art is not None, "Expected ArtifactService.register to be called with an Artifact"
    # Provenance basics
    assert art.provenance is not None
    assert art.provenance.algorithm == f"{ctx.algorithm_name}:{ctx.algorithm_version}"
    # PublicationContext propagated into provenance params
    p = dict(art.provenance.params or {})
    assert isinstance(p.get("task"), dict) and p["task"].get("name") == ctx.task_name and p["task"].get("version") == ctx.task_version
    assert isinstance(p.get("algorithm"), dict) and p["algorithm"].get("name") == ctx.algorithm_name and p["algorithm"].get("version") == ctx.algorithm_version
    assert isinstance(p.get("timings"), dict) and "execute" in p["timings"]
    # Parent ids are attached at provenance level (not only in params)
    assert list(art.provenance.parents) == ctx.parent_ids
    # Publication must not evaluate QA during publish
    # (No call is made to svc.diagnostics here; the absence of exceptions suffices.)
