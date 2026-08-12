from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits

from virusflow.algorithms import dar as dar_algo
from virusflow.algorithms import source_extraction, spatial_psf
from virusflow.algorithms.ccd import orient_amplifier_image
from virusflow.algorithms.astrometry import tan_fiber_coordinates
from virusflow.artifacts import ArtifactService, Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.artifacts.storage_conventions import FLUX_SCALE
from virusflow.config import ConfigurationService
from virusflow.config.defaults import (
    DAR_SEED_CONFIGURATION,
    DITHER_POLICY,
    FIBER_GEOMETRY_CONFIGURATION,
    SOURCE_EXTRACTION_CONFIGURATION,
)
from virusflow.core.identity import ZipCode
from virusflow.io.catalogs import FixtureCatalogProvider
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.planning.targets import ExposureTarget, ObservationTarget
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.exposure import ExposureTask
from virusflow.tasks.observation import ObservationTask


# ---------------------------------------------------------------------------
# Shared physical constants, matched exactly to the production configuration
# so injected raw counts land on a known calibrated amplitude.
# ---------------------------------------------------------------------------

FIBER_RADIUS = float(FIBER_GEOMETRY_CONFIGURATION.value["fiber_radius_arcsec"])
BETA = float(SOURCE_EXTRACTION_CONFIGURATION.value["beta"])
GRID_HALF_POINTS = int(SOURCE_EXTRACTION_CONFIGURATION.value["grid_half_points"])
MAX_FIBER_DISTANCE = float(SOURCE_EXTRACTION_CONFIGURATION.value["max_fiber_distance_arcsec"])

_DAR_RESULT = dar_algo.dar_seed_model(
    source_wavelength=np.asarray(DAR_SEED_CONFIGURATION.value["source_wavelength_angstrom"]),
    source_displacement=np.asarray(DAR_SEED_CONFIGURATION.value["source_displacement_arcsec"]),
)
_DAR_COEFFICIENTS = _DAR_RESULT.get_array("cubic_coefficients")
_DAR_ANGLE_DEG = float(DAR_SEED_CONFIGURATION.value["angle_deg"])


def _dar_delta_x(wavelength):
    """Reproduce evaluate_dar_seed's delta_x exactly (it does not depend on astrometry)."""

    scalar = np.polyval(_DAR_COEFFICIENTS, np.asarray(wavelength, dtype=float))
    return float(np.cos(np.deg2rad(_DAR_ANGLE_DEG))) * scalar


def _publish(
    service, root, kind, zipcode, components, at, *,
    parents=(), metadata=None, summaries=None, exposure_id=None,
):
    request = ArtifactRequest(
        kind=kind, components={
            name: LogicalComponent(name, "array1d" if np.asarray(value).ndim == 1 else "array2d", value)
            for name, value in components.items()
        },
        scope=Scope(zipcode=zipcode, exposure_id=exposure_id),
        validity=Validity(at, at, "fixture"),
        parents=list(parents), metadata=dict(metadata or {}),
        summaries=dict(summaries or {}),
    )
    publication = DefaultPublicationService(svc=service, policy=DefaultPersistencePolicy(), base_dir=str(root))
    context = PublicationContext("fixture", "1", "fixture", "1", {}, [], {})
    return publication.publish([request], context)[0]


def _traces(nx):
    positions = np.concatenate((20 + 7 * np.arange(38), 330 + 7 * np.arange(37), 640 + 7 * np.arange(37)))
    return np.broadcast_to(positions[:, None], (112, nx)).copy()


def _hms(ra_deg):
    ra_deg = float(ra_deg) % 360.0
    hours = ra_deg / 15.0
    h = int(hours)
    m_full = (hours - h) * 60.0
    m = int(m_full)
    s = (m_full - m) * 60.0
    return f"{h:02d}:{m:02d}:{s:09.6f}"


def _dms(dec_deg):
    dec_deg = float(dec_deg)
    sign = "-" if dec_deg < 0 else "+"
    dec_abs = abs(dec_deg)
    d = int(dec_abs)
    m_full = (dec_abs - d) * 60.0
    m = int(m_full)
    s = (m_full - m) * 60.0
    return f"{sign}{d:02d}:{m:02d}:{s:08.5f}"


