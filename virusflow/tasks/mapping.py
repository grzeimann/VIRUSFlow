from __future__ import annotations

"""
Default mapping from planning kinds to task classes.

This utility lives in the tasks layer (not planning) to avoid introducing a
reverse dependency from planning → tasks. Callers that want to run a plan can
import this to obtain a baseline kind→task mapping.
"""
from typing import Dict, Type

from .base import Task
from .calibs import BiasTask, DarkTask, FlatTask, CmpTask, TraceTask, WaveTask, TwiTask


def default_kind_to_task() -> Dict[str, Type[Task]]:
    """Return mapping from planning kinds to Task classes.

    Only canonical planning kinds are accepted. Historical Artifact aliases are
    resolved when old registry rows are read, never when a new graph is built.
    """
    return {
        "master_bias": BiasTask,
        "master_dark": DarkTask,
        "master_ldls": FlatTask,
        "master_arc": CmpTask,
        "master_twilight": TwiTask,
        "trace_map": TraceTask,
        "wavelength_map": WaveTask,
    }
