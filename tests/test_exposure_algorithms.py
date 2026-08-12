from __future__ import annotations

import hashlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from virusflow.algorithms.atmosphere import evaluate_atmospheric_extinction
from virusflow.algorithms.astrometry import fit_catalog_astrometry, tan_fiber_coordinates
from virusflow.algorithms.exposure_state import classify_mode_and_effective_time
from virusflow.algorithms.exposure import apply_relative_response
from virusflow.algorithms.extraction import extract_fractional_aperture, fractional_aperture_geometry
from virusflow.algorithms.response import (
    baseline_relative_response,
)
from virusflow.algorithms.fiber_response import fit_exposure_fiber_response
from virusflow.config import ConfigurationService
from virusflow.algorithms.sky import oversampled_incident_sky, select_sky_fibers
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
    np.testing.assert_allclose(result.get_array("spectrum"), expected_flux)
    np.testing.assert_allclose(result.get_array("variance"), expected_variance)
    np.testing.assert_allclose(result.get_array("fractional_weights"), actual)
    np.testing.assert_allclose(weights.sum(axis=-1)[valid], 5.0)
    assert result.get_array("valid_pixel_fraction")[0, 1] < 1.0


def test_extraction_rejects_edge_aperture_without_silent_zero():
    result = extract_fractional_aperture(
        np.ones((8, 3)), np.ones((8, 3)), np.array([[1.0, 4.0, 7.0]]), width=5.0
    )
    spectrum = result.get_array("spectrum")
    assert np.isnan(spectrum[0, 0])
    assert np.isfinite(spectrum[0, 1])
    assert np.isnan(spectrum[0, 2])
    assert result.get_array("extraction_valid").tolist() == [[0, 1, 0]]


def test_fractional_extraction_characterizes_legacy_parity_and_intentional_edge_difference():
    image = np.arange(20 * 7, dtype=float).reshape(20, 7)
    trace = np.asarray([[8.2, 8.5, 8.8, 9.1, 9.4, 9.7, 10.0]])
    legacy = legacy_get_spectra(image, trace, npix=5)
    current = extract_fractional_aperture(image, np.ones_like(image), trace, width=5.0)
    np.testing.assert_allclose(current.get_array("spectrum"), legacy, rtol=0, atol=1e-5)

    edge_trace = trace.copy()
    edge_trace[0, 0] = 1.0
    legacy_edge = legacy_get_spectra(image, edge_trace, npix=5)
    current_edge = extract_fractional_aperture(image, np.ones_like(image), edge_trace, width=5.0)
    assert np.all(legacy_edge == 0.0)  # legacy drops the entire fiber when one column hits an edge
    current_edge_spectrum = current_edge.get_array("spectrum")
    assert np.isnan(current_edge_spectrum[0, 0])
    assert np.all(np.isfinite(current_edge_spectrum[0, 1:]))


def test_normalization_stays_decomposed_and_multiplies_exactly():
    x = np.linspace(1.0, 2.0, 101)
    twilight = np.vstack((x, 2 * x, 4 * x))
    wave = np.broadcast_to(np.linspace(3500.0, 5500.0, x.size), twilight.shape)
    ldls = [
        np.vstack((x, 1.1 * x, 0.9 * x)),
        np.vstack((x, 1.1 * x, 0.9 * x)),
        np.vstack((x, 1.1 * x, 0.9 * x)),
    ]
    response = fit_exposure_fiber_response(
        ldls,
        [twilight, twilight * (1.0 + 0.2 * (x - 1.5)), twilight * 2.0],
        [wave, wave, wave],
        common_model_bins=x.size,
    )
    final = response.get_array("within_amplifier_response")
    final *= response.get_array("amplifier_response").repeat(3, axis=0)
    final *= np.repeat(response.get_array("amplifier_scalar"), 3)[:, None]
    np.testing.assert_allclose(final, response.get_array("normalization"))
    assert np.ptp(response.get_array("amplifier_response")[1]) > 0.1