def _inject_moffat_counts(oriented, local_offsets, fp_xy, nx, *, true_x, true_y, true_fwhm, true_amplitude, trace_rows):
    """Add per-fiber, per-column raw counts following the true Moffat coupling.

    Because the fixture's calibration chain is an exact identity (bias/dark
    zero, all normalizations one, baseline response one, no gray factors),
    and the fractional-aperture weight at the exact trace row is 1.0, a raw
    count added at ``trace_rows[i]`` survives to the final calibrated flux
    unchanged (up to FLUX_SCALE). This lets the fixture predict the exact
    recovered amplitude.
    """

    wavelength_cols = np.linspace(3500, 5500, nx)
    delta_x_cols = _dar_delta_x(wavelength_cols)
    fiber_x = fp_xy[0] + local_offsets[:, 0]
    fiber_y = fp_xy[1] + local_offsets[:, 1]
    for j in range(nx):
        coupling = spatial_psf.integrate_moffat_over_apertures(
            fiber_x, fiber_y, FIBER_RADIUS,
            true_x + float(delta_x_cols[j]), true_y, true_fwhm,
            beta=BETA, grid_half_points=GRID_HALF_POINTS,
        )
        addition = (true_amplitude * coupling).astype(np.float32)
        for i in range(local_offsets.shape[0]):
            oriented[int(trace_rows[i]), j] += addition[i]


