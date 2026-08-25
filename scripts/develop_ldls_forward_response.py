#!/usr/bin/env python3
"""Read-only forward LDLS compact-response and trace development solver.

This deliberately small development program is independent of
``develop_response_model.py``.  It reads a paired physical CCD through the
ArtifactService, builds immutable pixel evidence, and solves only the compact
unit-integral circular-fiber/Gaussian/pixel response and a smooth trace
correction.  It never publishes Artifacts or changes the registry.

Example
-------
python scripts/develop_ldls_forward_response.py --db ~/work/run/virusflow.sqlite3 \\
    --zipcode '074+070+403+RU+S/N 0063' --output-dir development/forward

The old workbench remains the A/B reference.  An optional ``--initial-field``
NPZ may supply its already accepted ``W`` and ``f_sigma`` fields during that
comparison; the solver itself has no dependency on that script or its types.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import sparse
from scipy.interpolate import BSpline
from scipy.sparse.linalg import lsqr, spsolve
from scipy.special import j1, jv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from virusflow.algorithms.extraction import (  # noqa: E402
    extract_fractional_aperture,
    fractional_aperture_geometry,
)
from virusflow.algorithms.physical_ccd import (  # noqa: E402
    assemble_physical_ccd,
    fit_gap_scattered_light,
    physical_trace_map,
)
from virusflow.artifacts import ArtifactService  # noqa: E402
from virusflow.core.identity import ZipCode, parse_zipcode_key  # noqa: E402
from virusflow.ontology.coordinates import UPPER_AMPLIFIER_Y_OFFSET  # noqa: E402


log = logging.getLogger(__name__)


PAIR = {
    "LL": ("left", "LL", "LU"), "LU": ("left", "LL", "LU"),
    "RU": ("right", "RU", "RL"), "RL": ("right", "RU", "RL"),
}


@dataclass(frozen=True)
class LDLSEvidence:
    """Immutable observation facts.  No iterative quantity belongs here."""

    image: np.ndarray
    variance: np.ndarray
    valid_mask: np.ndarray
    five_pixel_flux: np.ndarray
    base_trace: np.ndarray
    aperture_rows: np.ndarray
    aperture_weights: np.ndarray
    fiber_ids: np.ndarray
    amplifier_ids: np.ndarray
    amplifier_bounds: tuple[float, ...]
    pixel_x: np.ndarray
    pixel_y: np.ndarray
    pixel_value: np.ndarray
    pixel_variance: np.ndarray


@dataclass(frozen=True)
class LDLSGeometry:
    """Immutable contribution maps and basis values for one physical CCD."""

    trace_basis: np.ndarray  # (fiber, x, trace-term)
    response_basis_W: np.ndarray  # (fiber, x, response-term)
    response_basis_f: np.ndarray
    reference_W: np.ndarray
    reference_f_sigma: np.ndarray
    contribution_fiber: np.ndarray
    contribution_x: np.ndarray
    contribution_detector_row: np.ndarray
    contribution_sample_index: np.ndarray
    contribution_block: np.ndarray
    sample_index_image: np.ndarray
    sample_x: np.ndarray
    sample_y: np.ndarray
    sample_block: np.ndarray
    trace_neighbor_pairs: np.ndarray
    compact_support: float
    trace_margin: float
    amplifier_boundary: float


@dataclass(frozen=True)
class LDLSSampling:
    """An analysis representation; it never changes the detector model."""

    mode: str
    sample_indices: np.ndarray
    sample_weights: np.ndarray
    block_index: np.ndarray
    fiber_index: np.ndarray


@dataclass(frozen=True)
class ProfileTraceState:
    """The complete authoritative solved state, with no cached derivatives."""

    trace_coeff: np.ndarray
    W_coeff: np.ndarray
    f_sigma_coeff: np.ndarray
    generation: int = 0


@dataclass(frozen=True)
class ForwardEvaluation:
    """One fresh, internally consistent derivation of a solved state."""

    state_generation: int
    trace: np.ndarray
    W: np.ndarray
    f_sigma: np.ndarray
    R: np.ndarray
    sigma: np.ndarray
    C5: np.ndarray
    total_amplitude: np.ndarray
    model_samples: np.ndarray
    residuals: np.ndarray
    robust_weights: np.ndarray
    robust_loss: float
    P: np.ndarray | None = None
    Pprime: np.ndarray | None = None
    PW: np.ndarray | None = None
    Pf: np.ndarray | None = None
    fiber_contributions: np.ndarray | None = None
    Palpha: np.ndarray | None = None


@dataclass(frozen=True)
class ResponseStep:
    delta_W_coeff: np.ndarray
    delta_f_sigma_coeff: np.ndarray
    hessian: np.ndarray
    gradient: np.ndarray
    predicted_loss_change: float


@dataclass(frozen=True)
class TraceStep:
    incremental_trace_coeff: np.ndarray
    hessian: sparse.csr_matrix
    gradient: np.ndarray
    predicted_loss_change: float


@dataclass(frozen=True)
class DetectorDisplacementField:
    """Experimental CCD-coordinate cubic-spline displacement, separate from state."""

    dense: np.ndarray
    lower_x_knots: np.ndarray
    lower_y_knots: np.ndarray
    lower_coefficients: np.ndarray
    upper_x_knots: np.ndarray
    upper_y_knots: np.ndarray
    upper_coefficients: np.ndarray
    amplifier_boundary: int
    x_knot_spacing: float
    y_knot_spacing: float

    def evaluate(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        x, y = np.broadcast_arrays(np.asarray(x), np.asarray(y))
        return self.dense[np.asarray(y, dtype=np.intp), np.asarray(x, dtype=np.intp)]


@dataclass(frozen=True)
class DetectorDisplacementExperiment:
    """One post-fit detector-coordinate correction experiment; never solver state."""

    field: DetectorDisplacementField
    baseline: ForwardEvaluation
    corrected: ForwardEvaluation
    projection_before: dict[str, Any]
    projection_after: dict[str, Any]
    x_correlation_before: dict[str, np.ndarray | float | None]
    x_correlation_after: dict[str, np.ndarray | float | None]
    y_correlation_before: dict[str, np.ndarray | float | None]
    low_order_removed: np.ndarray
    timing_seconds: dict[str, float]


@dataclass
class RuntimeProfile:
    """Tiny top-level timing ledger, deliberately not a profiler framework."""

    seconds: dict[str, float]

    def __init__(self) -> None:
        self.seconds = {}

    def measure(self, name: str):
        return _Timer(self, name)


@dataclass
class _Timer:
    ledger: RuntimeProfile
    name: str
    started: float = 0.0

    def __enter__(self):
        self.started = perf_counter()

    def __exit__(self, *_: object) -> None:
        self.ledger.seconds[self.name] = self.ledger.seconds.get(self.name, 0.0) + perf_counter() - self.started

def trapezoidal_integral(values: np.ndarray, coordinates: np.ndarray) -> float:
    """Integrate sampled values with the trapezoidal rule without NumPy API dependencies."""
    values = np.asarray(values, dtype=float)
    coordinates = np.asarray(coordinates, dtype=float)
    if values.size < 2 or coordinates.size < 2:
        return 0.0
    return float(np.sum(0.5 * (values[1:] + values[:-1]) * np.diff(coordinates)))

def tensor_legendre_basis(x: np.ndarray, y: np.ndarray, degree: int) -> np.ndarray:
    xx, yy = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
    return (
        np.polynomial.legendre.legvander(xx.ravel(), degree)[:, :, None]
        * np.polynomial.legendre.legvander(yy.ravel(), degree)[:, None, :]
    ).reshape(xx.size, -1)


def response_to_physical(W: np.ndarray, f_sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convert the solved W/f_sigma coordinates to derived R/sigma."""
    W, f_sigma = np.broadcast_arrays(np.asarray(W, float), np.asarray(f_sigma, float))
    V = W * W - 1.0 / 12.0
    if np.any(~np.isfinite(V)) or np.any(V <= 0.0) or np.any(~np.isfinite(f_sigma)):
        raise ValueError("response field is outside the physical W/f_sigma domain")
    if np.any((f_sigma <= 0.0) | (f_sigma >= 1.0)):
        raise ValueError("f_sigma must be strictly between zero and one")
    return 2.0 * np.sqrt((1.0 - f_sigma) * V), np.sqrt(f_sigma * V)


