from __future__ import annotations

"""Header pointing, TAN projection, and catalog astrometric refinement."""

from typing import Mapping

import numpy as np
from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.wcs import WCS

from ..core.algo_result import AlgoResult


ASTROMETRY_VERSION = "header-tan-shift-rotation-1.0"


def parse_header_pointing(header: Mapping) -> AlgoResult:
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
    return AlgoResult(
        kind="header_pointing",
        version=ASTROMETRY_VERSION,
        meta={"evidence": evidence},
        scalars={"ra0": float(ra), "dec0": float(dec), "pa": pa},
    )


def tan_fiber_coordinates(ra0: float, dec0: float, pa: float, focal_x, focal_y, *, system_rotation: float = 1.55) -> AlgoResult:
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
    ra = np.asarray(world[0], dtype=float)
    dec = np.asarray(world[1], dtype=float)
    return AlgoResult(
        kind="tan_fiber_coordinates",
        version=ASTROMETRY_VERSION,
        arrays={"ra": ra, "dec": dec},
        scalars={"rotation": rotation},
    )


def sky_to_focal_plane(ra0: float, dec0: float, pa: float, ra, dec, *, system_rotation: float = 1.55) -> AlgoResult:
    """Invert :func:`tan_fiber_coordinates`: sky RA/Dec to instrument focal-plane arcsec.

    Used only for an explicit, fixed-sky-target source-position override; the
    production coupling path otherwise works entirely in the focal-plane frame
    already carried by fiber ``focal_plane_coordinates``.
    """

    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [0.0, 0.0]
    wcs.wcs.crval = [float(ra0), float(dec0)]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cdelt = [-1.0 / 3600.0, 1.0 / 3600.0]
    rotation = 360.0 - (90.0 + float(pa) + float(system_rotation))
    theta = np.deg2rad(rotation)
    wcs.wcs.pc = [[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]]
    pixel = wcs.wcs_world2pix(np.asarray(ra, dtype=float), np.asarray(dec, dtype=float), 1)
    focal_y = np.asarray(pixel[0], dtype=float)
    focal_x = np.asarray(pixel[1], dtype=float)
    return AlgoResult(
        kind="focal_plane_coordinates_from_sky",
        version=ASTROMETRY_VERSION,
        arrays={"focal_x": focal_x, "focal_y": focal_y},
        scalars={"rotation": rotation},
    )


def detect_fiber_sources(fiber_flux, ifu_index, focal_x, focal_y, *, threshold_sigma: float = 3.0):
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


def fit_catalog_astrometry(detections, initial_ra, initial_dec, catalog, *, minimum_matches: int = 4) -> AlgoResult:
    """Match detections and fit a small tangent-plane translation/rotation."""

    detected = np.asarray(detections, dtype=float)
    cat = np.asarray(catalog, dtype=float)
    if detected.size == 0 or cat.size == 0:
        return AlgoResult(
            kind="catalog_astrometry_fit",
            version=ASTROMETRY_VERSION,
            arrays={"matches": np.empty((0, 9), dtype=float), "parameters": np.array([0.0, 0.0, 0.0])},
            scalars={"success": False},
        )
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
    return AlgoResult(
        kind="catalog_astrometry_fit",
        version=ASTROMETRY_VERSION,
        arrays={"matches": table, "parameters": parameters},
        scalars={"success": bool(success)},
    )
