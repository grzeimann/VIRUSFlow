from __future__ import annotations

"""Empirical VIRUS differential atmospheric refraction (DAR) seed model.

Promotes the legacy Remedy DAR curve as the default starting model, following
the convention recorded in
``docs/architecture/spatial-psf-dar-coupling-resource-pycharm.md``: a cubic
fit through five tabulated wavelength/displacement measurements, rotated by
an instrument-angle convention into instrument-plane offsets, then converted
to sky-plane ``delta_ra``/``delta_dec`` using the actual VIRUSFlow astrometric
transform. This is a seed, not a general DAR framework: it is evaluated
directly on each exposure's own wavelength grid and astrometry.
"""

from typing import Callable, Tuple

import numpy as np

from ..core.algo_result import AlgoResult
from .astrometry import ASTROMETRY_VERSION, tan_fiber_coordinates


DAR_VERSION = "remedy-empirical-dar-seed-1.0"

# The five Remedy source measurements: tabulated chromatic displacement in
# arcseconds at five wavelengths (Angstrom), reproduced verbatim from
# Remedy's extract.py.
DAR_SOURCE_WAVELENGTH = np.array([3500.0, 4000.0, 4500.0, 5000.0, 5500.0])
DAR_SOURCE_DISPLACEMENT = np.array([-0.74, -0.40, -0.08, 0.08, 0.20])

ZERO_POINT_CONVENTION = (
    "absolute: the cubic curve is evaluated directly at each requested "
    "wavelength with no reference-wavelength subtraction; the astrometric "
    "reference position passed to the transform defines the instrument-plane "
    "origin (0, 0), not a zero-displacement wavelength"
)

INSTRUMENT_ANGLE_CONVENTION = (
    "angle measured counterclockwise from instrument +x; "
    "delta_x = cos(angle) * dar_scalar, delta_y = sin(angle) * dar_scalar"
)


def dar_seed_model(
    source_wavelength=DAR_SOURCE_WAVELENGTH,
    source_displacement=DAR_SOURCE_DISPLACEMENT,
    *,
    version: str = DAR_VERSION,
) -> AlgoResult:
    """Fit the Remedy cubic DAR displacement curve from five source measurements."""

    wave = np.asarray(source_wavelength, dtype=float)
    displacement = np.asarray(source_displacement, dtype=float)
    if wave.ndim != 1 or wave.shape != displacement.shape:
        raise ValueError("DAR source wavelength and displacement must be matched 1D arrays")
    if wave.size < 4:
        raise ValueError("a cubic DAR seed fit requires at least four source measurements")
    if not np.all(np.isfinite(wave)) or not np.all(np.isfinite(displacement)):
        raise ValueError("DAR source measurements must be finite")
    if not np.all(np.diff(wave) > 0.0):
        raise ValueError("DAR source wavelength must be strictly increasing")

    coefficients = np.polyfit(wave, displacement, 3)
    return AlgoResult(
        kind="dar_seed_model",
        version=version,
        arrays={
            "source_wavelength": wave.astype(np.float32),
            "source_displacement": displacement.astype(np.float32),
            "cubic_coefficients": coefficients.astype(np.float64),
        },
        scalars={
            "wavelength_min_angstrom": float(wave[0]),
            "wavelength_max_angstrom": float(wave[-1]),
            "zero_point_convention": ZERO_POINT_CONVENTION,
            "instrument_angle_convention": INSTRUMENT_ANGLE_CONVENTION,
            "source_baseline": "legacy Remedy extract.py DAR curve",
            "fit_degree": 3,
        },
    )


def tan_plane_dar_transform(
    ra0: float, dec0: float, pa: float, *, system_rotation: float = 1.55
) -> Tuple[Callable[[np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray]], str]:
    """Bind the VIRUSFlow TAN astrometric transform for DAR seed evaluation.

    Returns a ``(delta_x_arcsec, delta_y_arcsec) -> (ra_deg, dec_deg)`` callable
    built directly from :func:`tan_fiber_coordinates`, and an identity string
    recording the astrometry algorithm version and the bound exposure
    parameters, so the transform used is retained without a second
    independent rotation or a reimplementation of the WCS/axis convention.
    """

    ra0 = float(ra0)
    dec0 = float(dec0)
    pa = float(pa)
    system_rotation = float(system_rotation)

    def transform(delta_x_arcsec, delta_y_arcsec):
        result = tan_fiber_coordinates(
            ra0, dec0, pa, delta_x_arcsec, delta_y_arcsec, system_rotation=system_rotation
        )
        return result.get_array("ra"), result.get_array("dec")

    identity = (
        f"{ASTROMETRY_VERSION}:ra0={ra0:.8f},dec0={dec0:.8f},pa={pa:.8f},"
        f"system_rotation={system_rotation:.8f}"
    )
    return transform, identity


def evaluate_dar_seed(
    wavelength,
    *,
    cubic_coefficients,
    angle_deg: float,
    astrometric_transform: Callable[[np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray]],
    astrometric_transform_identity: str,
    reference_ra_deg: float,
    reference_dec_deg: float,
    version: str = DAR_VERSION,
) -> AlgoResult:
    """Evaluate the DAR seed on an exposure's wavelength grid and astrometry."""

    wave = np.asarray(wavelength, dtype=float)
    if wave.size == 0:
        raise ValueError("DAR seed evaluation requires at least one wavelength sample")
    coefficients = np.asarray(cubic_coefficients, dtype=float)

    dar_scalar = np.polyval(coefficients, wave)
    angle = np.deg2rad(float(angle_deg))
    delta_x = np.cos(angle) * dar_scalar
    delta_y = np.sin(angle) * dar_scalar

    ra_wave, dec_wave = astrometric_transform(delta_x, delta_y)
    ra_wave = np.asarray(ra_wave, dtype=float)
    dec_wave = np.asarray(dec_wave, dtype=float)

    reference_ra_deg = float(reference_ra_deg)
    reference_dec_deg = float(reference_dec_deg)
    delta_ra = (ra_wave - reference_ra_deg) * 3600.0 * np.cos(np.deg2rad(reference_dec_deg))
    delta_dec = (dec_wave - reference_dec_deg) * 3600.0

    return AlgoResult(
        kind="dar_seed_evaluation",
        version=version,
        arrays={
            "wavelength": wave.astype(np.float32),
            "delta_x": delta_x.astype(np.float32),
            "delta_y": delta_y.astype(np.float32),
            "delta_ra": delta_ra.astype(np.float32),
            "delta_dec": delta_dec.astype(np.float32),
        },
        scalars={
            "angle_deg": float(angle_deg),
            "reference_ra_deg": reference_ra_deg,
            "reference_dec_deg": reference_dec_deg,
            "astrometric_transform_identity": astrometric_transform_identity,
            "instrument_angle_convention": INSTRUMENT_ANGLE_CONVENTION,
        },
    )
