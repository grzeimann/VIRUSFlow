from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from virusflow.artifacts import ArtifactService
from virusflow.core.identity import ZipCode
from virusflow.io import RawFrameData
from virusflow.registry.database import init_db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.calibs import DarkTask


ZIPCODE = ZipCode("020", "001", "001", "LL", "A")


class _Target:
    zipcode = ZIPCODE
    start_date = "20260601"
    end_date = "20260701"
    start_dt = datetime(2026, 6, 1)
    end_dt = datetime(2026, 7, 1)


class _Loader:
    def __init__(self, exptimes):
        self.exptimes = dict(exptimes)

    def load(self, path, tar_member=None):
        exptime = self.exptimes[path]
        header = {
            "GAIN": 1.0,
            "RDNOISE": 3.0,
            "CCDPOS": "L",
            "CCDHALF": "L",
            "EXPTIME": exptime,
        }
        return RawFrameData(
            np.full((8, 8), exptime, dtype=float), header, path, tar_member
        )


def _task(tmp_path, exptimes):
    database = str(tmp_path / "registry.sqlite3")
    init_db(database)
    paths = [f"dark-{index}" for index in range(len(exptimes))]
    loader = _Loader(dict(zip(paths, exptimes)))
    task = DarkTask(
        TaskContext(database, str(tmp_path / "products"), {"raw_frame_loader": loader}),
        target=_Target(),
    )
    task.query_inputs = lambda: (
        [{"path": path, "tar_member": None} for path in paths], []
    )
    return task, ArtifactService(database)


def test_dark_task_accepts_nominal_360_variations_and_retains_measured_exptimes(tmp_path):
    measured = [360.00007, 360.00021, 360.00046]
    task, service = _task(tmp_path, measured)

    artifact = task.run({})["master_dark"]

    summary = service.describe(artifact.id)["summary"]
    assert summary["reference_exposure_time_seconds"] == pytest.approx(np.median(measured))
    assert summary["nominal_dark_exptime_seconds"] == 360
    assert summary["algorithm_metadata"]["input_exptime_seconds"] == measured


def test_dark_task_rejects_mixed_nominal_exposure_classes_defensively(tmp_path):
    task, _service = _task(tmp_path, [15.00012, 360.00045])

    with pytest.raises(
        RuntimeError,
        match=r"nominal classes=\[15, 360\].*EXPTIME range",
    ):
        task.run({})


def test_dark_task_preserves_existing_equal_exptime_behavior(tmp_path):
    task, service = _task(tmp_path, [360.0, 360.0])

    artifact = task.run({})["master_dark"]

    assert service.describe(artifact.id)["summary"]["nominal_dark_exptime_seconds"] == 360