def physical_to_response(R: np.ndarray, sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    R, sigma = np.broadcast_arrays(np.asarray(R, float), np.asarray(sigma, float))
    V = R * R / 4.0 + sigma * sigma
    if np.any(~np.isfinite(V)) or np.any(V <= 0.0):
        raise ValueError("R and sigma must define positive finite compact width")
    return np.sqrt(V + 1.0 / 12.0), sigma * sigma / V


@dataclass(frozen=True)
class FourierCompactProfile:
    coordinate: np.ndarray
    density: np.ndarray
    derivative: np.ndarray

    def evaluate(self, coordinate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        values = np.asarray(coordinate, float)
        return (
            np.interp(values, self.coordinate, self.density, left=0.0, right=0.0),
            np.interp(values, self.coordinate, self.derivative, left=0.0, right=0.0),
        )


def fourier_compact_profile(
    radius: float, sigma: float, *, alpha: float = 0.0, step: float = 0.02,
) -> FourierCompactProfile:
    """Unit-integral circular-fiber/Gaussian/unit-pixel profile.

    ``alpha`` is the sole experimental global illumination parameter,
    ``I(r) proportional to 1 - alpha (r/R)^2``.  The zero-alpha branch is
    intentionally the original uniform-aperture expression verbatim.
    """
    if not (np.isfinite(radius) and np.isfinite(sigma) and radius > 0.0 and sigma > 0.0):
        raise ValueError("compact profile requires positive finite R and sigma")
    if not np.isfinite(alpha) or alpha >= 2.0:
        raise ValueError("aperture illumination alpha must be finite and below two")
    extent = max(18.0, radius + 7.0 * sigma + 3.0)
    count = 1 << int(np.ceil(np.log2(2.0 * extent / step)))
    coordinate = (np.arange(count) - count // 2) * step
    frequency = 2.0 * np.pi * np.fft.fftfreq(count, d=step)
    argument = frequency * radius
    disk = np.ones_like(frequency)
    good = np.abs(argument) > 1e-12
    if alpha == 0.0:
        # Keep the accepted uniform model bit-for-bit on its established path.
        disk[good] = 2.0 * j1(argument[good]) / argument[good]
    else:
        # Normalized transform of 1 - alpha (r/R)^2.  At alpha=0 this is
        # 2 J1(z)/z; at alpha=1 it is 8 J2(z)/z^2, with the exact z=0 limit 1.
        z = argument[good]
        disk[good] = (
            4.0 * (1.0 - alpha) * j1(z) / z
            + 8.0 * alpha * jv(2, z) / (z * z)
        ) / (2.0 - alpha)
    pixel = np.ones_like(frequency)
    good = np.abs(frequency) > 1e-12
    pixel[good] = 2.0 * np.sin(frequency[good] / 2.0) / frequency[good]
    transform = disk * np.exp(-0.5 * (sigma * frequency) ** 2) * pixel
    density = np.fft.fftshift(np.fft.ifft(transform).real) / step
    derivative = np.fft.fftshift(np.fft.ifft(1j * frequency * transform).real) / step
    normalization = trapezoidal_integral(density, coordinate)
    return FourierCompactProfile(coordinate, density / normalization, derivative / normalization)


class ProfileCache:
    """Exact FFT templates at a deliberate, inspectable numerical cache grid."""

    def __init__(self, quantization: float = 2e-3) -> None:
        self.quantization = float(quantization)
        self._templates: dict[tuple[int, int, float], FourierCompactProfile] = {}

    def evaluate(
        self,
        u: np.ndarray,
        R: np.ndarray,
        sigma: np.ndarray,
        *,
        alpha: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        if not np.isfinite(alpha) or alpha >= 2.0:
            raise ValueError("aperture illumination alpha must be finite and below two")
        u, R, sigma = np.broadcast_arrays(np.asarray(u, float), np.asarray(R, float), np.asarray(sigma, float))
        key_r = np.rint(R / self.quantization).astype(np.int32)
        key_s = np.rint(sigma / self.quantization).astype(np.int32)
        key_r = key_r.ravel()
        key_s = key_s.ravel()
        order = np.lexsort((key_s, key_r))
        sorted_r = key_r[order]
        sorted_s = key_s[order]
        starts = np.r_[0, np.flatnonzero((np.diff(sorted_r) != 0) | (np.diff(sorted_s) != 0)) + 1]
        stops = np.r_[starts[1:], order.size]
        value = np.empty(u.size, float)
        derivative = np.empty(u.size, float)
        flat_u = u.ravel()
        for start, stop in zip(starts, stops):
            qr = sorted_r[start]
            qs = sorted_s[start]
            key = int(qr), int(qs), float(alpha)
            template = self._templates.get(key)
            if template is None:
                template = fourier_compact_profile(
                    qr * self.quantization, qs * self.quantization, alpha=float(alpha),
                )
                self._templates[key] = template
            positions = order[start:stop]
            value[positions], derivative[positions] = template.evaluate(flat_u[positions])
        return value.reshape(u.shape), derivative.reshape(u.shape)


def model_fwhm_from_radius_sigma(radius: float, sigma: float) -> float:
    """Return the exact-model FWHM from one unit-pixel compact profile."""
    profile = fourier_compact_profile(radius, sigma, alpha=0.0)
    coordinate = profile.coordinate
    density = profile.density
    center = int(np.argmin(np.abs(coordinate)))
    peak = float(density[center])
    if not np.isfinite(peak) or peak <= 0.0:
        raise ValueError("compact profile has no finite positive central maximum")
    half_max = 0.5 * peak
    positive_coordinate = coordinate[center:]
    positive_density = density[center:]
    crossing = np.flatnonzero(
        (positive_density[:-1] >= half_max) & (positive_density[1:] <= half_max)
    )
    if crossing.size == 0:
        raise ValueError("compact profile has no positive half-maximum crossing")
    index = int(crossing[0])
    x0, x1 = positive_coordinate[index:index + 2]
    y0, y1 = positive_density[index:index + 2]
    if y1 == y0:
        half_width = float(x0)
    else:
        half_width = float(x0 + (half_max - y0) * (x1 - x0) / (y1 - y0))
    if not np.isfinite(half_width) or half_width <= 0.0:
        raise ValueError("compact profile half-maximum crossing is invalid")
    return 2.0 * half_width


def model_fwhm_field(
    radius: np.ndarray, sigma: np.ndarray, *, quantization: float = 2e-3,
) -> np.ndarray:
    """Compute a quantized exact-profile FWHM field without one FFT per pixel."""
    radius, sigma = np.broadcast_arrays(np.asarray(radius, float), np.asarray(sigma, float))
    if not np.isfinite(quantization) or quantization <= 0.0:
        raise ValueError("FWHM quantization must be positive and finite")
    if np.any(~np.isfinite(radius)) or np.any(~np.isfinite(sigma)):
        raise ValueError("FWHM field requires finite R and sigma")
    quantized_radius = np.rint(radius / quantization).astype(np.int32)
    quantized_sigma = np.rint(sigma / quantization).astype(np.int32)
    pairs, inverse = np.unique(
        np.column_stack((quantized_radius.ravel(), quantized_sigma.ravel())),
        axis=0, return_inverse=True,
    )
    values = np.empty(pairs.shape[0], float)
    for index, (radius_key, sigma_key) in enumerate(pairs):
        values[index] = model_fwhm_from_radius_sigma(
            float(radius_key) * quantization, float(sigma_key) * quantization,
        )
    return values[inverse].reshape(radius.shape)


def _normalised_coordinate(value: np.ndarray, low: float, high: float) -> np.ndarray:
    return (np.asarray(value, float) - 0.5 * (low + high)) / max(0.5 * (high - low), 1.0)


def build_ldls_geometry(
    evidence: LDLSEvidence,
    *,
    support: float,
    trace_degree: int,
    response_degree: int,
    amplifier_boundary: float,
    reference_W: np.ndarray,
    reference_f_sigma: np.ndarray,
    trace_margin: float = 1.0,
    block_width: int = 32,
) -> LDLSGeometry:
    """Precompute all fixed compact-support and basis ownership once."""
    image = evidence.image
    fibers, nx = evidence.base_trace.shape
    ny = image.shape[0]
    if image.shape[1] != nx:
        raise ValueError("trace and LDLS image column counts differ")
    x = np.broadcast_to(np.arange(nx, dtype=float), (fibers, nx))
    trace_basis = np.polynomial.legendre.legvander(_normalised_coordinate(x, 0, nx - 1), trace_degree)
    term_count = (response_degree + 1) ** 2
    response_basis = np.zeros((fibers, nx, 2 * term_count), float)
    lower = evidence.base_trace < amplifier_boundary
    for half, selected in enumerate((lower, ~lower)):
        if not np.any(selected):
            continue
        basis = tensor_legendre_basis(
            _normalised_coordinate(x[selected], 0, nx - 1),
            _normalised_coordinate(
                evidence.base_trace[selected],
                0 if half == 0 else amplifier_boundary,
                amplifier_boundary - 1 if half == 0 else ny - 1,
            ),
            response_degree,
        )
        response_basis[selected, half * term_count:(half + 1) * term_count] = basis
    # The detector sample union is built once.  Contributions can overlap but
    # each (fiber, column, detector-row) contribution appears exactly once.
    sample_index_image = np.full(image.shape, -1, np.int64)
    c_fiber: list[np.ndarray] = []
    c_x: list[np.ndarray] = []
    c_y: list[np.ndarray] = []
    for fiber in range(fibers):
        for column in range(nx):
            center = evidence.base_trace[fiber, column]
            if not np.isfinite(center):
                continue
            low = max(0, int(np.floor(center - support - trace_margin)))
            high = min(ny, int(np.ceil(center + support + trace_margin)) + 1)
            rows = np.arange(low, high, dtype=np.int32)
            valid = evidence.valid_mask[rows, column]
            if not np.any(valid):
                continue
            rows = rows[valid]
            c_fiber.append(np.full(rows.size, fiber, np.int32))
            c_x.append(np.full(rows.size, column, np.int32))
            c_y.append(rows)
            sample_index_image[rows, column] = 0
    sample_rows, sample_x = np.nonzero(sample_index_image == 0)
    sample_index_image[sample_rows, sample_x] = np.arange(sample_rows.size, dtype=np.int64)
    contribution_fiber = np.concatenate(c_fiber)
    contribution_x = np.concatenate(c_x)
    contribution_y = np.concatenate(c_y)
    contribution_sample = sample_index_image[contribution_y, contribution_x]
    blocks = sample_x // max(1, int(block_width))
    # Only fibers whose immutable compact-support envelopes can share a
    # detector sample receive an off-diagonal trace-Hessian block.  This also
    # forbids a smoother from coupling across the physical amplifier boundary.
    trace_neighbor_pairs: list[tuple[int, int]] = []
    support_limit = 2.0 * (support + trace_margin)
    for first in range(fibers):
        first_half = evidence.base_trace[first] < amplifier_boundary
        for second in range(first + 1, fibers):
            same_half = first_half == (evidence.base_trace[second] < amplifier_boundary)
            close = np.abs(evidence.base_trace[first] - evidence.base_trace[second]) <= support_limit
            if np.any(same_half & close):
                trace_neighbor_pairs.append((first, second))
    return LDLSGeometry(
        trace_basis=trace_basis,
        response_basis_W=response_basis,
        response_basis_f=response_basis.copy(),
        reference_W=np.asarray(reference_W, float),
        reference_f_sigma=np.asarray(reference_f_sigma, float),
        contribution_fiber=contribution_fiber,
        contribution_x=contribution_x,
        contribution_detector_row=contribution_y,
        contribution_sample_index=contribution_sample,
        contribution_block=contribution_x // max(1, int(block_width)),
        sample_index_image=sample_index_image,
        sample_x=sample_x.astype(np.int32), sample_y=sample_rows.astype(np.int32),
        sample_block=blocks.astype(np.int32),
        trace_neighbor_pairs=np.asarray(trace_neighbor_pairs, dtype=np.int32).reshape((-1, 2)),
        compact_support=float(support),
        trace_margin=float(trace_margin), amplifier_boundary=float(amplifier_boundary),
    )


def build_ldls_sampling(evidence: LDLSEvidence, geometry: LDLSGeometry, *, mode: str = "full", stride: int = 1) -> LDLSSampling:
    """Select full or deterministic sparse raw-pixel evidence without model branches."""
    total = geometry.sample_x.size
    selected = np.arange(total, dtype=np.int64)
    if mode == "sparse":
        selected = selected[(geometry.sample_x[selected] % max(1, stride)) == 0]
    elif mode not in {"full", "robust_binned"}:
        raise ValueError("sampling mode must be full, sparse, or robust_binned")
    fibers = np.full(selected.size, -1, np.int32)
    # This is diagnostic-only ownership, not a model-neighbor relation.
    fibers[:] = np.searchsorted(evidence.base_trace[:, 0], geometry.sample_y[selected], side="left")
    return LDLSSampling(mode, selected, np.ones(selected.size, float), geometry.sample_block[selected], fibers)


def initial_profile_trace_state(geometry: LDLSGeometry) -> ProfileTraceState:
    fibers, _, trace_terms = geometry.trace_basis.shape
    response_terms = geometry.response_basis_W.shape[-1]
    return ProfileTraceState(np.zeros((fibers, trace_terms)), np.zeros(response_terms), np.zeros(response_terms))


def _derive_fields(state: ProfileTraceState, evidence: LDLSEvidence, geometry: LDLSGeometry) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    trace = evidence.base_trace + np.einsum("fxt,ft->fx", geometry.trace_basis, state.trace_coeff)
    if np.nanmax(np.abs(trace - evidence.base_trace)) > geometry.trace_margin:
        raise ValueError("trace candidate exceeds precomputed immutable support margin")
    W = geometry.reference_W + np.einsum("fxk,k->fx", geometry.response_basis_W, state.W_coeff)
    f_sigma = geometry.reference_f_sigma + np.einsum("fxk,k->fx", geometry.response_basis_f, state.f_sigma_coeff)
    R, sigma = response_to_physical(W, f_sigma)
    return trace, W, f_sigma, R, sigma


def _aperture_capture(
    evidence: LDLSEvidence, trace: np.ndarray, R: np.ndarray, sigma: np.ndarray, cache: ProfileCache,
    alpha: float,
    detector_displacement: DetectorDisplacementField | None = None,
) -> np.ndarray:
    rows = evidence.aperture_rows
    weights = evidence.aperture_weights
    u = rows - trace[..., None]
    if detector_displacement is not None:
        x = np.broadcast_to(np.arange(trace.shape[1], dtype=np.intp), trace.shape)[..., None]
        u = u - detector_displacement.evaluate(x, rows)
    profile, _ = cache.evaluate(u, R[..., None], sigma[..., None], alpha=alpha)
    return np.sum(weights * profile, axis=-1)


def _sample_arrays(evidence: LDLSEvidence, geometry: LDLSGeometry, sampling: LDLSSampling) -> tuple[np.ndarray, np.ndarray]:
    indices = sampling.sample_indices
    return (
        evidence.image[geometry.sample_y[indices], geometry.sample_x[indices]],
        evidence.variance[geometry.sample_y[indices], geometry.sample_x[indices]],
    )


def _huber_weights_and_loss(residual: np.ndarray, variance: np.ndarray, sample_weights: np.ndarray, tuning: float = 1.5) -> tuple[np.ndarray, float]:
    scale = np.sqrt(np.maximum(variance, np.finfo(float).tiny))
    standardized = residual / scale
    absolute = np.abs(standardized)
    weights = sample_weights * np.minimum(1.0, tuning / np.maximum(absolute, np.finfo(float).tiny))
    loss = np.where(absolute <= tuning, 0.5 * standardized * standardized, tuning * (absolute - 0.5 * tuning))
    return weights, float(np.sum(sample_weights * loss))


def evaluate_state(
    evidence: LDLSEvidence,
    geometry: LDLSGeometry,
    sampling: LDLSSampling,
    state: ProfileTraceState,
    *,
    cache: ProfileCache | None = None,
    derivatives: bool = False,
    debug_contributions: bool = False,
    finite_difference_W: float = 0.006,
    finite_difference_f: float = 0.006,
    finite_difference_alpha: float = 0.01,
    experimental_aperture_alpha: float = 0.0,
    experimental_alpha_derivative: bool = False,
    detector_displacement: DetectorDisplacementField | None = None,
) -> ForwardEvaluation:
    """The sole authoritative fresh derivation of all state-dependent facts."""
    if not (-0.5 <= experimental_aperture_alpha <= 1.0):
        raise ValueError("experimental aperture illumination alpha must lie in [-0.5, 1]")
    cache = cache or ProfileCache()
    trace, W, f_sigma, R, sigma = _derive_fields(state, evidence, geometry)
    C5 = _aperture_capture(
        evidence, trace, R, sigma, cache, experimental_aperture_alpha, detector_displacement,
    )
    valid_amplitude = np.isfinite(evidence.five_pixel_flux) & np.isfinite(C5) & (C5 > 0.0)
    amplitude = np.divide(evidence.five_pixel_flux, C5, out=np.zeros_like(C5), where=valid_amplitude)
    cf, cx, cy = geometry.contribution_fiber, geometry.contribution_x, geometry.contribution_detector_row
    u = cy - trace[cf, cx]
    if detector_displacement is not None:
        u = u - detector_displacement.evaluate(cx, cy)
    profile, profile_prime = cache.evaluate(
        u, R[cf, cx], sigma[cf, cx], alpha=experimental_aperture_alpha,
    )
    contribution = amplitude[cf, cx] * profile
    full_model = np.bincount(geometry.contribution_sample_index, weights=contribution, minlength=geometry.sample_x.size)
    sample_model = full_model[sampling.sample_indices]
    value, variance = _sample_arrays(evidence, geometry, sampling)
    residuals = value - sample_model
    robust_weights, robust_loss = _huber_weights_and_loss(residuals, variance, sampling.sample_weights)
    P_W = P_f = P_alpha = None
    if derivatives:
        # Amplitude is held fixed in one Newton response step by design.
        plus_R, plus_sigma = response_to_physical(W[cf, cx] + finite_difference_W, f_sigma[cf, cx])
        plus, _ = cache.evaluate(u, plus_R, plus_sigma, alpha=experimental_aperture_alpha)
        minus_R, minus_sigma = response_to_physical(W[cf, cx] - finite_difference_W, f_sigma[cf, cx])
        minus, _ = cache.evaluate(u, minus_R, minus_sigma, alpha=experimental_aperture_alpha)
        P_W = (plus - minus) / (2.0 * finite_difference_W)
        plus_R, plus_sigma = response_to_physical(W[cf, cx], f_sigma[cf, cx] + finite_difference_f)
        plus, _ = cache.evaluate(u, plus_R, plus_sigma, alpha=experimental_aperture_alpha)
        minus_R, minus_sigma = response_to_physical(W[cf, cx], f_sigma[cf, cx] - finite_difference_f)
        minus, _ = cache.evaluate(u, minus_R, minus_sigma, alpha=experimental_aperture_alpha)
        P_f = (plus - minus) / (2.0 * finite_difference_f)
        if experimental_alpha_derivative:
            plus, _ = cache.evaluate(
                u, R[cf, cx], sigma[cf, cx], alpha=experimental_aperture_alpha + finite_difference_alpha,
            )
            minus, _ = cache.evaluate(
                u, R[cf, cx], sigma[cf, cx], alpha=experimental_aperture_alpha - finite_difference_alpha,
            )
            P_alpha = (plus - minus) / (2.0 * finite_difference_alpha)
    result = ForwardEvaluation(
        state.generation, trace, W, f_sigma, R, sigma, C5, amplitude, sample_model,
        residuals, robust_weights, robust_loss, profile, profile_prime, P_W, P_f,
        contribution if debug_contributions else None, P_alpha,
    )
    assert np.allclose(amplitude[valid_amplitude] * C5[valid_amplitude], evidence.five_pixel_flux[valid_amplitude], rtol=2e-12, atol=2e-12)
    return result


def _local_sample_positions(geometry: LDLSGeometry, sampling: LDLSSampling, block: int) -> tuple[np.ndarray, np.ndarray]:
    local_indices = np.flatnonzero(sampling.block_index == block)
    image_to_local = np.full(geometry.sample_x.size, -1, np.int64)
    image_to_local[sampling.sample_indices[local_indices]] = np.arange(local_indices.size)
    return local_indices, image_to_local


def build_response_newton_step(
    evidence: LDLSEvidence, geometry: LDLSGeometry, sampling: LDLSSampling,
    state: ProfileTraceState, current: ForwardEvaluation, *, ridge: float = 1e-5,
) -> ResponseStep:
    """Accumulate small normal equations blockwise; never create a full-pixel Jacobian."""
    if current.state_generation != state.generation or current.PW is None or current.Pf is None:
        raise ValueError("response linearization requires derivative evaluation from this exact state")
    terms = state.W_coeff.size
    parameter_count = 2 * terms
    hessian = np.zeros((parameter_count, parameter_count))
    gradient = np.zeros(parameter_count)
    cf, cx = geometry.contribution_fiber, geometry.contribution_x
    derivative_W = current.total_amplitude[cf, cx] * current.PW
    derivative_f = current.total_amplitude[cf, cx] * current.Pf
    selected_set = np.zeros(geometry.sample_x.size, bool)
    selected_set[sampling.sample_indices] = True
    for block in np.unique(sampling.block_index):
        local, image_to_local = _local_sample_positions(geometry, sampling, int(block))
        included = (geometry.contribution_block == block) & selected_set[geometry.contribution_sample_index]
        if not np.any(included):
            continue
        rows = image_to_local[geometry.contribution_sample_index[included]]
        jac = np.zeros((local.size, parameter_count))
        for term in range(terms):
            np.add.at(jac[:, term], rows, derivative_W[included] * geometry.response_basis_W[cf[included], cx[included], term])
            np.add.at(jac[:, terms + term], rows, derivative_f[included] * geometry.response_basis_f[cf[included], cx[included], term])
        weight = current.robust_weights[local] / np.maximum(evidence.variance[geometry.sample_y[sampling.sample_indices[local]], geometry.sample_x[sampling.sample_indices[local]]], np.finfo(float).tiny)
        hessian += jac.T @ (weight[:, None] * jac)
        gradient += jac.T @ (weight * current.residuals[local])
    hessian += ridge * max(float(np.trace(hessian)) / max(1, hessian.shape[0]), 1.0) * np.eye(hessian.shape[0])
    delta = np.linalg.solve(hessian, gradient)
    return ResponseStep(delta[:terms], delta[terms:], hessian, gradient, float(-0.5 * gradient @ delta))


def build_trace_step(
    evidence: LDLSEvidence, geometry: LDLSGeometry, sampling: LDLSSampling,
    state: ProfileTraceState, current: ForwardEvaluation, *, ridge: float = 1e-5,
) -> TraceStep:
    """Accumulate a coupled sparse block-banded trace normal system.

    The residual remains that of the freshly rendered complete overlapping
    fiber model.  Detector/X blocks directly accumulate only diagonal fiber
    coefficient blocks and off-diagonal blocks for fibers sharing compact
    detector samples; a detector-pixel by trace-coefficient Jacobian is never
    materialized.
    """
    if current.state_generation != state.generation or current.Pprime is None:
        raise ValueError("trace linearization requires evaluation from this exact state")
    fibers, trace_terms = state.trace_coeff.shape
    count = fibers * trace_terms
    diagonal = [np.zeros((trace_terms, trace_terms)) for _ in range(fibers)]
    off_diagonal = {
        (int(first), int(second)): np.zeros((trace_terms, trace_terms))
        for first, second in geometry.trace_neighbor_pairs
    }
    gradient = np.zeros(count)
    cf, cx = geometry.contribution_fiber, geometry.contribution_x
    derivative = -current.total_amplitude[cf, cx] * current.Pprime
    selected_set = np.zeros(geometry.sample_x.size, bool)
    selected_set[sampling.sample_indices] = True
    sample_position = np.full(geometry.sample_x.size, -1, np.int64)
    sample_position[sampling.sample_indices] = np.arange(sampling.sample_indices.size)
    for block in np.unique(sampling.block_index):
        included = (
            (geometry.contribution_block == block)
            & selected_set[geometry.contribution_sample_index]
        )
        if not np.any(included):
            continue
        indices = np.flatnonzero(included)
        order = np.argsort(cf[indices], kind="stable")
        ordered = indices[order]
        ordered_fiber = cf[ordered]
        starts = np.r_[0, np.flatnonzero(np.diff(ordered_fiber)) + 1, ordered.size]
        local_design: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for start, stop in zip(starts[:-1], starts[1:]):
            fiber = int(ordered_fiber[start])
            fiber_indices = ordered[start:stop]
            sample_ids = geometry.contribution_sample_index[fiber_indices]
            positions = sample_position[sample_ids]
            design = (
                derivative[fiber_indices, None]
                * geometry.trace_basis[cf[fiber_indices], cx[fiber_indices]]
            )
            variance = evidence.variance[
                geometry.sample_y[sample_ids], geometry.sample_x[sample_ids]
            ]
            weight = current.robust_weights[positions] / np.maximum(
                variance, np.finfo(float).tiny
            )
            diagonal[fiber] += design.T @ (weight[:, None] * design)
            gradient[fiber * trace_terms:(fiber + 1) * trace_terms] += (
                design.T @ (weight * current.residuals[positions])
            )
            local_design[fiber] = sample_ids, design
        for first, second in off_diagonal:
            if first not in local_design or second not in local_design:
                continue
            first_samples, first_design = local_design[first]
            second_samples, second_design = local_design[second]
            shared, first_index, second_index = np.intersect1d(
                first_samples, second_samples, assume_unique=True, return_indices=True,
            )
            if shared.size == 0:
                continue
            positions = sample_position[shared]
            variance = evidence.variance[
                geometry.sample_y[shared], geometry.sample_x[shared]
            ]
            weight = current.robust_weights[positions] / np.maximum(
                variance, np.finfo(float).tiny
            )
            off_diagonal[first, second] += first_design[first_index].T @ (
                weight[:, None] * second_design[second_index]
            )
    trace_scale = sum(float(np.trace(block)) for block in diagonal) / max(1, count)
    block_data: list[np.ndarray] = []
    block_columns: list[int] = []
    block_indptr = [0]
    for fiber in range(fibers):
        block_data.append(
            diagonal[fiber] + ridge * max(trace_scale, 1.0) * np.eye(trace_terms)
        )
        block_columns.append(fiber)
        for (first, second), value in off_diagonal.items():
            if first == fiber:
                block_data.append(value)
                block_columns.append(second)
            elif second == fiber:
                block_data.append(value.T)
                block_columns.append(first)
        block_indptr.append(len(block_columns))
    hessian = sparse.bsr_matrix(
        (np.asarray(block_data), np.asarray(block_columns), np.asarray(block_indptr)),
        shape=(count, count), blocksize=(trace_terms, trace_terms),
    ).tocsr()
    delta = np.asarray(spsolve(hessian, gradient), float).reshape(fibers, trace_terms)
    return TraceStep(delta, hessian, gradient, float(-0.5 * gradient @ delta.ravel()))


def apply_response_step(state: ProfileTraceState, step: ResponseStep, damping: float = 1.0) -> ProfileTraceState:
    if not (np.any(step.delta_W_coeff) or np.any(step.delta_f_sigma_coeff)):
        return state
    return replace(state, W_coeff=state.W_coeff + damping * step.delta_W_coeff,
                   f_sigma_coeff=state.f_sigma_coeff + damping * step.delta_f_sigma_coeff,
                   generation=state.generation + 1)


def apply_trace_step(state: ProfileTraceState, step: TraceStep, damping: float = 1.0) -> ProfileTraceState:
    if not np.any(step.incremental_trace_coeff):
        return state
    return replace(state, trace_coeff=state.trace_coeff + damping * step.incremental_trace_coeff, generation=state.generation + 1)


def _accept_damped(
    state: ProfileTraceState, current: ForwardEvaluation, step: ResponseStep | TraceStep,
    apply: Any, evaluate: Any,
) -> tuple[ProfileTraceState, ForwardEvaluation, float]:
    for damping in 0.5 ** np.arange(8):
        candidate_state = apply(state, step, float(damping))
        try:
            candidate = evaluate(candidate_state)
        except ValueError:
            continue
        if candidate.robust_loss < current.robust_loss:
            return candidate_state, candidate, float(damping)
    return state, current, 0.0


def solve_response_state(
    evidence: LDLSEvidence, geometry: LDLSGeometry, sampling: LDLSSampling, state: ProfileTraceState,
    *, cache: ProfileCache, max_iterations: int = 3, tolerance: float = 2e-4,
) -> tuple[ProfileTraceState, list[dict[str, float]]]:
    history: list[dict[str, float]] = []
    for iteration in range(max_iterations):
        current = evaluate_state(evidence, geometry, sampling, state, cache=cache, derivatives=True)
        step = build_response_newton_step(evidence, geometry, sampling, state, current)
        accepted, candidate, damping = _accept_damped(
            state, current, step, apply_response_step,
            lambda candidate_state: evaluate_state(evidence, geometry, sampling, candidate_state, cache=cache),
        )
        if damping == 0.0:
            history.append({"iteration": float(iteration), "loss": current.robust_loss, "candidate_loss": candidate.robust_loss, "damping": damping, "max_delta_W": 0.0, "max_delta_f_sigma": 0.0})
            break
        state = accepted
        max_delta_W = float(np.max(np.abs(candidate.W - current.W)))
        max_delta_f_sigma = float(np.max(np.abs(candidate.f_sigma - current.f_sigma)))
        history.append({"iteration": float(iteration), "loss": current.robust_loss, "candidate_loss": candidate.robust_loss, "damping": damping, "max_delta_W": max_delta_W, "max_delta_f_sigma": max_delta_f_sigma})
        if max(max_delta_W, max_delta_f_sigma) < tolerance:
            break
    return state, history


def solve_trace_state(
    evidence: LDLSEvidence, geometry: LDLSGeometry, sampling: LDLSSampling, state: ProfileTraceState,
    *, cache: ProfileCache, max_iterations: int = 2, tolerance: float = 2e-4,
) -> tuple[ProfileTraceState, list[dict[str, float]]]:
    history: list[dict[str, float]] = []
    for iteration in range(max_iterations):
        current = evaluate_state(evidence, geometry, sampling, state, cache=cache)
        step = build_trace_step(evidence, geometry, sampling, state, current)
        accepted, candidate, damping = _accept_damped(
            state, current, step, apply_trace_step,
            lambda candidate_state: evaluate_state(evidence, geometry, sampling, candidate_state, cache=cache),
        )
        if damping == 0.0:
            history.append({"iteration": float(iteration), "loss": current.robust_loss, "candidate_loss": candidate.robust_loss, "damping": damping, "max_delta_trace": 0.0})
            break
        state = accepted
        max_delta_trace = float(np.max(np.abs(candidate.trace - current.trace)))
        history.append({"iteration": float(iteration), "loss": current.robust_loss, "candidate_loss": candidate.robust_loss, "damping": damping, "max_delta_trace": max_delta_trace})
        if max_delta_trace < tolerance:
            break
    return state, history


def assert_evaluation_invariants(evidence: LDLSEvidence, geometry: LDLSGeometry, sampling: LDLSSampling, state: ProfileTraceState, cache: ProfileCache) -> None:
    """Cheap development checks preventing stale derived-state bookkeeping."""
    first = evaluate_state(evidence, geometry, sampling, state, cache=cache, debug_contributions=True)
    second = evaluate_state(evidence, geometry, sampling, state, cache=cache, debug_contributions=True)
    for name in ("trace", "W", "f_sigma", "R", "sigma", "C5", "total_amplitude", "model_samples", "residuals"):
        assert np.array_equal(getattr(first, name), getattr(second, name)), f"non-deterministic {name}"
    assert first.state_generation == state.generation
    assert first.fiber_contributions is not None
    rendered = np.bincount(geometry.contribution_sample_index, weights=first.fiber_contributions, minlength=geometry.sample_x.size)[sampling.sample_indices]
    assert np.array_equal(rendered, first.model_samples), "fiber contribution was omitted or double counted"
    zero_response = ResponseStep(np.zeros_like(state.W_coeff), np.zeros_like(state.f_sigma_coeff), np.empty((0, 0)), np.empty(0), 0.0)
    zero_trace = TraceStep(
        np.zeros_like(state.trace_coeff), sparse.csr_matrix((0, 0)), np.empty(0), 0.0
    )
    assert apply_response_step(state, zero_response) is state
    assert apply_trace_step(state, zero_trace) is state


def develop_ldls_profile_and_trace(
    evidence: LDLSEvidence, geometry: LDLSGeometry, sampling: LDLSSampling, *,
    cache: ProfileCache | None = None,
) -> tuple[ProfileTraceState, ForwardEvaluation, TraceStep, dict[str, list[dict[str, float]]]]:
    """Response -> trace -> response, then closure and one unapplied trace proposal."""
    cache = cache or ProfileCache()
    started = perf_counter()
    log.info("initialize state")
    state = initial_profile_trace_state(geometry)
    log.info("initialize state: %.3f s", perf_counter() - started)
    started = perf_counter()
    log.info("first response solve")
    state, first_response = solve_response_state(evidence, geometry, sampling, state, cache=cache)
    log.info("first response solve: %.3f s", perf_counter() - started)
    started = perf_counter()
    log.info("coupled trace solve")
    state, trace_history = solve_trace_state(evidence, geometry, sampling, state, cache=cache)
    log.info("coupled trace solve: %.3f s", perf_counter() - started)
    started = perf_counter()
    log.info("second response solve")
    state, second_response = solve_response_state(evidence, geometry, sampling, state, cache=cache)
    log.info("second response solve: %.3f s", perf_counter() - started)
    started = perf_counter()
    log.info("final state evaluation")
    closure = evaluate_state(evidence, geometry, sampling, state, cache=cache, debug_contributions=True)
    log.info("final state evaluation: %.3f s", perf_counter() - started)
    started = perf_counter()
    log.info("unaccepted trace convergence diagnostic")
    trace_convergence = build_trace_step(evidence, geometry, sampling, state, closure)
    log.info("unaccepted trace convergence diagnostic: %.3f s", perf_counter() - started)
    return state, closure, trace_convergence, {
        "response_initial": first_response,
        "trace": trace_history,
        "response_final": second_response,
    }


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


def _local_residual_mode_projection(
    evidence: LDLSEvidence, geometry: LDLSGeometry, sampling: LDLSSampling, current: ForwardEvaluation,
) -> dict[str, Any]:
    """Project the full overlapping-model residual onto local frozen derivative modes."""
    if current.P is None or current.Pprime is None or current.PW is None or current.Pf is None:
        raise ValueError("residual-mode projection requires a derivative-enabled evaluation")
    fibers, columns = evidence.base_trace.shape
    local_count = fibers * columns
    sample_position = np.full(geometry.sample_x.size, -1, dtype=np.int64)
    sample_position[sampling.sample_indices] = np.arange(sampling.sample_indices.size)
    position = sample_position[geometry.contribution_sample_index]
    included = position >= 0
    cf, cx = geometry.contribution_fiber[included], geometry.contribution_x[included]
    local = cf.astype(np.int64) * columns + cx
    residual = current.residuals[position[included]]
    variance = evidence.variance[
        geometry.sample_y[geometry.contribution_sample_index[included]],
        geometry.sample_x[geometry.contribution_sample_index[included]],
    ]
    weight = current.robust_weights[position[included]] / np.maximum(variance, np.finfo(float).tiny)
    amplitude = current.total_amplitude[cf, cx]
    derivatives = (
        current.P[included],
        -amplitude * current.Pprime[included],
        amplitude * current.PW[included],
        amplitude * current.Pf[included],
    )
    labels = ("amplitude_P", "centroid_minus_APprime", "W_APW", "f_sigma_APf")
    power = np.bincount(local, weights=weight * residual * residual, minlength=local_count)
    gradient = np.column_stack([
        np.bincount(local, weights=weight * derivative * residual, minlength=local_count)
        for derivative in derivatives
    ])
    diagonal = np.column_stack([
        np.bincount(local, weights=weight * derivative * derivative, minlength=local_count)
        for derivative in derivatives
    ])
    coefficients = np.divide(gradient, diagonal, out=np.full_like(gradient, np.nan), where=diagonal > 0.0)
    individual_fraction = np.divide(
        gradient * gradient, diagonal * power[:, None], out=np.full_like(gradient, np.nan),
        where=(diagonal > 0.0) & (power[:, None] > 0.0),
    )
    hessian = np.empty((local_count, 4, 4))
    for first, first_derivative in enumerate(derivatives):
        for second, second_derivative in enumerate(derivatives):
            hessian[:, first, second] = np.bincount(
                local, weights=weight * first_derivative * second_derivative, minlength=local_count,
            )
    eigenvalue = np.linalg.eigvalsh(hessian)
    well_conditioned = (
        (power > 0.0) & np.isfinite(eigenvalue).all(axis=1) & (eigenvalue[:, -1] > 0.0)
        & (eigenvalue[:, 0] > 1e-10 * eigenvalue[:, -1])
    )
    joint = np.full_like(gradient, np.nan)
    joint[well_conditioned] = np.linalg.solve(
        hessian[well_conditioned], gradient[well_conditioned, :, None],
    )[:, :, 0]
    joint_fraction = np.full(local_count, np.nan)
    joint_fraction[well_conditioned] = np.sum(
        gradient[well_conditioned] * joint[well_conditioned], axis=1,
    ) / power[well_conditioned]
    global_power = float(np.sum(power))
    mode_power = {
        label: float(np.sum(np.divide(
            gradient[:, index] ** 2, diagonal[:, index],
            out=np.zeros(local_count), where=diagonal[:, index] > 0.0,
        )))
        for index, label in enumerate(labels)
    }
    return {
        "labels": labels,
        "coefficients": coefficients.reshape(fibers, columns, 4),
        "centroid_precision": diagonal[:, 1].reshape(fibers, columns),
        "power": power.reshape(fibers, columns),
        "individual_fraction": individual_fraction.reshape(fibers, columns, 4),
        "joint_fraction": joint_fraction.reshape(fibers, columns),
        "mode_weighted_residual_power": mode_power,
        "mode_power_fraction": {label: value / global_power for label, value in mode_power.items()},
        "joint_power_fraction": float(
            np.nansum(gradient[well_conditioned] * joint[well_conditioned]) / global_power
        ),
    }


def _correlation_scales(lag: np.ndarray, correlation: np.ndarray) -> dict[str, np.ndarray | float | None]:
    valid = np.isfinite(correlation)
    result: dict[str, np.ndarray | float | None] = {"lag": lag, "correlation": correlation}
    if lag.size < 2 or not valid[1]:
        result.update({"half_scale": None, "e_fold_scale": None, "zero_crossing": None})
        return result
    start = float(correlation[1])
    for name, threshold in (("half_scale", 0.5 * start), ("e_fold_scale", start / np.e), ("zero_crossing", 0.0)):
        selected = np.flatnonzero(valid[1:] & (correlation[1:] <= threshold)) + 1
        result[name] = None if not selected.size else float(lag[selected[0]])
    return result


def _x_autocorrelation(values: np.ndarray, max_lag: int = 128) -> dict[str, np.ndarray | float | None]:
    centered = values - np.nanmedian(values)
    lag = np.arange(min(max_lag, values.shape[1] - 1) + 1)
    correlation = np.full(lag.size, np.nan)
    for offset in lag:
        first = centered[:, : values.shape[1] - offset] if offset else centered
        second = centered[:, offset:] if offset else centered
        valid = np.isfinite(first) & np.isfinite(second)
        if not np.any(valid):
            continue
        first, second = first[valid], second[valid]
        denominator = np.sqrt(np.sum(first * first) * np.sum(second * second))
        correlation[offset] = np.sum(first * second) / denominator if denominator > 0.0 else np.nan
    return _correlation_scales(lag.astype(float), correlation)


def _y_autocorrelation(values: np.ndarray, y: np.ndarray, max_fiber_lag: int = 96) -> dict[str, np.ndarray | float | None]:
    centered = values - np.nanmedian(values)
    lag = np.arange(min(max_fiber_lag, values.shape[0] - 1) + 1)
    distance = np.zeros(lag.size)
    correlation = np.full(lag.size, np.nan)
    correlation[0] = 1.0
    for offset in lag[1:]:
        first, second = centered[:-offset], centered[offset:]
        valid = np.isfinite(first) & np.isfinite(second)
        if not np.any(valid):
            continue
        first, second = first[valid], second[valid]
        denominator = np.sqrt(np.sum(first * first) * np.sum(second * second))
        correlation[offset] = np.sum(first * second) / denominator if denominator > 0.0 else np.nan
        distance[offset] = float(np.nanmedian(np.abs(y[offset:] - y[:-offset])))
    result = _correlation_scales(distance, correlation)
    result["fiber_lag"] = lag
    return result


def _remove_trace_owned_low_order(
    delta: np.ndarray, precision: np.ndarray, geometry: LDLSGeometry,
) -> np.ndarray:
    """Reserve each fiber's validated degree-4 X component for the trace model."""
    residual = delta.copy()
    for fiber in range(delta.shape[0]):
        valid = np.isfinite(delta[fiber]) & np.isfinite(precision[fiber]) & (precision[fiber] > 0.0)
        if not np.any(valid):
            continue
        design = geometry.trace_basis[fiber, valid]
        root_weight = np.sqrt(precision[fiber, valid])
        coefficient, *_ = np.linalg.lstsq(
            design * root_weight[:, None], delta[fiber, valid] * root_weight, rcond=None,
        )
        residual[fiber, valid] -= design @ coefficient
    return residual


def _clamped_cubic_knots(low: float, high: float, spacing: float) -> np.ndarray:
    interior = np.arange(low + spacing, high, spacing)
    return np.r_[np.repeat(low, 4), interior, np.repeat(high, 4)]


def _tensor_cubic_design(x: np.ndarray, y: np.ndarray, x_knots: np.ndarray, y_knots: np.ndarray) -> sparse.csr_matrix:
    x_basis = BSpline.design_matrix(x, x_knots, 3, extrapolate=False).tocsr()
    y_basis = BSpline.design_matrix(y, y_knots, 3, extrapolate=False).tocsr()
    if not (np.all(np.diff(x_basis.indptr) == 4) and np.all(np.diff(y_basis.indptr) == 4)):
        raise RuntimeError("clamped cubic spline basis must have four local terms per coordinate")
    count = x.size
    x_index, x_value = x_basis.indices.reshape(count, 4), x_basis.data.reshape(count, 4)
    y_index, y_value = y_basis.indices.reshape(count, 4), y_basis.data.reshape(count, 4)
    row = np.repeat(np.arange(count), 16)
    column = (y_index[:, :, None] * x_basis.shape[1] + x_index[:, None, :]).reshape(-1)
    value = (y_value[:, :, None] * x_value[:, None, :]).reshape(-1)
    return sparse.csr_matrix((value, (row, column)), shape=(count, x_basis.shape[1] * y_basis.shape[1]))


def _select_y_knot_spacing(y_correlation: dict[str, np.ndarray | float | None]) -> float:
    measured = y_correlation["e_fold_scale"] or y_correlation["half_scale"] or 64.0
    # A cubic basis has four-knot local support, so use a simple spacing no
    # larger than half the observed e-fold scale rather than a fiber-scale grid.
    eligible = [candidate for candidate in (16.0, 32.0, 64.0, 128.0, 256.0) if candidate <= float(measured) / 2.0]
    return float(max(eligible, default=16.0))


def _fit_detector_displacement_field(
    evidence: LDLSEvidence,
    geometry: LDLSGeometry,
    trace: np.ndarray,
    delta: np.ndarray,
    precision: np.ndarray,
    *,
    x_knot_spacing: float,
    y_knot_spacing: float,
) -> DetectorDisplacementField:
    """One frozen-weight, independent-per-half cubic spline fit in detector coordinates."""
    ny, nx = evidence.image.shape
    dense = np.zeros((ny, nx), float)
    fit: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    x_coordinate = np.broadcast_to(np.arange(nx, dtype=float), trace.shape)
    for low, high in ((0, int(geometry.amplifier_boundary) - 1), (int(geometry.amplifier_boundary), ny - 1)):
        selected = (
            (trace >= low) & (trace <= high) & np.isfinite(delta) & np.isfinite(precision)
            & (precision > 0.0)
        )
        x = x_coordinate[selected]
        y = trace[selected]
        target = delta[selected]
        root_weight = np.sqrt(precision[selected])
        x_knots = _clamped_cubic_knots(0.0, float(nx - 1), x_knot_spacing)
        y_knots = _clamped_cubic_knots(float(low), float(high), y_knot_spacing)
        design = _tensor_cubic_design(x, y, x_knots, y_knots)
        solution = lsqr(
            design.multiply(root_weight[:, None]), target * root_weight,
            atol=1e-9, btol=1e-9, iter_lim=1000,
        )
        coefficient = solution[0].reshape(y_knots.size - 4, x_knots.size - 4)
        dense_y = BSpline.design_matrix(np.arange(low, high + 1, dtype=float), y_knots, 3).toarray()
        dense_x = BSpline.design_matrix(np.arange(nx, dtype=float), x_knots, 3).toarray()
        dense[low:high + 1] = dense_y @ coefficient @ dense_x.T
        fit.append((x_knots, y_knots, coefficient))
    return DetectorDisplacementField(
        dense, fit[0][0], fit[0][1], fit[0][2], fit[1][0], fit[1][1], fit[1][2],
        int(geometry.amplifier_boundary), x_knot_spacing, y_knot_spacing,
    )


def _neighbor_shift_summary(delta: np.ndarray) -> dict[str, dict[str, float | int]]:
    return {
        "common": _summary(0.5 * (delta[:-1] + delta[1:])),
        "differential": _summary(0.5 * (delta[:-1] - delta[1:])),
    }


def run_detector_displacement_experiment(
    evidence: LDLSEvidence,
    geometry: LDLSGeometry,
    sampling: LDLSSampling,
    state: ProfileTraceState,
    *,
    cache: ProfileCache,
) -> DetectorDisplacementExperiment:
    """Frozen state -> raw deltaT -> one detector field -> corrected closure only."""
    timing: dict[str, float] = {}
    started = perf_counter()
    baseline = evaluate_state(evidence, geometry, sampling, state, cache=cache, derivatives=True, debug_contributions=True)
    projection_before = _local_residual_mode_projection(evidence, geometry, sampling, baseline)
    timing["raw_deltaT_projection"] = perf_counter() - started
    delta_before = projection_before["coefficients"][:, :, 1]
    x_correlation_before = _x_autocorrelation(delta_before)
    y_correlation_before = _y_autocorrelation(delta_before, baseline.trace)
    low_order_removed = _remove_trace_owned_low_order(
        delta_before, projection_before["centroid_precision"], geometry,
    )
    started = perf_counter()
    field = _fit_detector_displacement_field(
        evidence, geometry, baseline.trace, low_order_removed, projection_before["centroid_precision"],
        x_knot_spacing=32.0, y_knot_spacing=_select_y_knot_spacing(y_correlation_before),
    )
    timing["dy_field_fit"] = perf_counter() - started
    started = perf_counter()
    corrected = evaluate_state(
        evidence, geometry, sampling, state, cache=cache, derivatives=True,
        debug_contributions=True, detector_displacement=field,
    )
    timing["dy_aware_rerender"] = perf_counter() - started
    started = perf_counter()
    projection_after = _local_residual_mode_projection(evidence, geometry, sampling, corrected)
    x_correlation_after = _x_autocorrelation(projection_after["coefficients"][:, :, 1])
    timing["final_closure_diagnostic_projection"] = perf_counter() - started
    return DetectorDisplacementExperiment(
        field, baseline, corrected, projection_before, projection_after,
        x_correlation_before, x_correlation_after, y_correlation_before,
        low_order_removed, timing,
    )


def _active_rows(service: ArtifactService, kind: str, zipcode: ZipCode) -> list[dict[str, Any]]:
    return sorted((row for row in service.adapter.list_all(kind=kind) if str(row.get("state") or "active") == "active" and str(row.get("amp_key") or "") == zipcode.key()), key=lambda row: int(row["id"]), reverse=True)


def _parents(service: ArtifactService, row: dict[str, Any]) -> set[int]:
    return {int(value) for value in service.describe(row)["provenance"]["parents"]}


def _load(service: ArtifactService, row: dict[str, Any], component: str) -> np.ndarray:
    return np.asarray(service.load_component(row, component)["data"])


def _group_exposure_ids(row: dict[str, Any]) -> tuple[str, ...]:
    """Return the persisted calibration-group exposure IDs for one artifact."""
    metadata = row.get("metadata") or {}
    group = metadata.get("calibration_group") or {}
    membership = group.get("frame_membership") or []
    values = [item.get("exposure_id") for item in membership if item.get("exposure_id")]
    if not values:
        values = group.get("exposure_ids") or metadata.get("exposure_ids") or []
    return tuple(sorted(str(value) for value in values))


def _select_one(rows: list[dict[str, Any]], *, label: str, zipcode: ZipCode) -> dict[str, Any]:
    if len(rows) != 1:
        ids = [int(row["id"]) for row in rows]
        raise RuntimeError(
            f"expected exactly one {label} for {zipcode.key()}, found {len(rows)}: {ids}"
        )
    return rows[0]


def _array_checksum(array: np.ndarray) -> str:
    """Shape-, dtype-, and byte-stable input provenance checksum."""
    values = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(values.dtype).encode())
    digest.update(np.asarray(values.shape, dtype=np.int64).tobytes())
    digest.update(values.tobytes())
    return digest.hexdigest()


def _select_ldls_trace(
    service: ArtifactService,
    zipcode: ZipCode,
    *,
    exposure_ids: Iterable[str] | None = None,
    ldls_artifact_id: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select one LDLS product and its directly derived trace map.

    With an exposure selector, both the LDLS group and its trace child are
    required to resolve unambiguously.  Without one, retain the historical
    newest-active behavior for the legacy development invocation.
    """
    requested = None if exposure_ids is None else tuple(sorted(str(value) for value in exposure_ids))
    if ldls_artifact_id is not None:
        ldls = service.adapter.get_row(int(ldls_artifact_id))
        if ldls is None:
            raise RuntimeError(f"master_ldls artifact {ldls_artifact_id} was not found")
        if str(ldls.get("state") or "active") != "active":
            raise RuntimeError(f"master_ldls artifact {ldls_artifact_id} is not active")
        if str(ldls.get("canonical_kind") or ldls.get("kind")) != "master_ldls":
            raise RuntimeError(
                f"artifact {ldls_artifact_id} is not a master_ldls: "
                f"{ldls.get('canonical_kind') or ldls.get('kind')}"
            )
        if str(ldls.get("amp_key") or "") != zipcode.key():
            raise RuntimeError(
                f"master_ldls artifact {ldls_artifact_id} belongs to "
                f"{ldls.get('amp_key')}, expected {zipcode.key()}"
            )
        if requested is not None and _group_exposure_ids(ldls) != requested:
            raise RuntimeError(
                f"master_ldls artifact {ldls_artifact_id} exposure group is "
                f"{list(_group_exposure_ids(ldls))}, expected {list(requested)}"
            )
    else:
        ldls_rows = _active_rows(service, "master_ldls", zipcode)
        if requested is not None:
            ldls_rows = [row for row in ldls_rows if _group_exposure_ids(row) == requested]
            ldls = _select_one(ldls_rows, label="master_ldls exposure group", zipcode=zipcode)
        else:
            ldls = ldls_rows[0] if ldls_rows else None
            if ldls is None:
                raise RuntimeError(f"no active master_ldls for {zipcode.key()}")

    trace_rows = [
        row for row in _active_rows(service, "trace_map", zipcode)
        if int(ldls["id"]) in _parents(service, row)
    ]
    if requested is not None:
        trace_rows = [
            row for row in trace_rows
            if not _group_exposure_ids(row) or _group_exposure_ids(row) == requested
        ]
        trace = _select_one(trace_rows, label="trace_map derived from selected master_ldls", zipcode=zipcode)
    elif not trace_rows:
        raise RuntimeError(
            f"no active trace_map directly derived from an active master_ldls for {zipcode.key()}"
        )
    else:
        trace = trace_rows[0]
    return ldls, trace


def _validate_pair_zipcodes(lower: ZipCode, upper: ZipCode) -> tuple[str, str, str]:
    if (lower.ifuslot, lower.ifuid, lower.specid, lower.controller) != (
        upper.ifuslot, upper.ifuid, upper.specid, upper.controller
    ):
        raise ValueError(
            "lower and upper ZipCodes must identify one physical CCD: "
            f"{lower.key()} vs {upper.key()}"
        )
    for side, lower_amp, upper_amp in PAIR.values():
        if (lower.amp, upper.amp) == (lower_amp, upper_amp):
            return side, lower_amp, upper_amp
    raise ValueError(
        "lower and upper ZipCodes must be an ordered physical-CCD pair "
        f"(LL/LU or RU/RL), got {lower.amp}/{upper.amp}"
    )


def _artifact_selection_summary(
    service: ArtifactService, row: dict[str, Any], *, array_shape: tuple[int, ...], component: str,
) -> dict[str, Any]:
    description = service.describe(row)
    metadata = description["summary"]
    group = metadata.get("calibration_group") or {}
    return {
        "id": int(row["id"]),
        "kind": description["canonical_kind"],
        "component": component,
        "calibration_group_id": metadata.get("calibration_group_id"),
        "computation_identity": (
            group.get("computation_id")
            or metadata.get("computation_identity")
            or description.get("revision")
        ),
        "artifact_revision": description.get("revision"),
        "source_exposure_ids": list(_group_exposure_ids(row)),
        "raw_parent_ids": list(description["provenance"]["raw_parents"]),
        "array_shape": list(array_shape),
    }


def load_ldls_evidence_pair(
    service: ArtifactService,
    lower_zipcode: ZipCode,
    upper_zipcode: ZipCode,
    *,
    exposure_ids: Iterable[str] | None = None,
    lower_ldls_artifact_id: int | None = None,
    upper_ldls_artifact_id: int | None = None,
    aperture_width: float = 5.0,
) -> tuple[LDLSEvidence, dict[str, Any], np.ndarray]:
    """Load an explicit amplifier pair through read-only ArtifactService APIs."""
    side, lower_amp, upper_amp = _validate_pair_zipcodes(lower_zipcode, upper_zipcode)
    lower_ldls, lower_trace = _select_ldls_trace(
        service, lower_zipcode, exposure_ids=exposure_ids,
        ldls_artifact_id=lower_ldls_artifact_id,
    )
    upper_ldls, upper_trace = _select_ldls_trace(
        service, upper_zipcode, exposure_ids=exposure_ids,
        ldls_artifact_id=upper_ldls_artifact_id,
    )
    lower_group = _group_exposure_ids(lower_ldls)
    upper_group = _group_exposure_ids(upper_ldls)
    if lower_group != upper_group:
        raise RuntimeError(
            "selected master_ldls products do not share one calibration exposure group: "
            f"{lower_zipcode.key()}={list(lower_group)}, {upper_zipcode.key()}={list(upper_group)}"
        )
    lower_image = _load(service, lower_ldls, "master_ldls").astype(float)
    upper_image = _load(service, upper_ldls, "master_ldls").astype(float)
    lower_mask, upper_mask = ~np.isfinite(lower_image), ~np.isfinite(upper_image)
    for row, image, mask in ((lower_ldls, lower_image, lower_mask), (upper_ldls, upper_image, upper_mask)):
        try:
            mask |= _load(service, row, "flat_response_mask").astype(bool)
        except KeyError:
            pass
    assembly = assemble_physical_ccd(
        lower_image, upper_image, side=side, lower_amp=lower_amp, upper_amp=upper_amp,
        lower_variance=np.maximum(lower_image, 1.0), upper_variance=np.maximum(upper_image, 1.0),
        lower_mask=lower_mask, upper_mask=upper_mask,
    )
    assembly_image = assembly.get_array("image")
    lower_trace_array = _load(service, lower_trace, "fiber_trace_map")
    upper_trace_array = _load(service, upper_trace, "fiber_trace_map")
    scatter = fit_gap_scattered_light(assembly, lower_trace_array, upper_trace_array)
    image = np.asarray(scatter.get_array("scatter_subtracted_image"), float)
    valid = ~np.asarray(assembly.get_array("pixel_mask"), bool) & np.isfinite(image)
    variance = np.maximum(np.abs(image), 1.0)
    base_trace = physical_trace_map(lower_trace_array, upper_trace_array).astype(float)
    extraction = extract_fractional_aperture(image, variance, base_trace, pixel_mask=~valid, width=aperture_width)
    rows, weights, _ = fractional_aperture_geometry(base_trace, image.shape[0], width=aperture_width)
    yy, xx = np.nonzero(valid)
    evidence = LDLSEvidence(image, variance, valid, np.asarray(extraction.get_array("spectrum"), float), base_trace, rows, weights, np.arange(base_trace.shape[0]), np.where(base_trace[:, 0] < UPPER_AMPLIFIER_Y_OFFSET, 0, 1), (0.0, float(UPPER_AMPLIFIER_Y_OFFSET), float(image.shape[0])), xx, yy, image[yy, xx], variance[yy, xx])
    input_checksums = {
        "scatter_subtracted_image": _array_checksum(image),
        "variance": _array_checksum(variance),
        "valid_mask": _array_checksum(valid),
        "five_pixel_flux": _array_checksum(evidence.five_pixel_flux),
        "base_trace": _array_checksum(base_trace),
        "aperture_rows": _array_checksum(rows),
        "aperture_weights": _array_checksum(weights),
    }
    return evidence, {
        "artifact_db": service.db_path,
        "lower_zipcode": lower_zipcode.key(),
        "upper_zipcode": upper_zipcode.key(),
        "selected_exposure_ids": list(lower_group),
        "lower_master_ldls": _artifact_selection_summary(service, lower_ldls, array_shape=lower_image.shape, component="master_ldls"),
        "upper_master_ldls": _artifact_selection_summary(service, upper_ldls, array_shape=upper_image.shape, component="master_ldls"),
        "lower_trace_map": _artifact_selection_summary(service, lower_trace, array_shape=lower_trace_array.shape, component="fiber_trace_map"),
        "upper_trace_map": _artifact_selection_summary(service, upper_trace, array_shape=upper_trace_array.shape, component="fiber_trace_map"),
        "master_ldls_array_shapes": {
            "lower": list(lower_image.shape), "upper": list(upper_image.shape),
        },
        "trace_map_shapes": {
            "lower": list(lower_trace_array.shape), "upper": list(upper_trace_array.shape),
        },
        "assembled_physical_ccd_shape": list(image.shape),
        "physical_ccd_side": side,
        "read_only": True,
        "input_checksums": input_checksums,
    }, assembly_image


def load_ldls_evidence(
    service: ArtifactService,
    requested: ZipCode,
    *,
    exposure_ids: Iterable[str] | None = None,
    lower_ldls_artifact_id: int | None = None,
    upper_ldls_artifact_id: int | None = None,
    aperture_width: float = 5.0,
) -> tuple[LDLSEvidence, dict[str, Any], np.ndarray]:
    """Load the same physical-CCD LDLS/trace/mask inputs through public APIs."""
    if requested.amp not in PAIR:
        raise ValueError("zipcode amplifier must be LL, LU, RU, or RL")
    side, lower_amp, upper_amp = PAIR[requested.amp]
    lower_zip = ZipCode(requested.ifuslot, requested.ifuid, requested.specid, lower_amp, requested.controller)
    upper_zip = ZipCode(requested.ifuslot, requested.ifuid, requested.specid, upper_amp, requested.controller)
    evidence, selection, assembly_image = load_ldls_evidence_pair(
        service, lower_zip, upper_zip, exposure_ids=exposure_ids,
        lower_ldls_artifact_id=lower_ldls_artifact_id,
        upper_ldls_artifact_id=upper_ldls_artifact_id,
        aperture_width=aperture_width,
    )
    selection["requested_zipcode"] = requested.key()
    return evidence, selection, assembly_image


def _write_assembled_master_ldls_diagnostic(
    output: Path, assembly_image: np.ndarray, selection: dict[str, Any],
) -> None:
    """Write the pre-scatter physical-CCD image for development inspection only."""
    from astropy.io import fits

    output.mkdir(parents=True, exist_ok=True)
    header = fits.Header()
    header["CCD_SIDE"] = selection["physical_ccd_side"]
    header["LOWERZC"] = selection["lower_zipcode"]
    header["UPPERZC"] = selection["upper_zipcode"]
    fits.PrimaryHDU(assembly_image, header=header).writeto(
        output / "assembled_master_ldls_physical_ccd.fits", overwrite=True,
    )


def _write_diagnostics(
    output: Path,
    evidence: LDLSEvidence,
    geometry: LDLSGeometry,
    sampling: LDLSSampling,
    closure: ForwardEvaluation,
    trace_convergence: TraceStep,
    displacement: DetectorDisplacementExperiment | None = None,
    profile_cache_quantization: float = 2e-3,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    model_fwhm = model_fwhm_field(
        closure.R, closure.sigma, quantization=profile_cache_quantization,
    )
    proposal_delta = np.einsum(
        "fxt,ft->fx", geometry.trace_basis, trace_convergence.incremental_trace_coeff,
    )
    diagnostic_arrays = {
        "trace": closure.trace, "W": closure.W, "f_sigma": closure.f_sigma,
        "aperture_illumination_alpha": np.asarray(0.0),
        "R": closure.R, "sigma": closure.sigma, "C5": closure.C5,
        "model_fwhm": model_fwhm,
        "amplitude": closure.total_amplitude,
        "aperture_flux_closure": closure.total_amplitude * closure.C5 - evidence.five_pixel_flux,
        "model_samples": closure.model_samples, "residuals": closure.residuals,
        "residual_sample_x": geometry.sample_x[sampling.sample_indices],
        "residual_sample_y": geometry.sample_y[sampling.sample_indices],
        "unapplied_trace_coeff": trace_convergence.incremental_trace_coeff,
        "unapplied_trace_delta": proposal_delta,
        "unapplied_trace_predicted_loss_change": trace_convergence.predicted_loss_change,
    }
    if displacement is not None:
        diagnostic_arrays.update({
            "detector_displacement": displacement.field.dense,
            "dy_corrected_model_samples": displacement.corrected.model_samples,
            "dy_corrected_residuals": displacement.corrected.residuals,
        })
    np.savez_compressed(output / "forward_ldls_diagnostics.npz", **diagnostic_arrays)
    residual_image = np.full(evidence.image.shape, np.nan)
    residual_image[
        geometry.sample_y[sampling.sample_indices], geometry.sample_x[sampling.sample_indices]
    ] = closure.residuals
    finite_residuals = closure.residuals[np.isfinite(closure.residuals)]
    residual_vmin, residual_vmax = np.percentile(finite_residuals, (2.0, 98.0))
    from astropy.io import fits
    fits.PrimaryHDU(residual_image).writeto(output / "residual_image.fits", overwrite=True)
    assert closure.fiber_contributions is not None
    sample_position = np.full(geometry.sample_x.size, -1, dtype=np.int64)
    sample_position[sampling.sample_indices] = np.arange(sampling.sample_indices.size)
    contribution_position = sample_position[geometry.contribution_sample_index]
    included = contribution_position >= 0
    cf, cx = geometry.contribution_fiber[included], geometry.contribution_x[included]
    u = geometry.contribution_detector_row[included] - closure.trace[cf, cx]
    deblended = closure.residuals[contribution_position[included]] + closure.fiber_contributions[included]
    normalized = deblended / closure.total_amplitude[cf, cx]
    profile = closure.P[included]
    core = np.isfinite(normalized) & np.isfinite(profile) & (np.abs(u) <= geometry.compact_support)
    core_u = u[core]
    core_normalized = normalized[core]
    core_profile = profile[core]
    profile_edges = np.linspace(-geometry.compact_support, geometry.compact_support, 91)
    profile_bin = np.clip(np.digitize(core_u, profile_edges) - 1, 0, profile_edges.size - 2)
    profile_center = 0.5 * (profile_edges[1:] + profile_edges[:-1])
    profile_data = np.full(profile_center.size, np.nan)
    profile_model = np.full(profile_center.size, np.nan)
    profile_order = np.argsort(profile_bin, kind="stable")
    sorted_bin = profile_bin[profile_order]
    starts = np.r_[0, np.flatnonzero(np.diff(sorted_bin)) + 1, profile_order.size]
    for start, stop in zip(starts[:-1], starts[1:]):
        index = sorted_bin[start]
        selected = profile_order[start:stop]
        profile_data[index] = np.median(core_normalized[selected])
        profile_model[index] = np.median(core_profile[selected])
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), constrained_layout=True)
    for axis, field, title in ((axes[0, 0], closure.W, "final W"), (axes[0, 1], closure.f_sigma, "final f_sigma"), (axes[1, 0], closure.trace - evidence.base_trace, "trace correction")):
        image = axis.imshow(field, aspect="auto", origin="lower")
        fig.colorbar(image, ax=axis)
        axis.set(title=title, xlabel="detector x", ylabel="fiber")

    image = axes[0, 2].imshow(
        residual_image, aspect="auto", origin="lower", cmap="coolwarm",
        vmin=residual_vmin, vmax=residual_vmax,
    )
    fig.colorbar(image, ax=axes[0, 2])
    axes[0, 2].set(title="final compact-sample residual", xlabel="detector x", ylabel="detector y")
    axes[1, 1].plot(profile_center, profile_data, "o", ms=2.5, color="tab:purple", label="deblended data")
    axes[1, 1].plot(profile_center, profile_model, color="black", lw=1.4, label="fixed model profile")
    axes[1, 1].set(
        title="compact profile samples at final trace",
        xlabel="u from final trace (pixel)", ylabel="deblended LDLS / compact flux",
        xlim=(-geometry.compact_support, geometry.compact_support),
    )
    axes[1, 1].grid(alpha=0.2)
    axes[1, 1].legend(frameon=False, fontsize=7)
    if displacement is None:
        axes[1, 2].hist(finite_residuals, bins=120, histtype="step", color="black")
        axes[1, 2].set(title="compact-sample residual distribution", xlabel="LDLS residual")
    else:
        limit = float(np.percentile(np.abs(displacement.field.dense), 98.0))
        image = axes[1, 2].imshow(
            displacement.field.dense, aspect="auto", origin="lower", cmap="coolwarm",
            vmin=-limit, vmax=limit,
        )
        axes[1, 2].axhline(displacement.field.amplifier_boundary - 0.5, color="black", linewidth=0.8)
        fig.colorbar(image, ax=axes[1, 2], label="pixels")
        axes[1, 2].set(
            title="detector-coordinate correction dy(x, y)", xlabel="detector x", ylabel="detector y",
        )
    fig.savefig(output / "forward_ldls_diagnostics.png", dpi=160)
    plt.close(fig)
    _write_profile_shape_diagnostics(
        output, closure.W, closure.f_sigma, closure.R, closure.sigma, model_fwhm,
    )


def _write_profile_shape_diagnostics(
    output: Path,
    W: np.ndarray,
    f_sigma: np.ndarray,
    radius: np.ndarray,
    sigma: np.ndarray,
    model_fwhm: np.ndarray,
) -> None:
    """Write development-only maps and per-fiber summaries of profile shape."""
    fields = (
        (W, "W", "W (pixels)"),
        (f_sigma, "f_sigma", "f_sigma"),
        (model_fwhm, "model FWHM", "pixels"),
        (radius, "R", "pixels"),
        (sigma, "sigma", "pixels"),
        (np.divide(model_fwhm, W, out=np.full_like(model_fwhm, np.nan), where=W != 0.0), "model FWHM / W", "ratio"),
    )
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), constrained_layout=True)
    for axis, (field, title, colorbar_label) in zip(axes.flat, fields):
        image = axis.imshow(field, aspect="auto", origin="lower")
        fig.colorbar(image, ax=axis, label=colorbar_label)
        axis.set(title=title, xlabel="detector x", ylabel="fiber")
    fig.savefig(output / "forward_ldls_profile_shape_diagnostics.png", dpi=160)
    plt.close(fig)

    fibers = np.arange(W.shape[0])
    summaries = (
        (W, "W", "pixels"),
        (radius, "R", "pixels"),
        (sigma, "sigma", "pixels"),
        (model_fwhm, "model FWHM", "pixels"),
    )
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True, constrained_layout=True)
    for axis, (field, title, unit) in zip(axes.flat, summaries):
        axis.plot(fibers, np.nanmedian(field, axis=1), color="black", linewidth=1.2)
        axis.axvline(112 - 0.5, color="tab:red", linewidth=0.8, alpha=0.8)
        axis.set(title=f"median {title} by fiber", ylabel=unit, xlabel="fiber")
        axis.grid(alpha=0.2)
    fig.savefig(output / "forward_ldls_profile_shape_by_fiber.png", dpi=160)
    plt.close(fig)


def _write_aperture_illumination_comparison(
    output: Path, closure: ForwardEvaluation, alpha: float,
) -> dict[str, float]:
    """Plot the one global illumination-law comparison at final median width."""
    output.mkdir(parents=True, exist_ok=True)
    representative_R = float(np.median(closure.R[np.isfinite(closure.R)]))
    representative_sigma = float(np.median(closure.sigma[np.isfinite(closure.sigma)]))
    uniform = fourier_compact_profile(representative_R, representative_sigma, alpha=0.0)
    fitted = fourier_compact_profile(representative_R, representative_sigma, alpha=alpha)
    parabolic = fourier_compact_profile(representative_R, representative_sigma, alpha=1.0)
    coordinate = uniform.coordinate
    fig, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True, constrained_layout=True)
    for profile, label, color in (
        (uniform, "uniform α=0", "black"),
        (fitted, f"experimental α={alpha:.5g}", "tab:blue"),
        (parabolic, "parabolic α=1", "tab:orange"),
    ):
        axes[0].plot(coordinate, profile.density, label=label, color=color)
    axes[0].set(
        title="EXPERIMENTAL: global radial fiber-illumination profiles",
        ylabel="unit-integral compact profile", xlim=(-8.0, 8.0),
    )
    axes[0].legend(frameon=False)
    safe = np.abs(uniform.density) > 1e-7 * np.max(np.abs(uniform.density))
    fractional = np.full_like(uniform.density, np.nan)
    fractional[safe] = fitted.density[safe] / uniform.density[safe] - 1.0
    axes[1].plot(coordinate, fractional, color="tab:blue")
    axes[1].axhline(0.0, color="black", linewidth=0.8)
    axes[1].set(
        xlabel="profile coordinate u (pixel)", ylabel="fitted / uniform − 1",
        xlim=(-8.0, 8.0),
    )
    fig.savefig(output / "aperture_illumination_profile_comparison.png", dpi=170)
    plt.close(fig)
    return {
        "alpha": float(alpha), "representative_R": representative_R,
        "representative_sigma": representative_sigma,
    }


def _write_detector_displacement_experiment(
    output: Path,
    evidence: LDLSEvidence,
    geometry: LDLSGeometry,
    sampling: LDLSSampling,
    experiment: DetectorDisplacementExperiment,
) -> dict[str, Any]:
    """Persist and plot the one-shot experimental detector-coordinate correction."""
    output.mkdir(parents=True, exist_ok=True)
    before = experiment.projection_before
    after = experiment.projection_after
    delta_before = before["coefficients"][:, :, 1]
    delta_after = after["coefficients"][:, :, 1]
    residual_image = np.full(evidence.image.shape, np.nan)
    residual_image[
        geometry.sample_y[sampling.sample_indices], geometry.sample_x[sampling.sample_indices]
    ] = -experiment.corrected.residuals
    x = np.broadcast_to(np.arange(evidence.image.shape[1]), delta_before.shape)
    trace = experiment.baseline.trace
    delta_limit = float(np.nanpercentile(np.abs(np.r_[delta_before.ravel(), delta_after.ravel()]), 98.0))
    dy_limit = float(np.percentile(np.abs(experiment.field.dense), 98.0))
    residual_limit = float(np.percentile(np.abs(experiment.corrected.residuals), 98.0))
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), constrained_layout=True)
    for axis, values, title in (
        (axes[0, 0], delta_before, "raw local deltaT before dy"),
        (axes[1, 0], delta_after, "raw local deltaT after dy corrected render"),
    ):
        image = axis.scatter(
            x.ravel(), trace.ravel(), c=values.ravel(), s=1.1, marker="s", linewidths=0,
            rasterized=True, cmap="coolwarm", vmin=-delta_limit, vmax=delta_limit,
        )
        fig.colorbar(image, ax=axis, label="pixels")
        axis.set(title=f"EXPERIMENTAL: {title}", xlabel="detector x", ylabel="detector y")
    image = axes[0, 1].imshow(
        experiment.field.dense, origin="lower", aspect="auto", cmap="coolwarm",
        vmin=-dy_limit, vmax=dy_limit,
    )
    axes[0, 1].axhline(experiment.field.amplifier_boundary - 0.5, color="black", linewidth=0.8)
    fig.colorbar(image, ax=axes[0, 1], label="pixels")
    axes[0, 1].set(
        title="EXPERIMENTAL: fitted detector-coordinate dy(x, y)", xlabel="detector x", ylabel="detector y",
    )
    image = axes[1, 1].imshow(
        residual_image, origin="lower", aspect="auto", cmap="coolwarm",
        vmin=-residual_limit, vmax=residual_limit,
    )
    fig.colorbar(image, ax=axes[1, 1], label="model − data")
    axes[1, 1].set(
        title="EXPERIMENTAL: dy-corrected compact-sample residual", xlabel="detector x", ylabel="detector y",
    )
    fig.savefig(output / "detector_displacement_comparison.png", dpi=170)
    plt.close(fig)
    common_before = _neighbor_shift_summary(delta_before)
    common_after = _neighbor_shift_summary(delta_after)
    loss_before = experiment.baseline.robust_loss
    loss_after = experiment.corrected.robust_loss
    report = {
        "experimental_only": True,
        "authoritative_state_modified": False,
        "response_or_trace_refit": False,
        "second_detector_displacement_fit": False,
        "residual_sign": "data - model; plots show model - data",
        "centroid_derivative": "-A Pprime",
        "loss_before_dy": loss_before,
        "loss_after_dy": loss_after,
        "loss_absolute_improvement": loss_before - loss_after,
        "loss_fractional_improvement": (loss_before - loss_after) / loss_before,
        "raw_deltaT_before": _summary(delta_before),
        "raw_deltaT_after": _summary(delta_after),
        "x_correlation_before": {
            key: value for key, value in experiment.x_correlation_before.items() if key not in {"lag", "correlation"}
        },
        "x_correlation_after": {
            key: value for key, value in experiment.x_correlation_after.items() if key not in {"lag", "correlation"}
        },
        "y_correlation_before": {
            key: value for key, value in experiment.y_correlation_before.items() if key not in {"lag", "correlation", "fiber_lag"}
        },
        "neighbor_shift_before": common_before,
        "neighbor_shift_after": common_after,
        "mode_weighted_residual_power_fraction_before": before["mode_power_fraction"],
        "mode_weighted_residual_power_fraction_after": after["mode_power_fraction"],
        "mode_weighted_residual_power_before": before["mode_weighted_residual_power"],
        "mode_weighted_residual_power_after": after["mode_weighted_residual_power"],
        "all_four_mode_weighted_residual_power_fraction_before": before["joint_power_fraction"],
        "all_four_mode_weighted_residual_power_fraction_after": after["joint_power_fraction"],
        "detector_field": {
            "representation": "independent per-amplifier-half tensor-product cubic B-splines",
            "x_knot_spacing_pixels": experiment.field.x_knot_spacing,
            "y_knot_spacing_pixels": experiment.field.y_knot_spacing,
            "amplifier_boundary": experiment.field.amplifier_boundary,
            "range_pixels": [float(np.min(experiment.field.dense)), float(np.max(experiment.field.dense))],
            "robust_limit_pixels": dy_limit,
            "fit_weights": "local frozen robust_weight / detector_variance through centroid projection precision",
            "fit_mask": "finite raw deltaT with positive centroid projection precision, separately by physical half",
            "low_order_constraint": "weighted per-fiber projection onto the accepted degree-4 trace Legendre basis removed before fitting dy",
            "lsqr_atol_btol": 1e-9,
            "lsqr_iter_lim": 1000,
        },
        "timing_seconds": experiment.timing_seconds,
    }
    np.savez_compressed(
        output / "detector_displacement_field.npz",
        lower_x_knots=experiment.field.lower_x_knots,
        lower_y_knots=experiment.field.lower_y_knots,
        lower_coefficients=experiment.field.lower_coefficients,
        upper_x_knots=experiment.field.upper_x_knots,
        upper_y_knots=experiment.field.upper_y_knots,
        upper_coefficients=experiment.field.upper_coefficients,
        dense_dy=experiment.field.dense,
        raw_deltaT_before=delta_before,
        low_order_removed_deltaT=experiment.low_order_removed,
        raw_deltaT_after=delta_after,
        centroid_explained_fraction_before=before["individual_fraction"][:, :, 1],
        centroid_explained_fraction_after=after["individual_fraction"][:, :, 1],
        all_four_explained_fraction_before=before["joint_fraction"],
        all_four_explained_fraction_after=after["joint_fraction"],
        x_autocorrelation_lag_before=experiment.x_correlation_before["lag"],
        x_autocorrelation_before=experiment.x_correlation_before["correlation"],
        x_autocorrelation_lag_after=experiment.x_correlation_after["lag"],
        x_autocorrelation_after=experiment.x_correlation_after["correlation"],
        y_autocorrelation_distance_before=experiment.y_correlation_before["lag"],
        y_autocorrelation_before=experiment.y_correlation_before["correlation"],
    )
    (output / "detector_displacement_experiment.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _write_state_and_provenance(
    output: Path,
    state: ProfileTraceState,
    geometry: LDLSGeometry,
    closure: ForwardEvaluation,
    trace_convergence: TraceStep,
    selection: dict[str, Any],
    settings: dict[str, Any],
    history: dict[str, list[dict[str, float]]],
    runtime: RuntimeProfile,
    detector_displacement_report: dict[str, Any] | None = None,
    aperture_illumination_experiment: dict[str, Any] | None = None,
) -> None:
    """Persist solved coefficients separately from all derived diagnostics."""
    np.savez_compressed(
        output / "forward_ldls_authoritative_state.npz",
        trace_coeff=state.trace_coeff,
        W_coeff=state.W_coeff,
        f_sigma_coeff=state.f_sigma_coeff,
        state_generation=np.asarray(state.generation, dtype=np.int64),
        reference_W=geometry.reference_W,
        reference_f_sigma=geometry.reference_f_sigma,
    )
    trace_delta = np.einsum("fxt,ft->fx", geometry.trace_basis, trace_convergence.incremental_trace_coeff)
    convergence = {
        "applied": False,
        "candidate_evaluated": False,
        "predicted_loss_change": trace_convergence.predicted_loss_change,
        "hessian_shape": list(trace_convergence.hessian.shape),
        "hessian_nnz": int(trace_convergence.hessian.nnz),
        "physical_delta_min": float(np.min(trace_delta)),
        "physical_delta_max": float(np.max(trace_delta)),
    }
    provenance = {
        "selected_artifacts": selection,
        "state_generation": state.generation,
        "aperture_illumination_model": "uniform",
        "alpha": 0.0,
        "alpha_fitted": False,
        "final_robust_loss": closure.robust_loss,
        "settings": settings,
        "trace_convergence_diagnostic": convergence,
        "history": history,
        "runtime_seconds": runtime.seconds,
        "detector_displacement_experiment": detector_displacement_report,
        "experimental_aperture_illumination": aperture_illumination_experiment,
    }
    (output / "forward_ldls_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="legacy artifact database option")
    parser.add_argument("--artifact-db", help="read-only ArtifactService database")
    parser.add_argument("--zipcode", help="legacy paired-CCD selector")
    parser.add_argument("--lower-zipcode", help="lower amplifier ZipCode for an explicit pair")
    parser.add_argument("--upper-zipcode", help="upper amplifier ZipCode for an explicit pair")
    parser.add_argument(
        "--ldls-exposure-id", action="append", dest="ldls_exposure_ids",
        help="exact exposure-group member; repeat once per selected LDLS input exposure",
    )
    parser.add_argument("--lower-master-ldls-id", type=int, help="explicit lower master_ldls artifact ID")
    parser.add_argument("--upper-master-ldls-id", type=int, help="explicit upper master_ldls artifact ID")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--support", type=float, default=9.0)
    parser.add_argument("--trace-degree", type=int, default=4)
    parser.add_argument("--response-degree", type=int, default=2)
    parser.add_argument("--sampling", choices=("full", "sparse", "robust_binned"), default="full")
    parser.add_argument("--sparse-stride", type=int, default=4)
    parser.add_argument("--initial-W", type=float, default=1.85)
    parser.add_argument("--initial-f-sigma", type=float, default=0.25)
    parser.add_argument("--initial-field", type=Path, help="NPZ with W and f_sigma arrays; optional A/B initialization only")
    parser.add_argument("--profile-cache-quantization", type=float, default=0.002)
    parser.add_argument(
        "--experimental-aperture-alpha", type=float,
        help="development-only fixed radial-illumination diagnostic alpha in [-0.5, 1]; never fitted or persisted as state",
    )
    args = parser.parse_args(argv)
    db_path = args.artifact_db or args.db
    if not db_path:
        parser.error("one of --artifact-db or --db is required")
    explicit_pair = args.lower_zipcode is not None or args.upper_zipcode is not None
    if explicit_pair and (args.lower_zipcode is None or args.upper_zipcode is None):
        parser.error("--lower-zipcode and --upper-zipcode must be supplied together")
    if explicit_pair and args.zipcode is not None:
        parser.error("use either --zipcode or --lower-zipcode/--upper-zipcode")
    if not explicit_pair and args.zipcode is None:
        parser.error("--zipcode or --lower-zipcode/--upper-zipcode is required")
    if (args.lower_master_ldls_id is None) != (args.upper_master_ldls_id is None):
        parser.error("--lower-master-ldls-id and --upper-master-ldls-id must be supplied together")
    runtime = RuntimeProfile()
    with runtime.measure("data/evidence setup"):
        service = ArtifactService(db_path)
        if explicit_pair:
            evidence, selection, assembly_image = load_ldls_evidence_pair(
                service,
                parse_zipcode_key(args.lower_zipcode),
                parse_zipcode_key(args.upper_zipcode),
                exposure_ids=args.ldls_exposure_ids,
                lower_ldls_artifact_id=args.lower_master_ldls_id,
                upper_ldls_artifact_id=args.upper_master_ldls_id,
            )
        else:
            evidence, selection, assembly_image = load_ldls_evidence(
                service, parse_zipcode_key(args.zipcode), exposure_ids=args.ldls_exposure_ids,
                lower_ldls_artifact_id=args.lower_master_ldls_id,
                upper_ldls_artifact_id=args.upper_master_ldls_id,
            )
    _write_assembled_master_ldls_diagnostic(args.output_dir, assembly_image, selection)
    print(f"artifact DB path: {selection['artifact_db']}")
    print(f"lower ZipCode: {selection['lower_zipcode']}")
    print(f"upper ZipCode: {selection['upper_zipcode']}")
    for label in ("lower_master_ldls", "upper_master_ldls", "lower_trace_map", "upper_trace_map"):
        product = selection[label]
        print(
            f"{label} artifact ID: {product['id']}; "
            f"calibration_group_id: {product['calibration_group_id']}; "
            f"computation identity: {product['computation_identity']}; "
            f"shape: {product['array_shape']}; "
            f"source exposure IDs: {product['source_exposure_ids']}"
        )
    print(f"assembled physical-CCD shape: {selection['assembled_physical_ccd_shape']}")
    reference_W = np.full(evidence.base_trace.shape, args.initial_W)
    reference_f = np.full(evidence.base_trace.shape, args.initial_f_sigma)
    initial_field_checksum = None
    if args.initial_field:
        initial = np.load(args.initial_field)
        reference_W, reference_f = np.asarray(initial["W"], float), np.asarray(initial["f_sigma"], float)
        if reference_W.shape != evidence.base_trace.shape or reference_f.shape != evidence.base_trace.shape:
            raise ValueError("--initial-field W/f_sigma must match the selected physical CCD trace shape")
        initial_field_checksum = {
            "W": _array_checksum(reference_W),
            "f_sigma": _array_checksum(reference_f),
        }
    with runtime.measure("geometry/cache construction"):
        geometry = build_ldls_geometry(evidence, support=args.support, trace_degree=args.trace_degree, response_degree=args.response_degree, amplifier_boundary=float(UPPER_AMPLIFIER_Y_OFFSET), reference_W=reference_W, reference_f_sigma=reference_f)
    with runtime.measure("sampling construction"):
        sampling = build_ldls_sampling(evidence, geometry, mode=args.sampling, stride=args.sparse_stride)
    cache = ProfileCache(args.profile_cache_quantization)
    with runtime.measure("response/trace scientific compute path"):
        state, closure, trace_convergence, history = develop_ldls_profile_and_trace(
            evidence, geometry, sampling, cache=cache,
        )
    illumination_experiment = None
    if args.experimental_aperture_alpha is not None:
        if not (-0.5 <= args.experimental_aperture_alpha <= 1.0):
            raise ValueError("--experimental-aperture-alpha must lie in [-0.5, 1]")
        with runtime.measure("experimental fixed aperture-illumination diagnostic"):
            log.info("experimental fixed aperture-illumination diagnostic")
            experimental = evaluate_state(
                evidence, geometry, sampling, state, cache=cache, derivatives=True,
                experimental_aperture_alpha=args.experimental_aperture_alpha,
                experimental_alpha_derivative=True,
            )
            illumination_experiment = _write_aperture_illumination_comparison(
                args.output_dir, experimental, args.experimental_aperture_alpha,
            )
            illumination_experiment.update({
                "fixed_alpha": float(args.experimental_aperture_alpha),
                "alpha_fitted": False,
                "robust_loss_with_fixed_experimental_alpha": experimental.robust_loss,
            })
    started = perf_counter()
    with runtime.measure("experimental detector-coordinate displacement"):
        log.info("experimental detector-coordinate displacement")
        displacement = run_detector_displacement_experiment(evidence, geometry, sampling, state, cache=cache)
    log.info("experimental detector-coordinate displacement: %.3f s", perf_counter() - started)
    settings = {
        "trace_degree": args.trace_degree,
        "response_degree": args.response_degree,
        "compact_support": args.support,
        "trace_margin": geometry.trace_margin,
        "sampling_mode": sampling.mode,
        "sample_count": int(sampling.sample_indices.size),
        "profile_cache_quantization": args.profile_cache_quantization,
        "finite_difference_W": 0.006,
        "finite_difference_f_sigma": 0.006,
        "aperture_illumination_model": "uniform",
        "alpha": 0.0,
        "alpha_fitted": False,
        "experimental_radial_illumination": {
            "available_only_by_explicit_opt_in": True,
            "law": "I(r; alpha) proportional to 1 - alpha (r/R)^2",
            "fixed_alpha_range": [-0.5, 1.0],
            "finite_difference_alpha": 0.01,
            "normalized_transform": "[4(1-alpha) J1(z)/z + 8 alpha J2(z)/z^2] / (2-alpha)",
        },
        "response_ridge": 1e-5,
        "trace_ridge": 1e-5,
        "huber_tuning": 1.5,
        "damping_sequence": (0.5 ** np.arange(8)).tolist(),
        "first_response_max_iterations": 3,
        "trace_max_iterations": 2,
        "second_response_max_iterations": 3,
        "five_pixel_aperture_width": 5.0,
        "initial_W": args.initial_W,
        "initial_f_sigma": args.initial_f_sigma,
        "initial_field": None if args.initial_field is None else str(args.initial_field),
        "initial_field_checksums": initial_field_checksum,
    }
    with runtime.measure("diagnostics/state persistence"):
        displacement_report = _write_detector_displacement_experiment(
            args.output_dir / "detector_displacement_experiment", evidence, geometry, sampling, displacement,
        )
        _write_diagnostics(
            args.output_dir, evidence, geometry, sampling, closure, trace_convergence, displacement,
            profile_cache_quantization=args.profile_cache_quantization,
        )
        _write_state_and_provenance(
            args.output_dir, state, geometry, closure, trace_convergence,
            selection, settings, history, runtime, displacement_report, illumination_experiment,
        )
    print(f"forward LDLS development completed: {args.output_dir}")
    print(f"final robust loss: {closure.robust_loss:.6g}; alpha: 0; state generation: {state.generation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
