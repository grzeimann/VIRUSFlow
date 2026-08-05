from __future__ import annotations

"""Run-local calibrated fiber state and final per-fiber response division."""

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ..core.algo_result import AlgoResult


RESPONSE_VERSION = "relative-response-factorized-3.0"


@dataclass(frozen=True)
class CalibratedFiberState:
    """Run-local final state passed to observation assembly without persistence."""

    exposure_id: str
    flux: np.ndarray
    variance: np.ndarray
    mask: np.ndarray
    wavelength: np.ndarray
    fiber_identity: np.ndarray
    sky_coordinates: np.ndarray
    focal_plane_coordinates: np.ndarray
    model_artifact_ids: tuple[int, ...]
    metadata: Mapping


def _evaluate_baseline_response(wavelength, baseline_wavelength, response, uncertainty, mask):
    wave = np.asarray(wavelength, dtype=float)
    knots = np.asarray(baseline_wavelength, dtype=float)
    values = np.asarray(response, dtype=float)
    errors = np.asarray(uncertainty, dtype=float)
    knot_mask = np.asarray(mask, dtype=np.uint16)
    flat = wave.reshape(-1)
    evaluated = np.interp(flat, knots, values, left=np.nan, right=np.nan)
    finite_errors = np.isfinite(errors)
    evaluated_uncertainty = np.interp(
        flat, knots, np.where(finite_errors, errors, 0.0), left=np.nan, right=np.nan
    )
    indices = np.searchsorted(knots, flat, side="left")
    outside = (flat < knots[0]) | (flat > knots[-1])
    right = np.clip(indices, 0, knots.size - 1)
    left = np.clip(indices - 1, 0, knots.size - 1)
    exact = (~outside) & (knots[right] == flat)
    left[exact] = right[exact]
    evaluated_mask = knot_mask[left] | knot_mask[right]
    evaluated_mask[outside] |= 1
    uncertainty_known = finite_errors[left] & finite_errors[right] & ~outside
    evaluated_uncertainty[~uncertainty_known] = np.nan
    evaluated_mask[~uncertainty_known] |= 2
    return (
        evaluated.reshape(wave.shape),
        evaluated_uncertainty.reshape(wave.shape),
        evaluated_mask.reshape(wave.shape),
    )


def apply_relative_response(
    sky_subtracted,
    spectrum_variance,
    wavelength,
    valid_fraction,
    *,
    baseline_wavelength,
    baseline_response,
    baseline_uncertainty,
    baseline_mask,
    fiber_illumination,
    exposure_transparency=None,
) -> AlgoResult:
    """Apply one baseline and separate exposure factors with exact variance scaling.

    Baseline uncertainty is added when measured.  Unknown imported uncertainty
    remains explicit in the evaluated mask and is not silently invented.
    """

    spectrum = np.asarray(sky_subtracted, dtype=float)
    input_variance = np.asarray(spectrum_variance, dtype=float)
    baseline, baseline_sigma, evaluated_baseline_mask = _evaluate_baseline_response(
        wavelength, baseline_wavelength, baseline_response, baseline_uncertainty, baseline_mask
    )
    illumination = np.asarray(fiber_illumination, dtype=float)[:, None]
    transparency = (
        np.ones((spectrum.shape[0], 1), dtype=float)
        if exposure_transparency is None
        else np.asarray(exposure_transparency, dtype=float).reshape(-1, 1)
    )
    final_response = baseline * illumination * transparency
    calibrated_flux = spectrum / final_response
    statistical_variance = input_variance / np.square(final_response)
    response_uncertainty_variance = np.square(
        spectrum * baseline_sigma / (illumination * transparency * np.square(baseline))
    )
    known_uncertainty = np.isfinite(response_uncertainty_variance)
    calibrated_variance = statistical_variance + np.where(
        known_uncertainty, response_uncertainty_variance, 0.0
    )
    final_mask = np.zeros(calibrated_flux.shape, dtype=np.uint16)
    final_mask[
        ~np.isfinite(calibrated_flux) | ~np.isfinite(calibrated_variance) | ~np.isfinite(np.asarray(wavelength))
    ] |= 1
    final_mask[np.asarray(valid_fraction) < 0.8] |= 2
    final_mask[(evaluated_baseline_mask & 1) != 0] |= 4
    return AlgoResult(
        kind="relative_response_application",
        version=RESPONSE_VERSION,
        arrays={
            "calibrated_flux": calibrated_flux,
            "calibrated_variance": calibrated_variance,
            "mask": final_mask,
            "evaluated_baseline_response": baseline,
            "evaluated_baseline_uncertainty": baseline_sigma,
            "evaluated_baseline_mask": evaluated_baseline_mask,
            "illumination_factor": np.broadcast_to(illumination, spectrum.shape),
            "transparency_factor": np.broadcast_to(transparency, spectrum.shape),
            "statistical_variance": statistical_variance,
            "response_uncertainty_variance": response_uncertainty_variance,
        },
        scalars={
            "baseline_applied_count": 1,
            "illumination_applied_count": 1,
            "transparency_measurement_present": exposure_transparency is not None,
            "baseline_uncertainty_unknown_fraction": float(np.mean(~known_uncertainty)),
        },
    )
