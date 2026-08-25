#!/usr/bin/env python3
"""Read-only LDLS trace-transfer experiment for one physical CCD.

This is deliberately a development harness.  It imports the frozen compact
forward model, transfers only a degree-four coupled trace correction to a
Master Twilight and Master Science pair, and never publishes artifacts or
modifies the frozen LDLS state.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from virusflow.algorithms.extraction import extract_fractional_aperture, fractional_aperture_geometry  # noqa: E402
from virusflow.algorithms.physical_ccd import assemble_physical_ccd, fit_gap_scattered_light  # noqa: E402
from virusflow.artifacts import ArtifactService  # noqa: E402
from virusflow.core.identity import ZipCode, parse_zipcode_key  # noqa: E402
from virusflow.ontology.coordinates import UPPER_AMPLIFIER_Y_OFFSET  # noqa: E402


log = logging.getLogger(__name__)
PAIR = {
    "LL": ("left", "LL", "LU"), "LU": ("left", "LL", "LU"),
    "RU": ("right", "RU", "RL"), "RL": ("right", "RU", "RL"),
}


def _forward_module():
    path = Path(__file__).with_name("develop_ldls_forward_response.py")
    spec = importlib.util.spec_from_file_location("ldls_forward_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import frozen forward reference from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


forward = _forward_module()


@dataclass(frozen=True)
class TransferInput:
    kind: str
    lower: dict[str, Any]
    upper: dict[str, Any]
    midpoint: datetime
    member_times: tuple[datetime, ...]


@dataclass(frozen=True)
class TransferResult:
    kind: str
    input: TransferInput
    evidence: Any
    geometry: Any
    sampling: Any
    baseline: Any
    final: Any
    state: Any
    history: list[dict[str, float]]
    proposal: Any
    raw_before: dict[str, Any]
    raw_after: dict[str, Any]
    mode_after: dict[str, Any]
    timing_seconds: dict[str, float]


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)


def _midpoint(row: dict[str, Any]) -> datetime:
    start, end = _time(str(row["validity_start"])), _time(str(row["validity_end"]))
    return start + (end - start) / 2


def _frame_times(row: dict[str, Any]) -> tuple[datetime, ...]:
    members = ((row.get("metadata") or {}).get("calibration_group") or {}).get("frame_membership") or ()
    return tuple(_time(str(item["timestamp"])) for item in members if item.get("timestamp"))


def _frame_signature(row: dict[str, Any]) -> tuple[str, ...]:
    members = ((row.get("metadata") or {}).get("calibration_group") or {}).get("frame_membership") or ()
    return tuple(str(item["exposure_id"]) for item in members if item.get("exposure_id"))


def _active_rows(service: ArtifactService, kind: str, zipcode: ZipCode) -> list[dict[str, Any]]:
    return sorted(
        (row for row in service.adapter.list_all(kind=kind)
         if str(row.get("state") or "active") == "active" and str(row.get("amp_key") or "") == zipcode.key()),
        key=lambda row: int(row["id"]), reverse=True,
    )


def _candidate_summary(candidate: TransferInput) -> dict[str, Any]:
    return {
        "kind": candidate.kind,
        "lower_id": int(candidate.lower["id"]), "upper_id": int(candidate.upper["id"]),
        "lower_validity": [candidate.lower["validity_start"], candidate.lower["validity_end"]],
        "upper_validity": [candidate.upper["validity_start"], candidate.upper["validity_end"]],
        "frame_signature": _frame_signature(candidate.lower),
        "member_times": [item.isoformat() for item in candidate.member_times],
        "midpoint": candidate.midpoint.isoformat(),
    }


def _paired_candidates(service: ArtifactService, kind: str, lower_zip: ZipCode, upper_zip: ZipCode) -> list[TransferInput]:
    result = []
    for lower in _active_rows(service, kind, lower_zip):
        signature = _frame_signature(lower)
        if not signature:
            continue
        for upper in _active_rows(service, kind, upper_zip):
            if signature != _frame_signature(upper):
                continue
            times = tuple(sorted(_frame_times(lower)))
            measurement_midpoint = (
                times[0] + (times[-1] - times[0]) / 2 if times else _midpoint(lower)
            )
            result.append(TransferInput(kind, lower, upper, measurement_midpoint, times))
    return result


def _select_transfer_input(
    service: ArtifactService, kind: str, lower_zip: ZipCode, upper_zip: ZipCode,
    *, lower_id: int | None, upper_id: int | None,
) -> TransferInput:
    candidates = _paired_candidates(service, kind, lower_zip, upper_zip)
    if lower_id is not None or upper_id is not None:
        matches = [item for item in candidates if (lower_id is None or int(item.lower["id"]) == lower_id)
                   and (upper_id is None or int(item.upper["id"]) == upper_id)]
        if len(matches) == 1:
            return matches[0]
    elif len(candidates) == 1:
        return candidates[0]
    detail = json.dumps([_candidate_summary(item) for item in candidates], indent=2)
    raise RuntimeError(
        f"ambiguous or missing matching active physical-CCD {kind} pair; candidates:\n{detail}\n"
        f"supply --{kind.replace('_', '-')}-lower-id/--{kind.replace('_', '-')}-upper-id explicitly"
    )


def _parents(service: ArtifactService, row: dict[str, Any]) -> set[int]:
    return {int(value) for value in service.describe(row)["provenance"]["parents"]}


def _image_mask(service: ArtifactService, row: dict[str, Any], component: str) -> tuple[np.ndarray, np.ndarray]:
    image = np.asarray(service.load_component(row, component)["data"], float)
    mask = ~np.isfinite(image)
    for parent_id in _parents(service, row):
        parent = service.adapter.get_row(parent_id)
        if parent is None or str(parent.get("canonical_kind") or parent.get("kind")) != "master_dark":
            continue
        try:
            mask |= np.asarray(service.load_component(parent, "dark_pixel_mask")["data"], bool)
        except KeyError:
            pass
    return image, mask


def _load_frozen_calibration(path: Path) -> tuple[Any, dict[str, Any], Any]:
    provenance = json.loads((path / "forward_ldls_provenance.json").read_text())
    if provenance.get("aperture_illumination_model") != "uniform" or provenance.get("alpha") != 0.0:
        raise ValueError("trace transfer requires a frozen uniform-alpha LDLS calibration")
    arrays = np.load(path / "forward_ldls_authoritative_state.npz")
    if "alpha" in arrays.files:
        raise ValueError("authoritative transfer calibration unexpectedly contains fitted alpha")
    return arrays, provenance, provenance["selected_artifacts"]


def _load_dy(path: Path) -> Any:
    values = np.load(path / "detector_displacement_experiment" / "detector_displacement_field.npz")
    return forward.DetectorDisplacementField(
        values["dense_dy"], values["lower_x_knots"], values["lower_y_knots"], values["lower_coefficients"],
        values["upper_x_knots"], values["upper_y_knots"], values["upper_coefficients"],
        amplifier_boundary=int(UPPER_AMPLIFIER_Y_OFFSET), x_knot_spacing=float(np.diff(values["lower_x_knots"])[1]),
        y_knot_spacing=float(np.diff(values["lower_y_knots"])[1]),
    )


def _summary(values: np.ndarray) -> dict[str, float | int]:
    values = np.asarray(values, float)
    values = values[np.isfinite(values)]
    median = float(np.median(values))
    return {
        "count": int(values.size), "median": median,
        "MAD": float(np.median(np.abs(values - median))),
        "p05": float(np.percentile(values, 5.0)), "p95": float(np.percentile(values, 95.0)),
        "min": float(np.min(values)), "max": float(np.max(values)),
    }


def _residual_summary(evaluation: Any) -> dict[str, float]:
    residual = np.asarray(evaluation.residuals, float)
    return {
        "robust_loss": float(evaluation.robust_loss), "RMS": float(np.sqrt(np.mean(residual * residual))),
        "MAD": float(np.median(np.abs(residual - np.median(residual)))),
    }


def _make_transfer_evidence(
    service: ArtifactService, item: TransferInput, *, frozen_trace: np.ndarray,
) -> Any:
    side, lower_amp, upper_amp = PAIR[parse_zipcode_key(str(item.lower["amp_key"])).amp]
    lower, lower_mask = _image_mask(service, item.lower, item.kind)
    upper, upper_mask = _image_mask(service, item.upper, item.kind)
    assembly = assemble_physical_ccd(
        lower, upper, side=side, lower_amp=lower_amp, upper_amp=upper_amp,
        lower_variance=np.maximum(np.abs(lower), 1.0), upper_variance=np.maximum(np.abs(upper), 1.0),
        lower_mask=lower_mask, upper_mask=upper_mask,
    )
    lower_count = lower.shape[0] // 2 if frozen_trace.shape[0] == lower.shape[0] else frozen_trace.shape[0] // 2
    scatter = fit_gap_scattered_light(
        assembly, frozen_trace[:lower_count], frozen_trace[lower_count:] - UPPER_AMPLIFIER_Y_OFFSET,
    )
    image = np.asarray(scatter.get_array("scatter_subtracted_image"), float)
    valid = ~np.asarray(assembly.get_array("pixel_mask"), bool) & np.isfinite(image)
    variance = np.maximum(np.abs(image), 1.0)
    extraction = extract_fractional_aperture(image, variance, frozen_trace, pixel_mask=~valid, width=5.0)
    rows, weights, _ = fractional_aperture_geometry(frozen_trace, image.shape[0], width=5.0)
    yy, xx = np.nonzero(valid)
    return forward.LDLSEvidence(
        image, variance, valid, np.asarray(extraction.get_array("spectrum"), float), frozen_trace,
        rows, weights, np.arange(frozen_trace.shape[0]),
        np.where(frozen_trace[:, 0] < UPPER_AMPLIFIER_Y_OFFSET, 0, 1),
        (0.0, float(UPPER_AMPLIFIER_Y_OFFSET), float(image.shape[0])), xx, yy,
        image[yy, xx], variance[yy, xx],
    )


def _raw_trace_evidence(evidence: Any, geometry: Any, sampling: Any, evaluation: Any) -> dict[str, Any]:
    projection = forward._local_residual_mode_projection(evidence, geometry, sampling, evaluation)
    information = projection["centroid_precision"]
    sigma = np.divide(1.0, np.sqrt(information), out=np.full_like(information, np.nan), where=information > 0.0)
    raw = projection["coefficients"][:, :, 1]
    significance = raw / sigma
    return {"raw_delta": raw, "information": information, "sigma": sigma, "significance": significance, "projection": projection}


def _solve_transfer_trace(evidence: Any, geometry: Any, sampling: Any, state: Any, cache: Any, dy: Any) -> tuple[Any, Any, list[dict[str, float]]]:
    history = []
    for iteration in range(2):
        current = forward.evaluate_state(evidence, geometry, sampling, state, cache=cache, detector_displacement=dy)
        step = forward.build_trace_step(evidence, geometry, sampling, state, current)
        accepted, candidate, damping = state, current, 0.0
        for value in 0.5 ** np.arange(8):
            candidate_state = forward.apply_trace_step(state, step, float(value))
            try:
                candidate_value = forward.evaluate_state(
                    evidence, geometry, sampling, candidate_state, cache=cache, detector_displacement=dy,
                )
            except ValueError:
                continue
            if candidate_value.robust_loss < current.robust_loss:
                accepted, candidate, damping = candidate_state, candidate_value, float(value)
                break
        if damping == 0.0:
            history.append({"iteration": float(iteration), "loss_before": current.robust_loss, "loss_after": current.robust_loss, "damping": 0.0, "max_delta_trace": 0.0})
            break
        state = accepted  # Commit the accepted state before convergence testing.
        max_delta = float(np.max(np.abs(candidate.trace - current.trace)))
        history.append({"iteration": float(iteration), "loss_before": current.robust_loss, "loss_after": candidate.robust_loss, "damping": damping, "max_delta_trace": max_delta})
        if max_delta < 2e-4:
            break
    final = forward.evaluate_state(evidence, geometry, sampling, state, cache=cache, detector_displacement=dy, derivatives=True, debug_contributions=True)
    proposal = forward.build_trace_step(evidence, geometry, sampling, state, final)
    return state, final, history, proposal


def _simple_trace_decomposition(delta: np.ndarray, frozen_trace: np.ndarray) -> dict[str, Any]:
    fibers, columns = delta.shape
    x = np.linspace(-1.0, 1.0, columns)
    valid = np.isfinite(delta)
    total = float(np.sum((delta[valid] - np.mean(delta[valid])) ** 2))
    def capture(residual: np.ndarray) -> float:
        return 1.0 - float(np.sum(residual[valid] ** 2)) / total if total > 0 else float("nan")
    global_shift = np.full_like(delta, np.mean(delta[valid]))
    design = np.polynomial.legendre.legvander(np.broadcast_to(x, delta.shape)[valid], 2)
    coefficient, *_ = np.linalg.lstsq(design, delta[valid], rcond=None)
    global_x = np.polynomial.legendre.legvander(x, 2) @ coefficient
    global_x = np.broadcast_to(global_x, delta.shape)
    half_x = np.empty_like(delta)
    for selected in (frozen_trace[:, 0] < UPPER_AMPLIFIER_Y_OFFSET, frozen_trace[:, 0] >= UPPER_AMPLIFIER_Y_OFFSET):
        local_valid = valid[selected]
        local_x = np.broadcast_to(x, delta[selected].shape)[local_valid]
        local_design = np.polynomial.legendre.legvander(local_x, 2)
        local_coeff, *_ = np.linalg.lstsq(local_design, delta[selected][local_valid], rcond=None)
        half_x[selected] = np.polynomial.legendre.legvander(x, 2) @ local_coeff
    common_x = np.nanmean(np.where(valid, delta, np.nan), axis=0)
    common_x = np.broadcast_to(common_x, delta.shape)
    return {
        "basis": "diagnostic only: constant and detector-X Legendre degree 2",
        "global_y_shift_variance_fraction": capture(delta - global_shift),
        "global_x_degree2_variance_fraction": capture(delta - global_x),
        "per_physical_half_x_degree2_variance_fraction": capture(delta - half_x),
        "all_fiber_common_x_variance_fraction": capture(delta - common_x),
        "fiber_specific_residual_variance_fraction": 1.0 - capture(delta - common_x),
    }


def _snr_summary(raw: dict[str, Any]) -> dict[str, Any]:
    significance = np.abs(raw["significance"])
    finite = np.isfinite(significance)
    return {
        "sigma_T": _summary(raw["sigma"]),
        "fraction_abs_delta_over_sigma_gt_1": float(np.mean(significance[finite] > 1.0)),
        "fraction_abs_delta_over_sigma_gt_3": float(np.mean(significance[finite] > 3.0)),
        "fraction_abs_delta_over_sigma_gt_5": float(np.mean(significance[finite] > 5.0)),
    }


def _residual_image(evidence: Any, geometry: Any, sampling: Any, evaluation: Any) -> np.ndarray:
    image = np.full(evidence.image.shape, np.nan)
    image[geometry.sample_y[sampling.sample_indices], geometry.sample_x[sampling.sample_indices]] = -evaluation.residuals
    return image


def _write_exposure(output: Path, result: TransferResult, frozen_trace: np.ndarray, *, residual_limit: float, raw_limit: float) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    raw_before, raw_after = result.raw_before, result.raw_after
    correction = result.final.trace - frozen_trace
    before_image, after_image = _residual_image(result.evidence, result.geometry, result.sampling, result.baseline), _residual_image(result.evidence, result.geometry, result.sampling, result.final)
    np.savez_compressed(
        output / "trace_transfer.npz", raw_deltaT_before=raw_before["raw_delta"], raw_deltaT_after=raw_after["raw_delta"],
        trace_information_before=raw_before["information"], sigma_T_before=raw_before["sigma"], significance_before=raw_before["significance"],
        incremental_trace_coeff=result.state.trace_coeff, accepted_deltaT=correction,
        unapplied_trace_coeff=result.proposal.incremental_trace_coeff,
        unapplied_deltaT=np.einsum("fxt,ft->fx", result.geometry.trace_basis, result.proposal.incremental_trace_coeff),
        baseline_residual=result.baseline.residuals, final_residual=result.final.residuals,
        baseline_model=result.baseline.model_samples, final_model=result.final.model_samples,
    )
    fig, axes = plt.subplots(2, 3, figsize=(16, 9), constrained_layout=True)
    observed_limit = float(np.nanpercentile(np.abs(result.evidence.image), 98.0))
    for axis, values, title, cmap, vmin, vmax in (
        (axes[0, 0], result.evidence.image, "observed transfer image", "viridis", -observed_limit, observed_limit),
        (axes[0, 1], before_image, "baseline model − data", "coolwarm", -residual_limit, residual_limit),
        (axes[0, 2], raw_before["raw_delta"], "raw deltaT", "coolwarm", -raw_limit, raw_limit),
        (axes[1, 0], raw_before["sigma"], "marginal sigma_T", "magma", 0.0, float(np.nanpercentile(raw_before["sigma"], 98))),
        (axes[1, 1], raw_before["significance"], "raw deltaT / sigma_T", "coolwarm", -5.0, 5.0),
        (axes[1, 2], after_image, "after transfer: model − data", "coolwarm", -residual_limit, residual_limit),
    ):
        image = axis.imshow(values, origin="lower", aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
        fig.colorbar(image, ax=axis)
        axis.set(title=title, xlabel="detector x", ylabel="detector y" if values.shape == result.evidence.image.shape else "fiber")
    fig.savefig(output / "transfer_diagnostics.png", dpi=160)
    plt.close(fig)
    proposal_delta = np.einsum("fxt,ft->fx", result.geometry.trace_basis, result.proposal.incremental_trace_coeff)
    report = {
        "kind": result.kind, "artifacts": _candidate_summary(result.input),
        "valid_pixel_count": int(np.count_nonzero(result.evidence.valid_mask)), "image_shape": list(result.evidence.image.shape),
        "loss_before": _residual_summary(result.baseline), "loss_after": _residual_summary(result.final),
        "loss_fractional_improvement": float((result.baseline.robust_loss - result.final.robust_loss) / result.baseline.robust_loss),
        "raw_deltaT_before": _summary(raw_before["raw_delta"]), "raw_deltaT_after": _summary(raw_after["raw_delta"]),
        "accepted_deltaT": _summary(correction), "accepted_iteration_history": result.history,
        "unaccepted_final_proposal": {**_summary(proposal_delta), "predicted_loss_change": result.proposal.predicted_loss_change},
        "trace_decomposition": _simple_trace_decomposition(correction, frozen_trace),
        "signal_to_noise": _snr_summary(raw_before),
        "absolute_weighted_residual_mode_power_after": result.mode_after["mode_weighted_residual_power"],
        "timing_seconds": result.timing_seconds,
        "frozen_response_trace_dy": True, "alpha": 0.0, "response_refit": False, "dy_refit": False,
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _write_comparison(output: Path, twilight: TransferResult, science: TransferResult, frozen_trace: np.ndarray, *, raw_limit: float) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    delta_t, delta_s = twilight.final.trace - frozen_trace, science.final.trace - frozen_trace
    info_t, info_s = twilight.raw_before["information"], science.raw_before["information"]
    sigma_t, sigma_s = twilight.raw_before["sigma"], science.raw_before["sigma"]
    well = np.isfinite(delta_t) & np.isfinite(delta_s) & (sigma_t <= np.nanpercentile(sigma_t, 75)) & (sigma_s <= np.nanpercentile(sigma_s, 75))
    correlation = float(np.corrcoef(delta_t[well], delta_s[well])[0, 1]) if np.count_nonzero(well) > 2 else float("nan")
    accepted_limit = float(np.nanpercentile(np.abs(np.r_[delta_t.ravel(), delta_s.ravel()]), 98.0))
    difference_limit = float(np.nanpercentile(np.abs(delta_t - delta_s), 98.0))
    fig, axes = plt.subplots(2, 3, figsize=(16, 9), constrained_layout=True)
    panels = (
        (twilight.raw_before["raw_delta"], "Twilight raw deltaT", raw_limit),
        (delta_t, "Twilight accepted DeltaT", accepted_limit),
        (science.raw_before["raw_delta"], "Science raw deltaT", raw_limit),
        (delta_s, "Science accepted DeltaT", accepted_limit),
        (delta_t - delta_s, "Twilight − Science accepted DeltaT", difference_limit),
        (np.log10(np.divide(info_t, info_s, out=np.full_like(info_t, np.nan), where=info_s > 0.0)), "log10 Twilight / Science trace information", 1.0),
    )
    for axis, (values, title, limit) in zip(axes.ravel(), panels):
        cmap = "coolwarm"
        image = axis.imshow(values, origin="lower", aspect="auto", cmap=cmap, vmin=-limit, vmax=limit)
        fig.colorbar(image, ax=axis, label="pixels" if "delta" in title else "log ratio")
        axis.set(title=title, xlabel="detector x", ylabel="fiber")
    fig.savefig(output / "twilight_science_comparison.png", dpi=170)
    plt.close(fig)
    report = {
        "accepted_deltaT_correlation_well_measured": correlation,
        "well_measured_definition": "both baseline sigma_T at or below each exposure's p75",
        "well_measured_count": int(np.count_nonzero(well)),
        "twilight_minus_science_deltaT": _summary(delta_t - delta_s),
    }
    (output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _run_transfer(item: TransferInput, *, frozen_trace: np.ndarray, reference_W: np.ndarray, reference_f: np.ndarray, settings: dict[str, Any], dy: Any, service: ArtifactService) -> TransferResult:
    timing: dict[str, float] = {}
    started = perf_counter()
    evidence = _make_transfer_evidence(service, item, frozen_trace=frozen_trace)
    timing["evidence"] = perf_counter() - started
    started = perf_counter()
    geometry = forward.build_ldls_geometry(
        evidence, support=float(settings["compact_support"]), trace_degree=4, response_degree=int(settings["response_degree"]),
        amplifier_boundary=float(UPPER_AMPLIFIER_Y_OFFSET), reference_W=reference_W, reference_f_sigma=reference_f,
        trace_margin=float(settings["trace_margin"]),
    )
    sampling = forward.build_ldls_sampling(evidence, geometry, mode="full")
    cache = forward.ProfileCache(float(settings["profile_cache_quantization"]))
    state = forward.initial_profile_trace_state(geometry)
    baseline = forward.evaluate_state(evidence, geometry, sampling, state, cache=cache, derivatives=True, debug_contributions=True, detector_displacement=dy)
    raw_before = _raw_trace_evidence(evidence, geometry, sampling, baseline)
    timing["baseline_and_raw_projection"] = perf_counter() - started
    started = perf_counter()
    state, final, history, proposal = _solve_transfer_trace(evidence, geometry, sampling, state, cache, dy)
    raw_after = _raw_trace_evidence(evidence, geometry, sampling, final)
    mode_after = raw_after["projection"]
    timing["coupled_trace_transfer_and_closure"] = perf_counter() - started
    return TransferResult(item.kind, item, evidence, geometry, sampling, baseline, final, state, history, proposal, raw_before, raw_after, mode_after, timing)


def _transfer_design(evidence: Any, *, degree_x: int, degree_y: int) -> tuple[np.ndarray, int]:
    """Independent physical-half tensor Legendre fields at fiber centres."""
    fibers, columns = evidence.base_trace.shape
    terms = (degree_x + 1) * (degree_y + 1)
    design = np.zeros((fibers, columns, 2 * terms), float)
    x = forward._normalised_coordinate(np.broadcast_to(np.arange(columns), (fibers, columns)), 0, columns - 1)
    for half, selected in enumerate((
        evidence.base_trace < UPPER_AMPLIFIER_Y_OFFSET,
        evidence.base_trace >= UPPER_AMPLIFIER_Y_OFFSET,
    )):
        y = forward._normalised_coordinate(
            evidence.base_trace[selected],
            0.0 if half == 0 else float(UPPER_AMPLIFIER_Y_OFFSET),
            float(UPPER_AMPLIFIER_Y_OFFSET - 1 if half == 0 else evidence.image.shape[0] - 1),
        )
        design[selected, half * terms:(half + 1) * terms] = forward.tensor_legendre_basis(
            x[selected], y, max(degree_x, degree_y),
        )[:, :terms] if degree_x == degree_y else np.column_stack([
            np.polynomial.legendre.legvander(x[selected], degree_x)[:, i] * np.polynomial.legendre.legvander(y, degree_y)[:, j]
            for i in range(degree_x + 1) for j in range(degree_y + 1)
        ])
    return design, terms


def _fit_low_dimensional_field(raw: dict[str, Any], evidence: Any, *, degree_x: int, degree_y: int) -> dict[str, Any]:
    """Robust IRLS fit of the raw local centroid measurements only."""
    design, terms = _transfer_design(evidence, degree_x=degree_x, degree_y=degree_y)
    observation, information = raw["raw_delta"], raw["information"]
    valid = np.isfinite(observation) & np.isfinite(information) & (information > 0.0)
    matrix, value, base_weight = design[valid], observation[valid], information[valid]
    coefficient = np.zeros(matrix.shape[1])
    robust_weight = np.ones_like(value)
    for _ in range(6):
        hessian = matrix.T @ ((base_weight * robust_weight)[:, None] * matrix)
        gradient = matrix.T @ (base_weight * robust_weight * value)
        next_coefficient = np.linalg.solve(hessian, gradient)
        residual = value - matrix @ next_coefficient
        center = float(np.median(residual))
        scale = float(1.4826 * np.median(np.abs(residual - center)))
        if not np.isfinite(scale) or scale <= 0.0:
            coefficient = next_coefficient
            break
        robust_weight = np.minimum(1.0, 4.685 * scale / np.maximum(np.abs(residual - center), np.finfo(float).tiny))
        if np.max(np.abs(next_coefficient - coefficient)) < 1e-7:
            coefficient = next_coefficient
            break
        coefficient = next_coefficient
    field = np.einsum("fxk,k->fx", design, coefficient)
    return {
        "degree_x": degree_x, "degree_y": degree_y, "coefficient": coefficient, "field": field,
        "terms_per_half": terms, "information_weighted": True,
        "robust_loss_scale_pixels": scale if "scale" in locals() else float("nan"),
        "robust_fit_fraction": float(np.mean(robust_weight < 1.0)),
    }


def _field_render_state(geometry: Any, field: np.ndarray) -> Any:
    """Represent a prescribed low-dimensional field in the existing renderer basis only."""
    coefficient = np.empty((field.shape[0], geometry.trace_basis.shape[-1]))
    for fiber in range(field.shape[0]):
        coefficient[fiber], *_ = np.linalg.lstsq(geometry.trace_basis[fiber], field[fiber], rcond=None)
    state = forward.initial_profile_trace_state(geometry)
    return replace(state, trace_coeff=coefficient, generation=1)


def _evaluate_low_dimensional(field_fit: dict[str, Any], result: TransferResult, dy: Any) -> dict[str, Any]:
    state = _field_render_state(result.geometry, field_fit["field"])
    cache = forward.ProfileCache(0.002)
    evaluation = forward.evaluate_state(
        result.evidence, result.geometry, result.sampling, state, cache=cache,
        derivatives=True, debug_contributions=True, detector_displacement=dy,
    )
    raw = _raw_trace_evidence(result.evidence, result.geometry, result.sampling, evaluation)
    flexible = result.final.trace - result.evidence.base_trace
    difference = evaluation.trace - result.final.trace
    variance = np.var(flexible)
    return {
        **field_fit, "state": state, "evaluation": evaluation, "raw_after": raw,
        "rms_difference_from_flexible_pixels": float(np.sqrt(np.mean(difference * difference))),
        "flexible_variance_explained": float(1.0 - np.var(difference) / variance) if variance > 0.0 else float("nan"),
        "loss": _residual_summary(evaluation),
    }


def _profile_half_step(result: TransferResult, state: Any, current: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Four frozen-trace profile-transfer normal equations: W/f by physical half."""
    hessian, gradient = np.zeros((4, 4)), np.zeros(4)
    geometry, sampling, evidence = result.geometry, result.sampling, result.evidence
    cf, cx = geometry.contribution_fiber, geometry.contribution_x
    derivative_w = current.total_amplitude[cf, cx] * current.PW
    derivative_f = current.total_amplitude[cf, cx] * current.Pf
    selected_set = np.zeros(geometry.sample_x.size, bool)
    selected_set[sampling.sample_indices] = True
    for block in np.unique(sampling.block_index):
        local, image_to_local = forward._local_sample_positions(geometry, sampling, int(block))
        included = (geometry.contribution_block == block) & selected_set[geometry.contribution_sample_index]
        rows = image_to_local[geometry.contribution_sample_index[included]]
        jacobian = np.zeros((local.size, 4))
        lower = result.evidence.base_trace[cf[included], cx[included]] < UPPER_AMPLIFIER_Y_OFFSET
        np.add.at(jacobian[:, 0], rows[lower], derivative_w[included][lower])
        np.add.at(jacobian[:, 1], rows[lower], derivative_f[included][lower])
        np.add.at(jacobian[:, 2], rows[~lower], derivative_w[included][~lower])
        np.add.at(jacobian[:, 3], rows[~lower], derivative_f[included][~lower])
        variance = evidence.variance[geometry.sample_y[sampling.sample_indices[local]], geometry.sample_x[sampling.sample_indices[local]]]
        weight = current.robust_weights[local] / np.maximum(variance, np.finfo(float).tiny)
        hessian += jacobian.T @ (weight[:, None] * jacobian)
        gradient += jacobian.T @ (weight * current.residuals[local])
    hessian += 1e-5 * max(float(np.trace(hessian)) / 4.0, 1.0) * np.eye(4)
    return np.linalg.solve(hessian, gradient), hessian, gradient


