"""Extract the detector-coordinate Master Science image into fiber spectra."""

from __future__ import annotations

import numpy as np

from ..core.algo_result import AlgoResult
from .exposure import extract_fractional_aperture


ALGO_VERSION = "master-sci-fractional-aperture-1.0"


def extract_master_sci_spectrum(
    master_sci: np.ndarray,
    fiber_trace_map: np.ndarray,
    *,
    aperture_width: float = 5.0,
) -> AlgoResult:
    """Apply the canonical fractional aperture to a Master Science image.

    The extraction is in detector-column space.  A zero variance plane is used
    only to reuse the canonical geometry and finite-pixel accounting; no
    extracted variance is asserted for this robustly combined image.
    """

    image = np.asarray(master_sci, dtype=float)
    trace = np.asarray(fiber_trace_map, dtype=float)
    if image.ndim != 2 or trace.ndim != 2 or trace.shape[1] != image.shape[1]:
        raise ValueError("master_sci and fiber_trace_map shapes are incompatible")
    extraction = extract_fractional_aperture(
        image,
        np.zeros_like(image, dtype=float),
        trace,
        width=float(aperture_width),
    )
    spectrum = np.asarray(extraction.spectrum, dtype=np.float32)
    valid = np.asarray(extraction.extraction_valid, dtype=np.uint8)
    return AlgoResult(
        kind="extracted_master_sci_spectrum",
        version=ALGO_VERSION,
        arrays={
            "spectrum": spectrum,
            "valid_pixel_fraction": np.asarray(
                extraction.valid_pixel_fraction, dtype=np.float32
            ),
            "effective_aperture_width": np.asarray(
                extraction.effective_aperture_width, dtype=np.float32
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
