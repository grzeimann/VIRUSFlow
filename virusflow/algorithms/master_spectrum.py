"""Extract detector-coordinate master images into per-fiber spectra."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult
from .extraction import extract_fractional_aperture


ALGO_VERSION = "master-fractional-aperture-1.0"


def extract_master_spectrum(
    master_image: np.ndarray,
    fiber_trace_map: np.ndarray,
    *,
    result_kind: str,
    aperture_width: float = 5.0,
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
        width=float(aperture_width),
    )
    spectrum = np.asarray(extraction.get_array("spectrum"), dtype=np.float32)
    valid = np.asarray(extraction.get_array("extraction_valid"), dtype=np.uint8)
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
        },
    )
