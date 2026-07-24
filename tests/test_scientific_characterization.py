from __future__ import annotations

import numpy as np
import pytest

from virusflow.algorithms.bias import step_bias
from virusflow.algorithms.ccd import orient_amplifier_image, reduce_amplifier_array
from virusflow.algorithms.fiber import get_spectra
from virusflow.algorithms.dark import detect_dark_current_outliers
from virusflow.algorithms.flat import detect_flat_response_outliers
from virusflow.algorithms.trace import _get_trace, fit_fiber_traces
from virusflow.algorithms.wave import (
    REFERENCE_ARC_WAVELENGTHS,
    _match_with_model,
    _sigma_clip_matches,
    fit_single_fiber_wavelength_solution,
    fit_wavelength_solution,
)
from virusflow.config import ConfigurationService
from virusflow.core.algo_result import AlgoResult
from virusflow.core.identity import ZipCode


@pytest.mark.parametrize(
    ("amp", "ampname", "expected"),
    [
        ("LL", None, [[0, 1, 2], [3, 4, 5]]),
        ("LU", None, [[5, 4, 3], [2, 1, 0]]),
        ("RL", None, [[5, 4, 3], [2, 1, 0]]),
        ("RU", None, [[0, 1, 2], [3, 4, 5]]),
        ("LL", "LR", [[2, 1, 0], [5, 4, 3]]),
        ("LL", "UL", [[2, 1, 0], [5, 4, 3]]),
    ],
)
def test_legacy_amplifier_orientation_is_frozen(amp, ampname, expected):
    image = np.arange(6).reshape(2, 3)
    np.testing.assert_array_equal(orient_amplifier_image(image, amp, ampname), expected)


def test_detector_reduction_preserves_overscan_gain_error_and_variance_evidence():
    raw = np.full((3, 1064), 110.0)
    raw[:, :1032] = np.array([10.0, 20.0, 30.0])[:, None] + 110.0
    result = reduce_amplifier_array(
        raw,
        {"CCDPOS": "L", "CCDHALF": "L", "GAIN": 2.0, "RDNOISE": 3.0},
    )
    assert result.scalars["overscan_columns"] == 32
    np.testing.assert_allclose(result.get_array("overscan_model"), 110.0)
    expected = np.repeat(np.array([20.0, 40.0, 60.0])[:, None], 1032, axis=1)
    np.testing.assert_allclose(result.get_array("oriented_detector_image"), expected)
    np.testing.assert_allclose(result.get_array("detector_variance"), expected + 9.0, rtol=2e-6)
    np.testing.assert_allclose(result.get_array("detector_error") ** 2, expected + 9.0, rtol=2e-6)


def test_bias_scatter_is_exact_legacy_mad_scale_and_file_inputs_are_rejected():
    frames = [np.zeros((2, 3)), np.ones((2, 3)), np.full((2, 3), 2.0)]
    result = step_bias(frames)
    np.testing.assert_allclose(result.get_array("master"), 1.0)
    np.testing.assert_allclose(result.get_array("per_pixel_bias_scatter"), 1.4826)
    assert result.scalars["read_noise"] == pytest.approx(1.4826)
    with pytest.raises(TypeError, match="Tasks must load raw files"):
        step_bias([{"path": "legacy.fits"}])


def test_sum_aperture_has_fractional_edges_and_exact_total_weight():
    # A row ramp exposes both the fractional edge weights and lack of averaging.
    image = np.repeat(np.arange(12, dtype=float)[:, None], 2, axis=1)
    trace = np.full((1, 2), 5.25)
    extracted = get_spectra(image, trace, npix=5)
    # Weighted rows: 2(.25), 3, 4, 5, 6, 7(.75); total weight is exactly 5.
    expected = 0.25 * 2 + 3 + 4 + 5 + 6 + 0.75 * 7
    np.testing.assert_allclose(extracted, expected)
    np.testing.assert_allclose(get_spectra(np.ones_like(image), trace, npix=5), 5.0)


def test_sum_aperture_skips_a_trace_that_reaches_detector_edge():
    image = np.ones((12, 2))
    trace = np.array([[2.0, 2.0], [5.0, 5.0], [9.0, 9.0]])
    extracted = get_spectra(image, trace, npix=5)
    np.testing.assert_array_equal(extracted[0], 0.0)
    np.testing.assert_array_equal(extracted[1], 5.0)
    np.testing.assert_array_equal(extracted[2], 0.0)


def test_dark_and_ldls_mask_thresholds_and_full_column_heuristics():
    dark = np.zeros((20, 40))
    dark[5:7, 20] = 500.0
    dark_mask = detect_dark_current_outliers(dark)
    assert np.all(dark_mask[:, 20] == 1)

    exact = np.full((20, 40), 400.0)
    exact[10, 20] = 440.0  # exactly 10%, and the rule is strictly greater than 10%
    assert detect_flat_response_outliers(exact)[10, 20] == 0
    exact[10, 20] = 441.0
    assert detect_flat_response_outliers(exact)[10, 20] == 1

    column = np.full((302, 40), 400.0)
    column[:301, 20] = 500.0
    flat_mask = detect_flat_response_outliers(column)
    assert np.all(flat_mask[:, 20] == 1)
    assert np.all(flat_mask[:, :8] == 0) and np.all(flat_mask[:, -8:] == 0)


