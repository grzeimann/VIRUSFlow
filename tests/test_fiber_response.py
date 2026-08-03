from __future__ import annotations

import numpy as np

from virusflow.algorithms.fiber_response import fit_within_amplifier_response


def _calibration_spectra():
    fiber_count, sample_count = 24, 256
    pixel = np.arange(sample_count, dtype=float)
    wavelength = np.broadcast_to(
        np.linspace(3500.0, 5500.0, sample_count),
        (fiber_count, sample_count),
    ).copy()
    fiber_coordinate = np.linspace(-1.0, 1.0, fiber_count)[:, None]
    fine = (
        1.0
        + 0.18 * fiber_coordinate
        + 0.025 * fiber_coordinate * np.sin(pixel[None, :] / 3.7)
    )
    broad = 1.0 + 0.04 * fiber_coordinate * (pixel[None, :] / pixel[-1] - 0.5)
    residual = 1.0 + 0.006 * fiber_coordinate * np.sin(pixel[None, :] / 27.0)
    expected = fine * broad * residual
    ldls_common = 2500.0 * (1.0 + 0.08 * np.cos(pixel / 80.0))
    twilight_common = 1400.0 * (1.0 + 0.12 * np.sin(pixel / 65.0))
    twilight_common[119:123] *= 0.55  # solar-like narrow structure
    ldls = fine * ldls_common[None, :]
    twilight = expected * twilight_common[None, :]
    science = expected * (900.0 + 30.0 * np.cos(pixel / 19.0))[None, :]
    return ldls, twilight, science, wavelength, expected


def test_ldls_fine_structure_is_anchored_to_twilight_large_scale_response():
    ldls, twilight, _, wavelength, expected = _calibration_spectra()
    result = fit_within_amplifier_response(
        ldls,
        twilight,
        wavelength,
        common_model_bins=500,
        broad_twilight_bins=5,
        twilight_residual_bins=25,
    )
    fitted = result.get_array("normalization")
    np.testing.assert_allclose(
        fitted,
        result.get_array("ftf_ldls")
        * result.get_array("twilight_broad_correction")
        * (1.0 + result.get_array("twilight_residual_correction")),
        rtol=2e-6,
        atol=2e-6,
    )
    fitted = fitted.copy()
    expected /= np.nanmedian(expected)
    fitted /= np.nanmedian(fitted)
    np.testing.assert_allclose(fitted, expected, rtol=0.025, atol=0.01)
    assert result.meta["fine_structure_source"] == "master_ldls"
    assert result.meta["large_scale_anchor"] == "master_twilight"
    assert result.scalars["valid_fraction"] == 1.0


def test_master_science_is_validation_only_and_does_not_change_response():
    ldls, twilight, science, wavelength, _ = _calibration_spectra()
    without_science = fit_within_amplifier_response(
        ldls, twilight, wavelength, common_model_bins=500
    )
    with_science = fit_within_amplifier_response(
        ldls, twilight, wavelength, science_spectrum=science, common_model_bins=500
    )
    np.testing.assert_array_equal(
        with_science.get_array("normalization"),
        without_science.get_array("normalization"),
    )
    assert with_science.get_array("science_residual_per_fiber").shape == (24,)
    assert with_science.meta["science_role"] == "validation_only"
    assert "science_residual_per_fiber" not in without_science.arrays


def test_fiber_response_rejects_incompatible_calibration_shapes():
    ldls, twilight, _, wavelength, _ = _calibration_spectra()
    with np.testing.assert_raises_regex(ValueError, "matching 2D arrays"):
        fit_within_amplifier_response(ldls[:, :-1], twilight, wavelength)