def test_default_baseline_removes_mcdonald_extinction_at_construction_airmass():
    payload, reference = ConfigurationService().resolve_baseline_response()
    stored = np.loadtxt(payload["source_path"], comments="#")
    result = baseline_relative_response(
        payload["wavelength"], payload["response"], payload["uncertainty"], payload["mask"],
        version=reference.version,
    )
    extinction, _ = ConfigurationService().resolve_atmospheric_extinction()
    evaluation = evaluate_atmospheric_extinction(
        stored[:, 0],
        extinction["wavelength"],
        extinction["extinction_coefficient"],
        extinction["uncertainty"],
        extinction["mask"],
        airmass=1.22,
    )
    factor = evaluation.get_array("correction_factor")
    legacy_response = stored[:, 1] / factor
    legacy_digest = hashlib.sha256(
        ("\n".join(format(value, ".15g") for value in legacy_response) + "\n").encode()
    ).hexdigest()

    assert reference.version == "remedy-effective-response-atmosphere-separated-1.0"
    assert result.get_array("wavelength").shape == (1036,)
    assert legacy_digest == "73cd0673cca8103c6b1fcc25c7cf59b96804ab3e76fd4110a400dc3adf050d1f"
    np.testing.assert_allclose(
        stored[:, 1] / legacy_response,
        10.0 ** (0.4 * evaluation.get_array("extinction_coefficient") * 1.22),
        rtol=2e-15,
    )
    np.testing.assert_allclose(result.get_array("response"), stored[:, 1], rtol=1e-7)
    assert np.all(np.isnan(result.get_array("uncertainty")))
    assert np.all(result.get_array("mask") == 2)
    assert set(result.arrays) == {"wavelength", "response", "uncertainty", "mask"}


def test_airmass_122_atmosphere_separated_response_reproduces_legacy_remedy_result():
    baseline, _ = ConfigurationService().resolve_baseline_response()
    extinction, _ = ConfigurationService().resolve_atmospheric_extinction()
    evaluation = evaluate_atmospheric_extinction(
        baseline["wavelength"],
        extinction["wavelength"],
        extinction["extinction_coefficient"],
        extinction["uncertainty"],
        extinction["mask"],
        airmass=1.22,
    )
    correction = evaluation.get_array("correction_factor")
    legacy_response = np.asarray(baseline["response"], dtype=float) / correction
    shape = (1, legacy_response.size)
    common = {
        "sky_subtracted": np.ones(shape),
        "spectrum_variance": np.ones(shape),
        "wavelength": np.asarray(baseline["wavelength"])[None, :],
        "valid_fraction": np.ones(shape),
        "baseline_wavelength": baseline["wavelength"],
        "baseline_uncertainty": baseline["uncertainty"],
        "baseline_mask": baseline["mask"],
        "fiber_illumination": [1.0],
    }
    previous = apply_relative_response(
        **common,
        baseline_response=legacy_response,
        baseline_atmospheric_content="absorbed_unknown",
    )
    current = apply_relative_response(
        **common,
        baseline_response=baseline["response"],
        baseline_atmospheric_content="removed_with_model",
        extinction_correction=correction,
        extinction_uncertainty=evaluation.get_array("correction_uncertainty"),
        extinction_mask=evaluation.get_array("mask"),
    )
    np.testing.assert_allclose(
        current.get_array("calibrated_flux"),
        previous.get_array("calibrated_flux"),
        rtol=2e-7,
    )


