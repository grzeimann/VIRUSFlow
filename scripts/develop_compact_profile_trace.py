#!/usr/bin/env python3
"""Auditable development calibration of the compact VIRUS profile and trace.

This is a small reference implementation, not production integration.  It
models the cross-dispersion image of adjacent VIRUS fibers with a unit-integral
projected circular fiber, Gaussian compact blur, and unit pixel response:

    R_geom(f,x) = (133 / 352) * p_smooth(f,x)
    R(f,x)      = q_R * R_geom(f,x)
    sigma(f,x)  = exp(B_B3+x(f,x) @ c_sigma)
    P           = semicircular_fiber(R) * Gaussian(sigma) * pixel_top_hat
    M(y,x)      = sum_f A_f(x) P(y - T_f(x))
    A_f         = F5_f / C5_f

The trace is

    T_f(x) = T_base,f(x) + B_degree4(x) @ c_trace,f,

and the trace derivative used by the Gauss--Newton system is

    dM/dT = -A P'.

Immutable measurement evidence is the scatter-subtracted physical CCD,
variance, valid mask, five-pixel flux, and aperture rows/weights constructed
relative to ``evidence.base_trace``.  Candidate states always recompute the
smooth pitch, geometric radius, effective radius, sigma field, frozen-aperture
capture C5, amplitudes, model, and residual.  The aperture is never recentered
when a fitted trace moves.  The effective q_R is a radius scale relative to the
266-micron-core / 352-micron-pitch geometric relation, not a literal new
measurement of the fiber-core diameter; sigma is the effective compact blur.
The degree-four correction is owned by each fiber but is solved with
neighboring-fiber overlap coupling in the sparse Hessian.

The sequence is: initialize the physical compact profile, fit its one log-radius
plus ten B3+x log-sigma coefficients on x % stride == inference_phase, refine
the degree-four trace twice using the disjoint validation phase, make a small
compact-profile closure, and perform exactly one full-resolution diagnostic
closure.  STRIDE-8 retains every valid compact-support detector row at selected
columns, without x binning, interpolation, S/N selection, or thinning; all
overlapping fiber contributions remain in each selected pixel.  Sparse working
likelihoods make iteration practical and the final full-resolution likelihood is
authoritative only for reporting, never for another update.

The compact model deliberately excludes the broad power-law fiber halo and
scattered-light component, detector-coordinate dy, empirical kernels, Hermite
terms, and W/f_sigma response coordinates.  Coherent residual structure may
therefore belong to that later physical model and should not be absorbed by
extra compact/trace iterations.

The loader uses the established read-only ArtifactService paired physical-CCD
machinery.  The default validation block is LL/LU 106+033+506, exposures
20260609T233613.5, 20260609T233737.0, and 20260609T233900.0.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# These are the validated scientific primitives and the provenance-secure
# loader.  Keeping this import list explicit makes the dependency auditable.
from develop_ldls_forward_response import (  # noqa: E402
    LDLSEvidence,
    LDLSGeometry,
    LDLSSampling,
    ProfileCache,
    _build_compact_profile_normal_equations,
    _compact_profile_parameter_derivatives,
    _fixed_r_sigma_basis,
    build_compact_trace_step as _build_compact_trace_step,
    build_full_cross_dispersion_sampling_plan,
    build_ldls_geometry,
    fractional_aperture_geometry,
    load_ldls_evidence_pair,
    parse_zipcode_key,
    predict_compact_profile,
)
from virusflow.artifacts import ArtifactService  # noqa: E402
from virusflow.ontology.coordinates import UPPER_AMPLIFIER_Y_OFFSET  # noqa: E402
DEFAULT_LOWER = "106+033+506+LL+S/N 0013"
DEFAULT_UPPER = "106+033+506+LU+S/N 0013"
DEFAULT_EXPOSURES = ("20260609T233613.5", "20260609T233737.0", "20260609T233900.0")
@dataclass(frozen=True)
class RunConfig:
    """Visible experimental knobs and numerical controls for one run.
    q_r_initial and sigma_initial are physical initializations (dimensionless
    radius scale and detector pixels).  stride and phases define the fixed raw
    detector-pixel working representation.  trace_degree and support are fixed
    compact-model assumptions.  Iteration counts and damping values are
    numerical controls; they do not add model degrees of freedom.
    """

    q_r_initial: float = 0.975
    sigma_initial: float = 0.8
    stride: int = 8
    inference_phase: int = 0
    validation_phase: int = 4
    trace_degree: int = 4
    compact_support: float = 9.0
    trace_margin: float = 1.0
    max_initial_profile_iterations: int = 4
    max_trace_iterations: int = 2
    max_profile_closure_iterations: int = 1
    profile_damping_sequence: tuple[float, ...] = (1.0, 0.5, 0.25, 0.125)
    primary_damping: float = 1.0
    fallback_damping: float = 0.5
    profile_cache_quantization: float = 0.002
    ridge: float = 1e-5
    def resolved(self) -> dict[str, Any]:
        values = asdict(self)
        values["profile_damping_sequence"] = list(self.profile_damping_sequence)
        return values
@dataclass
class TimingReport:
    """Permanent timing ledger; scientific runtime is part of the output."""

    stages: dict[str, float]
    sparse_evaluation_seconds: float = 0.0
    full_evaluation_seconds: float = 0.0
    sparse_evaluation_count: int = 0
    full_evaluation_count: int = 0
    def add(self, name: str, seconds: float) -> None:
        self.stages[name] = self.stages.get(name, 0.0) + max(0.0, float(seconds))

    def evaluate(self, sparse: bool, function, *args, **kwargs):
        started = perf_counter()
        result = function(*args, **kwargs)
        elapsed = perf_counter() - started
        if sparse:
            self.sparse_evaluation_count += 1
            self.sparse_evaluation_seconds += elapsed
        else:
            self.full_evaluation_count += 1
            self.full_evaluation_seconds += elapsed
        return result, elapsed

    def as_dict(self, numerical_seconds: float, complete_seconds: float) -> dict[str, Any]:
        return {
            "stages_seconds": {key: float(value) for key, value in self.stages.items()},
            "numerical_inference_time_seconds": float(numerical_seconds),
            "complete_script_wall_time_seconds": float(complete_seconds),
            "sparse_evaluation_count": self.sparse_evaluation_count,
            "mean_sparse_evaluation_seconds": self.sparse_evaluation_seconds / max(self.sparse_evaluation_count, 1),
            "full_evaluation_count": self.full_evaluation_count,
            "mean_full_evaluation_seconds": self.full_evaluation_seconds / max(self.full_evaluation_count, 1),
            "sparse_evaluation_seconds": self.sparse_evaluation_seconds,
            "full_evaluation_seconds": self.full_evaluation_seconds,
        }


def _initial_sigma_coefficients(sigma: float) -> np.ndarray:
    coefficients = np.zeros(10, float)
    coefficients[[0, 5]] = np.log(float(sigma))
    return coefficients


def _calibration(q_r: float, sigma_coefficients: np.ndarray, config: RunConfig):
    # A tiny local object is sufficient; the predictor only needs these fields.
    from develop_ldls_forward_response import CompactProfileCalibration

    return CompactProfileCalibration(
        q_R=float(q_r), sigma_coefficients=tuple(np.asarray(sigma_coefficients, float)),
        profile_cache_quantization=config.profile_cache_quantization,
    )


def load_run_inputs(db: Path, lower: str, upper: str, exposures: Iterable[str]):
    """Load one paired physical CCD through the existing read-only machinery."""
    service = ArtifactService(str(db))
    return load_ldls_evidence_pair(
        service, parse_zipcode_key(lower), parse_zipcode_key(upper),
        exposure_ids=tuple(exposures), aperture_width=5.0,
    )


def build_sparse_views(evidence: LDLSEvidence, config: RunConfig):
    """Build one immutable geometry and disjoint raw-pixel inference views."""
    shape = evidence.base_trace.shape
    geometry = build_ldls_geometry(
        evidence, support=config.compact_support, trace_degree=config.trace_degree,
        response_degree=0, amplifier_boundary=float(UPPER_AMPLIFIER_Y_OFFSET),
        # The geometry helper carries legacy response fields, but this reference
        # never evaluates or solves them; compact R and sigma are authoritative.
        reference_W=np.ones(shape), reference_f_sigma=np.full(shape, 0.25),
        trace_margin=config.trace_margin,
    )
    cache = ProfileCache(config.profile_cache_quantization)
    inference = build_full_cross_dispersion_sampling_plan(
        evidence, geometry, stride=config.stride, phase=config.inference_phase, role="inference",
    )
    validation = build_full_cross_dispersion_sampling_plan(
        evidence, geometry, stride=config.stride, phase=config.validation_phase, role="validation",
    )
    full = LDLSSampling(
        "full", np.arange(geometry.sample_x.size, dtype=np.int64),
        np.ones(geometry.sample_x.size), geometry.sample_block,
        np.full(geometry.sample_x.size, -1, dtype=np.int32), role="full",
        selected_detector_x=geometry.sample_x, selected_detector_y=geometry.sample_y,
    )
    return geometry, inference, validation, full, cache


def evaluate_compact_state(evidence, geometry, sampling, trace, q_r, sigma_coefficients, config, cache):
    """Fresh candidate evaluation: geometry, frozen C5, amplitudes, model, residual."""
    return predict_compact_profile(
        evidence, geometry, sampling, np.asarray(trace, float),
        _calibration(q_r, sigma_coefficients, config), cache=cache,
    )


def build_profile_step(evidence, geometry, sampling, prediction, basis, config, cache, timing=None):
    started = perf_counter()
    d_radius, d_sigma = _compact_profile_parameter_derivatives(
        evidence, geometry, prediction, cache=cache,
        contribution_indices=prediction.contribution_indices,
    )
    if timing is not None:
        timing["derivatives"] = perf_counter() - started
    normal_started = perf_counter()
    result = _build_compact_profile_normal_equations(
        evidence, geometry, sampling, prediction, basis, d_radius, d_sigma,
        fit_q_R=True, ridge=config.ridge,
        contribution_indices=prediction.contribution_indices,
    )
    if timing is not None:
        timing["normal_equations"] = perf_counter() - normal_started
    return result
def _timed_compact(evidence, geometry, sampling, trace, q_r, coefficients, config, cache, timing, stage, audit_stage=None):
    prediction, elapsed = timing.evaluate(
        sampling.role != "full", evaluate_compact_state, evidence, geometry, sampling,
        trace, q_r, coefficients, config, cache,
    )
    timing.add(stage, elapsed)
    if audit_stage is not None:
        timing.add(audit_stage, elapsed)
    return prediction

def fit_compact_profile_stage(evidence, geometry, inference, validation, trace, q_r, coefficients, config, cache, timing, max_iterations, boundary_audit=None):
    """Fit exactly 11 parameters with amplitudes fixed only inside each step."""
    basis = _fixed_r_sigma_basis(evidence, geometry, 3, include_x=True)
    q_r = float(q_r)
    coefficients = np.asarray(coefficients, float).copy()
    current = _timed_compact(evidence, geometry, inference, trace, q_r, coefficients, config, cache, timing, "profile initial sparse evaluation", boundary_audit)
    current_validation = _timed_compact(evidence, geometry, validation, trace, q_r, coefficients, config, cache, timing, "profile initial validation evaluation", boundary_audit)
    history = []
    stage_started = perf_counter()
    for iteration in range(int(max_iterations)):
        iteration_started = perf_counter()
        profile_timing = {}
        hessian, gradient = build_profile_step(evidence, geometry, inference, current, basis, config, cache, profile_timing)
        timing.add("compact-profile derivative construction", profile_timing["derivatives"])
        timing.add("compact-profile normal equations", profile_timing["normal_equations"])
        solve_started = perf_counter()
        delta = np.linalg.solve(hessian, gradient)
        timing.add("compact-profile coefficient solve", perf_counter() - solve_started)
        accepted = False
        candidate = current
        candidate_validation = current_validation
        used_damping = 0.0
        before_loss = float(current.robust_loss)
        candidate_evaluations = 0
        candidate_render_seconds = 0.0
        candidate_validation_seconds = 0.0
        for damping in config.profile_damping_sequence:
            trial_q = float(np.exp(np.log(q_r) + damping * delta[0]))
            trial_coefficients = coefficients + damping * delta[1:]
            if not 0.90 < trial_q < 1.10:
                continue
            candidate_started = perf_counter()
            trial = _timed_compact(evidence, geometry, inference, trace, trial_q, trial_coefficients, config, cache, timing, "compact-profile candidate evaluation")
            candidate_render_seconds += perf_counter() - candidate_started
            validation_started = perf_counter()
            trial_validation = _timed_compact(evidence, geometry, validation, trace, trial_q, trial_coefficients, config, cache, timing, "compact-profile candidate validation")
            candidate_validation_seconds += perf_counter() - validation_started
            candidate_evaluations += 1
            if trial.robust_loss < current.robust_loss:
                q_r, coefficients, candidate, candidate_validation = trial_q, trial_coefficients, trial, trial_validation
                used_damping, accepted = float(damping), True
                break
        if accepted:
            current, current_validation = candidate, candidate_validation
        history.append({
            "iteration": iteration + 1, "q_R": q_r,
            "sigma_coefficients": coefficients.tolist(),
            "median_sigma": float(np.median(current.sigma)),
            "candidate_damping": used_damping, "accepted": accepted,
            "inference_loss": float(current.robust_loss),
            "validation_loss": float(current_validation.robust_loss),
            "actual_inference_improvement": before_loss - float(current.robust_loss),
            "candidate_evaluation_count": candidate_evaluations,
            "profile_derivative_seconds": profile_timing["derivatives"],
            "profile_normal_equation_seconds": profile_timing["normal_equations"],
            "profile_candidate_render_seconds": candidate_render_seconds,
            "profile_candidate_validation_seconds": candidate_validation_seconds,
            "runtime_seconds": perf_counter() - iteration_started,
            "parameter_count": 11,
        })
        if not accepted:
            break
    timing.add("compact-profile fit stage", perf_counter() - stage_started)
    return q_r, coefficients, current, current_validation, history
def build_trace_step(evidence, geometry, inference, prediction, config, timing=None):
    """Build dM/dT = -A P' with the overlap-coupled sparse Hessian."""
    return _build_compact_trace_step(
        evidence, geometry, inference, prediction, ridge=config.ridge,
        timing=timing,
    )
