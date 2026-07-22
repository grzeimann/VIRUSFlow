from __future__ import annotations

import numpy as np
import pytest

from virusflow.algorithms.bias import step_bias
from virusflow.algorithms.ccd import orient_amplifier_image, reduce_amplifier_array
from virusflow.algorithms.fiber import get_spectra


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