def _build_source_exposure(
    tmp_path, database, service, exposure_id, at, *,
    nx, trace, wavelength, fplane, offsets,
    ra0_deg, dec0_deg, pa_deg=180.0,
    true_focal_x, true_focal_y, true_fwhm, true_amplitude,
    beacon_fiber_indices=(5, 45, 80, 105),
):
    """Build one full raw-amplifier + calibration fixture with an injected
    Moffat point source, and run ExposureTask end to end.

    Mirrors the fixture pattern in
    tests/test_exposure_task.py::test_full_exposure_task_fixture_produces_baseline_products_and_refined_catalog_astrometry,
    with two additions: (1) a per-fiber-per-column injected Moffat source at
    a known focal-plane position, and (2) a custom identity
    ``baseline_relative_response`` (response=1, atmospheric_content=
    "absorbed_unknown") so no extinction model is required and the
    calibration chain from raw counts to calibrated flux is exact.
    """

    zipcodes = [ZipCode("060", "003", "206", amp, "S/N 0039") for amp in ("LL", "LU", "RU", "RL")]
    ra_hms = _hms(ra0_deg)
    dec_dms = _dms(dec0_deg)
    with db.connect(str(database)) as connection:
        connection.execute(
            "INSERT INTO exposures(id, when_utc, frame_type) VALUES(?,?,?)", (exposure_id, exposure_id, "sci")
        )
        connection.execute(
            "INSERT INTO exposure_details(exposure_id,airmass) VALUES(?,?)", (exposure_id, 1.0)
        )
        for zipcode in zipcodes:
            oriented = np.full((1032, nx), 20.0, dtype=np.float32)
            for y in trace[list(beacon_fiber_indices), 0].astype(int):
                oriented[y, :] += 200.0
            _inject_moffat_counts(
                oriented, offsets[zipcode.amp], fplane["060"], nx,
                true_x=true_focal_x, true_y=true_focal_y,
                true_fwhm=true_fwhm, true_amplitude=true_amplitude,
                trace_rows=trace[:, 0],
            )
            raw_science = orient_amplifier_image(oriented, zipcode.amp, "XX")
            raw = np.column_stack((raw_science, np.zeros(1032, dtype=np.float32)))
            path = tmp_path / f"{exposure_id}_{zipcode.ifuslot}{zipcode.amp}_sci.fits"
            header = fits.Header({
                "IFUSLOT": 60, "IFUID": "003", "SPECID": 206,
                "CCDPOS": zipcode.amp[0], "CCDHALF": zipcode.amp[1], "AMPNAME": "XX", "CONTID": "S/N 0039",
                "GAIN": 1.0, "RDNOISE": 2.0, "EXPTIME": 67.4, "PEXPTIME": 75.5,
                "AIRMASS": 1.0,
                "OBJECT": "SOURCE_TEST_052_E", "QOBJECT": "SOURCE_TEST",
                "QRA": ra_hms, "QDEC": dec_dms, "QPROG": "SCIENCE-1",
                "PARANGLE": pa_deg, "OBSID": 6,
                "DATE": "2026-06-09T03:16:49.600000",
                "AMBTEMP": 12.5, "HUMIDITY": 44.0, "PRESSURE": 798.2,
                "RHO_STRT": 1.1, "THE_STRT": 2.2, "PHI_STRT": 3.3,
                "X_STRT": 4.4, "Y_STRT": 5.5,
            })
            fits.PrimaryHDU(raw, header=header).writeto(path)
            connection.execute(
                "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend,amp_key) VALUES(?,?,?,?,?,?)",
                (exposure_id, "sci", str(path), None, "filesystem", zipcode.key()),
            )

    response_rows = []
    for zipcode in zipcodes:
        zero = np.zeros((1032, nx), dtype=np.float32)
        one = np.ones((1032, nx), dtype=np.float32)
        twilight = np.full((1032, nx), 100.0, dtype=np.float32)
        for y in trace[:, 0].astype(int):
            twilight[y, :] += 1000.0
        _publish(service, tmp_path, "master_bias", zipcode, {"master": zero, "per_pixel_bias_scatter": one}, at)
        _publish(
            service, tmp_path, "master_dark", zipcode,
            {"master_dark": zero, "dark_pixel_mask": zero.astype(np.uint8)}, at,
            summaries={"reference_exposure_time_seconds": 600.0, "bias_convention": "included_in_electron_master"},
        )
        _publish(service, tmp_path, "master_ldls", zipcode, {"master_ldls": twilight, "flat_response_mask": zero.astype(np.uint8)}, at)
        _publish(service, tmp_path, "master_arc", zipcode, {"master_arc": one}, at)
        _publish(service, tmp_path, "master_twilight", zipcode, {"master_twilight": twilight}, at)
        sample_columns = np.asarray([0, nx - 1], dtype=float)
        _publish(service, tmp_path, "trace_map", zipcode, {
            "fiber_trace_map": trace,
            "trace_sample_columns": sample_columns,
            "sampled_trace_positions": trace[:, [0, nx - 1]],
            "per_fiber_trace_residual_rms": np.zeros(trace.shape[0]),
            "trace_sample_valid_mask": np.ones((trace.shape[0], 2), dtype=np.uint8),
            "trace_fit_residuals": np.zeros((trace.shape[0], 2)),
            "per_fiber_valid_sample_count": np.full(trace.shape[0], 2),
            "trace_interpolated_fiber_mask": np.zeros(trace.shape[0], dtype=np.uint8),
        }, at)
        _publish(service, tmp_path, "wavelength_map", zipcode, {
            "wavelength_map": wavelength,
            "per_fiber_wavelength_residual_rms": np.zeros(trace.shape[0]),
            "arc_identification": np.asarray([[0.0, 3500.0, 3500.0, 0.0, 0.0, 0.0]]),
            "arc_candidate_evidence": np.asarray([[0.0, 0.0, 1.0, 1.0, 3500.0, 0.0, 0.0]]),
            "arc_line_evidence": np.asarray([[0.0, 0.0, 3500.0, 3500.0, 0.0, 0.0, 0.0]]),
            "seed_region_attempted_mask": np.ones(1, dtype=np.uint8),
            "seed_region_success_mask": np.ones(1, dtype=np.uint8),
            "seed_region_failure_code": np.zeros(1, dtype=np.uint8),
            "seed_fit_coefficients": np.asarray([[3500.0, 1.0]]),
            "interpolated_fiber_mask": np.zeros(trace.shape[0], dtype=np.uint8),
            "extrapolated_fiber_mask": np.zeros(trace.shape[0], dtype=np.uint8),
            "input_mask_indices": np.asarray([], dtype=np.int32),
            "input_mask_shape": np.asarray([1032, nx], dtype=np.int32),
        }, at)
    total_fibers = len(zipcodes) * trace.shape[0]
    _publish(
        service, tmp_path, "exposure_fiber_response", None,
        {
            "raw_ratio": np.ones((total_fibers, nx), dtype=np.float32),
            "normalization": np.ones((total_fibers, nx), dtype=np.float32),
            "valid_mask": np.ones((total_fibers, nx), dtype=np.uint8),
            "common_ldls": np.full((total_fibers, nx), 1000.0, dtype=np.float32),
            "common_twilight": np.full((total_fibers, nx), 1000.0, dtype=np.float32),
            "within_amplifier_response": np.ones((total_fibers, nx), dtype=np.float32),
            "amplifier_response": np.ones((len(zipcodes), nx), dtype=np.float32),
            "amplifier_scalar": np.ones(len(zipcodes), dtype=np.float32),
            "amplifier_common_response": np.ones((len(zipcodes), nx), dtype=np.float32),
            "fiber_amplifier_index": np.repeat(np.arange(len(zipcodes)), trace.shape[0]),
            "amplifier_identity": np.asarray([
                [int(item.ifuslot), int(item.ifuid), int(item.specid), index]
                for index, item in enumerate(zipcodes)
            ], dtype=np.int32),
            "wavelength": np.tile(np.linspace(3500, 5500, nx, dtype=np.float32), (total_fibers, 1)),
        },
        at,
        metadata={
            "algorithm_metadata": {
                "amplifier_keys": [item.key() for item in zipcodes],
            }
        },
        summaries={"fibers_per_amplifier": trace.shape[0]},
    )

    task = ExposureTask(
        TaskContext(str(database), str(tmp_path / "artifacts"), {}),
        target=ExposureTarget(exposure_id, at),
    )
    _publish(
        service, tmp_path, "baseline_relative_response", None,
        {
            "wavelength": np.asarray([3500.0, 5500.0]),
            "response": np.asarray([1.0, 1.0]),
            "uncertainty": np.asarray([0.0, 0.0]),
            "mask": np.zeros(2, dtype=np.uint8),
        },
        at,
        metadata={
            "derivation_method_identity": {"extraction": "fixture identity baseline"},
            "atmospheric_content": "absorbed_unknown",
            "atmospheric_separation": {"extinction_model_identity": None, "calibration_exposure_airmasses": []},
            "applicability": {
                "instrument_epoch": "fixture epoch",
                "algorithm_versions": task._baseline_application_versions(),
            },
        },
    )

    catalog = []
    for zipcode, fiber_index in zip(zipcodes, beacon_fiber_indices):
        fx, fy = fplane["060"]
        local = offsets[zipcode.amp][fiber_index]
        tan_result = tan_fiber_coordinates(ra0_deg, dec0_deg, pa_deg, [fx + local[0]], [fy + local[1]])
        catalog.append((tan_result.get_array("ra")[0], tan_result.get_array("dec")[0], 18.0))

    context = TaskContext(
        str(database), str(tmp_path / "artifacts"),
        {
            "configuration_root": str(Path.cwd()), "fplane_path": str(Path.cwd() / "fplaneall.txt"),
            "catalog_provider": FixtureCatalogProvider(catalog),
            "source_position": {"focal_x": true_focal_x, "focal_y": true_focal_y},
        },
    )
    task = ExposureTask(context, target=ExposureTarget(exposure_id, at), params={
        "source_position": {"focal_x": true_focal_x, "focal_y": true_focal_y},
    })
    result = task.run({})
    return result


