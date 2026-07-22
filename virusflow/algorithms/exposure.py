from __future__ import annotations

"""Pure baseline algorithms for the atomic full-exposure scientific slice."""

from dataclasses import dataclass
from typing import Mapping, Optional

import numpy as np
from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.wcs import WCS
from scipy.ndimage import median_filter


EXTRACTION_VERSION = "fractional-sum-aperture-1.0"
NORMALIZATION_VERSION = "twilight-within-and-amplifier-1.0"
ASTROMETRY_VERSION = "header-tan-shift-rotation-1.0"
SKY_VERSION = "native-grid-oversampled-1.0"
RESPONSE_VERSION = "relative-response-factorized-1.0"


@dataclass(frozen=True)
class ExtractionResult:
    spectrum: np.ndarray
    variance: np.ndarray
    valid_pixel_fraction: np.ndarray
    effective_aperture_width: np.ndarray
    aperture_start_row: np.ndarray
    fractional_weights: np.ndarray
    extraction_valid: np.ndarray


def fractional_aperture_geometry(traces, detector_rows: int, *, width: float = 5.0):
    """Return exact detector-pixel overlaps for a continuous top-hat aperture."""

    trace = np.asarray(traces, dtype=float)
    if trace.ndim != 2 or detector_rows <= 0 or not np.isfinite(width) or width <= 0:
        raise ValueError("fractional aperture requires 2D traces, positive rows, and positive width")
    nsample = int(np.ceil(width)) + 1
    left = trace - width / 2.0
    right = trace + width / 2.0
    start = np.floor(left).astype(np.int32)
    offsets = np.arange(nsample, dtype=np.int32)
    rows = start[..., None] + offsets
    weights = np.maximum(
        0.0,
        np.minimum(rows + 1.0, right[..., None]) - np.maximum(rows, left[..., None]),
    )
    valid = np.isfinite(trace) & (left >= 0.0) & (right <= float(detector_rows))
    weights[~valid] = 0.0
    return rows, weights.astype(np.float32), valid


def extract_fractional_aperture(image, variance, traces, *, pixel_mask=None, width: float = 5.0) -> ExtractionResult:
    """Sum flux and propagate diagonal variance with the exact same weights."""

    data = np.asarray(image, dtype=float)
    var = np.asarray(variance, dtype=float)
    trace = np.asarray(traces, dtype=float)
    if data.ndim != 2 or var.shape != data.shape or trace.ndim != 2 or trace.shape[1] != data.shape[1]:
        raise ValueError("image, variance, and trace shapes are incompatible")
    mask = np.zeros(data.shape, dtype=bool) if pixel_mask is None else np.asarray(pixel_mask, dtype=bool)
    if mask.shape != data.shape:
        raise ValueError("pixel_mask must match image")
    rows, weights, aperture_valid = fractional_aperture_geometry(trace, data.shape[0], width=width)
    clipped = np.clip(rows, 0, data.shape[0] - 1)
    columns = np.broadcast_to(np.arange(data.shape[1])[None, :, None], clipped.shape)
    samples = data[clipped, columns]
    sample_variance = var[clipped, columns]
    sample_valid = (
        aperture_valid[..., None]
        & ~mask[clipped, columns]
        & np.isfinite(samples)
        & np.isfinite(sample_variance)
        & (sample_variance >= 0.0)
    )
    actual_weights = np.where(sample_valid, weights, 0.0)
    spectrum = np.sum(actual_weights * np.where(sample_valid, samples, 0.0), axis=-1)
    extracted_variance = np.sum(np.square(actual_weights) * np.where(sample_valid, sample_variance, 0.0), axis=-1)
    effective_width = np.sum(actual_weights, axis=-1)
    valid_fraction = effective_width / float(width)
    extraction_valid = aperture_valid & (effective_width > 0.0)
    spectrum = np.where(extraction_valid, spectrum, np.nan).astype(np.float32)
    extracted_variance = np.where(extraction_valid, extracted_variance, np.nan).astype(np.float32)
    return ExtractionResult(
        spectrum=spectrum,
        variance=extracted_variance,
        valid_pixel_fraction=valid_fraction.astype(np.float32),
        effective_aperture_width=effective_width.astype(np.float32),
        aperture_start_row=rows[..., 0].astype(np.int16),
        fractional_weights=actual_weights.astype(np.float32),
        extraction_valid=extraction_valid.astype(np.uint8),
    )


