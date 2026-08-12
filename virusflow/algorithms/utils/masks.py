from __future__ import annotations

import warnings
import numpy as np
from astropy.stats import biweight_location, mad_std
from scipy.interpolate import interp1d
from scipy.ndimage import gaussian_filter1d

from ..robust import chunked_biweight_location

def interpolate_masked_detector_pixels(image: np.ndarray, mask: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """Fill masked pixels in an IFU arc image using row-wise interpolation and Gaussian smoothing.

    - Operates along spectral columns (x) within each detector row (y).
    - For rows with masked values, do a nearest-edge linear interpolation between valid
      pixels, then blend masked spans with a Gaussian-filtered version to avoid sharp edges.
    - Ensures no NaNs in the result; uses nearest/median fallbacks when needed.
    """
    img = np.asarray(image, dtype=float)
    m = np.asarray(mask).astype(bool)
    out = img.copy()
    ny, nx = out.shape
    # Process each detector row independently
    for y in range(ny):
        row = out[y, :]
        mrow = m[y, :]
        if not mrow.any():
            continue
        # valid points are finite and unmasked
        good = (~mrow) & np.isfinite(row)
        if good.sum() >= 2:
            x = np.arange(nx)
            # Nearest extrapolation behavior from np.interp is fine for edges
            interp_vals = np.interp(x, x[good], row[good])
            # Blend masked spans with a lightly smoothed version to reduce discontinuities
            smooth = gaussian_filter1d(interp_vals, sigma=max(0.5, float(sigma)), mode='nearest')
            filled = row.copy()
            filled[mrow] = smooth[mrow]
            out[y, :] = filled
        elif good.sum() == 1:
            v = float(row[good][0]) if np.isfinite(row[good][0]) else 0.0
            filled = row.copy()
            filled[mrow] = v
            # light smooth to avoid flat plateaus at transitions
            out[y, :] = gaussian_filter1d(filled, sigma=max(0.5, float(sigma)), mode='nearest')
        else:
            # No good samples in this row: use row median (fallback) then smooth
            med = float(np.nanmedian(row)) if np.isfinite(row).any() else 0.0
            filled = np.full_like(row, med)
            out[y, :] = gaussian_filter1d(filled, sigma=max(0.5, float(sigma)), mode='nearest')
    # Ensure no NaNs
    bad = ~np.isfinite(out)
    if bad.any():
        out[bad] = 0.0
    return out

# Algorithms here remain storage-neutral; tasks use ArtifactService to load and
# persist the detector and spectral products they operate on.


def coarse_self_normalization(
    spectrum: np.ndarray,
    wavelength: np.ndarray,
    *,
    nbins: int = 32,
) -> np.ndarray:
    """Estimate a smooth relative fiber response in coarse wavelength bins.

    Each fiber is compared with the cross-fiber median in broad wavelength
    intervals.  Interpolating those ratios removes slowly varying throughput
    while retaining narrow spectral defects for the residual mask.
    """

    flux = np.asarray(spectrum, dtype=float)
    wave = np.asarray(wavelength, dtype=float)
    if flux.ndim != 2 or wave.shape != flux.shape:
        raise ValueError("spectrum and wavelength must be matching 2D arrays")
    if int(nbins) < 2:
        raise ValueError("coarse self-normalization requires at least two bins")
    finite_wave = wave[np.isfinite(wave)]
    if finite_wave.size < 2 or np.nanmax(finite_wave) <= np.nanmin(finite_wave):
        raise ValueError("wavelength map has no finite span for normalization")

    edges = np.linspace(float(np.nanmin(finite_wave)), float(np.nanmax(finite_wave)), int(nbins) + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    levels = np.full((flux.shape[0], int(nbins)), np.nan, dtype=float)
    for index in range(int(nbins)):
        selected = (
            np.isfinite(wave)
            & np.isfinite(flux)
            & (wave >= edges[index])
            & (wave < edges[index + 1] if index + 1 < int(nbins) else wave <= edges[index + 1])
        )
        for fiber in range(flux.shape[0]):
            values = flux[fiber, selected[fiber]]
            if values.size:
                levels[fiber, index] = np.nanmedian(values)

    common = np.nanmedian(levels, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = levels / common[None, :]
    normalization = np.full(flux.shape, np.nan, dtype=float)
    for fiber in range(flux.shape[0]):
        good = np.isfinite(centers) & np.isfinite(ratios[fiber]) & (ratios[fiber] > 0.0)
        if np.count_nonzero(good) >= 2:
            normalization[fiber] = np.interp(
                wave[fiber], centers[good], ratios[fiber, good],
                left=ratios[fiber, good][0], right=ratios[fiber, good][-1],
            )
        elif np.count_nonzero(good) == 1:
            normalization[fiber] = ratios[fiber, good][0]
    return normalization


def build_model_spectra(
    spectra: np.ndarray,
    wavelength: np.ndarray,
    good_solutions: np.ndarray,
    *,
    nbins: int = 2000,
):
    """Build the archival robust common spectrum on a wavelength grid.

    Returns the interpolation callable and the same diagnostic arrays as the
    archival helper so the numerical operation remains independently testable.
    """

    image = np.asarray(spectra, dtype=float).copy()
    wave = np.asarray(wavelength, dtype=float)
    rows = np.asarray(good_solutions, dtype=bool)
    if image.ndim != 2 or wave.shape != image.shape:
        raise ValueError("spectra and wavelength must be matching 2D arrays")
    if rows.shape != (image.shape[0],):
        raise ValueError("good_solutions must contain one value per fiber")
    if int(nbins) < 2:
        raise ValueError("spectral model requires at least two bins")

    finite = rows[:, None] & np.isfinite(wave) & np.isfinite(image)
    norm_per_fiber = chunked_biweight_location(
        np.where(finite, image, np.nan).T, axis=0
    )
    good_norm = rows & np.isfinite(norm_per_fiber) & (norm_per_fiber > 0)
    finite &= good_norm[:, None]
    sample_wave = wave[finite]
    sample_flux = (image / norm_per_fiber[:, None])[finite]

    if sample_wave.size < 4 or np.nanmax(sample_wave) <= np.nanmin(sample_wave):
        raise ValueError("insufficient finite samples to build spectral model")

    edges = np.linspace(float(np.nanmin(sample_wave)), float(np.nanmax(sample_wave)), int(nbins) + 1)
    indices = np.clip(np.searchsorted(edges, sample_wave, side="right") - 1, 0, int(nbins) - 1)
    model_wave = np.full(int(nbins), np.nan, dtype=float)
    model_flux = np.full(int(nbins), np.nan, dtype=float)
    for index in np.unique(indices):
        selected = indices == index
        model_wave[index] = np.nanmean(sample_wave[selected])
        try:
            model_flux[index] = float(biweight_location(sample_flux[selected], ignore_nan=True))
        except (TypeError, ValueError):
            model_flux[index] = np.nanmedian(sample_flux[selected])

    finite_bins = np.isfinite(model_wave) & np.isfinite(model_flux)
    x, unique = np.unique(model_wave[finite_bins], return_index=True)
    y = model_flux[finite_bins][unique]
    if x.size < 2:
        raise ValueError("spectral model has fewer than two populated wavelength bins")
    interpolator = interp1d(
        x,
        y,
        bounds_error=False,
        fill_value=np.nan,
        kind="quadratic" if x.size >= 3 else "linear",
        assume_sorted=True,
    )
    model = np.asarray(interpolator(wave), dtype=float)
    modeled_image = image.copy()
    modeled_image[~rows] = 0.0
    residual = modeled_image - model
    return interpolator, model, modeled_image, residual


def make_spectral_mask(
    spectrum: np.ndarray,
    model_spectrum: np.ndarray,
    *,
    amp_rows: int = 112,
    very_bad_thresh: float = 10.0,
) -> np.ndarray:
    """Apply the archival residual-standardization spectral-mask heuristic."""

    observed = np.asarray(spectrum, dtype=float)
    model = np.asarray(model_spectrum, dtype=float)
    if observed.ndim != 2 or model.shape != observed.shape:
        raise ValueError("spectrum and model_spectrum must be matching 2D arrays")
    residual = observed - model
    good_rows = np.isfinite(model).sum(axis=1) > 0.5 * model.shape[1]
    if np.any(good_rows):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            row_scale = mad_std(residual[good_rows], ignore_nan=True, axis=1)
        valid_row_scale = np.isfinite(row_scale) & (row_scale > 0.0)
        fallback = np.nanmedian(row_scale[valid_row_scale]) if np.any(valid_row_scale) else 1.0
        row_scale[~valid_row_scale] = fallback
        row_normalized = residual[good_rows] / row_scale[:, None]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            column_scale = mad_std(row_normalized, ignore_nan=True, axis=0)
        valid_column_scale = np.isfinite(column_scale) & (column_scale > 0.0)
        fallback = (
            np.nanmedian(column_scale[valid_column_scale])
            if np.any(valid_column_scale) else 1.0
        )
        column_scale[~valid_column_scale] = fallback
        residual[good_rows] /= row_scale[:, None] * column_scale[None, :]

    residual = np.clip(residual, -50.0, 50.0)
    residual[np.abs(residual) > float(very_bad_thresh)] = 0.0
    residual[~np.isfinite(residual)] = 0.0
    for start in range(0, residual.shape[0], int(amp_rows)):
        block = residual[start:start + int(amp_rows)]
        block[(block == 0.0).sum(axis=1) > 200, :] = 0.0
        block[:, (block == 0.0).sum(axis=0) > 30] = 0.0
        count = np.sum(block != 0.0, axis=0)
        column_outlier = np.abs(np.sum(block, axis=0)) > 5.0 * np.sqrt(count)
        block[:, column_outlier] = 0.0
    return (residual == 0.0).astype(np.uint8)
