"""Wavelength-dependent atmospheric-extinction models and evaluation."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult


EXTINCTION_VERSION = "magnitude-per-airmass-extinction-1.0"
INVALID_MODEL_BIT = np.uint16(1)
UNCERTAINTY_UNAVAILABLE_BIT = np.uint16(2)


def atmospheric_extinction_model(
    wavelength, extinction_coefficient, uncertainty, mask, *, version: str
) -> AlgoResult:
    """Validate one extinction model expressed in magnitudes per airmass."""

    wave = np.asarray(wavelength, dtype=float)
    coefficient = np.asarray(extinction_coefficient, dtype=float)
    sigma = np.asarray(uncertainty, dtype=float)
    flags = np.asarray(mask)
    if wave.ndim != 1 or not (coefficient.shape == sigma.shape == flags.shape == wave.shape):
        raise ValueError(
            "extinction wavelength, coefficient, uncertainty, and mask must be matched 1D arrays"
        )
    if wave.size < 2 or not np.all(np.isfinite(wave)) or not np.all(np.diff(wave) > 0.0):
        raise ValueError("extinction wavelength must be finite and strictly increasing")
    if flags.dtype.kind not in "uib" or np.any(flags < 0) or np.any(flags > 65535):
        raise ValueError("extinction mask must contain uint16-compatible non-negative integers")
    flags = flags.astype(np.uint16)
    valid = (flags & INVALID_MODEL_BIT) == 0
    if not np.any(valid):
        raise ValueError("extinction model must contain at least one valid coefficient")
    if np.any(valid & (~np.isfinite(coefficient) | (coefficient < 0.0))):
        raise ValueError("valid extinction coefficients must be finite and non-negative")
    uncertainty_unknown = (flags & UNCERTAINTY_UNAVAILABLE_BIT) != 0
    if np.any(~uncertainty_unknown & (~np.isfinite(sigma) | (sigma < 0.0))):
        raise ValueError("known extinction uncertainties must be finite and non-negative")
    return AlgoResult(
        kind="atmospheric_extinction_model",
        version=version,
        arrays={
            "wavelength": wave.astype(np.float32),
            "extinction_coefficient": coefficient.astype(np.float32),
            "uncertainty": sigma.astype(np.float32),
            "mask": flags,
        },
        scalars={
            "wavelength_min_angstrom": float(wave[0]),
            "wavelength_max_angstrom": float(wave[-1]),
            "valid_fraction": float(np.mean(valid)),
            "uncertainty_unknown_fraction": float(np.mean(uncertainty_unknown)),
        },
    )


def evaluate_atmospheric_extinction(
    wavelength,
    model_wavelength,
    extinction_coefficient,
    uncertainty,
    mask,
    *,
    airmass,
    range_policy: str = "mask",
) -> AlgoResult:
    """Evaluate transmission and above-atmosphere correction on a science grid."""

    if airmass is None:
        raise ValueError("explicit exposure airmass is required for atmospheric extinction")
    try:
        exposure_airmass = float(airmass)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("exposure airmass must be a finite positive number") from exc
    if not np.isfinite(exposure_airmass) or exposure_airmass <= 0.0:
        raise ValueError("exposure airmass must be a finite positive number")
    if range_policy not in {"mask", "fail"}:
        raise ValueError("extinction range_policy must be 'mask' or 'fail'")

    model = atmospheric_extinction_model(
        model_wavelength,
        extinction_coefficient,
        uncertainty,
        mask,
        version=EXTINCTION_VERSION,
    )
    wave = np.asarray(wavelength, dtype=float)
    knots = np.asarray(model.get_array("wavelength"), dtype=float)
    values = np.asarray(model.get_array("extinction_coefficient"), dtype=float)
    errors = np.asarray(model.get_array("uncertainty"), dtype=float)
    knot_mask = np.asarray(model.get_array("mask"), dtype=np.uint16)
    flat = wave.reshape(-1)
    outside = (flat < knots[0]) | (flat > knots[-1]) | ~np.isfinite(flat)
    if range_policy == "fail" and np.any(outside):
        requested = flat[outside & np.isfinite(flat)]
        requested_text = (
            "non-finite wavelength"
            if requested.size == 0
            else f"requested range {float(np.min(requested)):.3f}--{float(np.max(requested)):.3f} Angstrom"
        )
        raise ValueError(
            f"atmospheric-extinction evaluation is outside the valid "
            f"{knots[0]:.3f}--{knots[-1]:.3f} Angstrom range: {requested_text}"
        )

    evaluated = np.interp(flat, knots, values, left=np.nan, right=np.nan)
    finite_errors = np.isfinite(errors)
    evaluated_uncertainty = np.interp(
        flat, knots, np.where(finite_errors, errors, 0.0), left=np.nan, right=np.nan
    )
    indices = np.searchsorted(knots, flat, side="left")
    right = np.clip(indices, 0, knots.size - 1)
    left = np.clip(indices - 1, 0, knots.size - 1)
    exact = (~outside) & (knots[right] == flat)
    left[exact] = right[exact]
    evaluated_mask = knot_mask[left] | knot_mask[right]
    evaluated_mask[outside] |= INVALID_MODEL_BIT
    uncertainty_known = finite_errors[left] & finite_errors[right] & ~outside
    evaluated_uncertainty[~uncertainty_known] = np.nan
    evaluated_mask[~uncertainty_known] |= UNCERTAINTY_UNAVAILABLE_BIT
    invalid = (evaluated_mask & INVALID_MODEL_BIT) != 0
    evaluated[invalid] = np.nan

    transmission = np.power(10.0, -0.4 * evaluated * exposure_airmass)
    correction = np.power(10.0, 0.4 * evaluated * exposure_airmass)
    correction_uncertainty = (
        correction * (0.4 * np.log(10.0) * exposure_airmass) * evaluated_uncertainty
    )
    shape = wave.shape
    return AlgoResult(
        kind="atmospheric_extinction_evaluation",
        version=EXTINCTION_VERSION,
        arrays={
            "extinction_coefficient": evaluated.reshape(shape),
            "extinction_uncertainty": evaluated_uncertainty.reshape(shape),
            "mask": evaluated_mask.reshape(shape),
            "transmission": transmission.reshape(shape),
            "correction_factor": correction.reshape(shape),
            "correction_uncertainty": correction_uncertainty.reshape(shape),
        },
        scalars={
            "airmass": exposure_airmass,
            "extinction_applied_count": 1,
            "uncertainty_unknown_fraction": float(np.mean(~uncertainty_known)),
            "outside_valid_range_count": int(np.sum(outside)),
            "range_policy": range_policy,
        },
    )
