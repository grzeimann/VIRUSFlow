"""Extract detector-coordinate master images into per-fiber spectra."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult
from .extraction import extract_fractional_aperture


ALGO_VERSION = "master-fractional-aperture-1.1"


def extract_master_spectrum(
    master_image: np.ndarray,
    fiber_trace_map: np.ndarray,
    *,
    result_kind: str,
    aperture_width: float = 5.0,
    pixel_mask: np.ndarray | None = None,
) -> AlgoResult:
    """Apply the canonical fractional aperture to one master image.

    The returned spectrum remains in detector-column coordinates.  A zero
    variance plane is supplied only to reuse the canonical extraction geometry;
    no variance estimate is asserted for a robustly combined master image.
    """

    image = np.asarray(master_image, dtype=float)
    trace = np.asarray(fiber_trace_map, dtype=float)
    if image.ndim != 2 or trace.ndim != 2 or trace.shape[1] != image.shape[1]:
        raise ValueError("master_image and fiber_trace_map shapes are incompatible")
    extraction = extract_fractional_aperture(
        image,
        np.zeros_like(image, dtype=float),
        trace,
        pixel_mask=pixel_mask,
        width=float(aperture_width),
    )
    spectrum = np.asarray(extraction.get_array("spectrum"), dtype=np.float32)
    valid = np.asarray(extraction.get_array("extraction_valid"), dtype=np.uint8)
    weights = np.asarray(extraction.get_array("fractional_weights"), dtype=np.float32)
    if weights.shape[-1] > 8:
        raise ValueError(
            "compact aperture sample evidence supports at most eight detector rows"
        )
    sample_mask_bits = np.zeros(weights.shape[:-1], dtype=np.uint8)
    for index in range(weights.shape[-1]):
        sample_mask_bits |= ((weights[..., index] > 0.0).astype(np.uint8) << index)
    return AlgoResult(
        kind=str(result_kind),
        version=ALGO_VERSION,
        arrays={
            "spectrum": spectrum,
            "valid_pixel_fraction": np.asarray(
                extraction.get_array("valid_pixel_fraction"), dtype=np.float32
            ),
            "effective_aperture_width": np.asarray(
                extraction.get_array("effective_aperture_width"), dtype=np.float32
            ),
            "extraction_valid": valid,
            "aperture_start_row": np.asarray(
                extraction.get_array("aperture_start_row"), dtype=np.int16
            ),
            "aperture_first_weight": weights[..., 0],
            "aperture_last_weight": weights[..., -1],
            "aperture_sample_mask_bits": sample_mask_bits,
        },
        scalars={
            "fiber_count": int(spectrum.shape[0]),
            "spectral_sample_count": int(spectrum.shape[1]),
            "valid_sample_fraction": float(valid.mean()),
        },
        meta={
            "spectrum_shape": list(spectrum.shape),
            "extraction_method": "fractional_top_hat_aperture",
            "aperture_width_pixels": float(aperture_width),
            "fractional_boundary_weighting": True,
            "output_scale_convention": "integrated_aperture_counts",
            "spectral_coordinate": "detector_column",
            "pixel_mask_applied": pixel_mask is not None,
            "aperture_sample_mask_encoding": (
                "bit_i_is_one_when_detector_row_start_plus_i_contributed"
            ),
            "aperture_sample_count": int(weights.shape[-1]),
        },
    )
