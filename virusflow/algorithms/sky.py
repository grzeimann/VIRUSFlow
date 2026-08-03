from __future__ import annotations

"""Native-grid oversampled latent sky model and sky-fiber selection."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.algo_result import AlgoResult


SKY_VERSION = "native-grid-oversampled-1.0"


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
        wavelength = np.asarray(self.wavelength, dtype=np.float32)
        density = np.asarray(self.flux_density, dtype=np.float32)
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
    def _integral(grid: np.ndarray, density: np.ndarray, edges: np.ndarray) -> np.ndarray:
        increments = 0.5 * (density[1:] + density[:-1]) * np.diff(grid)
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
            output[fiber_index] = self._integral(self.wavelength, density, edges[fiber_index])
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
    broadband = np.nanmedian(spec, axis=1)
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


def oversampled_incident_sky(
    wavelength,
    spectrum,
    sky_mask,
    *,
    oversample: int | None = None,
    minimum_lsf_fwhm: float | None = None,
    target_samples_per_fwhm: float = 6.0,
) -> AlgoResult:
    """Combine native wavelength samples into a sigma-clipped oversampled sky."""

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
    selected_edges = native_edges[selected]
    grid_start = float(np.nanmin(selected_edges))
    grid_stop = float(np.nanmax(selected_edges))
    grid = np.arange(grid_start, grid_stop + step / 2.0, step)
    total = np.zeros(grid.size, dtype=float)
    total2 = np.zeros(grid.size, dtype=float)
    count = np.zeros(grid.size, dtype=np.int64)
    for w, widths, s in zip(wave[selected], native_width[selected], spec[selected]):
        valid = np.isfinite(w) & np.isfinite(s)
        if not valid.any():
            continue
        index = np.clip(np.rint((w[valid] - grid[0]) / step).astype(int), 0, grid.size - 1)
        density = s[valid] / widths[valid]
        total += np.bincount(index, weights=density, minlength=grid.size)
        total2 += np.bincount(index, weights=np.square(density), minlength=grid.size)
        count += np.bincount(index, minlength=grid.size)
    mean = np.divide(total, count, out=np.full_like(total, np.nan), where=count > 0)
    variance = np.divide(total2, count, out=np.full_like(total2, np.nan), where=count > 0) - np.square(mean)
    # Fill rare empty oversampled bins from neighboring finite samples.
    finite = np.isfinite(mean)
    if finite.sum() >= 2:
        mean[~finite] = np.interp(grid[~finite], grid[finite], mean[finite])
        variance[~finite] = np.interp(grid[~finite], grid[finite], variance[finite])
    return AlgoResult(
        kind="oversampled_incident_sky",
        version=SKY_VERSION,
        arrays={
            "wavelength": grid.astype(np.float32),
            "flux_density": mean.astype(np.float32),
            "variance_density": np.maximum(variance, 0).astype(np.float32),
            "sample_count": count.astype(np.int32),
        },
        scalars={"oversampling_factor": int(oversample), "native_step": native_step},
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
            grid, density, variance, float(target), factor,
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


def predict_and_subtract_sky(sky_model: LatentSkyModel, wavelength, spectrum, sky_mask, fiber_coefficients) -> AlgoResult:
    """Evaluate the latent sky through native bins and subtract it from the spectra."""

    edges = wavelength_bin_edges(wavelength)
    sky_prediction = sky_model.evaluate(edges, coefficients=fiber_coefficients)
    spec = np.asarray(spectrum, dtype=float)
    sky_subtracted = spec - sky_prediction
    residual = sky_subtracted[np.asarray(sky_mask, dtype=bool)]
    residual_sigma = float(1.4826 * np.nanmedian(np.abs(residual - np.nanmedian(residual))))
    return AlgoResult(
        kind="sky_subtraction",
        version=SKY_VERSION,
        arrays={"sky_prediction": sky_prediction.astype(np.float32), "sky_subtracted": sky_subtracted.astype(np.float32)},
        scalars={"residual_robust_sigma": residual_sigma},
    )