def test_trace_reference_resolves_nearest_date_and_records_version(tmp_path):
    zipcode = ZipCode("060", "003", "206", "LL", "controller-unknown")
    for date, value in (("20200101", 1.0), ("20260601", 2.0)):
        folder = tmp_path / "Fiber_Locations" / date
        folder.mkdir(parents=True)
        np.savetxt(folder / "fiber_loc_206_060_003_LL.txt", np.array([[value, 0.0], [value + 1, 0.0]]))
    reference, config = ConfigurationService(tmp_path).resolve_trace_reference(
        zipcode=zipcode, at="20260609"
    )
    assert reference[0, 0] == 2.0
    assert config.version == "20260601"
    assert config.evidence_state == "verified"


def test_trace_hardware_exception_and_sample_residual_outputs(monkeypatch):
    image = np.zeros((20, 80))
    image[4:7] = np.array([2.0, 10.0, 2.0])[:, None]
    image[11:14] = np.array([2.0, 10.0, 2.0])[:, None]
    reference = np.array([[5.0, 0.0], [12.0, 0.0]])
    trace, ref, columns, samples = _get_trace(image, "504", "060", "018", "RU", reference)
    assert trace.shape == (1, 80) and ref.shape == (1, 2) and samples.shape == (1, 40)
    assert columns.shape == (40,)

    def deterministic_trace(*args, **kwargs):
        model = np.repeat(np.array([[5.0], [12.0]]), 8, axis=1)
        return model, reference, np.array([1.0, 4.0, 7.0]), np.array([[5.0, 5.2, 4.8], [12.0, 12.1, 11.9]])

    import virusflow.algorithms.trace as trace_module

    monkeypatch.setattr(trace_module, "_get_trace", deterministic_trace)
    result = fit_fiber_traces(
        master_ldls_array=np.ones((20, 8)),
        trace_reference=reference,
        zipcode=ZipCode("060", "018", "504", "RU", "unknown"),
    )
    assert result.get_array("trace_sample_columns").tolist() == [1.0, 4.0, 7.0]
    assert np.all(np.isfinite(result.get_array("per_fiber_trace_residual_rms")))
    legacy_shape = fit_fiber_traces(
        [],
        {
            "master_flat_array": np.ones((20, 8)),
            "trace_reference": reference,
            "ifuslot": "060", "ifuid": "018", "specid": "504", "amp": "RU",
        },
    )
    assert legacy_shape.get_array("fiber_trace_map").shape == (2, 8)
    with pytest.raises(TypeError, match="legacy path parameters are not accepted"):
        fit_fiber_traces([], {"master_flat_path": "legacy.fits"})


def test_arc_matching_rejection_recovery_and_residual_evidence(monkeypatch):
    coeff = np.array([2.0, 3500.0])
    peaks = np.array([55.0, 75.0, 900.0])
    matches = _match_with_model(peaks, [3610.0, 3650.0, 5000.0], coeff, x_tol=1.0)
    assert len(matches) == 2  # the unmatched 5000-A line is rejected by tolerance
    with_outlier = matches + [{**matches[0], "wave_resid": 100.0}]
    assert len(_sigma_clip_matches(with_outlier, nsig=2.0)) == 2

    spectrum = np.zeros(1032)
    line_pixels = ((np.asarray(REFERENCE_ARC_WAVELENGTHS) - 3500.0) / 2.0).astype(int)
    spectrum[line_pixels] = 100.0
    spectrum[np.clip(line_pixels - 1, 0, 1031)] = 30.0
    spectrum[np.clip(line_pixels + 1, 0, 1031)] = 30.0
    _, rms, best = fit_single_fiber_wavelength_solution(spectrum)
    assert best["nmatch"] == len(REFERENCE_ARC_WAVELENGTHS)
    assert rms < 1.0

    def fake_map(*args, **kwargs):
        best = {"matches": [{"x_obs": 10, "wave_ref": 4000, "wave_fit": 4000.1, "wave_resid": 0.1, "x_resid": 0.05, "peak_index": 2}], "nmatch": 1, "rms": 0.1}
        return np.full((2, 8), 4000.0), None, np.array([0.1, 0.2]), {"best": best}

    import virusflow.algorithms.wave as wave_module

    monkeypatch.setattr(wave_module, "fit_amplifier_wavelength_map", fake_map)
    result = fit_wavelength_solution(
        comparison_lamp_fiber_spectra=np.ones((2, 8)), fiber_trace_map=np.ones((2, 8))
    )
    assert result.get_array("per_fiber_wavelength_residual_rms").tolist() == [0.1, 0.2]
    assert result.get_array("arc_identification").shape == (1, 6)


def test_wave_failure_preserves_scientific_reason_before_publication():
    from virusflow.tasks.calibs import WaveTask

    trace = np.broadcast_to(np.arange(8, dtype=float)[:, None], (8, 16))
    result = fit_wavelength_solution(
        comparison_lamp_fiber_spectra=np.zeros_like(trace), fiber_trace_map=trace,
    )

    assert result.get_array("wavelength_map") is None
    assert result.meta["failure_reason"] == (
        "comparison-lamp extraction contains no finite nonzero signal"
    )
    with pytest.raises(RuntimeError, match=result.meta["failure_reason"]):
        WaveTask._require_wavelength_map(result)