def refine_trace(evidence, geometry, inference, validation, trace, trace_coefficients, q_r, coefficients, config, cache, timing, boundary_audit=None):
    """Refine degree-four fiber-owned traces, accepting only validation gains."""
    trace = np.asarray(trace, float).copy()
    trace_coefficients = np.asarray(trace_coefficients, float).copy()
    current = _timed_compact(evidence, geometry, inference, trace, q_r, coefficients, config, cache, timing, "trace initial sparse evaluation", boundary_audit)
    current_validation = _timed_compact(evidence, geometry, validation, trace, q_r, coefficients, config, cache, timing, "trace initial validation evaluation", boundary_audit)
    history = []
    for iteration in range(config.max_trace_iterations):
        iteration_started = perf_counter()
        derivative_started = perf_counter()
        current_with_derivative, derivative_elapsed = timing.evaluate(
            True, predict_compact_profile, evidence, geometry, inference, trace,
            _calibration(q_r, coefficients, config), cache=cache, profile_prime=True,
        )
        timing.add("trace derivative construction", perf_counter() - derivative_started)
        timing.add("trace derivative sparse evaluation", derivative_elapsed)
        trace_timing = {}
        hessian_started = perf_counter()
        step = build_trace_step(evidence, geometry, inference, current_with_derivative, config, trace_timing)
        timing.add("trace Hessian accumulation and solve", perf_counter() - hessian_started)
        timing.add("trace Hessian accumulation", trace_timing.get("sparse_normal_equation_accumulation_seconds", 0.0))
        timing.add("trace sparse linear solve", trace_timing.get("sparse_solve_seconds", 0.0))
        proposed = np.einsum("fxt,ft->fx", geometry.trace_basis, step.incremental_trace_coeff)
        candidate_records = []
        accepted = False
        accepted_delta = np.zeros_like(trace)
        accepted_damping = 0.0
        before_validation_loss = float(current_validation.robust_loss)
        candidate_started = perf_counter()
        for damping in (config.primary_damping, config.fallback_damping):
            candidate_trace = trace + float(damping) * proposed
            trial = _timed_compact(evidence, geometry, inference, candidate_trace, q_r, coefficients, config, cache, timing, "trace candidate inference evaluation")
            trial_validation = _timed_compact(evidence, geometry, validation, candidate_trace, q_r, coefficients, config, cache, timing, "trace candidate validation evaluation")
            candidate_records.append({"damping": float(damping), "inference_loss": float(trial.robust_loss), "validation_loss": float(trial_validation.robust_loss)})
            if trial_validation.robust_loss < current_validation.robust_loss:
                trace = candidate_trace
                trace_coefficients += float(damping) * step.incremental_trace_coeff
                current, current_validation = trial, trial_validation
                accepted, accepted_damping, accepted_delta = True, float(damping), float(damping) * proposed
                break
        history.append({
            "iteration": iteration + 1,
            "proposed_max_abs_delta_T": float(np.max(np.abs(proposed))),
            "proposed_median_delta_T": float(np.median(proposed)),
            "proposed_MAD_delta_T": float(np.median(np.abs(proposed - np.median(proposed)))),
            "proposed_p95_abs_delta_T": float(np.percentile(np.abs(proposed), 95.0)),
            "accepted": accepted, "accepted_damping": accepted_damping,
            "accepted_delta_summary": _summary(accepted_delta),
            "inference_loss": float(current.robust_loss),
            "validation_loss": float(current_validation.robust_loss),
            "validation_improvement": before_validation_loss - float(current_validation.robust_loss) if accepted else 0.0,
            "candidate_evaluations": candidate_records,
            "hessian_seconds": trace_timing.get("sparse_normal_equation_accumulation_seconds", 0.0),
            "solve_seconds": trace_timing.get("sparse_solve_seconds", 0.0),
            "candidate_evaluation_seconds": perf_counter() - candidate_started,
            "runtime_seconds": perf_counter() - iteration_started,
        })
        if not accepted:
            break
    return trace, trace_coefficients, current, current_validation, history