def test_baseline_and_illumination_apply_once_with_response_uncertainty_variance():
    wavelength = np.asarray([[4000.0, 4100.0]])
    spectrum = np.asarray([[20.0, 40.0]])
    variance = np.asarray([[9.0, 16.0]])
    result = apply_relative_response(
        spectrum,
        variance,
        wavelength,
        np.ones_like(spectrum),
        baseline_wavelength=np.asarray([4000.0, 4100.0]),
        baseline_response=np.asarray([2.0, 4.0]),
        baseline_uncertainty=np.asarray([0.2, 0.4]),
        baseline_mask=np.zeros(2, dtype=np.uint8),
        fiber_illumination=np.asarray([0.5]),
        exposure_transparency=0.8,
    )
    np.testing.assert_allclose(result.get_array("calibrated_flux"), [[25.0, 25.0]])
    expected_statistical = variance / np.square([[0.8, 1.6]])
    expected_response_term = np.square(
        spectrum * [[0.2, 0.4]]
        / (np.asarray([[0.5, 0.5]]) * 0.8 * np.square([[2.0, 4.0]]))
    )
    np.testing.assert_allclose(result.get_array("statistical_variance"), expected_statistical)
    np.testing.assert_allclose(
        result.get_array("calibrated_variance"), expected_statistical + expected_response_term
    )
    assert result.scalars["baseline_applied_count"] == 1
    assert result.scalars["illumination_applied_count"] == 1
    assert result.scalars["transparency_measurement_present"] is True
    np.testing.assert_allclose(result.get_array("transparency_factor"), 0.8)


def test_unknown_imported_response_uncertainty_does_not_invent_variance():
    result = apply_relative_response(
        [[10.0]], [[4.0]], [[4000.0]], [[1.0]],
        baseline_wavelength=[3900.0, 4100.0],
        baseline_response=[2.0, 2.0],
        baseline_uncertainty=[np.nan, np.nan],
        baseline_mask=np.asarray([2, 2], dtype=np.uint8),
        fiber_illumination=[1.0],
    )
    np.testing.assert_allclose(result.get_array("calibrated_flux"), [[5.0]])
    np.testing.assert_allclose(result.get_array("calibrated_variance"), [[1.0]])
    assert np.isnan(result.get_array("evaluated_baseline_uncertainty")[0, 0])
    assert result.get_array("evaluated_baseline_mask")[0, 0] & 2
    assert result.get_array("mask")[0, 0] == 0


def test_mcdonald_extinction_analytic_factor_and_linear_interpolation():
    payload, reference = ConfigurationService().resolve_atmospheric_extinction()
    result = evaluate_atmospheric_extinction(
        [[4000.0, 4050.0]],
        payload["wavelength"],
        payload["extinction_coefficient"],
        payload["uncertainty"],
        payload["mask"],
        airmass=1.5,
    )
    assert reference.identity == "mcdonald-observatory-mean-extinction"
    np.testing.assert_allclose(
        result.get_array("extinction_coefficient"), [[0.374, (0.374 + 0.337) / 2.0]]
    )
    expected = 10.0 ** (0.4 * np.asarray([[0.374, 0.3555]]) * 1.5)
    np.testing.assert_allclose(result.get_array("correction_factor"), expected, rtol=1e-6)
    np.testing.assert_allclose(result.get_array("transmission"), 1.0 / expected, rtol=1e-6)
    assert np.all(np.isnan(result.get_array("correction_uncertainty")))
    assert np.all(result.get_array("mask") == 2)


def test_extinction_requires_airmass_and_handles_range_without_extrapolation():
    model_wavelength = [3400.0, 3500.0]
    coefficient = [0.68, 0.63]
    uncertainty = [0.01, 0.01]
    mask = np.zeros(2, dtype=np.uint8)
    with pytest.raises(ValueError, match="explicit exposure airmass"):
        evaluate_atmospheric_extinction(
            [3450.0], model_wavelength, coefficient, uncertainty, mask, airmass=None
        )
    masked = evaluate_atmospheric_extinction(
        [3300.0, 3450.0],
        model_wavelength,
        coefficient,
        uncertainty,
        mask,
        airmass=1.2,
        range_policy="mask",
    )
    assert np.isnan(masked.get_array("correction_factor")[0])
    assert masked.get_array("mask")[0] & 1
    assert np.isfinite(masked.get_array("correction_factor")[1])
    assert masked.scalars["outside_valid_range_count"] == 1
    with pytest.raises(ValueError, match="outside the valid"):
        evaluate_atmospheric_extinction(
            [3300.0], model_wavelength, coefficient, uncertainty, mask,
            airmass=1.2, range_policy="fail",
        )


