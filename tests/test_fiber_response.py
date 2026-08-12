from __future__ import annotations

import numpy as np

from virusflow.algorithms.fiber_response import fit_exposure_fiber_response
from virusflow.algorithms.calibration_detector import correct_response_calibration_frames
from virusflow.algorithms.extraction import extract_fractional_aperture
from virusflow.algorithms.master_spectrum import extract_master_spectrum


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


def test_exposure_response_keeps_three_components_and_reconstructs_total():
    ldls, twilight, _, wavelength, _ = _calibration_spectra()
    second_ldls = ldls * 1.03
    second_twilight = twilight * 1.7 * (1.0 + 0.10 * np.sin(np.arange(twilight.shape[1]) / 31.0))
    result = fit_exposure_fiber_response(
        [ldls, second_ldls], [twilight, second_twilight], [wavelength, wavelength],
        common_model_bins=500,
        broad_ldls_bins=5,
        twilight_residual_bins=25,
    )
    fitted = result.get_array("normalization")
    np.testing.assert_allclose(
        fitted,
        result.get_array("within_amplifier_response")
        * result.get_array("amplifier_response").repeat(24, axis=0)
        * np.repeat(result.get_array("amplifier_scalar"), 24)[:, None],
        rtol=2e-6,
        atol=2e-6,
    )
    np.testing.assert_allclose(np.nanmedian(result.get_array("within_amplifier_response")[:24], axis=0), 1.0, rtol=2e-5)
    np.testing.assert_allclose(np.nanmedian(result.get_array("within_amplifier_response")[24:], axis=0), 1.0, rtol=2e-5)
    assert np.ptp(result.get_array("amplifier_response")[1]) > 0.05
    np.testing.assert_allclose(result.get_array("amplifier_scalar"), [1 / 1.35, 1.7 / 1.35], rtol=0.05)
    assert result.meta["fine_structure_source"] == "master_ldls"
    assert result.meta["large_scale_anchor"] == "master_twilight"
    assert result.scalars["valid_fraction"] == 1.0
    assert result.meta["amplifier_response_representation"] == "linear"


def test_master_science_is_validation_only_and_does_not_change_response():
    ldls, twilight, science, wavelength, _ = _calibration_spectra()
    without_science = fit_exposure_fiber_response(
        [ldls], [twilight], [wavelength], common_model_bins=500
    )
    with_science = fit_exposure_fiber_response(
        [ldls], [twilight], [wavelength], science_spectrum=science, common_model_bins=500
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
        fit_exposure_fiber_response([ldls[:, :-1]], [twilight], [wavelength])


def test_response_calibration_detector_correction_matches_science_convention():
    images = np.asarray([np.full((2, 3), 150.0), np.full((2, 3), 180.0)])
    variance = np.full_like(images, 4.0)
    bias = np.full((2, 3), 10.0)
    dark = np.full((2, 3), 14.0)
    bias_scatter = np.full((2, 3), 2.0)
    dark_mask = np.zeros((2, 3), dtype=np.uint8)
    dark_mask[0, 1] = 1

    result = correct_response_calibration_frames(
        images,
        variance,
        [20.0, 40.0],
        master_bias=bias,
        master_bias_scatter=bias_scatter,
        master_dark=dark,
        dark_pixel_mask=dark_mask,
        dark_reference_exposure_time=20.0,
        dark_bias_convention="included_in_electron_master",
    )

    np.testing.assert_allclose(result.get_array("corrected_images")[0], 136.0)
    np.testing.assert_allclose(result.get_array("corrected_images")[1], 162.0)
    np.testing.assert_allclose(result.get_array("corrected_variances"), 8.0)
    assert np.all(result.get_array("pixel_masks")[:, 0, 1] == 1)
    np.testing.assert_allclose(result.get_array("dark_scales"), [1.0, 2.0])


def test_master_spectrum_retains_compact_exact_aperture_evidence():
    image = np.arange(48, dtype=float).reshape(8, 6)
    trace = np.asarray([[3.2, 3.4, 3.6, 3.8, 4.0, 4.2]])
    pixel_mask = np.zeros_like(image, dtype=np.uint8)
    pixel_mask[2, 1] = 1
    retained = extract_master_spectrum(
        image, trace, result_kind="extracted_master_ldls_spectrum",
        pixel_mask=pixel_mask,
    )
    direct = extract_fractional_aperture(
        image, np.zeros_like(image), trace, pixel_mask=pixel_mask, width=5.0,
    )

    direct_weights = direct.get_array("fractional_weights")
    bits = retained.get_array("aperture_sample_mask_bits")
    reconstructed = np.zeros_like(direct_weights)
    for index in range(direct_weights.shape[-1]):
        reconstructed[..., index] = (bits & (1 << index)) != 0
    reconstructed[..., 0] *= retained.get_array("aperture_first_weight")
    reconstructed[..., -1] *= retained.get_array("aperture_last_weight")

    np.testing.assert_array_equal(
        retained.get_array("aperture_start_row"),
        direct.get_array("aperture_start_row"),
    )
    np.testing.assert_allclose(reconstructed, direct_weights)
