from __future__ import annotations

from typing import Callable, Dict, Optional

import numpy as np

from ..core.algo_result import ensure_algo_result
from .diagnostics import _qa_inc
from .registry_adapter import RegistryAdapter


class QADiagnosticsService:
    """Canonical QA boundary using ArtifactService component loading."""

    def __init__(self, adapter: RegistryAdapter, component_loader: Callable, yaml_path: Optional[str] = None) -> None:
        self.adapter = adapter
        self._component_loader = component_loader
        self._yaml_path = yaml_path
        self._engine = None

    def _engine_or_load(self):
        if self._engine is None:
            from ..qa.engine import QAEngine

            self._engine = QAEngine(self._yaml_path)
        return self._engine

    def get(self, artifact_id: int) -> Optional[dict]:
        return self.adapter.get_diagnostics(int(artifact_id))

    def set(self, artifact_id: int, status: str, metrics: Optional[Dict] = None) -> None:
        self.adapter.set_diagnostics(int(artifact_id), status=status, metrics=metrics)

    def evaluate_and_save(self, *, artifact_id: int, kind: str, meta=None) -> str:
        engine = self._engine_or_load()
        result = ensure_algo_result(meta or {}, kind=kind)
        values = dict(result.as_meta() or {})
        payload = self._component_loader(int(artifact_id))
        data = payload.get("data") if isinstance(payload, dict) else None
        if data is not None:
            component = dict(values.get("component") or {})
            component["data"] = np.asarray(data, dtype=float)
            values["component"] = component
        decision = engine.evaluate(kind=kind, meta=values)
        unit_by_name = {
            "read_noise": "electron",
            "bad_fraction": "1",
            "n_inputs": "1",
            "trace_len": "pixel",
            "best_nmatch": "1",
            "best_rms": "Angstrom",
        }
        facts = {
            name: {"value": value, "units": unit_by_name.get(name), "component": None}
            for name, value in (result.scalars or {}).items()
            if np.isscalar(value) and not isinstance(value, (str, bytes))
        }
        facts.update({
            name: {"value": value, "units": None, "component": None}
            for name, value in (decision.metrics or {}).items()
        })
        for component_name, units in (
            ("per_pixel_bias_scatter", "electron"),
            ("per_fiber_trace_residual_rms", "pixel"),
            ("per_fiber_wavelength_residual_rms", "Angstrom"),
        ):
            array = result.get_array(component_name)
            if array is not None:
                with np.errstate(all="ignore"):
                    median = float(np.nanmedian(np.asarray(array, dtype=float)))
                if np.isfinite(median):
                    facts[f"{component_name}_median"] = {
                        "value": median,
                        "units": units,
                        "component": component_name,
                    }
        usability = "unusable" if decision.should_block else (
            "degraded" if decision.status in {"warn", "fail"} else "usable"
        )
        self.adapter.set_qa_bundle(
            int(artifact_id),
            facts=facts,
            status=decision.status,
            usability=usability,
            policy_version="1",
            rules=[{"message": message} for message in (decision.messages or [])],
        )
        _qa_inc(kind, decision.status)
        return decision.status

    def should_block(self, *, kind: str, status: Optional[str]) -> bool:
        return self._engine_or_load().policy_for(kind) == "hard" and str(status or "").lower() == "fail"
