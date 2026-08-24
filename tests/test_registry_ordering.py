from __future__ import annotations

from datetime import datetime

from virusflow.artifacts import ArtifactService
from virusflow.artifacts.models import Scope
from virusflow.artifacts.provenance import build_provenance
from virusflow.core.identity import ZipCode
from virusflow.registry import database as db


ZIPCODE = ZipCode("020", "001", "001", "LL", "A")


def _seed_artifact(db_path: str, created_at: datetime | None) -> int:
    artifact_id = db.save_artifact(
        {
            "kind": "master_bias",
            "name": "master_bias",
            "path": "/tmp/master_bias.fits",
            "zipcode": ZIPCODE,
        },
        {
            **build_provenance("test:master_bias", {}),
            "created_at": created_at,
        },
        db_path=db_path,
    )
    return int(artifact_id)


def test_artifact_lookup_orders_created_at_portably_with_nulls_last(tmp_path):
    db_path = str(tmp_path / "registry.sqlite3")
    db.init_db(db_path)

    newer = _seed_artifact(db_path, datetime(2026, 6, 3))
    older = _seed_artifact(db_path, datetime(2026, 6, 1))
    tie_low = _seed_artifact(db_path, datetime(2026, 6, 2))
    tie_high = _seed_artifact(db_path, datetime(2026, 6, 2))
    null_created = _seed_artifact(db_path, None)
    with db.connect(db_path) as connection:
        connection.execute(
            "UPDATE provenance SET created_at=NULL WHERE artifact_id=?",
            (null_created,),
        )

    rows = ArtifactService(db_path).adapter.find(
        kind="master_bias", zipcode=ZIPCODE, at_time=None, limit=None,
    )

    assert [int(row["id"]) for row in rows] == [
        newer, tie_high, tie_low, older, null_created,
    ]
