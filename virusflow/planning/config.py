"""
YAML-driven configuration for planning rules (nodes, cadences, and edges).

This module parses an external YAML mapping to override the default planning
rules without changing code. It intentionally avoids importing `tasks/*` to
preserve the planning layer's modularity; callers can later inject concrete
`task_cls` objects after applying config overrides.

Example YAML schema (see docs/planning_config.md for details):

version: 1
nodes:
  master_bias:
    enabled: true
    scope_mode: per_zipcode
    inputs_raw: ["zro"]
    cadence:
      type: time
      every_days: 30
      min_n_inputs: 25
  master_dark:
    cadence:
      type: exposure_count
      min_n: 20
      max_span_days: 45
edges:
  - src: master_ldls
    dst: trace_map
    policy: latest_valid
    tolerance_days: 90
  - src: master_arc
    dst: wavelength_map
    policy: latest_valid
    tolerance_days: 90

"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is an optional runtime dep but present in CLI env
    yaml = None  # type: ignore

from .targets import TimeCadence, ExposureCountCadence, CadencePolicy
from .graph import TaskSpec, Edge


SUPPORTED_CALIBRATION_KINDS = frozenset({
    "master_bias",
    "master_dark",
    "master_ldls",
    "master_arc",
    "master_twilight",
    "trace_map",
    "wavelength_map",
})


@dataclass(frozen=True)
class NodeConfig:
    enabled: bool = True
    scope_mode: Optional[str] = None
    inputs_raw: Optional[Sequence[str]] = None
    inputs_artifacts: Optional[Sequence[str]] = None
    params: Optional[Dict[str, Any]] = None
    cadence: Optional[CadencePolicy] = None  # concrete policy instance built from YAML


@dataclass(frozen=True)
class PlanningConfig:
    version: int = 1
    nworkers: Optional[int] = None
    progress: bool = True
    progress_mode: str = "auto"
    progress_interval: float = 30.0
    progress_path: Optional[str] = None
    max_retries: int = 0
    nodes: Dict[str, NodeConfig] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)

    def apply_overrides(self, base_nodes: Sequence[TaskSpec], base_edges: Sequence[Edge]) -> Tuple[List[TaskSpec], List[Edge]]:
        """Apply YAML-driven overrides to base TaskSpec/Edge definitions.

        - Disables nodes when configured with enabled: false (they will be removed).
        - Overrides scope_mode, inputs, params_schema (from params), and cadence.
        - Merges/overrides edges: if any edges are provided in config, they fully
          replace the base edges; otherwise, base edges are kept.
        """
        # Index by kind for quick lookup
        base_by_kind: Dict[str, TaskSpec] = {n.kind: n for n in base_nodes}
        out_nodes: List[TaskSpec] = []
        for kind, n in base_by_kind.items():
            nc = self.nodes.get(kind)
            if nc is None:
                out_nodes.append(n)
                continue
            if nc.enabled is False:
                continue  # drop this node
            # Build updated TaskSpec while preserving task_cls and other unspecified fields
            new_n = n
            if nc.scope_mode is not None:
                new_n = replace(new_n, scope_mode=str(nc.scope_mode))
            if nc.inputs_raw is not None:
                new_n = replace(new_n, inputs_raw=list(nc.inputs_raw))
            if nc.inputs_artifacts is not None:
                new_n = replace(new_n, inputs_artifacts=list(nc.inputs_artifacts))
            if nc.params is not None:
                new_n = replace(new_n, params_schema=dict(nc.params))
            if nc.cadence is not None:
                new_n = replace(new_n, cadence=nc.cadence)
            out_nodes.append(new_n)
        # Edges: replace if any provided, else keep base
        out_edges: List[Edge]
        if self.edges:
            out_edges = list(self.edges)
        else:
            out_edges = list(base_edges)
        return out_nodes, out_edges


# -----------------
# YAML parsing
# -----------------

def _parse_cadence(d: Mapping[str, Any] | None) -> Optional[CadencePolicy]:
    if not d:
        return None
    t = str(d.get("type", "")).strip().lower()
    if t == "time":
        every = int(d.get("every_days", 0) or 0)
        if every <= 0:
            raise ValueError("time cadence requires positive every_days")
        min_n = int(d.get("min_n_inputs", 1) or 1)
        return TimeCadence(every_days=every, min_n_inputs=min_n)
    if t == "exposure_count":
        min_n = int(d.get("min_n", 0) or 0)
        span = int(d.get("max_span_days", 0) or 0)
        if min_n <= 0 or span <= 0:
            raise ValueError("exposure_count cadence requires min_n and max_span_days > 0")
        return ExposureCountCadence(min_n=min_n, max_span_days=span)
    raise ValueError(f"Unknown cadence type: {t!r}")


def _parse_node(kind: str, d: Mapping[str, Any]) -> NodeConfig:
    enabled = bool(d.get("enabled", True))
    scope_mode = d.get("scope_mode")
    inputs_raw = d.get("inputs_raw")
    inputs_artifacts = d.get("inputs_artifacts")
    params = d.get("params")
    cadence = _parse_cadence(d.get("cadence"))
    return NodeConfig(
        enabled=enabled,
        scope_mode=str(scope_mode) if scope_mode is not None else None,
        inputs_raw=list(inputs_raw) if isinstance(inputs_raw, (list, tuple)) else None,
        inputs_artifacts=list(inputs_artifacts) if isinstance(inputs_artifacts, (list, tuple)) else None,
        params=dict(params) if isinstance(params, Mapping) else None,
        cadence=cadence,
    )


def _parse_edges(lst: Sequence[Mapping[str, Any]] | None) -> List[Edge]:
    out: List[Edge] = []
    if not lst:
        return out
    for i, e in enumerate(lst):
        try:
            src = str(e["src"]).strip()
            dst = str(e["dst"]).strip()
        except KeyError as ex:
            raise ValueError(f"edge[{i}] missing key: {ex}") from ex
        policy = str(e.get("policy", "latest_valid"))
        tol = int(e.get("tolerance_days", 90) or 90)
        out.append(Edge(src=TaskSpec(kind=src, task_cls=object), dst=TaskSpec(kind=dst, task_cls=object), policy=policy, tolerance_days=tol))
    return out


def load_planning_config_from_dict(cfg: Mapping[str, Any]) -> PlanningConfig:
    version = int(cfg.get("version", 1) or 1)
    nodes_cfg = cfg.get("nodes") or {}
    if not isinstance(nodes_cfg, Mapping):
        raise ValueError("config.nodes must be a mapping")
    nodes: Dict[str, NodeConfig] = {}
    for kind, nd in nodes_cfg.items():
        if str(kind) not in SUPPORTED_CALIBRATION_KINDS:
            raise ValueError(
                f"unsupported planning node {kind!r}; use a canonical calibration kind"
            )
        if not isinstance(nd, Mapping):
            raise ValueError(f"nodes.{kind} must be a mapping")
        nodes[kind] = _parse_node(kind, nd)
    edges = _parse_edges(cfg.get("edges"))
    for edge in edges:
        for endpoint in (edge.src.kind, edge.dst.kind):
            if endpoint not in SUPPORTED_CALIBRATION_KINDS:
                raise ValueError(
                    f"unsupported planning edge kind {endpoint!r}; use a canonical calibration kind"
                )
    execution = cfg.get("execution") or {}
    configured_workers = execution.get("nworkers") if isinstance(execution, Mapping) else None
    if configured_workers is None:
        configured_workers = cfg.get("nworkers")
    nworkers = None if configured_workers is None else int(configured_workers)
    if nworkers is not None and nworkers < 1:
        raise ValueError("nworkers must be at least one")
    progress = execution.get("progress", True) if isinstance(execution, Mapping) else True
    progress_mode = str(execution.get("progress_mode", "auto")) if isinstance(execution, Mapping) else "auto"
    if progress_mode not in {"auto", "tty", "plain", "json"}:
        raise ValueError("execution.progress_mode must be auto, tty, plain, or json")
    progress_interval = float(execution.get("progress_interval", 30.0)) if isinstance(execution, Mapping) else 30.0
    if progress_interval <= 0:
        raise ValueError("execution.progress_interval must be positive")
    progress_path = execution.get("progress_path") if isinstance(execution, Mapping) else None
    max_retries = int(execution.get("max_retries", 0)) if isinstance(execution, Mapping) else 0
    if max_retries < 0:
        raise ValueError("execution.max_retries cannot be negative")
    return PlanningConfig(
        version=version,
        nworkers=nworkers,
        progress=bool(progress),
        progress_mode=progress_mode,
        progress_interval=progress_interval,
        progress_path=(str(progress_path) if progress_path else None),
        max_retries=max_retries,
        nodes=nodes,
        edges=edges,
    )


def load_planning_config(path: str) -> PlanningConfig:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load planning config but is not installed")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, Mapping):
        raise ValueError("Top-level YAML must be a mapping")
    return load_planning_config_from_dict(data)