def within_amplifier_normalization(twilight_spectrum, *, smooth_pixels: int = 51):
    """Return raw and smoothed fiber/common-twilight response ratios."""

    twilight = np.asarray(twilight_spectrum, dtype=float)
    if twilight.ndim != 2:
        raise ValueError("twilight_spectrum must be fiber by wavelength")
    common = np.nanmedian(twilight, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_ratio = twilight / common[None, :]
    size = max(3, int(smooth_pixels))
    if size % 2 == 0:
        size += 1
    filled = raw_ratio.copy()
    row_median = np.nanmedian(filled, axis=1)
    bad = ~np.isfinite(filled)
    filled[bad] = np.broadcast_to(row_median[:, None], filled.shape)[bad]
    smooth = median_filter(filled, size=(1, size), mode="nearest")
    norm = np.nanmedian(smooth, axis=1)
    smooth = smooth / np.where(np.isfinite(norm) & (norm != 0), norm, 1.0)[:, None]
    valid = np.isfinite(raw_ratio) & np.isfinite(smooth) & (smooth > 0)
    return raw_ratio.astype(np.float32), smooth.astype(np.float32), valid.astype(np.uint8), common.astype(np.float32)


def amplifier_normalization(amplifier_twilight_levels):
    """Place amplifiers in one exposure-wide robust twilight reference frame."""

    levels = np.asarray(amplifier_twilight_levels, dtype=float)
    positive = np.isfinite(levels) & (levels > 0)
    reference = float(np.nanmedian(levels[positive])) if positive.any() else float("nan")
    factors = np.full(levels.shape, np.nan, dtype=float)
    factors[positive] = levels[positive] / reference
    return factors.astype(np.float32), reference


def parse_header_pointing(header: Mapping) -> tuple[float, float, float, dict]:
    """Resolve the initial header tangent point with retained keyword evidence."""

    def angle(value, *, hour=False):
        if value is None:
            return None
        try:
            return float(SkyCoord(str(value), "0d", unit=(u.hourangle if hour else u.deg, u.deg)).ra.deg)
        except Exception:
            try:
                return float(value) * (15.0 if hour else 1.0)
            except Exception:
                return None

    def dec_angle(value):
        if value is None:
            return None
        try:
            return float(SkyCoord("0h", str(value), unit=(u.hourangle, u.deg)).dec.deg)
        except Exception:
            try:
                return float(value)
            except Exception:
                return None

    commanded_ra = angle(header.get("TRAJCRA"), hour=True)
    commanded_dec = dec_angle(header.get("TRAJCDEC"))
    trajectory_ra = angle(header.get("TRAJRA"), hour=True)
    trajectory_dec = dec_angle(header.get("TRAJDEC"))
    qra = angle(header.get("QRA"), hour=True)
    qdec = dec_angle(header.get("QDEC"))
    ra, dec, source = commanded_ra, commanded_dec, "TRAJCRA/TRAJCDEC"
    if ra is None or dec is None:
        ra, dec, source = trajectory_ra, trajectory_dec, "TRAJRA/TRAJDEC"
    if ra is None or dec is None:
        ra, dec, source = qra, qdec, "QRA/QDEC"
    if ra is None or dec is None:
        raise ValueError("science header has no usable pointing")
    if commanded_ra is not None and trajectory_ra is not None and commanded_dec is not None and trajectory_dec is not None:
        separation = SkyCoord(commanded_ra * u.deg, commanded_dec * u.deg).separation(
            SkyCoord(trajectory_ra * u.deg, trajectory_dec * u.deg)
        ).arcsec
        if separation > 25.0:
            ra, dec, source = trajectory_ra, trajectory_dec, "TRAJRA/TRAJDEC_fallback"
    pa = float(header.get("PARANGLE") or 0.0)
    evidence = {
        "source": source,
        "TRAJCRA": header.get("TRAJCRA"), "TRAJCDEC": header.get("TRAJCDEC"),
        "TRAJRA": header.get("TRAJRA"), "TRAJDEC": header.get("TRAJDEC"),
        "QRA": header.get("QRA"), "QDEC": header.get("QDEC"), "PARANGLE": header.get("PARANGLE"),
    }
    return float(ra), float(dec), pa, evidence


def tan_fiber_coordinates(ra0: float, dec0: float, pa: float, focal_x, focal_y, *, system_rotation: float = 1.55):
    """Apply the reference TAN projection with the required focal-plane axis swap."""

    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [0.0, 0.0]
    wcs.wcs.crval = [float(ra0), float(dec0)]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cdelt = [-1.0 / 3600.0, 1.0 / 3600.0]
    rotation = 360.0 - (90.0 + float(pa) + float(system_rotation))
    theta = np.deg2rad(rotation)
    wcs.wcs.pc = [[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]]
    world = wcs.wcs_pix2world(np.asarray(focal_y, dtype=float), np.asarray(focal_x, dtype=float), 1)
    return np.asarray(world[0], dtype=float), np.asarray(world[1], dtype=float), rotation


def detect_fiber_sources(fiber_flux, ifu_index, focal_x, focal_y, *, threshold_sigma: float = 5.0):
    """Deterministic broadband source candidates retained before catalog matching."""

    flux = np.asarray(fiber_flux, dtype=float)
    ifus = np.asarray(ifu_index, dtype=int)
    rows = []
    for ifu in np.unique(ifus):
        selected = np.where((ifus == ifu) & np.isfinite(flux))[0]
        if selected.size == 0:
            continue
        values = flux[selected]
        center = float(np.nanmedian(values))
        sigma = float(1.4826 * np.nanmedian(np.abs(values - center)))
        threshold = center + float(threshold_sigma) * max(sigma, np.finfo(float).eps)
        candidates = selected[values > threshold]
        for index in candidates:
            rows.append((index, ifu, focal_x[index], focal_y[index], flux[index], (flux[index] - center) / max(sigma, np.finfo(float).eps)))
    return np.asarray(rows, dtype=float).reshape((-1, 6)) if rows else np.empty((0, 6), dtype=float)


def fit_catalog_astrometry(detections, initial_ra, initial_dec, catalog, *, minimum_matches: int = 4):
    """Match detections and fit a small tangent-plane translation/rotation."""

    detected = np.asarray(detections, dtype=float)
    cat = np.asarray(catalog, dtype=float)
    if detected.size == 0 or cat.size == 0:
        return np.empty((0, 9), dtype=float), np.array([0.0, 0.0, 0.0]), False
    detection_coord = SkyCoord(detected[:, 6] * u.deg, detected[:, 7] * u.deg)
    catalog_coord = SkyCoord(cat[:, 0] * u.deg, cat[:, 1] * u.deg)
    match_index, separation, _ = detection_coord.match_to_catalog_sky(catalog_coord)
    dra = (detected[:, 6] - cat[match_index, 0]) * np.cos(np.deg2rad(initial_dec)) * 3600.0
    ddec = (detected[:, 7] - cat[match_index, 1]) * 3600.0
    candidate = separation.arcsec < 25.0
    if cat.shape[1] > 2:
        magnitude = cat[match_index, 2]
        candidate &= np.isfinite(magnitude) & (magnitude > 15.0) & (magnitude < 22.0)
    offsets = np.column_stack((dra, ddec))
    coherent = np.zeros(candidate.shape, dtype=bool)
    candidate_indices = np.where(candidate)[0]
    if candidate_indices.size:
        candidate_offsets = offsets[candidate_indices]
        distances = np.sqrt(np.sum((candidate_offsets[:, None, :] - candidate_offsets[None, :, :]) ** 2, axis=2))
        seed = candidate_indices[int(np.argmax(np.sum(distances <= 1.5, axis=1)))]
        coherent = candidate & (np.sqrt(np.sum((offsets - offsets[seed]) ** 2, axis=1)) <= 1.5)
    success = int(coherent.sum()) >= int(minimum_matches)
    parameters = np.array([0.0, 0.0, 0.0], dtype=float)
    residual = np.full(detected.shape[0], np.nan)
    if success:
        # Small-angle rigid fit: catalog tangent offset = translation + rotation*focal.
        x = detected[coherent, 2]
        y = detected[coherent, 3]
        target_x = (cat[match_index[coherent], 0] - detected[coherent, 6]) * np.cos(np.deg2rad(initial_dec)) * 3600.0
        target_y = (cat[match_index[coherent], 1] - detected[coherent, 7]) * 3600.0
        design = np.vstack((
            np.column_stack((np.ones(x.size), np.zeros(x.size), -y)),
            np.column_stack((np.zeros(x.size), np.ones(x.size), x)),
        ))
        target = np.concatenate((target_x, target_y))
        parameters, *_ = np.linalg.lstsq(design, target, rcond=None)
        pred_x = parameters[0] - parameters[2] * x
        pred_y = parameters[1] + parameters[2] * x
        residual[coherent] = np.sqrt((target_x - pred_x) ** 2 + (target_y - pred_y) ** 2)
    table = np.column_stack((
        np.arange(detected.shape[0]), match_index, separation.arcsec,
        dra, ddec, candidate.astype(float), coherent.astype(float), residual,
        cat[match_index, 2] if cat.shape[1] > 2 else np.full(detected.shape[0], np.nan),
    ))
    return table, parameters, success


def select_sky_fibers(spectrum, valid_fraction, *, upper_sigma: float = 2.5, source_mask=None):
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
    return sky.astype(np.uint8), broadband.astype(np.float32), center, sigma


def oversampled_incident_sky(wavelength, spectrum, sky_mask, *, oversample: int = 2):
    """Combine native wavelength samples into a sigma-clipped oversampled sky."""

    wave = np.asarray(wavelength, dtype=float)
    spec = np.asarray(spectrum, dtype=float)
    selected = np.asarray(sky_mask, dtype=bool)
    if wave.shape != spec.shape or wave.ndim != 2 or selected.shape != (wave.shape[0],):
        raise ValueError("wavelength, spectra, and sky mask have incompatible shapes")
    finite_wave = wave[selected][np.isfinite(wave[selected])]
    if finite_wave.size == 0:
        raise ValueError("no finite sky-fiber wavelength samples")
    native_step = float(np.nanmedian(np.abs(np.diff(wave[selected], axis=1))))
    step = native_step / max(1, int(oversample))
    grid = np.arange(float(np.nanmin(finite_wave)), float(np.nanmax(finite_wave)) + step / 2.0, step)
    total = np.zeros(grid.size, dtype=float)
    total2 = np.zeros(grid.size, dtype=float)
    count = np.zeros(grid.size, dtype=np.int64)
    for w, s in zip(wave[selected], spec[selected]):
        valid = np.isfinite(w) & np.isfinite(s)
        if not valid.any():
            continue
        index = np.clip(np.rint((w[valid] - grid[0]) / step).astype(int), 0, grid.size - 1)
        total += np.bincount(index, weights=s[valid], minlength=grid.size)
        total2 += np.bincount(index, weights=np.square(s[valid]), minlength=grid.size)
        count += np.bincount(index, minlength=grid.size)
    mean = np.divide(total, count, out=np.full_like(total, np.nan), where=count > 0)
    variance = np.divide(total2, count, out=np.full_like(total2, np.nan), where=count > 0) - np.square(mean)
    # Fill rare empty oversampled bins from neighboring finite samples.
    finite = np.isfinite(mean)
    if finite.sum() >= 2:
        mean[~finite] = np.interp(grid[~finite], grid[finite], mean[finite])
        variance[~finite] = np.interp(grid[~finite], grid[finite], variance[finite])
    return grid.astype(np.float32), mean.astype(np.float32), np.maximum(variance, 0).astype(np.float32), count.astype(np.int32)


def predict_sky(wavelength, sky_wavelength, incident_sky):
    wave = np.asarray(wavelength, dtype=float)
    grid = np.asarray(sky_wavelength, dtype=float)
    model = np.asarray(incident_sky, dtype=float)
    output = np.empty(wave.shape, dtype=np.float32)
    for index in range(wave.shape[0]):
        output[index] = np.interp(wave[index], grid, model, left=np.nan, right=np.nan)
    return output


def classify_mode_and_effective_time(header: Mapping, *, parallel_offset_seconds: float = 8.0):
    object_label = str(header.get("OBJECT") or "").strip().lower()
    mode = "parallel" if object_label == "parallel" else "primary"
    exptime = float(header["EXPTIME"]) if header.get("EXPTIME") is not None else None
    pexptime = float(header["PEXPTIME"]) if header.get("PEXPTIME") is not None else None
    if mode == "primary":
        effective = exptime
        source = "EXPTIME"
    else:
        effective = None if pexptime is None else max(0.0, pexptime - float(parallel_offset_seconds))
        source = "PEXPTIME_minus_offset"
    return mode, effective, {"EXPTIME": exptime, "PEXPTIME": pexptime, "OBJECT": header.get("OBJECT"), "source": source}
