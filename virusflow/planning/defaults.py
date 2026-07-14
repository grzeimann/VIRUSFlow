from __future__ import annotations

"""
Default calibration reduction graph specification.

This module defines TaskSpec nodes and Edge dependencies for the core
calibration products and provides a factory to construct a ReductionGraph.

Task classes are not imported here to keep the planning layer independent;
callers (CLI/scheduler) can map kinds to concrete task classes at run time if
needed. For now we store a simple placeholder object in task_cls.
"""

from typing import List, Tuple

from .graph import TaskSpec, Edge
from .targets import TimeCadence, ExposureCountCadence
from .config import PlanningConfig


class _TaskPlaceholder:
    pass


def default_calibration_graph(config: PlanningConfig | None = None) -> Tuple[List[TaskSpec], List[Edge]]:
    """Build default calibration TaskSpec nodes and Edge list, optionally apply overrides.

    Kinds and cadences (initial recommendations):
    - master_bias: TimeCadence(every_days=30, min_n_inputs=25), inputs_raw=["zro"]
    - master_dark: ExposureCountCadence(min_n=20, max_span_days=45), inputs_raw=["drk"]
    - master_flat: ExposureCountCadence(min_n=30, max_span_days=30), inputs_raw=["flt"]
    - master_cmp: TimeCadence(every_days=90, min_n_inputs=1), inputs_artifacts=["master_flat"]
    - trace: derived, inputs_artifacts=["master_flat"], no cadence
    - wave: derived, inputs_artifacts=["master_cmp", "trace"], no cadence
    """
    # Nodes
    bias = TaskSpec(
        kind="master_bias",
        task_cls=_TaskPlaceholder,
        inputs_raw=["zro"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=TimeCadence(every_days=30, min_n_inputs=25),
    )
    dark = TaskSpec(
        kind="master_dark",
        task_cls=_TaskPlaceholder,
        inputs_raw=["drk"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=ExposureCountCadence(min_n=20, max_span_days=45),
    )
    flat = TaskSpec(
        kind="master_flat",
        task_cls=_TaskPlaceholder,
        inputs_raw=["flt"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=ExposureCountCadence(min_n=30, max_span_days=30),
    )
    cmpn = TaskSpec(
        kind="master_cmp",
        task_cls=_TaskPlaceholder,
        inputs_raw=None,
        inputs_artifacts=["master_flat"],
        scope_mode="per_zipcode",
        cadence=TimeCadence(every_days=90, min_n_inputs=1),
    )
    trace = TaskSpec(
        kind="trace",
        task_cls=_TaskPlaceholder,
        inputs_raw=None,
        inputs_artifacts=["master_flat"],
        scope_mode="per_zipcode",
        cadence=None,
    )
    wave = TaskSpec(
        kind="wave",
        task_cls=_TaskPlaceholder,
        inputs_raw=None,
        inputs_artifacts=["master_cmp", "trace"],
        scope_mode="per_zipcode",
        cadence=None,
    )

    nodes = [bias, dark, flat, cmpn, trace, wave]

    # Edges
    edges: List[Edge] = [
        Edge(src=flat, dst=trace, policy="latest_valid", tolerance_days=90),
        Edge(src=cmpn, dst=wave, policy="latest_valid", tolerance_days=90),
    ]

    # Apply external overrides if provided
    if config is not None:
        nodes, edges = config.apply_overrides(nodes, edges)

    return nodes, edges
