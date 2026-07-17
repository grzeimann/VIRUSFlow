from __future__ import annotations

"""
Default mapping from planning kinds to task classes.

This utility lives in the tasks layer (not planning) to avoid introducing a
reverse dependency from planning → tasks. Callers that want to run a plan can
import this to obtain a baseline kind→task mapping.
"""
from typing import Dict, Type

from .base import Task
from .calibs import BiasTask, DarkTask, FlatTask, CmpTask, TraceTask, WaveTask, TwiTask, SciTask


def default_kind_to_task() -> Dict[str, Type[Task]]:
    """Return mapping from planning kinds to Task classes.

    Kinds covered:
    - master_bias → BiasTask
    - master_dark → DarkTask
    - master_flat → FlatTask
    - master_cmp → CmpTask
    - trace → TraceTask
    - wave → WaveTask

    Notes:
    - twilight flat (twi) is not a planned kind here; mapping provided for completeness.
    """
    return {
        "master_bias": BiasTask,
        "master_dark": DarkTask,
        "master_flat": FlatTask,
        "master_cmp": CmpTask,
        "trace": TraceTask,
        "wave": WaveTask,
        # Non-standard/planned kinds that may appear in legacy flows
        "twi": TwiTask,
    }