def _geometry():
    config = ConfigurationService(root=Path.cwd())
    fplane, _ = config.resolve_fplane(Path.cwd() / "fplaneall.txt")
    offsets, _ = config.fiber_offsets("003")
    return fplane, offsets


# ---------------------------------------------------------------------------
# 1. Full ExposureTask.run() path: injected point source recovery.
# ---------------------------------------------------------------------------

def test_injected_point_source_recovered_through_full_exposure_task(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    service = ArtifactService(str(database))
    exposure_id = "20260609T031649.6"
    at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
    nx = 39
    trace = _traces(nx)
    wavelength = np.broadcast_to(np.linspace(3500, 5500, nx), trace.shape).copy()
    fplane, offsets = _geometry()

    ll_center = np.asarray(offsets["LL"]).mean(axis=0)
    true_focal_x = float(fplane["060"][0] + ll_center[0])
    true_focal_y = float(fplane["060"][1] + ll_center[1])
    true_fwhm = 2.5
    true_amplitude = 4000.0
    ra0 = (13 + 30 / 60 + 13.64 / 3600) * 15
    dec0 = -(8 + 34 / 60 + 29.47 / 3600)

    result = _build_source_exposure(
        tmp_path, database, service, exposure_id, at,
        nx=nx, trace=trace, wavelength=wavelength, fplane=fplane, offsets=offsets,
        ra0_deg=ra0, dec0_deg=dec0,
        true_focal_x=true_focal_x, true_focal_y=true_focal_y,
        true_fwhm=true_fwhm, true_amplitude=true_amplitude,
    )

    assert {"dar_seed_model", "chromatic_psf_model", "point_source_extraction"} <= set(result)
    extraction = service.describe(result["point_source_extraction"].id)
    # The real fiber grid has a modest fill factor (fiber_radius=0.75 arcsec
    # against a ~2.54 arcsec pitch), so captured_fraction for a source this
    # wide is well below 1; just confirm it is a sane, well-defined value.
    captured_fraction = extraction["summary"]["median_captured_fraction"]
    assert 0.15 < captured_fraction < 1.0

    # The production pipeline applies a per-amplifier, per-exposure gray
    # "illumination" correction (virusflow.algorithms.response.
    # measure_exposure_illumination) derived from the exposure's own measured
    # sky level per amp, independent of the (here, identity) calibration
    # build. Even with an exact-identity calibration chain, this fixture's
    # small, synthetic fiber grid does not give every amp a numerically
    # identical robust sky estimate, so the applied factor for the source's
    # amp is not exactly 1. That is legitimate, deterministic pipeline
    # behavior (not something under test here), so the expected amplitude
    # must be scaled by the actual measured factor for the source's amp
    # fibers, fetched from the exposure's own illumination-correction
    # artifact, rather than assumed to be 1.
    illumination_fiber_factor = service.load_component(
        result["exposure_illumination_correction"].id, "fiber_factor"
    )["data"]
    illumination_fiber_identity = service.load_component(
        result["exposure_illumination_correction"].id, "fiber_identity"
    )["data"]
    source_zipcode = ZipCode("060", "003", "206", "LL", "S/N 0039")
    ordered_keys = sorted({
        ZipCode("060", "003", "206", amp, "S/N 0039").key() for amp in ("LL", "LU", "RU", "RL")
    })
    source_amp_index = ordered_keys.index(source_zipcode.key())
    on_source_amp = illumination_fiber_identity[:, 0].astype(int) == source_amp_index
    assert on_source_amp.any()
    illumination_factor = float(np.median(illumination_fiber_factor[on_source_amp]))

    amplitude = service.load_component(result["point_source_extraction"].id, "amplitude")["data"]
    expected_amplitude = true_amplitude * FLUX_SCALE / illumination_factor
    finite = np.isfinite(amplitude)
    assert finite.mean() > 0.8
    finite_values = amplitude[finite]
    np.testing.assert_allclose(np.median(finite_values), expected_amplitude, rtol=0.2)
    close = np.isclose(finite_values, expected_amplitude, rtol=0.2)
    assert close.mean() > 0.8

    chromatic = service.describe(result["chromatic_psf_model"].id)
    assert chromatic["summary"]["status"] == "fitted"
    assert chromatic["summary"]["fitted_interval_count"] >= 3

    # Background is a per-wavelength-interval quantity fitted by the
    # wavelength-local PSF stage, not carried on the final
    # point_source_extraction artifact (that artifact only reports the
    # source amplitude/variance/mask/captured_fraction).
    psf_measurements = result["spatial_psf_measurements"]
    fwhm_values = []
    background_values = []
    centroid_x_by_wavelength = []
    for artifact in psf_measurements:
        summary = service.describe(artifact.id)["summary"]
        if not summary["valid"]:
            continue
        fwhm_values.append(service.load_component(artifact.id, "fwhm")["data"].item())
        background_values.append(service.load_component(artifact.id, "background")["data"].item())
        centroid_x_by_wavelength.append((
            service.load_component(artifact.id, "reference_wavelength")["data"].item(),
            service.load_component(artifact.id, "centroid_x")["data"].item(),
        ))
    assert len(fwhm_values) >= 3
    assert np.all(np.abs(background_values) < 0.15 * expected_amplitude)
    np.testing.assert_allclose(fwhm_values, true_fwhm, rtol=0.2)

    # DAR-offset recovery: centroid_x should track the true DAR curve shift
    # between the blue and red ends of the band.
    centroid_x_by_wavelength.sort()
    blue_wave, blue_centroid = centroid_x_by_wavelength[0]
    red_wave, red_centroid = centroid_x_by_wavelength[-1]
    expected_shift = _dar_delta_x(red_wave) - _dar_delta_x(blue_wave)
    observed_shift = red_centroid - blue_centroid
    assert abs(observed_shift - expected_shift) < 0.25


# ---------------------------------------------------------------------------
# 2 & 3. PSF extraction vs. direct aperture sum, full and partial coverage.
# ---------------------------------------------------------------------------

def _fiber_grid(spacing=2.54, half_extent=4):
    coords = np.arange(-half_extent, half_extent + 1) * spacing
    gx, gy = np.meshgrid(coords, coords, indexing="ij")
    return gx.ravel(), gy.ravel()


def test_psf_extraction_matches_aperture_sum_at_full_capture():
    fiber_x, fiber_y = _fiber_grid(half_extent=6)
    true_amplitude = 100.0
    # Very narrow relative to the fiber radius (0.75 arcsec) and centered
    # exactly on a fiber: essentially all flux lands inside that one fiber's
    # aperture, so captured_fraction is close to 1 (unlike a broad source,
    # which is limited by the fill factor of the sparse fiber grid).
    true_fwhm = 0.3
    coupling = spatial_psf.integrate_moffat_over_apertures(
        fiber_x, fiber_y, FIBER_RADIUS, 0.0, 0.0, true_fwhm, beta=BETA, grid_half_points=GRID_HALF_POINTS,
    )
    flux = (true_amplitude * coupling).astype(float)
    variance = np.ones_like(flux)

    captured_fraction = float(np.sum(coupling))
    assert captured_fraction > 0.95

    psf_result = source_extraction.extract_source_spectrum(
        flux[:, None], variance[:, None], coupling[:, None], background=False,
    )
    aperture_result = source_extraction.sum_aperture_flux(flux[:, None], variance[:, None])

    psf_amplitude = float(psf_result.get_array("amplitude")[0])
    aperture_amplitude = float(aperture_result.get_array("amplitude")[0])
    np.testing.assert_allclose(psf_amplitude, true_amplitude, rtol=1e-6)
    np.testing.assert_allclose(aperture_amplitude, psf_amplitude, rtol=0.1)


def test_aperture_sum_corrected_by_captured_fraction_matches_psf_under_partial_coverage():
    fiber_x, fiber_y = _fiber_grid(half_extent=6)
    true_amplitude = 100.0
    true_fwhm = 1.0  # narrow: much of the flux falls in the gaps between fibers
    # offset from the nearest fiber center so flux spills into the gaps
    centroid_x, centroid_y = 1.2, 0.7
    coupling = spatial_psf.integrate_moffat_over_apertures(
        fiber_x, fiber_y, FIBER_RADIUS, centroid_x, centroid_y, true_fwhm, beta=BETA, grid_half_points=GRID_HALF_POINTS,
    )
    flux = (true_amplitude * coupling).astype(float)
    variance = np.ones_like(flux)

    captured_fraction = float(np.sum(coupling))
    assert captured_fraction < 0.9  # confirms genuinely partial coverage

    psf_result = source_extraction.extract_source_spectrum(
        flux[:, None], variance[:, None], coupling[:, None], background=False,
    )
    aperture_result = source_extraction.sum_aperture_flux(flux[:, None], variance[:, None])

    psf_amplitude = float(psf_result.get_array("amplitude")[0])
    aperture_amplitude = float(aperture_result.get_array("amplitude")[0])
    corrected_aperture_amplitude = aperture_amplitude / captured_fraction
    np.testing.assert_allclose(psf_amplitude, true_amplitude, rtol=1e-6)
    np.testing.assert_allclose(corrected_aperture_amplitude, psf_amplitude, rtol=1e-6)


# ---------------------------------------------------------------------------
# 4. Masking fibers / edge placement reduces captured_fraction without
#    renormalizing the recovered amplitude away.
# ---------------------------------------------------------------------------

def test_masking_or_edge_placement_reduces_captured_fraction_but_not_amplitude():
    fiber_x, fiber_y = _fiber_grid(half_extent=6)
    true_amplitude = 100.0
    true_fwhm = 6.0
    coupling = spatial_psf.integrate_moffat_over_apertures(
        fiber_x, fiber_y, FIBER_RADIUS, 0.0, 0.0, true_fwhm, beta=BETA, grid_half_points=GRID_HALF_POINTS,
    )
    flux = (true_amplitude * coupling).astype(float)
    variance = np.ones_like(flux)

    baseline_result = source_extraction.extract_source_spectrum(
        flux[:, None], variance[:, None], coupling[:, None], background=False,
    )
    baseline_amplitude = float(baseline_result.get_array("amplitude")[0])
    baseline_captured = float(baseline_result.get_array("captured_fraction")[0])

    # Mask out the fibers closest to the source (largest coupling values),
    # leaving plenty of fibers for a well-posed solve.
    order = np.argsort(coupling)[::-1]
    fiber_mask = np.zeros(fiber_x.shape[0], dtype=bool)
    fiber_mask[order[:5]] = True

    masked_result = source_extraction.extract_source_spectrum(
        flux[:, None], variance[:, None], coupling[:, None], background=False, fiber_mask=fiber_mask,
    )
    masked_amplitude = float(masked_result.get_array("amplitude")[0])
    masked_captured = float(masked_result.get_array("captured_fraction")[0])

    assert masked_captured < baseline_captured
    np.testing.assert_allclose(masked_amplitude, true_amplitude, rtol=1e-6)
    np.testing.assert_allclose(masked_amplitude, baseline_amplitude, rtol=1e-6)

    # Placing the source near the edge of the sampled fiber footprint has the
    # same effect: captured_fraction drops but the calibrated amplitude does
    # not get renormalized away.
    edge_x, edge_y = float(fiber_x.max()), 0.0
    edge_coupling = spatial_psf.integrate_moffat_over_apertures(
        fiber_x, fiber_y, FIBER_RADIUS, edge_x, edge_y, true_fwhm, beta=BETA, grid_half_points=GRID_HALF_POINTS,
    )
    edge_flux = (true_amplitude * edge_coupling).astype(float)
    edge_result = source_extraction.extract_source_spectrum(
        edge_flux[:, None], variance[:, None], edge_coupling[:, None], background=False,
    )
    edge_amplitude = float(edge_result.get_array("amplitude")[0])
    edge_captured = float(edge_result.get_array("captured_fraction")[0])
    assert edge_captured < baseline_captured
    np.testing.assert_allclose(edge_amplitude, true_amplitude, rtol=1e-6)


# ---------------------------------------------------------------------------
# 7. point_source_extraction.variance matches the weighted-linear-solve
#    covariance diagonal exactly.
# ---------------------------------------------------------------------------

def test_extraction_variance_matches_design_matrix_solve_covariance_diagonal():
    fiber_x, fiber_y = _fiber_grid(half_extent=6)
    true_amplitude = 50.0
    true_background = 3.0
    true_fwhm = 3.0
    coupling = spatial_psf.integrate_moffat_over_apertures(
        fiber_x, fiber_y, FIBER_RADIUS, 0.3, -0.2, true_fwhm, beta=BETA, grid_half_points=GRID_HALF_POINTS,
    )
    flux = true_amplitude * coupling + true_background
    rng = np.random.default_rng(1234)
    variance = 1.0 + rng.uniform(0.0, 2.0, size=coupling.shape)

    spectrum_result = source_extraction.extract_source_spectrum(
        flux[:, None], variance[:, None], coupling[:, None], background=True,
    )
    design_matrix = np.column_stack([coupling, np.ones_like(coupling)])
    direct_result = source_extraction.solve_source_design_matrix(flux, variance, design_matrix)

    np.testing.assert_allclose(
        spectrum_result.get_array("variance")[0], direct_result.get_array("covariance")[0, 0], rtol=1e-10,
    )
    np.testing.assert_allclose(
        spectrum_result.get_array("amplitude")[0], direct_result.get_array("amplitude")[0], rtol=1e-10,
    )
    np.testing.assert_allclose(
        spectrum_result.get_array("background")[0], direct_result.get_array("amplitude")[1], rtol=1e-10,
    )


# ---------------------------------------------------------------------------
# 8. Mask propagation: excluded fibers/wavelengths are excluded from the
#    solve and reflected in the output mask.
# ---------------------------------------------------------------------------

def test_mask_propagation_excludes_fibers_from_solve_and_flags_output():
    fiber_x, fiber_y = _fiber_grid(half_extent=6)
    true_amplitude = 40.0
    true_fwhm = 3.0
    coupling = spatial_psf.integrate_moffat_over_apertures(
        fiber_x, fiber_y, FIBER_RADIUS, 0.0, 0.0, true_fwhm, beta=BETA, grid_half_points=GRID_HALF_POINTS,
    )
    n_fiber = fiber_x.shape[0]
    n_wave = 2
    flux = np.tile((true_amplitude * coupling)[:, None], (1, n_wave))
    variance = np.ones((n_fiber, n_wave))

    # Column 1 ("bad" wavelength): only leave one finite fiber, which is too
    # few for a two-column (coupling + background) design matrix solve.
    flux[:, 1] = np.nan
    variance[:, 1] = np.nan
    order_index = int(np.argmax(coupling))
    flux[order_index, 1] = true_amplitude * coupling[order_index]
    variance[order_index, 1] = 1.0

    result = source_extraction.extract_source_spectrum(
        flux, variance, np.tile(coupling[:, None], (1, n_wave)), background=True,
    )
    mask = result.get_array("mask")
    amplitude = result.get_array("amplitude")
    usable_fiber_count = result.get_array("usable_fiber_count")

    assert mask[0] == 0
    np.testing.assert_allclose(amplitude[0], true_amplitude, rtol=1e-6)
    assert mask[1] & source_extraction.INVALID_SOLVE_BIT
    assert np.isnan(amplitude[1])
    assert usable_fiber_count[1] < usable_fiber_count[0]

    # An explicit fiber_mask must also be excluded from the usable count even
    # when its flux/variance would otherwise be perfectly usable.
    fiber_mask = np.zeros(n_fiber, dtype=bool)
    fiber_mask[0] = True
    masked_result = source_extraction.extract_source_spectrum(
        flux[:, :1], variance[:, :1], coupling[:, None], background=True, fiber_mask=fiber_mask,
    )
    assert masked_result.get_array("usable_fiber_count")[0] == result.get_array("usable_fiber_count")[0] - int(
        np.isfinite(flux[0, 0])
    )


# ---------------------------------------------------------------------------
# 5. Two (of three, for a complete dither) exposures combined via
#    ObservationTask; per-exposure model artifact ids must be distinct.
# ---------------------------------------------------------------------------

def test_observation_task_combines_dithered_point_source_spectra(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    service = ArtifactService(str(database))
    nx = 39
    trace = _traces(nx)
    wavelength = np.broadcast_to(np.linspace(3500, 5500, nx), trace.shape).copy()
    fplane, offsets = _geometry()

    ll_center = np.asarray(offsets["LL"]).mean(axis=0)
    true_focal_x = float(fplane["060"][0] + ll_center[0])
    true_focal_y = float(fplane["060"][1] + ll_center[1])
    true_fwhm = 2.5
    true_amplitude = 4000.0

    ra0_base = (13 + 30 / 60 + 13.64 / 3600) * 15
    dec0_base = -(8 + 34 / 60 + 29.47 / 3600)
    nominal = np.asarray(DITHER_POLICY.nominal_pattern_arcsec, dtype=float)
    exposure_ids = ("20260609T031649.6", "20260609T031859.3", "20260609T032112.2")

    exposure_results = []
    for index, exposure_id in enumerate(exposure_ids):
        at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
        ra0 = ra0_base + nominal[index, 0] / np.cos(np.deg2rad(dec0_base)) / 3600.0
        dec0 = dec0_base + nominal[index, 1] / 3600.0
        result = _build_source_exposure(
            tmp_path, database, service, exposure_id, at,
            nx=nx, trace=trace, wavelength=wavelength, fplane=fplane, offsets=offsets,
            ra0_deg=ra0, dec0_deg=dec0,
            true_focal_x=true_focal_x, true_focal_y=true_focal_y,
            true_fwhm=true_fwhm, true_amplitude=true_amplitude,
        )
        exposure_results.append(result)

    dar_ids = {result["dar_seed_model"].id for result in exposure_results}
    chromatic_ids = {result["chromatic_psf_model"].id for result in exposure_results}
    extraction_ids = {result["point_source_extraction"].id for result in exposure_results}
    assert len(dar_ids) == len(exposure_results)
    assert len(chromatic_ids) == len(exposure_results)
    assert len(extraction_ids) == len(exposure_results)

    calibrated_states = [result["calibrated_fiber_state"] for result in exposure_results]
    assert all(state.point_source_spectrum is not None for state in calibrated_states)

    context = TaskContext(str(database), str(tmp_path / "artifacts"), {
        "configuration_root": str(Path.cwd()), "calibrated_fiber_states": calibrated_states,
    })
    target = ObservationTarget("20260609-OBSID6", "20260609-OBSID6-DITHER", exposure_ids)
    observation_result = ObservationTask(context, target=target).run({})

    assert "observation_source_spectrum" in observation_result
    spectrum = service.describe(observation_result["observation_source_spectrum"].id)
    assert spectrum["summary"]["status"] == "combined"
    assert spectrum["summary"]["exposure_count"] == 3

    amplitude = service.load_component(observation_result["observation_source_spectrum"].id, "amplitude")["data"]
    expected_amplitude = true_amplitude * FLUX_SCALE
    finite = np.isfinite(amplitude)
    assert finite.mean() > 0.8
    # A handful of edge wavelength columns (outside the fitted chromatic PSF
    # model's DAR-shifted coverage) are expected to be poorly constrained;
    # check the bulk (interior) of the recovered spectrum against the
    # injected amplitude via the median, rather than every column.
    finite_values = amplitude[finite]
    np.testing.assert_allclose(np.median(finite_values), expected_amplitude, rtol=0.2)
    close = np.isclose(finite_values, expected_amplitude, rtol=0.2)
    assert close.mean() > 0.8


# ---------------------------------------------------------------------------
# 6. Under-constrained interval ends up degraded/prior_only end to end.
# ---------------------------------------------------------------------------

def test_under_constrained_source_position_is_marked_degraded_not_silently_valid(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    service = ArtifactService(str(database))
    exposure_id = "20260609T031649.6"
    at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
    nx = 39
    trace = _traces(nx)
    wavelength = np.broadcast_to(np.linspace(3500, 5500, nx), trace.shape).copy()
    fplane, offsets = _geometry()

    # A corner position with only a handful of fibers within max_fiber_distance
    # (verified offline: 3 fibers within 6 arcsec, well under n_params+1=5).
    true_focal_x = float(fplane["060"][0] + 28.0)
    true_focal_y = float(fplane["060"][1] + 2.0)
    true_fwhm = 1.5
    true_amplitude = 1000.0
    ra0 = (13 + 30 / 60 + 13.64 / 3600) * 15
    dec0 = -(8 + 34 / 60 + 29.47 / 3600)

    fiber_x_all = []
    fiber_y_all = []
    for amp in ("LL", "LU", "RU", "RL"):
        local = np.asarray(offsets[amp])
        fiber_x_all.append(fplane["060"][0] + local[:, 0])
        fiber_y_all.append(fplane["060"][1] + local[:, 1])
    fiber_x_all = np.concatenate(fiber_x_all)
    fiber_y_all = np.concatenate(fiber_y_all)
    within_range = np.hypot(fiber_x_all - true_focal_x, fiber_y_all - true_focal_y) <= MAX_FIBER_DISTANCE
    assert 0 < int(within_range.sum()) < 5

    result = _build_source_exposure(
        tmp_path, database, service, exposure_id, at,
        nx=nx, trace=trace, wavelength=wavelength, fplane=fplane, offsets=offsets,
        ra0_deg=ra0, dec0_deg=dec0,
        true_focal_x=true_focal_x, true_focal_y=true_focal_y,
        true_fwhm=true_fwhm, true_amplitude=true_amplitude,
    )

    manifest = service.describe(result["exposure_completion_manifest"].id)
    assert manifest["summary"]["point_source_extraction_status"] == "extracted"

    chromatic = service.describe(result["chromatic_psf_model"].id)
    assert chromatic["summary"]["status"] == "prior_only"
    assert chromatic["qa"]["usability"] == "degraded"

    for artifact in result["spatial_psf_measurements"]:
        summary = service.describe(artifact.id)["summary"]
        assert summary["valid"] is False
        assert summary["status"] == "degraded"
