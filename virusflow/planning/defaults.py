"""
Default calibration reduction graph specification.

This module defines TaskSpec nodes and Edge dependencies for the core
calibration products and provides a factory to construct a ReductionGraph.

Task classes are not imported here to keep the planning layer independent;
callers (CLI/scheduler) can map kinds to concrete task classes at run time if
needed. For now we store a simple placeholder object in task_cls.
"""

from __future__ import annotations

from typing import List, Tuple

from .graph import TaskSpec, Edge
from .targets import PurposeCadence
from .config import PlanningConfig


class _TaskPlaceholder:
    pass


def default_calibration_graph(config: PlanningConfig | None = None) -> Tuple[List[TaskSpec], List[Edge]]:
    """Build default calibration TaskSpec nodes and Edge list, optionally apply overrides.

    Canonical kinds and cadences:
    - master_bias: nightly, all available frames
    - master_dark: calendar-month groups (weekly is configurable)
    - master_ldls: isolated <=3-hour groups with at least three exposures
    - master_hg/master_cd: separate isolated <=3-hour groups, paired into master_arc
    - master_twilight: weekly groups
    - master_sci: eligible >300 s science in monthly groups, subject to sufficiency
    - trace_map: derived from master_ldls
    - wavelength_map: derived from master_arc + trace_map
    - extracted_master_sci_spectrum: derived from master_sci + trace_map
    - fiber_wavelength_spectral_mask: derived from extracted spectra + wavelength_map
    - master_bias: hard QA gate for every other raw calibration branch

    Planning YAML may override canonical node fields and may replace the edge
    list. Dependencies have one representation: explicit edges.
    """
    # Nodes
    bias = TaskSpec(
        kind="master_bias",
        task_cls=_TaskPlaceholder,
        inputs_raw=["zro"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=PurposeCadence("nightly", minimum_exposures=1),
    )
    dark = TaskSpec(
        kind="master_dark",
        task_cls=_TaskPlaceholder,
        inputs_raw=["drk"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=PurposeCadence("monthly", minimum_exposures=1),
    )
    flat = TaskSpec(
        kind="master_ldls",
        task_cls=_TaskPlaceholder,
        inputs_raw=["flt"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=PurposeCadence("isolated", maximum_span_hours=3, minimum_exposures=3),
    )
    hg = TaskSpec(
        kind="master_hg", task_cls=_TaskPlaceholder, inputs_raw=["cmp", "hg"],
        inputs_artifacts=None, scope_mode="per_zipcode",
        cadence=PurposeCadence("isolated", maximum_span_hours=3, minimum_exposures=1),
    )
    cd = TaskSpec(
        kind="master_cd", task_cls=_TaskPlaceholder, inputs_raw=["cmp", "cd"],
        inputs_artifacts=None, scope_mode="per_zipcode",
        cadence=PurposeCadence("isolated", maximum_span_hours=3, minimum_exposures=1),
    )
    arc = TaskSpec(
        kind="master_arc",
        task_cls=_TaskPlaceholder,
        inputs_raw=None,
        inputs_artifacts=["master_hg", "master_cd"],
        scope_mode="per_zipcode",
        cadence=PurposeCadence("paired", maximum_pair_separation_hours=3.0),
    )
    twilight = TaskSpec(
        kind="master_twilight",
        task_cls=_TaskPlaceholder,
        inputs_raw=["twi"],
        inputs_artifacts=None,
        scope_mode="per_zipcode",
        cadence=PurposeCadence("weekly", minimum_exposures=1),
    )
    master_sci = TaskSpec(
        kind="master_sci", task_cls=_TaskPlaceholder, inputs_raw=["sci"],
        inputs_artifacts=None, scope_mode="per_zipcode",
        cadence=PurposeCadence(
            "monthly", minimum_exposure_seconds=300.0, minimum_exposures=3,
            minimum_total_exposure_seconds=1800.0,
        ),
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
    master_sci_spectrum = TaskSpec(
        kind="extracted_master_sci_spectrum",
        task_cls=_TaskPlaceholder,
        inputs_raw=None,
        inputs_artifacts=["master_sci", "trace_map"],
        scope_mode="per_zipcode",
        cadence=None,
    )
    master_sci_mask = TaskSpec(
        kind="fiber_wavelength_spectral_mask",
        task_cls=_TaskPlaceholder,
        inputs_raw=None,
        inputs_artifacts=["extracted_master_sci_spectrum", "wavelength_map"],
        scope_mode="per_zipcode",
        cadence=None,
    )

    nodes = [
        bias, dark, flat, hg, cd, arc, twilight, master_sci, trace, wave,
        master_sci_spectrum, master_sci_mask,
    ]

    # Edges (base)
    edges: List[Edge] = [
        # These are execution/QA gates, not scientific derivation relations.
        # A critical nightly read-noise result is retained as diagnostic
        # evidence, then blocks all other calibration branches for that
        # amplifier before trace or wavelength fitting can run.
        Edge(src=bias, dst=dark, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=flat, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=hg, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=cd, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=twilight, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=master_sci, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=arc, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=trace, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=wave, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=master_sci_spectrum, policy="qa_gate", tolerance_days=1),
        Edge(src=bias, dst=master_sci_mask, policy="qa_gate", tolerance_days=1),
        Edge(src=flat, dst=trace, policy="latest_valid", tolerance_days=90),
        Edge(src=hg, dst=arc, policy="nearest_valid", tolerance_days=1),
        Edge(src=cd, dst=arc, policy="nearest_valid", tolerance_days=1),
        Edge(src=arc, dst=wave, policy="latest_valid", tolerance_days=90),
        Edge(src=trace, dst=wave, policy="latest_valid", tolerance_days=90),
        Edge(src=master_sci, dst=master_sci_spectrum, policy="exact_parent_group", tolerance_days=0),
        Edge(src=trace, dst=master_sci_spectrum, policy="latest_valid", tolerance_days=90),
        Edge(src=master_sci_spectrum, dst=master_sci_mask, policy="exact_parent_group", tolerance_days=0),
        Edge(src=wave, dst=master_sci_mask, policy="latest_valid", tolerance_days=90),
    ]

    # Apply external overrides if provided (edges replaced only if config.edges is non-empty)
    if config is not None:
        nodes, edges = config.apply_overrides(nodes, edges)

    return nodes, edges
