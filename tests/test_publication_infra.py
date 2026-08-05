from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pytest

from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.artifacts.models import Scope
from virusflow.artifacts.service import ArtifactService
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.registry.database import init_db


def test_master_dark_publication_requires_product_local_scaling_state(tmp_path: Path):
    db_path = str(tmp_path / "vf.sqlite")
    init_db(db_path)
    publisher = DefaultPublicationService(
        svc=ArtifactService(db_path),
        policy=DefaultPersistencePolicy(),
        base_dir=str(tmp_path),
    )
    data = np.ones((2, 3), dtype=float)
    request = ArtifactRequest(
        kind="master_dark",
        components={
            "master_dark": LogicalComponent("master_dark", "array2d", data),
            "dark_pixel_mask": LogicalComponent(
                "dark_pixel_mask", "array2d", np.zeros_like(data, dtype=np.uint8)
            ),
        },
        scope=Scope(zipcode=None),
    )
    context = PublicationContext("dark", "v3", "dark", "dark-1.1", {}, [], {})

    with pytest.raises(
        ValueError,
        match="reference_exposure_time_seconds, bias_convention",
    ):
        publisher.publish([request], context)


def test_publication_roundtrip_master_bias(tmp_path: Path):
    # Arrange: service, policy, publication
    db_path = str(tmp_path / "vf.sqlite")
    init_db(db_path)
    svc = ArtifactService(db_path)
    policy = DefaultPersistencePolicy()
    pub = DefaultPublicationService(svc=svc, policy=policy, base_dir=str(tmp_path))

    # Build a simple array component
    arr = np.ones((5, 7), dtype=float)
    req = ArtifactRequest(
        kind="master_bias",
        components={
            "master": LogicalComponent("master", "array2d", arr),
            "per_pixel_bias_scatter": LogicalComponent(
                "per_pixel_bias_scatter", "array2d", np.zeros_like(arr)
            ),
        },
        summaries={"read_noise": 1.23},
        metadata={"n_inputs": 3, "algo_version": "bias-1.0"},
        scope=Scope(zipcode=None),
        parents=[],
    )

    ctx = PublicationContext(
        task_name="bias", task_version="v2",
        algorithm_name="bias", algorithm_version="bias-1.0",
        parameters={"foo": "bar"}, parent_ids=[], timings={}
    )

    # Act
    arts = pub.publish([req], ctx)

    # Assert basic registration and storage
    assert isinstance(arts, list) and len(arts) == 1
    art = arts[0]
    assert art.id is not None and int(art.id) > 0
    assert art.storage and art.storage.uri

    out = Path(art.storage.uri)
    assert out.exists(), f"Expected output file to exist: {out}"
    side = Path(str(out) + ".json")
    assert side.exists(), f"Expected sidecar to exist: {side}"

    sc = json.loads(side.read_text())
    assert sc.get("kind") == "master_bias"
    assert sc.get("payload_type") == "array"
    assert sc.get("storage_format") == "fits"
    assert sc.get("shape") == [5, 7]
    assert "read_noise" in sc

    # Verify describe surfaces logical summaries independent of storage format
    desc = svc.describe(art.id)
    assert desc["id"] == art.id
    assert desc["kind"] == "master_bias"
    assert desc["summary"].get("payload_type") == "array"
    assert desc["summary"].get("storage_format") == "fits"
    # The describe() summary should include keys from sidecar (policy-owned)
    assert "shape" in desc["summary"]
    # Ensure publication did not attempt to evaluate QA (no status yet)
    qa = desc.get("qa")
    # RegistryAdapter.get_qa_results returns None for new artifacts
    assert qa is None or qa.get("status") in (None, "",)  # be tolerant across adapters