def _summary(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, float)
    median = float(np.median(values))
    return {"median": median, "MAD": float(np.median(np.abs(values - median))), "p05": float(np.percentile(values, 5)), "p95": float(np.percentile(values, 95)), "max_abs": float(np.max(np.abs(values)))}


def evaluate_full_closure(evidence, geometry, full, trace, q_r, coefficients, config, cache, timing):
    """The sole authoritative full-resolution prediction; it cannot update state."""
    started = perf_counter()
    prediction = _timed_compact(evidence, geometry, full, trace, q_r, coefficients, config, cache, timing, "final full-resolution evaluation")
    timing.add("final full-resolution closure", perf_counter() - started)
    residual = np.asarray(prediction.residuals, float)
    median = float(np.median(residual))
    image = np.full(evidence.image.shape, np.nan)
    image[geometry.sample_y[full.sample_indices], geometry.sample_x[full.sample_indices]] = residual
    return prediction, {"robust_loss": float(prediction.robust_loss), "RMS": float(np.sqrt(np.mean(residual * residual))), "median": median, "MAD": float(np.median(np.abs(residual - median))), "sample_count": int(residual.size), "residual_image": image}


def save_outputs(output, evidence, geometry, inference, validation, trace, trace_coefficients, q_r, coefficients, initial_prediction, final_prediction, full_prediction, full_summary, selection, config, histories, timing, script_started, loading_seconds, numerical_seconds):
    output.mkdir(parents=True, exist_ok=True)
    complete_seconds = perf_counter() - script_started
    final_trace_correction = trace - evidence.base_trace
    timing_json = timing.as_dict(numerical_seconds, complete_seconds)
    timing_json["stages_seconds"]["loading"] = loading_seconds
    timing_json["calibration_counts"] = {
        "compact_profile_iterations": len(histories["initial_profile"]),
        "accepted_trace_iterations": int(sum(item["accepted"] for item in histories["trace"])),
        "compact_profile_closure_iterations": len(histories["closure_summary"]["history"]),
        "trace_candidate_evaluations_per_iteration": [len(item["candidate_evaluations"]) for item in histories["trace"]],
    }
    timing_json["contribution_counts"] = {
        "total_geometry_contributions": int(geometry.contribution_sample_index.size),
        "selected_inference_contributions": int(np.count_nonzero(np.isin(geometry.contribution_sample_index, inference.sample_indices))),
        "profile_derivative_contributions": int(np.count_nonzero(np.isin(geometry.contribution_sample_index, inference.sample_indices))),
    }
    timing_json["boundary_redundancy_audit_seconds"] = {
        key: float(value) for key, value in timing.stages.items() if key.startswith("redundant ")
    }
    full_json = {key: value for key, value in full_summary.items() if key != "residual_image"}
    full_json["runtime_seconds"] = timing.stages.get("final full-resolution closure", 0.0)
    model_json = {
        "fiber_core_diameter_um": 266.0, "slit_pitch_um": 352.0,
        "geometric_radius_fraction": 133.0 / 352.0,
        "profile_model": "semicircular_fiber(R) convolved with Gaussian(sigma) and unit pixel top-hat",
        "sigma_model": "sigma=exp(B_B3+x @ c_sigma), ten coefficients, positive effective compact blur in detector pixels",
        "trace_model": "T_base + degree-4 Legendre correction per fiber; overlap-coupled Gauss-Newton Hessian",
        "amplitude_model": "A_f=F5_f/C5_f using immutable base-trace aperture rows and weights",
        "q_R_interpretation": "effective radius scale relative to 266/352, not literal core-diameter measurement",
        "excluded_physics": ["broad power-law fiber halo / scattered light", "detector dy", "W/f_sigma response model", "Hermites", "empirical kernel"],
        "sparse_likelihood": "all valid compact-support rows at selected raw detector columns; all overlapping fibers included",
        "full_closure": "exactly one diagnostic full-resolution evaluation, never an update",
    }
    state_json = {"q_R": float(q_r), "sigma_coefficients": coefficients.tolist(), "sigma_median": float(np.median(final_prediction.sigma)), "sigma_range": [float(np.min(final_prediction.sigma)), float(np.max(final_prediction.sigma))], "trace_correction": _summary(final_trace_correction), "trace_coefficients": np.asarray(trace_coefficients, float).tolist()}
    report = {
        "model": model_json, "run_config": config.resolved(), "provenance": selection,
        "initial_state": {"q_R": config.q_r_initial, "sigma_initial_pixels": config.sigma_initial, "sigma_coefficients": _initial_sigma_coefficients(config.sigma_initial).tolist(), "sparse_inference_loss": float(initial_prediction.robust_loss)},
        "compact_profile_initial_fit": {"parameter_count": 11, "history": histories["initial_profile"]},
        "trace_fit": {"degree": config.trace_degree, "compact_profile_fixed": True, "history": histories["trace"], "accepted_iterations": int(sum(item["accepted"] for item in histories["trace"]))},
        "compact_profile_closure": histories["closure_summary"], "final_state": state_json,
        "full_closure": full_json, "timing": timing_json,
        "aperture_semantics": "immutable evidence.five_pixel_flux, aperture_rows, aperture_weights relative to evidence.base_trace; no moving aperture",
    }
    (output / "compact_profile_trace.json").write_text(json.dumps(report, indent=2, allow_nan=True) + "\n")
    np.savez_compressed(output / "compact_profile_trace.npz", base_trace=evidence.base_trace, final_trace=trace, trace_correction=final_trace_correction, final_p_smooth=final_prediction.p_smooth, final_R_geom=final_prediction.R_geom, final_R=final_prediction.R, final_sigma=final_prediction.sigma, final_trace_coefficients=trace_coefficients, final_sigma_coefficients=coefficients, final_full_model=full_prediction.model_detector, final_full_residuals=full_prediction.residuals, final_full_residual_image=full_summary["residual_image"], sparse_inference_indices=inference.sample_indices, sparse_validation_indices=validation.sample_indices)
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), constrained_layout=True)
    image = axes[0, 0].imshow(final_trace_correction, aspect="auto", origin="lower", cmap="coolwarm")
    axes[0, 0].set(title="Final trace correction", xlabel="detector x", ylabel="fiber"); fig.colorbar(image, ax=axes[0, 0], label="pixels")
    image = axes[0, 1].imshow(final_prediction.sigma, aspect="auto", origin="lower", cmap="viridis")
    axes[0, 1].set(title="Final sigma field", xlabel="detector x", ylabel="fiber"); fig.colorbar(image, ax=axes[0, 1], label="pixels")
    image = axes[1, 0].imshow(full_summary["residual_image"], aspect="auto", origin="lower", cmap="coolwarm")
    axes[1, 0].set(title="Full-resolution residual", xlabel="detector x", ylabel="detector y"); fig.colorbar(image, ax=axes[1, 0])
    labels = list(timing.stages); values = [timing.stages[label] for label in labels]
    axes[1, 1].barh(np.arange(len(labels)), values); axes[1, 1].set(yticks=np.arange(len(labels)), yticklabels=labels, title="Stage timing (s)"); axes[1, 1].tick_params(axis="y", labelsize=6)
    fig.savefig(output / "compact_profile_trace.png", dpi=130); plt.close(fig)
    return report


