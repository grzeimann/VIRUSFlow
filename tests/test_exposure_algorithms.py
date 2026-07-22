from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np

from virusflow.algorithms.exposure import (
    amplifier_normalization,
    classify_mode_and_effective_time,
    extract_fractional_aperture,
    fit_catalog_astrometry,
    fractional_aperture_geometry,
    oversampled_incident_sky,
    select_sky_fibers,
    tan_fiber_coordinates,
    within_amplifier_normalization,
)
from virusflow.io.catalogs import PanSTARRSCSVProvider
from virusflow.algorithms.fiber import get_spectra as legacy_get_spectra


def test_fractional_sum_extraction_and_exact_variance_use_identical_actual_weights():
    image = np.arange(12 * 4, dtype=float).reshape(12, 4)
    variance = np.full_like(image, 4.0)
    trace = np.array([[5.2, 5.5, 5.8, 6.1]])
    mask = np.zeros_like(image, dtype=bool)
    mask[5, 1] = True
    result = extract_fractional_aperture(image, variance, trace, pixel_mask=mask, width=5.0)
    rows, weights, valid = fractional_aperture_geometry(trace, 12, width=5.0)
    columns = np.broadcast_to(np.arange(4)[None, :, None], rows.shape)
    actual = weights.copy()
    actual[mask[np.clip(rows, 0, 11), columns]] = 0.0
    expected_flux = np.sum(actual * image[np.clip(rows, 0, 11), columns], axis=-1)
    expected_variance = np.sum(np.square(actual) * variance[np.clip(rows, 0, 11), columns], axis=-1)
    np.testing.assert_allclose(result.spectrum, expected_flux)
    np.testing.assert_allclose(result.variance, expected_variance)
    np.testing.assert_allclose(result.fractional_weights, actual)
    np.testing.assert_allclose(weights.sum(axis=-1)[valid], 5.0)
    assert result.valid_pixel_fraction[0, 1] < 1.0


def test_extraction_rejects_edge_aperture_without_silent_zero():
    result = extract_fractional_aperture(
        np.ones((8, 3)), np.ones((8, 3)), np.array([[1.0, 4.0, 7.0]]), width=5.0
    )
    assert np.isnan(result.spectrum[0, 0])
    assert np.isfinite(result.spectrum[0, 1])
    assert np.isnan(result.spectrum[0, 2])
    assert result.extraction_valid.tolist() == [[0, 1, 0]]


def test_fractional_extraction_characterizes_legacy_parity_and_intentional_edge_difference():
    image = np.arange(20 * 7, dtype=float).reshape(20, 7)
    trace = np.asarray([[8.2, 8.5, 8.8, 9.1, 9.4, 9.7, 10.0]])
    legacy = legacy_get_spectra(image, trace, npix=5)
    current = extract_fractional_aperture(image, np.ones_like(image), trace, width=5.0)
    np.testing.assert_allclose(current.spectrum, legacy, rtol=0, atol=1e-5)

    edge_trace = trace.copy()
    edge_trace[0, 0] = 1.0
    legacy_edge = legacy_get_spectra(image, edge_trace, npix=5)
    current_edge = extract_fractional_aperture(image, np.ones_like(image), edge_trace, width=5.0)
    assert np.all(legacy_edge == 0.0)  # legacy drops the entire fiber when one column hits an edge
    assert np.isnan(current_edge.spectrum[0, 0])
    assert np.all(np.isfinite(current_edge.spectrum[0, 1:]))


def test_normalization_stays_decomposed_and_multiplies_exactly():
    x = np.linspace(1.0, 2.0, 101)
    twilight = np.vstack((x, 2 * x, 4 * x))
    raw, within, valid, common = within_amplifier_normalization(twilight, smooth_pixels=11)
    assert raw.shape == within.shape == valid.shape == twilight.shape
    amp_factor, reference = amplifier_normalization([10.0, 20.0, 40.0])
    np.testing.assert_allclose(amp_factor, [0.5, 1.0, 2.0])
    assert reference == 20.0
    final = within * amp_factor[1]
    np.testing.assert_allclose(final, within)
    assert common.shape == x.shape


def test_header_tan_projection_and_effective_time_policy():
    ra, dec, rotation = tan_fiber_coordinates(200.0, 30.0, 180.0, [0.0], [0.0])
    assert abs(ra[0] - 200.0) < 1e-3
    assert abs(dec[0] - 30.0) < 1e-3
    assert np.isclose(rotation, 88.45)
    mode, effective, evidence = classify_mode_and_effective_time(
        {"OBJECT": "science", "EXPTIME": 67.4, "PEXPTIME": 75.5}
    )
    assert (mode, effective, evidence["source"]) == ("primary", 67.4, "EXPTIME")
    mode, effective, evidence = classify_mode_and_effective_time(
        {"OBJECT": "parallel", "EXPTIME": 2007.5, "PEXPTIME": 2000.0}
    )
    assert (mode, effective, evidence["source"]) == ("parallel", 1992.0, "PEXPTIME_minus_offset")


def test_catalog_fit_retains_candidates_rejections_and_recovers_shift():
    focal = np.array([[-10, -10], [10, -10], [-10, 10], [10, 10], [30, 30]], dtype=float)
    ra0, dec0 = 200.0, 30.0
    ra, dec, _ = tan_fiber_coordinates(ra0, dec0, 180.0, focal[:, 0], focal[:, 1])
    detections = np.column_stack((np.arange(5), np.zeros(5), focal, np.ones(5), np.ones(5) * 10, ra, dec))
    catalog = np.column_stack((ra + 0.5 / np.cos(np.deg2rad(dec0)) / 3600.0, dec - 0.25 / 3600.0, np.full(5, 18.0)))
    table, parameters, success = fit_catalog_astrometry(detections, ra0, dec0, catalog)
    assert success
    assert table.shape == (5, 9)
    assert table[:, 6].sum() == 5
    np.testing.assert_allclose(parameters[:2], [0.5, -0.25], atol=0.02)


def test_sky_selection_and_native_grid_oversampling():
    wave = np.tile(np.linspace(3500, 3510, 8), (5, 1))
    spectrum = np.tile(np.linspace(10, 12, 8), (5, 1))
    spectrum[-1] += 100
    valid = np.ones_like(spectrum)
    mask, broadband, center, sigma = select_sky_fibers(spectrum, valid)
    assert mask.sum() == 4
    grid, sky, variance, count = oversampled_incident_sky(wave, spectrum, mask, oversample=2)
    assert grid.size > wave.shape[1]
    assert np.all(np.isfinite(sky))
    assert np.all(variance >= 0)
    assert count.max() > 0


def test_panstarrs_csv_provider_contract_and_sentinel_handling(monkeypatch):
    csv = "objID,raMean,decMean,gPSFMag,gPSFMagErr,qualityFlag\n1,200.0,30.0,18.2,-999.0,0\n"
    response = SimpleNamespace(text=csv, raise_for_status=lambda: None)
    fake_requests = SimpleNamespace(get=lambda *args, **kwargs: response)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    table = PanSTARRSCSVProvider().cone_search(200.0, 30.0, 0.15)
    assert len(table) == 1
    assert "gMeanPSFMag" in table.colnames
    assert "gPSFMag" not in table.colnames
    assert np.isnan(table["gMeanPSFMagErr"][0])
