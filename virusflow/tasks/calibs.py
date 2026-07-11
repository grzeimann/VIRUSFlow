from __future__ import annotations

from .base import CalibrationTask
from ..algorithms.bias import step_bias
from ..algorithms.dark import step_dark
from ..algorithms.flat import step_flt


class BiasTask(CalibrationTask):
    """Bias master-frame task scoped by a BiasTarget, using the generic CalibrationTask template."""
    name = "bias"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "zro"
    artifact_name = "master_bias"
    algorithm = step_bias


class DarkTask(CalibrationTask):
    """Dark master-frame task scoped by a target, using the generic CalibrationTask template.

    Differences from BiasTask:
    - Uses dark frames (frame_type='drk')
    - Produces 'master_dark' artifact via algorithms.dark.step_dark
    """
    name = "dark"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "drk"
    artifact_name = "master_dark"
    algorithm = step_dark


class FlatTask(CalibrationTask):
    """Flat master-frame task using generic CalibrationTask.

    - Uses flat frames (frame_type='flt')
    - Produces 'master_flat' artifact via algorithms.flat.step_flt
    """
    name = "flat"
    version = "v1"

    # CalibrationTask configuration
    frame_type = "flt"
    artifact_name = "master_flat"
    algorithm = step_flt
