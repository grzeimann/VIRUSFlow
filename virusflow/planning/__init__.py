from __future__ import annotations

# Public planning interfaces
from .targets import TemporalWindow, Target, CadencePolicy, TimeCadence, ExposureCountCadence
from .graph import TaskSpec, Edge, PlanningReport, ReductionGraph
from .defaults import default_calibration_graph
from .config import (
    NodeConfig,
    PlanningConfig,
    load_planning_config,
    load_planning_config_from_dict,
)
# Optional thin scheduler (planning-first execution assembly)
try:
    from .scheduler import schedule, ScheduledTask  # type: ignore
except Exception:  # pragma: no cover - scheduler is optional for now
    schedule = None  # type: ignore
    ScheduledTask = None  # type: ignore

__all__ = [
    # targets
    "TemporalWindow",
    "Target",
    "CadencePolicy",
    "TimeCadence",
    "ExposureCountCadence",
    # graph
    "TaskSpec",
    "Edge",
    "PlanningReport",
    "ReductionGraph",
    # defaults/config
    "default_calibration_graph",
    "NodeConfig",
    "PlanningConfig",
    "load_planning_config",
    "load_planning_config_from_dict",
    # scheduler (optional)
    "schedule",
    "ScheduledTask",
]
