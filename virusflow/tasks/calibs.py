from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List
from datetime import datetime

from .base import CalibrationTask, Task
from ..core.artifacts import Artifact, CalibrationProduct
from ..core.targets import BiasTarget
from ..algorithms.bias import step_bias


class BiasTask(CalibrationTask):
    """Bias master-frame task scoped by a BiasTarget, using the generic CalibrationTask template."""
    name = "bias"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "zro"
    artifact_name = "master_bias"
    algorithm = step_bias


class DarkTask(Task):
    """Dark master-frame task (stub).

    Reference algorithm: see reference/build_calibration_h5file.py
    - step_drk for master dark construction (uses pixel mask from get_pixelmask)
    - related utilities in reference/fiber_utils.py
    """
    name = "dark"
    version = "v1"

    @classmethod
    def inputs(cls):
        return ["raw_dark", "master_bias"]

    @classmethod
    def outputs(cls):
        return ["master_dark"]

    def run(self, inputs: Dict[str, Artifact]):
        out_path = os.path.join(self.ctx.workdir, "master_dark.fits")
        os.makedirs(self.ctx.workdir, exist_ok=True)
        with open(out_path, "w") as f:
            f.write("SIMULATED MASTER DARK")
        art = CalibrationProduct(
            id=None,
            kind="calibration",
            name="master_dark",
            path=out_path,
            zipcode=None,
        )
        self.save_artifact(art, parent_ids=[])
        return {"master_dark": art}
