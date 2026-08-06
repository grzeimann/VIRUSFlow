from __future__ import annotations

"""Run-local calibrated fiber state and final per-fiber response division."""

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ..core.algo_result import AlgoResult


RESPONSE_VERSION = "relative-response-atmosphere-separated-4.0"


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
    mirror_illumination=None,
    baseline_atmospheric_content="absorbed_unknown",
    extinction_correction=None,
    extinction_uncertainty=None,
    extinction_mask=None,
) -> AlgoResult:
    """Apply one baseline and explicit, non-overlapping multiplicative factors.

    Baseline uncertainty is added when measured.  Unknown imported uncertainty
    remains explicit in the evaluated mask and is not silently invented.
    """

    spectrum = np.asarray(sky_subtracted, dtype=float)
    input_variance = np.asarray(spectrum_variance, dtype=float)
    if spectrum.ndim != 2 or input_variance.shape != spectrum.shape:
        raise ValueError("spectrum and variance must be matched fiber-by-wavelength arrays")
    if baseline_atmospheric_content not in {"absorbed_unknown", "removed_with_model"}:
        raise ValueError(
            "baseline atmospheric_content must be 'absorbed_unknown' or 'removed_with_model'"
        )
    has_extinction = extinction_correction is not None
    if baseline_atmospheric_content == "absorbed_unknown" and has_extinction:
        raise ValueError(
            "an atmosphere-absorbing baseline cannot be combined with a separate extinction correction"
        )
    if baseline_atmospheric_content == "removed_with_model" and not has_extinction:
        raise ValueError(
            "an atmosphere-separated baseline requires a separate extinction correction"
        )

    def gray_factor(value, name):
        if value is None:
            return np.ones((spectrum.shape[0], 1), dtype=float)
        factor = np.asarray(value, dtype=float)
        if factor.ndim == 0:
            factor = np.full((spectrum.shape[0], 1), float(factor), dtype=float)
        elif factor.size == spectrum.shape[0]:
            factor = factor.reshape(-1, 1)
        else:
            raise ValueError(f"{name} must be scalar or have one value per fiber")
        if np.any(~np.isfinite(factor) | (factor <= 0.0)):
            raise ValueError(f"{name} must contain finite positive values")
        return factor

    baseline, baseline_sigma, evaluated_baseline_mask = _evaluate_baseline_response(
        wavelength, baseline_wavelength, baseline_response, baseline_uncertainty, baseline_mask
    )
    illumination = gray_factor(fiber_illumination, "fiber illumination")
    transparency = gray_factor(exposure_transparency, "exposure transparency")
    mirror = gray_factor(mirror_illumination, "mirror illumination")
    denominator = baseline * illumination * transparency * mirror
    below_atmosphere_flux = spectrum / denominator

    if has_extinction:
        correction = np.broadcast_to(
            np.asarray(extinction_correction, dtype=float), spectrum.shape
        )
        if extinction_uncertainty is None or extinction_mask is None:
            raise ValueError(
                "extinction correction requires matched uncertainty and mask arrays"
            )
        correction_sigma = np.broadcast_to(
            np.asarray(extinction_uncertainty, dtype=float), spectrum.shape
        )
        evaluated_extinction_mask = np.broadcast_to(
            np.asarray(extinction_mask, dtype=np.uint16), spectrum.shape
        )
        valid_extinction = (evaluated_extinction_mask & 1) == 0
        if np.any(valid_extinction & (~np.isfinite(correction) | (correction <= 0.0))):
            raise ValueError("valid extinction correction factors must be finite and positive")
        if np.any(
            valid_extinction
            & np.isfinite(correction_sigma)
            & (correction_sigma < 0.0)
        ):
            raise ValueError("extinction correction uncertainties must be non-negative")
    else:
        correction = np.ones(spectrum.shape, dtype=float)
        correction_sigma = np.full(spectrum.shape, np.nan, dtype=float)
        evaluated_extinction_mask = np.zeros(spectrum.shape, dtype=np.uint16)

    calibrated_flux = below_atmosphere_flux * correction
    statistical_variance = input_variance / np.square(denominator) * np.square(correction)
    response_uncertainty_variance = np.square(
        spectrum
        * correction
        * baseline_sigma
        / (illumination * transparency * mirror * np.square(baseline))
    )
    known_response_uncertainty = np.isfinite(response_uncertainty_variance)
    extinction_uncertainty_variance = np.square(below_atmosphere_flux * correction_sigma)
    known_extinction_uncertainty = np.isfinite(extinction_uncertainty_variance)
    calibrated_variance = statistical_variance + np.where(
        known_response_uncertainty, response_uncertainty_variance, 0.0
    ) + np.where(
        known_extinction_uncertainty, extinction_uncertainty_variance, 0.0
    )
    final_mask = np.zeros(calibrated_flux.shape, dtype=np.uint16)
    final_mask[
        ~np.isfinite(calibrated_flux) | ~np.isfinite(calibrated_variance) | ~np.isfinite(np.asarray(wavelength))
    ] |= 1
    final_mask[np.asarray(valid_fraction) < 0.8] |= 2
    final_mask[(evaluated_baseline_mask & 1) != 0] |= 4
    final_mask[(evaluated_extinction_mask & 1) != 0] |= 8
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
            "mirror_illumination_factor": np.broadcast_to(mirror, spectrum.shape),
            "extinction_correction_factor": correction,
            "extinction_correction_uncertainty": correction_sigma,
            "evaluated_extinction_mask": evaluated_extinction_mask,
            "response_denominator": denominator,
            "statistical_variance": statistical_variance,
            "response_uncertainty_variance": response_uncertainty_variance,
            "extinction_uncertainty_variance": extinction_uncertainty_variance,
        },
        scalars={
            "baseline_applied_count": 1,
            "illumination_applied_count": 1,
            "transparency_measurement_present": exposure_transparency is not None,
            "transparency_applied_count": int(exposure_transparency is not None),
            "mirror_illumination_applied_count": int(mirror_illumination is not None),
            "extinction_applied_count": int(has_extinction),
            "baseline_atmospheric_content": baseline_atmospheric_content,
            "baseline_uncertainty_unknown_fraction": float(
                np.mean(~known_response_uncertainty)
            ),
            "extinction_uncertainty_unknown_fraction": (
                float(np.mean(~known_extinction_uncertainty)) if has_extinction else 0.0
            ),
        },
    )
