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

    Canonical kinds and cadences:
    - master_bias: TimeCadence(every_days=30, min_n_inputs=25), inputs_raw=["zro"]
    - master_dark: ExposureCountCadence(min_n=20, max_span_days=45), inputs_raw=["drk"]
    - master_ldls: ExposureCountCadence(min_n=30, max_span_days=30), inputs_raw=["flt"]
    - master_arc: TimeCadence(every_days=90, min_n_inputs=1), inputs_raw=["cmp"]
    - master_twilight: ExposureCountCadence(min_n=1, max_span_days=30), inputs_raw=["twi"]
    - trace_map: derived from master_ldls
    - wavelength_map: derived from master_arc + trace_map

    Config surface:
    - To declare preprocessing prerequisites for master_flat (e.g., master_bias/master_dark),
      provide nodes.master_flat.params.preprocess_requires: ["master_bias", "master_dark"] in the planning YAML.
      When set, edges from each listed kind to master_flat are added by default.
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
        kind="master_ldls",
        task_cls=_TaskPlaceholder,
        inputs_raw=["flt"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=ExposureCountCadence(min_n=30, max_span_days=30),
    )
    cmpn = TaskSpec(
        kind="master_arc",
        task_cls=_TaskPlaceholder,
        inputs_raw=["cmp"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=TimeCadence(every_days=90, min_n_inputs=1),
    )
    twilight = TaskSpec(
        kind="master_twilight",
        task_cls=_TaskPlaceholder,
        inputs_raw=["twi"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=ExposureCountCadence(min_n=1, max_span_days=30),
    )
    trace = TaskSpec(
        kind="trace_map",
        task_cls=_TaskPlaceholder,
        inputs_raw=None,
        inputs_artifacts=["master_ldls"],
        scope_mode="per_zipcode",
        cadence=None,
    )
    wave = TaskSpec(
        kind="wavelength_map",
        task_cls=_TaskPlaceholder,
        inputs_raw=None,
        inputs_artifacts=["master_arc", "trace_map"],
        scope_mode="per_zipcode",
        cadence=None,
    )

    nodes = [bias, dark, flat, cmpn, twilight, trace, wave]

    # Edges (base)
    edges: List[Edge] = [
        Edge(src=flat, dst=trace, policy="latest_valid", tolerance_days=90),
        Edge(src=cmpn, dst=wave, policy="latest_valid", tolerance_days=90),
        Edge(src=trace, dst=wave, policy="latest_valid", tolerance_days=90),
    ]

    # Optional preprocessing dependencies for master_flat via config params
    if config is not None:
        try:
            ncfg = config.nodes.get("master_flat") if hasattr(config, "nodes") else None
            params = getattr(ncfg, "params", None)
            reqs = []
            if isinstance(params, dict):
                val = params.get("preprocess_requires")
                if isinstance(val, (list, tuple)):
                    reqs = [str(x).strip() for x in val if str(x).strip()]
            if reqs:
                by_kind = {n.kind: n for n in nodes}
                for rk in reqs:
                    src_n = by_kind.get(rk)
                    if src_n is None:
                        continue
                    # Avoid duplicates
                    exists = any((e.src.kind == rk and e.dst.kind == "master_flat") for e in edges)
                    if not exists:
                        edges.append(Edge(src=src_n, dst=flat, policy="latest_valid", tolerance_days=90))
        except Exception:
            pass

    # Apply external overrides if provided (edges replaced only if config.edges is non-empty)
    if config is not None:
        nodes, edges = config.apply_overrides(nodes, edges)

    return nodes, edges