def main(argv: list[str] | None = None) -> int:
    script_started = perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path.home() / "work/run/virusflow.sqlite3")
    parser.add_argument("--lower-zipcode", default=DEFAULT_LOWER)
    parser.add_argument("--upper-zipcode", default=DEFAULT_UPPER)
    parser.add_argument("--ldls-exposure-id", action="append", dest="exposures", default=list(DEFAULT_EXPOSURES))
    parser.add_argument("--output-dir", type=Path, default=Path("development/compact_profile_trace"))
    parser.add_argument("--q-r-initial", type=float, default=RunConfig.q_r_initial)
    parser.add_argument("--sigma-initial", type=float, default=RunConfig.sigma_initial)
    parser.add_argument("--stride", type=int, default=RunConfig.stride)
    parser.add_argument("--inference-phase", type=int, default=RunConfig.inference_phase)
    parser.add_argument("--validation-phase", type=int, default=RunConfig.validation_phase)
    parser.add_argument("--max-trace-iterations", type=int, default=RunConfig.max_trace_iterations)
    args = parser.parse_args(argv)
    config = RunConfig(q_r_initial=args.q_r_initial, sigma_initial=args.sigma_initial, stride=args.stride, inference_phase=args.inference_phase, validation_phase=args.validation_phase, max_trace_iterations=args.max_trace_iterations)
    timing = TimingReport({})
    loading_started = perf_counter()
    evidence, selection, _assembly_image = load_run_inputs(args.db, args.lower_zipcode, args.upper_zipcode, args.exposures)
    loading_seconds = perf_counter() - loading_started; timing.add("evidence / artifact loading", loading_seconds)
    geometry_started = perf_counter(); geometry, inference, validation, full, cache = build_sparse_views(evidence, config); timing.add("physical geometry construction and sparse-plan construction", perf_counter() - geometry_started)
    if np.intersect1d(inference.sample_indices, validation.sample_indices).size:
        raise RuntimeError("inference and validation pixel sets are not disjoint")
    trace = evidence.base_trace.copy(); coefficients = _initial_sigma_coefficients(config.sigma_initial); q_r = config.q_r_initial
    inference_started = perf_counter()
    initial_prediction = _timed_compact(evidence, geometry, inference, trace, q_r, coefficients, config, cache, timing, "initial sparse evaluation")
    initial_validation = _timed_compact(evidence, geometry, validation, trace, q_r, coefficients, config, cache, timing, "initial validation evaluation")
    q_r, coefficients, profile_prediction, _profile_validation, profile_history = fit_compact_profile_stage(evidence, geometry, inference, validation, trace, q_r, coefficients, config, cache, timing, config.max_initial_profile_iterations, "redundant initial/profile boundary evaluations")
    trace, trace_coefficients, trace_prediction, trace_validation, trace_history = refine_trace(evidence, geometry, inference, validation, trace, np.zeros((trace.shape[0], config.trace_degree + 1)), q_r, coefficients, config, cache, timing, "redundant profile/trace boundary evaluations")
    q_before, sigma_before = q_r, coefficients.copy()
    q_r, coefficients, closure_prediction, closure_validation, closure_history = fit_compact_profile_stage(evidence, geometry, inference, validation, trace, q_r, coefficients, config, cache, timing, config.max_profile_closure_iterations, "redundant trace/closure boundary evaluations")
    final_full, full_summary = evaluate_full_closure(evidence, geometry, full, trace, q_r, coefficients, config, cache, timing)
    numerical_seconds = max(0.0, perf_counter() - inference_started - timing.stages.get("final full-resolution closure", timing.full_evaluation_seconds))
    histories = {"initial_profile": profile_history, "trace": trace_history, "closure_summary": {"q_R_before": q_before, "q_R_after": q_r, "delta_q_R": q_r - q_before, "sigma_coefficients_before": sigma_before.tolist(), "sigma_coefficients_after": coefficients.tolist(), "max_sigma_coefficient_change": float(np.max(np.abs(coefficients - sigma_before))), "median_absolute_sigma_field_change": float(np.median(np.abs(closure_prediction.sigma - trace_prediction.sigma))), "inference_loss_improvement": float(trace_prediction.robust_loss - closure_prediction.robust_loss), "validation_loss_improvement": float(trace_validation.robust_loss - closure_validation.robust_loss), "history": closure_history}}
    report = save_outputs(args.output_dir, evidence, geometry, inference, validation, trace, trace_coefficients, q_r, coefficients, initial_prediction, closure_prediction, final_full, full_summary, selection, config, histories, timing, script_started, loading_seconds, numerical_seconds)
    print(json.dumps({"output": str(args.output_dir), "q_R": report["final_state"]["q_R"], "full_robust_loss": report["full_closure"]["robust_loss"], "numerical_inference_seconds": report["timing"]["numerical_inference_time_seconds"], "complete_script_wall_seconds": report["timing"]["complete_script_wall_time_seconds"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
