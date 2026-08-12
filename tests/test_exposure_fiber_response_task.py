from __future__ import annotations

from types import SimpleNamespace

import numpy as np

import virusflow.tasks.calibs as calibs
from virusflow.core.algo_result import AlgoResult
from virusflow.core.identity import ZipCode
from virusflow.tasks.base import TaskContext


def _result(amplifier_count: int, fibers_per_amplifier: int = 1) -> AlgoResult:
    fiber_count = amplifier_count * fibers_per_amplifier
    samples = 4
    return AlgoResult(
        kind="exposure_fiber_response",
        version="test",
        arrays={
            "raw_ratio": np.ones((fiber_count, samples)),
            "normalization": np.ones((fiber_count, samples)),
            "valid_mask": np.ones((fiber_count, samples), dtype=np.uint8),
            "common_ldls": np.ones((fiber_count, samples)),
            "common_twilight": np.ones((fiber_count, samples)),
            "within_amplifier_response": np.ones((fiber_count, samples)),
            "amplifier_response": np.ones((amplifier_count, samples)),
            "amplifier_scalar": np.ones(amplifier_count),
            "amplifier_common_response": np.ones((amplifier_count, samples)),
            "fiber_amplifier_index": np.repeat(np.arange(amplifier_count), fibers_per_amplifier),
            "wavelength": np.tile(np.arange(samples, dtype=float), (fiber_count, 1)),
        },
        scalars={
            "valid_fraction": 1.0,
            "amplifier_count": amplifier_count,
            "amplifier_reference_scalar": 1.0,
        },
    )


def _row(identifier: int, zipcode: ZipCode, value: float) -> dict:
    return {"id": identifier, "amp_key": zipcode.key(), "value": value}


def _run_task(monkeypatch, *, scheduled, planned, requested):
    rows = {
        int(row["id"]): row
        for items in planned.values() for row in items
    }
    for values in scheduled.values():
        for artifact in values.values():
            rows[int(artifact.id)] = artifact.row
    select_calls = []
    captured = {}

    class FakeService:
        def __init__(self, _path):
            self.adapter = self

        def get_row(self, identifier):
            return rows[int(identifier)]

        def load_component(self, row, _component):
            return {"data": np.full((1, 4), row["value"], dtype=float)}

        def get_scientific_metadata(self, _identifier):
            return {}

        def select_best(self, **kwargs):
            select_calls.append(kwargs)
            raise AssertionError("ExposureFiberResponseTask must not query the registry")

    def fake_fit(ldls, twilight, wavelength, *, science_spectrum=None, **_kwargs):
        captured["ldls"] = [float(item[0, 0]) for item in ldls]
        captured["twilight"] = [float(item[0, 0]) for item in twilight]
        captured["wavelength"] = [float(item[0, 0]) for item in wavelength]
        captured["science"] = science_spectrum
        return _result(len(ldls))

    monkeypatch.setattr(calibs, "ArtifactService", FakeService)
    monkeypatch.setattr(
        calibs, "_planned_parent_rows", lambda _service, _target, kind: planned.get(kind, [])
    )
    monkeypatch.setattr(calibs, "fit_exposure_fiber_response", fake_fit)
    target = SimpleNamespace(
        zipcode=None,
        start_date=None,
        end_date=None,
        group_metadata={"amplifier_keys": [zipcode.key() for zipcode in requested]},
    )
    task = calibs.ExposureFiberResponseTask(TaskContext("unused", "unused"), target=target)

    def publish(result, parent_ids, **_kwargs):
        captured["meta"] = dict(result.meta)
        captured["parent_ids"] = list(parent_ids)
        return SimpleNamespace(id=99)

    task._publish = publish
    task.run(scheduled)
    return captured, select_calls, task


def test_exposure_response_uses_scheduled_rows_before_planner_cache_and_excludes_missing(monkeypatch):
    first = ZipCode("020", "001", "001", "LL", "A")
    second = ZipCode("020", "001", "001", "LU", "A")
    missing = ZipCode("020", "001", "001", "RU", "A")
    kinds = (
        "extracted_master_ldls_spectrum",
        "extracted_master_twilight_spectrum",
        "wavelength_map",
    )
    scheduled = {
        kind: {kind: SimpleNamespace(id=identifier, row=_row(identifier, first, value))}
        for kind, identifier, value in zip(kinds, (1, 2, 3), (10.0, 20.0, 30.0))
    }
    planned = {
        kind: [_row(10 + index, first, 100.0 + index), _row(20 + index, second, 40.0 + index)]
        for index, kind in enumerate(kinds)
    }

    captured, select_calls, task = _run_task(
        monkeypatch, scheduled=scheduled, planned=planned, requested=(first, second, missing)
    )

    assert captured["ldls"] == [10.0, 40.0]
    assert captured["twilight"] == [20.0, 41.0]
    assert captured["wavelength"] == [30.0, 42.0]
    assert captured["science"] is None
    assert select_calls == []
    assert captured["meta"]["excluded_amplifier_keys"] == [missing.key()]


def test_exposure_response_science_validation_is_all_or_nothing_without_registry_fallback(monkeypatch):
    first = ZipCode("020", "001", "001", "LL", "A")
    second = ZipCode("020", "001", "001", "LU", "A")
    kinds = (
        "extracted_master_ldls_spectrum",
        "extracted_master_twilight_spectrum",
        "wavelength_map",
    )
    scheduled = {}
    for index, kind in enumerate(kinds, start=1):
        scheduled[f"{kind}-first"] = {
            kind: SimpleNamespace(id=10 * index + 1, row=_row(10 * index + 1, first, 1.0))
        }
        scheduled[f"{kind}-second"] = {
            kind: SimpleNamespace(id=10 * index + 2, row=_row(10 * index + 2, second, 1.0))
        }
    scheduled["science"] = {
        "extracted_master_sci_spectrum": SimpleNamespace(id=99, row=_row(99, first, 5.0))
    }

    captured, select_calls, _ = _run_task(
        monkeypatch, scheduled=scheduled, planned={}, requested=(first, second)
    )

    assert captured["science"] is None
    assert select_calls == []
