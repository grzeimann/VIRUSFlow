from __future__ import annotations

"""Native-grid oversampled latent sky model and sky-fiber selection."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.algo_result import AlgoResult
from .robust import chunked_biweight_location
from .utils.masks import build_model_spectra


SKY_VERSION = "native-grid-overlap-conserving-2.0"


def wavelength_bin_edges(wavelength) -> np.ndarray:
    """Infer native bin boundaries from one or more monotonic center grids."""

    centers = np.asarray(wavelength, dtype=float)
    one_dimensional = centers.ndim == 1
    if one_dimensional:
        centers = centers[None, :]
    if centers.ndim != 2 or centers.shape[1] < 2:
        raise ValueError("wavelength centers must have at least two samples per fiber")
    middle = 0.5 * (centers[:, :-1] + centers[:, 1:])
    edges = np.empty((centers.shape[0], centers.shape[1] + 1), dtype=float)
    edges[:, 1:-1] = middle
    edges[:, 0] = centers[:, 0] - (middle[:, 0] - centers[:, 0])
    edges[:, -1] = centers[:, -1] + (centers[:, -1] - middle[:, -1])
    if np.any(np.diff(edges, axis=1) <= 0):
        raise ValueError("wavelength coordinates must be strictly increasing")
    return edges[0] if one_dimensional else edges


def derive_sky_oversampling_factor(
    minimum_lsf_fwhm: float,
    representative_native_pixel_width: float,
    *,
    target_samples_per_fwhm: float = 6.0,
) -> tuple[int, float]:
    if minimum_lsf_fwhm <= 0 or representative_native_pixel_width <= 0:
        raise ValueError("LSF FWHM and native pixel width must be positive")
    if target_samples_per_fwhm <= 0:
        raise ValueError("sky sampling target must be positive")
    native_samples = float(minimum_lsf_fwhm) / float(representative_native_pixel_width)
    return max(1, int(np.ceil(float(target_samples_per_fwhm) / native_samples))), native_samples


@dataclass(frozen=True)
class LatentSkyModel:
    """Continuous latent flux-density model evaluated through native pixel bins."""

    wavelength: np.ndarray
    flux_density: np.ndarray
    variance_density: Optional[np.ndarray] = None
    sampling_target: float = 6.0
    oversampling_factor: int = 1
    reference_resolution: str = "intrinsic_without_accepted_lsf"
    integration_method: str = "piecewise_linear_bin_integral"

    def __post_init__(self) -> None:
        # The oversampled grid is used as an integration boundary.  Keep its
        # precision through serialization so a narrow line is not shifted by
        # float32 rounding at a 3500--5500 Angstrom coordinate offset.
        wavelength = np.asarray(self.wavelength, dtype=np.float64)
        density = np.asarray(self.flux_density, dtype=np.float64)
        if wavelength.ndim != 1 or wavelength.size < 2 or density.shape != wavelength.shape:
            raise ValueError("latent wavelength and flux density must be matched 1D arrays")
        if np.any(np.diff(wavelength) <= 0):
            raise ValueError("latent wavelength must increase strictly")
        object.__setattr__(self, "wavelength", wavelength)
        object.__setattr__(self, "flux_density", density)
        if self.variance_density is not None:
            variance = np.asarray(self.variance_density, dtype=np.float32)
            if variance.shape != wavelength.shape:
                raise ValueError("latent variance density must match wavelength")
            object.__setattr__(self, "variance_density", variance)

    @staticmethod
    def _integral(
        grid: np.ndarray,
        density: np.ndarray,
        edges: np.ndarray,
        *,
        method: str = "piecewise_linear_bin_integral",
    ) -> np.ndarray:
        if method == "piecewise_constant_bin_integral":
            increments = density[:-1] * np.diff(grid)
        elif method == "piecewise_linear_bin_integral":
            increments = 0.5 * (density[1:] + density[:-1]) * np.diff(grid)
        else:
            raise ValueError(f"unsupported sky integration method {method!r}")
        cumulative = np.r_[0.0, np.cumsum(increments, dtype=np.float64)]
        values = np.interp(edges, grid, cumulative, left=np.nan, right=np.nan)
        return np.diff(values)

    def evaluate(self, wavelength_bin_edges, *, lsf_model=None, fiber_metadata=None, coefficients=None) -> np.ndarray:
        edges = np.asarray(wavelength_bin_edges, dtype=float)
        one_dimensional = edges.ndim == 1
        if one_dimensional:
            edges = edges[None, :]
        if edges.ndim != 2 or edges.shape[1] < 2:
            raise ValueError("wavelength_bin_edges must be one- or two-dimensional")
        output = np.empty((edges.shape[0], edges.shape[1] - 1), dtype=np.float32)
        for fiber_index in range(edges.shape[0]):
            density = self.flux_density
            if lsf_model is not None:
                if hasattr(lsf_model, "convolve"):
                    try:
                        density = lsf_model.convolve(
                            self.wavelength, density, fiber_index=fiber_index,
                            fiber_metadata=fiber_metadata,
                        )
                    except TypeError:
                        density = lsf_model.convolve(self.wavelength, density)
                elif callable(lsf_model):
                    density = lsf_model(self.wavelength, density, fiber_index)
                else:
                    raise TypeError("lsf_model must be callable or expose convolve")
                density = np.asarray(density, dtype=float)
            output[fiber_index] = self._integral(
                self.wavelength,
                density,
                edges[fiber_index],
                method=self.integration_method,
            )
        if coefficients is not None:
            factors = np.asarray(coefficients, dtype=np.float32)
            if factors.shape != (output.shape[0],):
                raise ValueError("one sky coefficient is required per fiber")
            output *= factors[:, None]
        return output[0] if one_dimensional else output


def select_sky_fibers(spectrum, valid_fraction, *, upper_sigma: float = 2.5, source_mask=None) -> AlgoResult:
    """Select explicit blank-sky fibers from quality and robust broadband flux."""

    spec = np.asarray(spectrum, dtype=float)
    valid = np.asarray(valid_fraction, dtype=float)
    broadband = chunked_biweight_location(spec.T, axis=0)
    good = np.nanmedian(valid, axis=1) >= 0.8
    if source_mask is not None:
        good &= ~np.asarray(source_mask, dtype=bool)
    center = float(np.nanmedian(broadband[good])) if good.any() else float("nan")
    sigma = float(1.4826 * np.nanmedian(np.abs(broadband[good] - center))) if good.any() else float("nan")
    sky = good & np.isfinite(broadband) & (broadband <= center + upper_sigma * max(sigma, np.finfo(float).eps))
    return AlgoResult(
        kind="sky_fiber_selection",
        version=SKY_VERSION,
        arrays={"mask": sky.astype(np.uint8), "broadband_flux": broadband.astype(np.float32)},
        scalars={"center": center, "sigma": sigma},
    )


def _common_spectrum(
    spectrum: np.ndarray,
    wavelength: np.ndarray,
    good_solutions: np.ndarray,
    *,
    nbins: int,
) -> np.ndarray:
    """Fit a common spectrum without changing already-normalized fiber levels."""

    interpolator, _, _, _ = build_model_spectra(
        spectrum,
        wavelength,
        good_solutions,
        nbins=int(nbins),
        normalize_per_fiber=False,
    )
    return np.asarray(interpolator(wavelength), dtype=float)


def oversampled_incident_sky(
    wavelength,
    spectrum,
    sky_mask,
    *,
    oversample: int | None = None,
    minimum_lsf_fwhm: float | None = None,
    target_samples_per_fwhm: float = 6.0,
) -> AlgoResult:
    """Fit the already-normalized sky fibers on an oversampled wavelength grid."""

    wave = np.asarray(wavelength, dtype=float)
    spec = np.asarray(spectrum, dtype=float)
    selected = np.asarray(sky_mask, dtype=bool)
    if wave.shape != spec.shape or wave.ndim != 2 or selected.shape != (wave.shape[0],):
        raise ValueError("wavelength, spectra, and sky mask have incompatible shapes")
    finite_wave = wave[selected][np.isfinite(wave[selected])]
    if finite_wave.size == 0:
        raise ValueError("no finite sky-fiber wavelength samples")
    native_edges = wavelength_bin_edges(wave)
    native_width = np.diff(native_edges, axis=1)
    native_step = float(np.nanmedian(native_width[selected]))
    if oversample is None:
        assumed_fwhm = float(minimum_lsf_fwhm or (2.0 * native_step))
        oversample, _ = derive_sky_oversampling_factor(
            assumed_fwhm, native_step,
            target_samples_per_fwhm=target_samples_per_fwhm,
        )
    step = native_step / max(1, int(oversample))
    selected_wave = wave[selected]
    selected_density = spec[selected] / native_width[selected]
    selected_edges = native_edges[selected]
    grid_start = float(np.nanmin(selected_edges))
    grid_stop = float(np.nanmax(selected_edges))
    grid = grid_start + step * np.arange(
        int(np.ceil((grid_stop - grid_start) / step)) + 1,
        dtype=float,
    )
    if grid[-1] < grid_stop:
        grid = np.append(grid, grid_stop)
    common = _common_spectrum(
        selected_density,
        selected_wave,
        np.ones(selected_density.shape[0], dtype=bool),
        nbins=max(2, grid.size),
    )
    model_valid = np.isfinite(selected_wave) & np.isfinite(common)
    model_order = np.argsort(selected_wave[model_valid])
    mean = np.interp(
        grid,
        selected_wave[model_valid][model_order],
        common[model_valid][model_order],
    )
    flat_wave = selected_wave[np.isfinite(selected_wave) & np.isfinite(selected_density)]
    index = np.clip(np.searchsorted(grid, flat_wave), 0, grid.size - 1)
    sample_count = np.bincount(index, minlength=grid.size).astype(np.int32)
    variance = np.zeros(grid.size, dtype=float)
    return AlgoResult(
        kind="oversampled_incident_sky",
        version=SKY_VERSION,
        arrays={
            "wavelength": grid.astype(np.float64),
            "flux_density": mean,
            "variance_density": np.maximum(variance, 0).astype(np.float32),
            "sample_count": sample_count,
        },
        scalars={
            "oversampling_factor": int(oversample),
            "native_step": native_step,
            "integration_method": "piecewise_linear_bin_integral",
        },
    )


def predict_sky(wavelength, sky_wavelength, incident_sky):
    model = LatentSkyModel(sky_wavelength, incident_sky)
    return model.evaluate(wavelength_bin_edges(wavelength))


def sky_sampling_convergence(
    wavelength,
    spectrum,
    sky_mask,
    *,
    minimum_lsf_fwhm: float,
    candidate_samples_per_fwhm=(4.0, 6.0, 8.0),
    lsf_model=None,
) -> dict:
    """Compare native-bin predictions, which is the scientifically relevant grid test."""

    wave = np.asarray(wavelength, dtype=float)
    widths = np.diff(wavelength_bin_edges(wave), axis=1)
    representative_width = float(np.nanmedian(widths))
    predictions = []
    rows = []
    edges = wavelength_bin_edges(wave)
    for target in candidate_samples_per_fwhm:
        factor, native_samples = derive_sky_oversampling_factor(
            minimum_lsf_fwhm, representative_width,
            target_samples_per_fwhm=float(target),
        )
        sky_result = oversampled_incident_sky(
            wave, spectrum, sky_mask, oversample=factor,
            minimum_lsf_fwhm=minimum_lsf_fwhm,
            target_samples_per_fwhm=float(target),
        )
        grid = sky_result.get_array("wavelength")
        density = sky_result.get_array("flux_density")
        variance = sky_result.get_array("variance_density")
        prediction = LatentSkyModel(
            grid,
            density,
            variance,
            float(target),
            factor,
            integration_method=str(sky_result.scalars["integration_method"]),
        ).evaluate(edges, lsf_model=lsf_model)
        delta = float("nan") if not predictions else float(np.nanmax(np.abs(prediction - predictions[-1])))
        rows.append(
            {
                "target_samples_per_fwhm": float(target),
                "oversampling_factor": int(factor),
                "native_samples_per_fwhm": float(native_samples),
                "maximum_native_prediction_change": delta,
                "latent_sample_count": int(grid.size),
            }
        )
        predictions.append(prediction)
    return {"candidates": rows, "predictions": predictions}


def predict_and_subtract_sky(
    sky_model: LatentSkyModel,
    wavelength,
    spectrum,
    sky_mask,
    fiber_coefficients,
    *,
    measured_spectrum=None,
    normalization=None,
) -> AlgoResult:
    """Project a normalized sky model into detector space before subtraction.

    The latent model is fit from response-normalized spectra.  When the
    corresponding measured spectra and normalizations are supplied, evaluate
    the sky model there, multiply it by each fiber response, subtract in the
    measured space, and only then return the normalized residual.  This keeps
    response interpolation errors from being silently folded into the sky
    model or its diagnostics.
    """

    edges = wavelength_bin_edges(wavelength)
    sky_prediction = sky_model.evaluate(edges, coefficients=fiber_coefficients)
    spec = np.asarray(spectrum, dtype=float)
    if measured_spectrum is None and normalization is None:
        measured_prediction = sky_prediction
        sky_subtracted = spec - sky_prediction
    elif measured_spectrum is None or normalization is None:
        raise ValueError("measured_spectrum and normalization must be supplied together")
    else:
        measured = np.asarray(measured_spectrum, dtype=float)
        response = np.asarray(normalization, dtype=float)
        if measured.shape != spec.shape or response.shape != spec.shape:
            raise ValueError("measured_spectrum and normalization must match spectrum")
        measured_prediction = sky_prediction * response
        sky_subtracted = np.full(spec.shape, np.nan, dtype=float)
        usable = np.isfinite(response) & (response != 0.0)
        sky_subtracted[usable] = (
            measured[usable] - measured_prediction[usable]
        ) / response[usable]
    residual = sky_subtracted[np.asarray(sky_mask, dtype=bool)]
    residual_sigma = float(1.4826 * np.nanmedian(np.abs(residual - np.nanmedian(residual))))
    return AlgoResult(
        kind="sky_subtraction",
        version=SKY_VERSION,
        arrays={
            "sky_prediction": sky_prediction.astype(np.float32),
            "measured_sky_prediction": measured_prediction.astype(np.float32),
            "sky_subtracted": sky_subtracted.astype(np.float32),
        },
        scalars={"residual_robust_sigma": residual_sigma},
    )
