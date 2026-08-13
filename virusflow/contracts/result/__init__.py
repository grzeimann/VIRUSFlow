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
    required_scalars = [
        "bad_fraction", "reference_exposure_time_seconds", "bias_convention",
    ]


class FlatResultContract(_BaseSimpleContract):
    required_arrays = ["master_flat"]
    required_scalars = ["bad_fraction"]


class TraceResultContract(_BaseSimpleContract):
    required_arrays = [
        "fiber_trace_map", "trace_sample_columns", "sampled_trace_positions",
        "per_fiber_trace_residual_rms", "trace_sample_valid_mask",
        "trace_fit_residuals", "per_fiber_valid_sample_count",
        "trace_interpolated_fiber_mask",
    ]


class WaveResultContract(_BaseSimpleContract):
    required_arrays = [
        "wavelength_map", "per_fiber_wavelength_residual_rms",
        "arc_identification", "arc_candidate_evidence", "arc_line_evidence",
        "seed_region_attempted_mask", "seed_region_success_mask",
        "seed_region_failure_code", "seed_fit_coefficients",
        "interpolated_fiber_mask", "extrapolated_fiber_mask",
        "input_mask_indices", "input_mask_shape",
    ]

class CmpResultContract(_BaseSimpleContract):
    required_arrays = ["master_comparison_lamp"]

class TwiResultContract(_BaseSimpleContract):
    required_arrays = ["master_twilight"]


class MasterSciResultContract(_BaseSimpleContract):
    required_arrays = ["master_sci"]
    required_scalars = ["n_inputs", "robust_illumination"]


class ExtractedMasterSpectrumResultContract(_BaseSimpleContract):
    required_arrays = [
        "spectrum", "valid_pixel_fraction", "effective_aperture_width",
        "extraction_valid", "aperture_start_row", "aperture_first_weight",
        "aperture_last_weight", "aperture_sample_mask_bits",
    ]
    required_scalars = ["fiber_count", "valid_sample_fraction"]


class ExtractedMasterSciSpectrumResultContract(ExtractedMasterSpectrumResultContract):
    """Backward-compatible name for the shared master-spectrum contract."""


class ExposureFiberResponseResultContract(_BaseSimpleContract):
    required_arrays = [
        "raw_ratio", "normalization", "valid_mask", "common_ldls",
        "common_twilight", "within_amplifier_response",
        "amplifier_response", "amplifier_scalar", "amplifier_common_response",
        "fiber_amplifier_index", "wavelength",
    ]
    required_scalars = ["valid_fraction", "amplifier_count", "amplifier_reference_scalar"]