def _apply_profile_half_delta(state: Any, geometry: Any, delta: np.ndarray, damping: float) -> Any:
    terms = geometry.response_basis_W.shape[-1] // 2
    W_coeff, f_coeff = state.W_coeff.copy(), state.f_sigma_coeff.copy()
    W_coeff[0] += damping * delta[0]
    f_coeff[0] += damping * delta[1]
    W_coeff[terms] += damping * delta[2]
    f_coeff[terms] += damping * delta[3]
    return replace(state, W_coeff=W_coeff, f_sigma_coeff=f_coeff, generation=state.generation + 1)


def _fit_twilight_profile_transfer(result: TransferResult, low: dict[str, Any], dy: Any) -> dict[str, Any]:
    """Minimal four-parameter profile test after the selected 2-D trace only."""
    state, current = low["state"], low["evaluation"]
    before_modes = _raw_trace_evidence(result.evidence, result.geometry, result.sampling, current)["projection"]
    history, covariance = [], None
    cache = forward.ProfileCache(0.002)
    for iteration in range(2):
        delta, hessian, _ = _profile_half_step(result, state, current)
        covariance = np.linalg.inv(hessian)
        accepted, candidate, damping = state, current, 0.0
        for value in 0.5 ** np.arange(8):
            candidate_state = _apply_profile_half_delta(state, result.geometry, delta, float(value))
            try:
                candidate_eval = forward.evaluate_state(
                    result.evidence, result.geometry, result.sampling, candidate_state, cache=cache,
                    derivatives=True, debug_contributions=True, detector_displacement=dy,
                )
            except ValueError:
                continue
            if candidate_eval.robust_loss < current.robust_loss:
                accepted, candidate, damping = candidate_state, candidate_eval, float(value)
                break
        if damping == 0.0:
            break
        state, current = accepted, candidate
        history.append({"iteration": float(iteration), "damping": damping, "loss": current.robust_loss, "delta_W_lower": float(damping * delta[0]), "delta_f_lower": float(damping * delta[1]), "delta_W_upper": float(damping * delta[2]), "delta_f_upper": float(damping * delta[3])})
    after_modes = _raw_trace_evidence(result.evidence, result.geometry, result.sampling, current)["projection"]
    return {
        "state": state, "evaluation": current, "history": history,
        "delta_W_f_sigma_by_half": {"lower": [float(state.W_coeff[0]), float(state.f_sigma_coeff[0])], "upper": [float(state.W_coeff[result.geometry.response_basis_W.shape[-1] // 2]), float(state.f_sigma_coeff[result.geometry.response_basis_W.shape[-1] // 2])]},
        "estimated_uncertainty": None if covariance is None else np.sqrt(np.diag(covariance)).tolist(),
        "loss_before": _residual_summary(low["evaluation"]), "loss_after": _residual_summary(current),
        "mode_power_before": before_modes["mode_weighted_residual_power"], "mode_power_after": after_modes["mode_weighted_residual_power"],
        "mode_maps_before": before_modes["coefficients"][:, :, 2:],
    }


def _environment(row: dict[str, Any], ldls_temperature: float | None = None) -> dict[str, Any]:
    members = ((row.get("metadata") or {}).get("calibration_group") or {}).get("frame_membership") or ()
    times = [_time(str(item["timestamp"])) for item in members if item.get("timestamp")]
    temperature = [float(item["ambient_temperature"]) for item in members if item.get("ambient_temperature") is not None]
    midpoint = times[0] + (times[-1] - times[0]) / 2 if times else _midpoint(row)
    result = {
        "member_exposure_midpoint": midpoint.isoformat(),
        "member_exposure_count": len(members),
        "ambient_temperature": None if not temperature else float(np.median(temperature)),
        "ambient_temperature_range": None if not temperature else [float(np.min(temperature)), float(np.max(temperature))],
        "temperature_metadata_key": "metadata.calibration_group.frame_membership[].ambient_temperature",
    }
    if ldls_temperature is not None and result["ambient_temperature"] is not None:
        result["delta_ambient_temperature_from_ldls"] = result["ambient_temperature"] - ldls_temperature
    return result


def _write_low_dimensional_diagnostics(output: Path, twilight: TransferResult, science: TransferResult, low_twilight: dict[str, Any], low_science: dict[str, Any], frozen_trace: np.ndarray, *, raw_limit: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    field_limit = float(np.nanpercentile(np.abs(np.r_[low_twilight["field"].ravel(), low_science["field"].ravel()]), 98.0))
    info = twilight.raw_before["information"]
    log_information = np.full_like(info, np.nan)
    positive_information = np.isfinite(info) & (info > 0.0)
    log_information[positive_information] = np.log10(info[positive_information])
    panels = (
        (twilight.raw_before["raw_delta"], "Twilight raw deltaT", raw_limit, "coolwarm"),
        (low_twilight["field"], "Twilight 2-D transfer field", field_limit, "coolwarm"),
        (low_twilight["raw_after"]["raw_delta"], "Twilight residual deltaT", raw_limit, "coolwarm"),
        (science.raw_before["raw_delta"], "Science raw deltaT", raw_limit, "coolwarm"),
        (low_science["field"], "Science 2-D transfer field", field_limit, "coolwarm"),
        (low_science["raw_after"]["raw_delta"], "Science residual deltaT", raw_limit, "coolwarm"),
        (low_twilight["field"] - low_science["field"], "Twilight 2-D − Science 2-D", field_limit, "coolwarm"),
        (log_information, "Twilight log10 trace information", float(np.nanpercentile(np.abs(log_information[positive_information]), 98.0)), "viridis"),
        (twilight.final.trace - frozen_trace, "Twilight flexible degree-4 correction", field_limit, "coolwarm"),
    )
    fig, axes = plt.subplots(3, 3, figsize=(17, 12), constrained_layout=True)
    for axis, (values, title, limit, cmap) in zip(axes.ravel(), panels):
        if cmap == "coolwarm":
            image = axis.imshow(values, origin="lower", aspect="auto", cmap=cmap, vmin=-limit, vmax=limit)
        else:
            image = axis.imshow(values, origin="lower", aspect="auto", cmap=cmap)
        fig.colorbar(image, ax=axis)
        axis.set(title=title, xlabel="detector x", ylabel="fiber")
    fig.savefig(output / "low_dimensional_trace_comparison.png", dpi=170)
    plt.close(fig)


def _write_twilight_profile_maps(output: Path, result: TransferResult, profile: dict[str, Any]) -> None:
    values = profile["mode_maps_before"]
    limit = float(np.nanpercentile(np.abs(values), 98.0))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    x = np.broadcast_to(np.arange(values.shape[1]), values.shape[:2])
    trace = profile["evaluation"].trace
    for axis, field, title in zip(axes, values.transpose(2, 0, 1), ("Twilight W-mode projection", "Twilight f_sigma-mode projection")):
        image = axis.scatter(x.ravel(), trace.ravel(), c=field.ravel(), s=1.0, marker="s", linewidths=0, rasterized=True, cmap="coolwarm", vmin=-limit, vmax=limit)
        fig.colorbar(image, ax=axis)
        axis.set(title=title, xlabel="detector x", ylabel="detector y")
    fig.savefig(output / "twilight_profile_transfer_mode_maps.png", dpi=170)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--zipcode", required=True)
    parser.add_argument("--calibration-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--master-twilight-lower-id", type=int)
    parser.add_argument("--master-twilight-upper-id", type=int)
    parser.add_argument("--master-sci-lower-id", type=int)
    parser.add_argument("--master-sci-upper-id", type=int)
    args = parser.parse_args(argv)
    requested = parse_zipcode_key(args.zipcode)
    if requested.amp not in PAIR:
        raise ValueError("zipcode must select one amplifier of a physical CCD")
    side, lower_amp, upper_amp = PAIR[requested.amp]
    lower_zip = ZipCode(requested.ifuslot, requested.ifuid, requested.specid, lower_amp, requested.controller)
    upper_zip = ZipCode(requested.ifuslot, requested.ifuid, requested.specid, upper_amp, requested.controller)
    arrays, calibration_provenance, calibration_selection = _load_frozen_calibration(args.calibration_dir)
    service = ArtifactService(args.db)
    current_evidence, current_selection, _assembly_image = forward.load_ldls_evidence(service, requested)
    for key in ("lower_ldls_id", "upper_ldls_id", "lower_trace_id", "upper_trace_id"):
        if int(current_selection[key]) != int(calibration_selection[key]):
            raise RuntimeError(f"frozen calibration {key}={calibration_selection[key]} does not match active selected evidence {current_selection[key]}")
    calibration_geometry = forward.build_ldls_geometry(
        current_evidence, support=float(calibration_provenance["settings"]["compact_support"]), trace_degree=4,
        response_degree=int(calibration_provenance["settings"]["response_degree"]), amplifier_boundary=float(UPPER_AMPLIFIER_Y_OFFSET),
        reference_W=arrays["reference_W"], reference_f_sigma=arrays["reference_f_sigma"],
        trace_margin=float(calibration_provenance["settings"]["trace_margin"]),
    )
    calibration_state = forward.ProfileTraceState(arrays["trace_coeff"], arrays["W_coeff"], arrays["f_sigma_coeff"], int(arrays["state_generation"]))
    calibration_sampling = forward.build_ldls_sampling(current_evidence, calibration_geometry, mode="full")
    calibration_eval = forward.evaluate_state(current_evidence, calibration_geometry, calibration_sampling, calibration_state, cache=forward.ProfileCache(float(calibration_provenance["settings"]["profile_cache_quantization"])))
    frozen_trace = calibration_eval.trace
    dy = _load_dy(args.calibration_dir)
    twilight = _select_transfer_input(service, "master_twilight", lower_zip, upper_zip, lower_id=args.master_twilight_lower_id, upper_id=args.master_twilight_upper_id)
    science = _select_transfer_input(service, "master_sci", lower_zip, upper_zip, lower_id=args.master_sci_lower_id, upper_id=args.master_sci_upper_id)
    ldls_time = _midpoint(service.adapter.get_row(int(calibration_selection["lower_ldls_id"])))
    selection = {
        "physical_ccd_side": side, "frozen_ldls": calibration_selection,
        "ldls_midpoint": ldls_time.isoformat(),
        "twilight": {**_candidate_summary(twilight), "elapsed_hours_from_ldls": (twilight.midpoint - ldls_time).total_seconds() / 3600.0},
        "science": {**_candidate_summary(science), "elapsed_hours_from_ldls": (science.midpoint - ldls_time).total_seconds() / 3600.0},
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    log.info("selected frozen LDLS %s/%s; Twilight %s/%s; Science %s/%s", calibration_selection["lower_ldls_id"], calibration_selection["upper_ldls_id"], twilight.lower["id"], twilight.upper["id"], science.lower["id"], science.upper["id"])
    log.info("LDLS -> Twilight %.2f h; LDLS -> Science %.2f h", selection["twilight"]["elapsed_hours_from_ldls"], selection["science"]["elapsed_hours_from_ldls"])
    results = []
    for item in (twilight, science):
        log.info("%s transfer baseline -> coupled trace -> closure", item.kind)
        results.append(_run_transfer(item, frozen_trace=frozen_trace, reference_W=calibration_eval.W, reference_f=calibration_eval.f_sigma, settings=calibration_provenance["settings"], dy=dy, service=service))
    residual_limit = float(np.nanpercentile(np.abs(np.r_[results[0].baseline.residuals, results[0].final.residuals, results[1].baseline.residuals, results[1].final.residuals]), 98.0))
    raw_limit = float(np.nanpercentile(np.abs(np.r_[results[0].raw_before["raw_delta"].ravel(), results[1].raw_before["raw_delta"].ravel()]), 98.0))
    twilight_report = _write_exposure(args.output_dir / "twilight", results[0], frozen_trace, residual_limit=residual_limit, raw_limit=raw_limit)
    science_report = _write_exposure(args.output_dir / "science", results[1], frozen_trace, residual_limit=residual_limit, raw_limit=raw_limit)
    comparison = _write_comparison(args.output_dir / "comparison", results[0], results[1], frozen_trace, raw_limit=raw_limit)
    log.info("low-dimensional 2-D trace-transfer complexity test")
    complexity = []
    for degree_x, degree_y in ((1, 1), (2, 1), (2, 2), (3, 2)):
        fitted = _fit_low_dimensional_field(results[0].raw_before, results[0].evidence, degree_x=degree_x, degree_y=degree_y)
        evaluated = _evaluate_low_dimensional(fitted, results[0], dy)
        complexity.append(evaluated)
    best_loss = min(item["evaluation"].robust_loss for item in complexity)
    baseline_gain = results[0].baseline.robust_loss - best_loss
    selected = next(
        item for item in complexity
        if item["evaluation"].robust_loss <= best_loss + 0.01 * baseline_gain
    )
    science_fit = _fit_low_dimensional_field(
        results[1].raw_before, results[1].evidence,
        degree_x=selected["degree_x"], degree_y=selected["degree_y"],
    )
    low_science = _evaluate_low_dimensional(science_fit, results[1], dy)
    twilight_on_science = _evaluate_low_dimensional(
        {**selected, "field": selected["field"].copy()}, results[1], dy,
    )
    residual_science_fit = _fit_low_dimensional_field(
        twilight_on_science["raw_after"], results[1].evidence,
        degree_x=selected["degree_x"], degree_y=selected["degree_y"],
    )
    log.info("minimal Twilight profile-transfer diagnostic")
    twilight_profile = _fit_twilight_profile_transfer(results[0], selected, dy)
    low_output = args.output_dir / "low_dimensional"
    _write_low_dimensional_diagnostics(low_output, results[0], results[1], selected, low_science, frozen_trace, raw_limit=raw_limit)
    _write_twilight_profile_maps(low_output, results[0], twilight_profile)
    environment_ldls = _environment(service.adapter.get_row(int(calibration_selection["lower_ldls_id"])))
    environment_twilight = _environment(twilight.lower, environment_ldls["ambient_temperature"])
    environment_science = _environment(science.lower, environment_ldls["ambient_temperature"])
    for target, item in ((environment_twilight, twilight), (environment_science, science)):
        target["delta_time_hours_from_ldls"] = (item.midpoint - ldls_time).total_seconds() / 3600.0
    complexity_report = [{
        "degree_x": item["degree_x"], "degree_y": item["degree_y"], "parameter_count": 2 * item["terms_per_half"],
        "loss": item["loss"], "raw_post_transfer_deltaT": _summary(item["raw_after"]["raw_delta"]),
        "raw_post_transfer_significance": _snr_summary(item["raw_after"]),
    } for item in complexity]
    twilight_profile_report = {
        key: value for key, value in twilight_profile.items()
        if key not in {"state", "evaluation", "mode_maps_before"}
    }
    low_report = {
        "selection_rule": "lowest tested basis within one percent of the best Twilight forward-model loss improvement",
        "selected_basis": {"degree_x": selected["degree_x"], "degree_y": selected["degree_y"], "parameter_count": 2 * selected["terms_per_half"]},
        "twilight_complexity": complexity_report,
        "twilight_selected": {
            "loss": selected["loss"], "accepted_deltaT": _summary(selected["field"]),
            "rms_difference_from_flexible_pixels": selected["rms_difference_from_flexible_pixels"],
            "flexible_variance_explained": selected["flexible_variance_explained"],
            "raw_post_transfer_deltaT": _summary(selected["raw_after"]["raw_delta"]),
            "raw_post_transfer_significance": _snr_summary(selected["raw_after"]),
        },
        "science_selected": {
            "loss": low_science["loss"], "accepted_deltaT": _summary(low_science["field"]),
            "rms_difference_from_flexible_pixels": low_science["rms_difference_from_flexible_pixels"],
            "flexible_variance_explained": low_science["flexible_variance_explained"],
            "raw_post_transfer_deltaT": _summary(low_science["raw_after"]["raw_delta"]),
            "raw_post_transfer_significance": _snr_summary(low_science["raw_after"]),
        },
        "science_loss_comparison": {
            "frozen_LDLS": results[1].baseline.robust_loss,
            "Twilight_derived_2D": twilight_on_science["evaluation"].robust_loss,
            "Science_derived_2D": low_science["evaluation"].robust_loss,
            "Science_flexible_degree4": results[1].final.robust_loss,
        },
        "science_residual_after_twilight_field": {
            "raw_deltaT": _summary(twilight_on_science["raw_after"]["raw_delta"]),
            "diagnostic_same_basis_residual_field": _summary(residual_science_fit["field"]),
        },
        "twilight_profile_transfer": twilight_profile_report,
    }
    np.savez_compressed(
        low_output / "low_dimensional_transfer.npz",
        twilight_selected_field=selected["field"], science_selected_field=low_science["field"],
        twilight_flexible_field=results[0].final.trace - frozen_trace,
        science_flexible_field=results[1].final.trace - frozen_trace,
        twilight_on_science_raw_deltaT=twilight_on_science["raw_after"]["raw_delta"],
        science_residual_same_basis_field=residual_science_fit["field"],
    )
    (low_output / "summary.json").write_text(json.dumps(low_report, indent=2, default=lambda value: value.tolist() if isinstance(value, np.ndarray) else value) + "\n")
    report = {
        "selection": selection, "environmental_provenance": {"LDLS": environment_ldls, "Twilight": environment_twilight, "Science": environment_science},
        "twilight": twilight_report, "science": science_report, "comparison": comparison,
        "low_dimensional_trace_transfer": low_report, "frozen_state_modified": False,
    }
    (args.output_dir / "trace_transfer_experiment.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"trace transfer completed: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
