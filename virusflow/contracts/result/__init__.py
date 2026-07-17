from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol

from ...core.algo_result import AlgoResult


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: List[str] = field(default_factory=list)


class ResultContract(Protocol):
    """Protocol for validating storage-neutral computational results.

    Responsibilities:
    - Validate presence of required outputs and structural consistency.
    - No QA thresholds or acceptance policy here.
    """

    def validate(self, result: AlgoResult) -> ValidationReport:  # pragma: no cover - Protocol signature
        ...


class _BaseSimpleContract:
    """A permissive base that checks type and minimal structure.

    Concrete contracts can extend this class in later sections to add
    presence/shape checks without introducing QA thresholds.
    """

    required_arrays: List[str] = []
    required_scalars: List[str] = []

    def validate(self, result: AlgoResult) -> ValidationReport:
        errs: List[str] = []
        if not isinstance(result, AlgoResult):
            return ValidationReport(ok=False, errors=["not an AlgoResult"])
        # Minimal presence checks (non-failing for now; will be tightened later)
        for k in getattr(self, "required_arrays", []) or []:
            if k not in (result.arrays or {}):
                errs.append(f"missing array component: {k}")
        for k in getattr(self, "required_scalars", []) or []:
            if k not in (result.scalars or {}) and k not in (result.meta or {}):
                errs.append(f"missing scalar/meta: {k}")
        return ValidationReport(ok=(len(errs) == 0), errors=errs)


class BiasResultContract(_BaseSimpleContract):
    required_arrays = ["master"]
    required_scalars = ["read_noise"]


class DarkResultContract(_BaseSimpleContract):
    required_arrays = ["master_dark"]
    required_scalars = ["bad_fraction"]


class FlatResultContract(_BaseSimpleContract):
    required_arrays = ["master_flat"]
    required_scalars = ["bad_fraction"]


class TraceResultContract(_BaseSimpleContract):
    pass


class WaveResultContract(_BaseSimpleContract):
    pass

class CmpResultContract(_BaseSimpleContract):
    required_arrays = ["master_comparison_lamp"]

class TwiResultContract(_BaseSimpleContract):
    required_arrays = ["master_twilight"]


class SciResultContract(_BaseSimpleContract):
    required_arrays = ["master_science"]
    required_scalars = ["n_inputs"]