def test_extinction_uncertainty_and_all_response_factors_remain_separate():
    extinction = evaluate_atmospheric_extinction(
        [[4000.0]],
        [3900.0, 4100.0],
        [0.2, 0.2],
        [0.01, 0.01],
        np.zeros(2, dtype=np.uint8),
        airmass=1.5,
    )
    correction = float(extinction.get_array("correction_factor")[0, 0])
    correction_sigma = float(extinction.get_array("correction_uncertainty")[0, 0])
    expected_sigma = correction * 0.4 * np.log(10.0) * 1.5 * 0.01
    assert np.isclose(correction_sigma, expected_sigma)

    applied = apply_relative_response(
        [[20.0]], [[4.0]], [[4000.0]], [[1.0]],
        baseline_wavelength=[3900.0, 4100.0],
        baseline_response=[2.0, 2.0],
        baseline_uncertainty=[0.1, 0.1],
        baseline_mask=np.zeros(2, dtype=np.uint8),
        fiber_illumination=[0.5],
        exposure_transparency=0.8,
        mirror_illumination=0.9,
        baseline_atmospheric_content="removed_with_model",
        extinction_correction=extinction.get_array("correction_factor"),
        extinction_uncertainty=extinction.get_array("correction_uncertainty"),
        extinction_mask=extinction.get_array("mask"),
    )
    denominator = 2.0 * 0.5 * 0.8 * 0.9
    below_atmosphere = 20.0 / denominator
    np.testing.assert_allclose(applied.get_array("calibrated_flux"), below_atmosphere * correction)
    np.testing.assert_allclose(applied.get_array("response_denominator"), denominator)
    np.testing.assert_allclose(applied.get_array("illumination_factor"), 0.5)
    np.testing.assert_allclose(applied.get_array("transparency_factor"), 0.8)
    np.testing.assert_allclose(applied.get_array("mirror_illumination_factor"), 0.9)
    expected_extinction_variance = (below_atmosphere * correction_sigma) ** 2
    np.testing.assert_allclose(
        applied.get_array("extinction_uncertainty_variance"), expected_extinction_variance
    )
    expected_statistical_variance = 4.0 / denominator**2 * correction**2
    expected_baseline_variance = (
        20.0 * correction * 0.1 / (0.5 * 0.8 * 0.9 * 2.0**2)
    ) ** 2
    np.testing.assert_allclose(
        applied.get_array("calibrated_variance"),
        expected_statistical_variance
        + expected_baseline_variance
        + expected_extinction_variance,
    )
    assert applied.scalars["baseline_applied_count"] == 1
    assert applied.scalars["illumination_applied_count"] == 1
    assert applied.scalars["transparency_applied_count"] == 1
    assert applied.scalars["mirror_illumination_applied_count"] == 1
    assert applied.scalars["extinction_applied_count"] == 1


def test_atmosphere_absorbing_and_separated_baseline_conventions_cannot_mix():
    common = dict(
        sky_subtracted=[[10.0]],
        spectrum_variance=[[1.0]],
        wavelength=[[4000.0]],
        valid_fraction=[[1.0]],
        baseline_wavelength=[3900.0, 4100.0],
        baseline_response=[1.0, 1.0],
        baseline_uncertainty=[np.nan, np.nan],
        baseline_mask=np.asarray([2, 2], dtype=np.uint8),
        fiber_illumination=[1.0],
    )
    with pytest.raises(ValueError, match="atmosphere-absorbing baseline"):
        apply_relative_response(
            **common,
            baseline_atmospheric_content="absorbed_unknown",
            extinction_correction=[[1.2]],
            extinction_uncertainty=[[0.01]],
            extinction_mask=[[0]],
        )
    with pytest.raises(ValueError, match="requires a separate extinction"):
        apply_relative_response(
            **common,
            baseline_atmospheric_content="removed_with_model",
        )


