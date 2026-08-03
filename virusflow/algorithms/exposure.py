from __future__ import annotations

"""Run-local calibrated fiber state and final per-fiber response division."""

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ..core.algo_result import AlgoResult


RESPONSE_VERSION = "relative-response-factorized-2.0"


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


def apply_relative_response(sky_subtracted, spectrum_variance, wavelength, valid_fraction, fiber_illumination) -> AlgoResult:
    """Divide by the final per-fiber response and derive the quality mask bits."""

    final_response = np.asarray(fiber_illumination, dtype=float)[:, None]
    calibrated_flux = np.asarray(sky_subtracted, dtype=float) / final_response
    calibrated_variance = np.asarray(spectrum_variance, dtype=float) / np.square(final_response)
    final_mask = np.zeros(calibrated_flux.shape, dtype=np.uint16)
    final_mask[
        ~np.isfinite(calibrated_flux) | ~np.isfinite(calibrated_variance) | ~np.isfinite(np.asarray(wavelength))
    ] |= 1
    final_mask[np.asarray(valid_fraction) < 0.8] |= 2
    return AlgoResult(
        kind="relative_response_application",
        version=RESPONSE_VERSION,
        arrays={
            "calibrated_flux": calibrated_flux,
            "calibrated_variance": calibrated_variance,
            "mask": final_mask,
        },
    )
