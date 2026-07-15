from __future__ import annotations

"""
Tests for analytics storage-agnostic payload loading (Section 7).

Verifies that analytics path uses ArtifactService logical accessors rather than
hard-coding storage formats/serializers.
"""
from typing import Dict

from virusflow.artifacts.service import ArtifactService
from virusflow.artifacts.serializers import Serializer
from virusflow.registry.database import init_db


def test_queries_load_array_uses_service_load_payload(tmp_path):
    # Arrange: temp DB and service
    db_path = str(tmp_path / "vf.sqlite")
    init_db(db_path)
    svc = ArtifactService(db_path)

    # Register a mock serializer backend different from FITS to prove decoupling
    def _mock_describe(path: str) -> Dict:
        # Should not be called by load path in this test
        return {"payload_type": "array", "storage_format": "mockz", "shape": [1]}

    def _mock_load(path: str) -> Dict:
        # Ignore path; return a fixed small payload
        return {"data": [42.0], "header": {"MOCK": True}}

    svc.serializers.register("array", "mockz", Serializer(describe=_mock_describe, load=_mock_load))

    # Build a fake row that declares payload_type/storage_format to avoid describe()
    row = {
        "id": 123,
        "kind": "trace",
        "path": str(tmp_path / "nonexistent.mockz"),
        "payload_type": "array",
        "storage_format": "mockz",
    }

    # Act: use analytics.queries helper which must delegate to ArtifactService.load_payload
    from virusflow.analytics.queries import load_array

    payload = load_array(svc=svc, row=row)

    # Assert: payload comes from our mock serializer, independent of FITS
    assert isinstance(payload, dict)
    assert payload.get("data") == [42.0]
    assert payload.get("header", {}).get("MOCK") is True
