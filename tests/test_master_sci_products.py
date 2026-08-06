import numpy as np

from virusflow.algorithms.extraction import extract_fractional_aperture
from virusflow.algorithms.master_sci import build_master_sci
from virusflow.algorithms.master_sci_mask import build_master_sci_spectral_mask
from virusflow.algorithms.master_sci_spectrum import extract_master_sci_spectrum


def test_master_sci_is_only_the_detector_aggregate():
    frames = [
        {"data": np.full((8, 12), value, dtype=np.float32)}
        for value in (10.0, 11.0, 12.0)
    ]
    result = build_master_sci(frames)
    assert set(result.arrays) == {"master_sci"}
    assert result.arrays["master_sci"].dtype == np.float32
    assert "mask_support_semantics" not in result.meta


def test_master_sci_extraction_reuses_canonical_fractional_aperture():
    yy, xx = np.indices((24, 32))
    image = (100.0 + 3.0 * yy + xx).astype(np.float32)
    trace = np.repeat(np.array([[6.25], [14.5]]), image.shape[1], axis=1)
    expected = extract_fractional_aperture(
        image, np.zeros_like(image), trace, width=5.0
    )
    result = extract_master_sci_spectrum(image, trace, aperture_width=5.0)
    np.testing.assert_allclose(result.arrays["spectrum"], expected.get_array("spectrum"))
    np.testing.assert_array_equal(
        result.arrays["extraction_valid"], expected.get_array("extraction_valid")
    )
    assert result.meta["spectral_coordinate"] == "detector_column"
    assert result.meta["output_scale_convention"] == "integrated_aperture_counts"


def _spectral_inputs():
    fiber_count, sample_count = 112, 128
    wavelength = np.broadcast_to(
        np.linspace(3500.0, 5500.0, sample_count),
        (fiber_count, sample_count),
    ).copy()
    common = 1000.0 + 100.0 * np.sin(np.arange(sample_count) / 15.0)
    throughput = np.linspace(0.7, 1.6, fiber_count)[:, None]
    spectrum = throughput * common + np.random.default_rng(7).normal(
        0.0, 2.0, (fiber_count, sample_count)
    )
    spectrum[1, 60] = 5000.0
    return spectrum, wavelength, throughput


def test_spectral_mask_self_normalizes_coarse_fiber_response_and_flags_outlier():
    spectrum, wavelength, _ = _spectral_inputs()
    result = build_master_sci_spectral_mask(
        spectrum, wavelength, coarse_bins=8, model_bins=100
    )
    assert result.meta["normalization_mode"] == "coarse_self_normalization"
    assert result.arrays["mask"].dtype == np.uint8
    assert result.arrays["mask"][1, 60] == 1
    assert result.arrays["mask"][0, 40] == 0
    medians = np.nanmedian(result.arrays["normalization"], axis=1)
    assert np.all(np.diff(medians) > 0.0)


def test_spectral_mask_prefers_and_retains_twilight_normalization():
    spectrum, wavelength, throughput = _spectral_inputs()
    normalization = np.broadcast_to(throughput, spectrum.shape).copy()
    result = build_master_sci_spectral_mask(
        spectrum,
        wavelength,
        fiber_normalization=normalization,
        model_bins=100,
    )
    assert result.meta["normalization_mode"] == "twilight_fiber_normalization"
    np.testing.assert_allclose(result.arrays["normalization"], normalization)
    assert result.arrays["mask"][1, 60] == 1


def test_spectral_mask_marks_samples_without_wavelength_solution():
    spectrum, wavelength, _ = _spectral_inputs()
    wavelength[3, :40] = np.nan
    result = build_master_sci_spectral_mask(
        spectrum, wavelength, coarse_bins=8, model_bins=100
    )
    assert result.arrays["good_wavelength_solution"][3] == 0
    assert result.scalars["good_wavelength_solution_count"] == 111
    assert np.all(result.arrays["mask"][3, :40] == 1)
