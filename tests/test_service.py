from __future__ import annotations

from pathlib import Path
import numpy as np

from virusflow.artifacts.service import ArtifactService
from virusflow.artifacts.models import Artifact, StorageRef, Scope
from virusflow.artifacts.io_fits import write_array_fits
from virusflow.registry import database as db


def test_service_register_describe_and_select_best(tmp_path: Path):
    # Initialize a temp DB
    db_path = str(tmp_path / "test.sqlite3")
    db.init_db(db_path=db_path)

    # Create a simple FITS artifact file
    out = tmp_path / "master_flat_test.fits"
    data = np.ones((8, 8), dtype=float)
    write_array_fits(
        str(out),
        data=data,
        n_inputs=2,
        algo_version="unit-1.0",
        sidecar={"kind": "master_flat", "role": "calibration"},
    )

    # Register via service
    svc = ArtifactService(db_path)
    art = Artifact(
        id=None,
        kind="master_flat",
        role="calibration",
        payload_type="array",
        storage_format="fits",
        storage=StorageRef(uri=str(out), storage_format="fits", backend="fs"),
        scope=Scope(zipcode=None),
        metadata={},
        provenance=None,
    )
    art_id = svc.register(art)
    assert isinstance(art_id, int) and art_id > 0

    # Describe returns a dict with basic fields
    desc = svc.describe(art_id)
    assert desc["id"] == art_id
    assert desc["kind"] == "master_flat"
    assert desc["payload_type"] == "array"
    assert desc["storage_format"] == "fits"
    assert Path(desc["path"]).exists()

    # select_best should find it for a None-zipcode scope
    row = svc.select_best(kind="master_flat", scope=Scope(zipcode=None))
    assert row is not None
    assert int(row.get("id")) == art_id
