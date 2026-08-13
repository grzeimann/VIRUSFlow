"""
Default mapping from planning kinds to task classes.

This utility lives in the tasks layer (not planning) to avoid introducing a
reverse dependency from planning → tasks. Callers that want to run a plan can
import this to obtain a baseline kind→task mapping.
"""

from __future__ import annotations
from typing import Dict, Type

from .base import Task
from .calibs import (
    ExposureFiberResponseTask, ArcTask, BiasTask,
    CdTask, DarkTask,
    ExtractedMasterLdlsSpectrumTask, ExtractedMasterSciSpectrumTask,
    ExtractedMasterTwilightSpectrumTask,
    FlatTask, HgTask, MasterSciTask, TraceTask, TwiTask, WaveTask,
)


def default_kind_to_task() -> Dict[str, Type[Task]]:
    """Return mapping from planning kinds to Task classes.

    Only canonical planning kinds are accepted. Historical Artifact aliases are
    resolved when old registry rows are read, never when a new graph is built.
    """
    return {
        "master_bias": BiasTask,
        "master_dark": DarkTask,
        "master_ldls": FlatTask,
        "master_hg": HgTask,
        "master_cd": CdTask,
        "master_arc": ArcTask,
        "master_twilight": TwiTask,
        "master_sci": MasterSciTask,
        "extracted_master_ldls_spectrum": ExtractedMasterLdlsSpectrumTask,
        "extracted_master_twilight_spectrum": ExtractedMasterTwilightSpectrumTask,
        "extracted_master_sci_spectrum": ExtractedMasterSciSpectrumTask,
        "exposure_fiber_response": ExposureFiberResponseTask,
        "trace_map": TraceTask,
        "wavelength_map": WaveTask,
    }