def test_header_tan_projection_and_effective_time_policy():
    tan_result = tan_fiber_coordinates(200.0, 30.0, 180.0, [0.0], [0.0])
    ra = tan_result.get_array("ra")
    dec = tan_result.get_array("dec")
    rotation = tan_result.scalars["rotation"]
    assert abs(ra[0] - 200.0) < 1e-3
    assert abs(dec[0] - 30.0) < 1e-3
    assert np.isclose(rotation, 88.45)
    classification = classify_mode_and_effective_time(
        {
            "OBJECT": "Target_Name_082_W", "QOBJECT": "Target_Name",
            "QRA": "13:30:00", "QDEC": "-08:30:00", "QPROG": "P001",
            "EXPTIME": 67.4, "PEXPTIME": 75.5,
        }
    )
    mode = classification.scalars["mode"]
    effective = classification.scalars["effective_seconds"]
    evidence = classification.meta["time_evidence"]
    assert (mode, effective, evidence["source"]) == ("primary", 67.4, "EXPTIME")
    assert evidence["requested_target"] == "Target_Name"
    assert evidence["requested_ifuslot"] == "082" and evidence["het_track"] == "W"
    assert evidence["virus_primary"] is True
    classification = classify_mode_and_effective_time(
        {
            "OBJECT": "parallel", "QOBJECT": "Target_Name", "QRA": "13:30:00",
            "QDEC": "-08:30:00", "QPROG": "P001", "EXPTIME": 2007.5,
            "PEXPTIME": 2000.0,
        }
    )
    mode = classification.scalars["mode"]
    effective = classification.scalars["effective_seconds"]
    evidence = classification.meta["time_evidence"]
    assert (mode, effective, evidence["source"]) == ("parallel", 1992.0, "PEXPTIME_minus_offset")
    assert evidence["requested_target"] == "Target_Name"
    assert evidence["requested_ifuslot"] is None and evidence["virus_primary"] is False


def test_catalog_fit_retains_candidates_rejections_and_recovers_shift():
    focal = np.array([[-10, -10], [10, -10], [-10, 10], [10, 10], [30, 30]], dtype=float)
    ra0, dec0 = 200.0, 30.0
    tan_result = tan_fiber_coordinates(ra0, dec0, 180.0, focal[:, 0], focal[:, 1])
    ra = tan_result.get_array("ra")
    dec = tan_result.get_array("dec")
    detections = np.column_stack((np.arange(5), np.zeros(5), focal, np.ones(5), np.ones(5) * 10, ra, dec))
    catalog = np.column_stack((ra + 0.5 / np.cos(np.deg2rad(dec0)) / 3600.0, dec - 0.25 / 3600.0, np.full(5, 18.0)))
    fit_result = fit_catalog_astrometry(detections, ra0, dec0, catalog)
    table = fit_result.get_array("matches")
    parameters = fit_result.get_array("parameters")
    success = fit_result.scalars["success"]
    assert success
    assert table.shape == (5, 9)
    assert table[:, 6].sum() == 5
    np.testing.assert_allclose(parameters[:2], [0.5, -0.25], atol=0.02)


def test_sky_selection_and_native_grid_oversampling():
    wave = np.tile(np.linspace(3500, 3510, 8), (5, 1))
    spectrum = np.tile(np.linspace(10, 12, 8), (5, 1))
    spectrum[-1] += 100
    valid = np.ones_like(spectrum)
    sky_selection = select_sky_fibers(spectrum, valid)
    mask = sky_selection.get_array("mask")
    assert mask.sum() == 4
    incident_result = oversampled_incident_sky(wave, spectrum, mask, oversample=2)
    grid = incident_result.get_array("wavelength")
    sky = incident_result.get_array("flux_density")
    variance = incident_result.get_array("variance_density")
    count = incident_result.get_array("sample_count")
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
