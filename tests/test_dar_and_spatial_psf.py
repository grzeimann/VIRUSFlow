import numpy as np

from virusflow.algorithms.astrometry import sky_to_focal_plane, tan_fiber_coordinates
from virusflow.algorithms.dar import (
    DAR_SOURCE_DISPLACEMENT,
    DAR_SOURCE_WAVELENGTH,
    dar_seed_model,
    evaluate_dar_seed,
    tan_plane_dar_transform,
)
from virusflow.algorithms.spatial_psf import (
    ChromaticPSFModel,
    bin_flux_by_wavelength_interval,
    build_wavelength_intervals,
    fit_chromatic_psf_model,
    fit_wavelength_interval_psf,
    integrate_moffat_over_apertures,
    moffat_psf_value,
)
from virusflow.algorithms.source_extraction import (
    combine_observation_source_spectra,
    extract_source_spectrum,
    select_source_fibers,
    solve_source_design_matrix,
    sum_aperture_flux,
)


def test_dar_seed_model_reproduces_remedy_curve():
    result = dar_seed_model()
    coefficients = result.get_array("cubic_coefficients")
    expected_coefficients = np.polyfit(DAR_SOURCE_WAVELENGTH, DAR_SOURCE_DISPLACEMENT, 3)
    np.testing.assert_allclose(coefficients, expected_coefficients)

    # A cubic least-squares fit through five points is not an exact
    # interpolation; this pins the residual to the known Remedy curve shape.
    fitted = np.polyval(coefficients, DAR_SOURCE_WAVELENGTH)
    np.testing.assert_allclose(fitted, DAR_SOURCE_DISPLACEMENT, atol=0.03)


def test_evaluate_dar_seed_zero_angle_has_no_y_displacement():
    seed = dar_seed_model()
    coefficients = seed.get_array("cubic_coefficients")
    wavelength = np.linspace(3500.0, 5500.0, 9)

    def identity_transform(delta_x, delta_y):
        return np.asarray(delta_x) + 100.0, np.asarray(delta_y) + 20.0

    evaluation = evaluate_dar_seed(
        wavelength,
        cubic_coefficients=coefficients,
        angle_deg=0.0,
        astrometric_transform=identity_transform,
        astrometric_transform_identity="identity-test",
        reference_ra_deg=100.0,
        reference_dec_deg=20.0,
    )
    np.testing.assert_allclose(evaluation.get_array("delta_y"), 0.0, atol=1e-10)
    expected_delta_x = np.polyval(coefficients, wavelength)
    np.testing.assert_allclose(evaluation.get_array("delta_x"), expected_delta_x, atol=1e-6)


def test_evaluate_dar_seed_coordinate_conversion_matches_manual_formula():
    seed = dar_seed_model()
    coefficients = seed.get_array("cubic_coefficients")
    wavelength = np.linspace(3500.0, 5500.0, 6)
    ra0, dec0, pa = 150.0, 30.0, 12.0

    transform, identity = tan_plane_dar_transform(ra0, dec0, pa)
    evaluation = evaluate_dar_seed(
        wavelength,
        cubic_coefficients=coefficients,
        angle_deg=37.0,
        astrometric_transform=transform,
        astrometric_transform_identity=identity,
        reference_ra_deg=ra0,
        reference_dec_deg=dec0,
    )

    angle = np.deg2rad(37.0)
    dar_scalar = np.polyval(coefficients, wavelength)
    delta_x = np.cos(angle) * dar_scalar
    delta_y = np.sin(angle) * dar_scalar
    manual = tan_fiber_coordinates(ra0, dec0, pa, delta_x, delta_y)
    expected_delta_ra = (manual.get_array("ra") - ra0) * 3600.0 * np.cos(np.deg2rad(dec0))
    expected_delta_dec = (manual.get_array("dec") - dec0) * 3600.0

    np.testing.assert_allclose(evaluation.get_array("delta_ra"), expected_delta_ra, atol=1e-9)
    np.testing.assert_allclose(evaluation.get_array("delta_dec"), expected_delta_dec, atol=1e-9)


def test_integrate_moffat_over_apertures_matches_analytic_centered_integral():
    fwhm = 1.8
    beta = 3.5
    radius = 0.75
    alpha = fwhm / (2.0 * np.sqrt(2.0 ** (1.0 / beta) - 1.0))
    analytic = 1.0 - (1.0 + (radius / alpha) ** 2) ** (1.0 - beta)

    coupling = integrate_moffat_over_apertures(
        np.array([0.0]), np.array([0.0]), radius, 0.0, 0.0, fwhm, beta=beta, grid_half_points=60
    )
    np.testing.assert_allclose(coupling[0], analytic, rtol=2e-3)


def test_integrate_moffat_over_apertures_nonnegative_and_monotonic_with_distance():
    fwhm = 1.5
    radius = 0.6
    distances = np.array([0.0, 0.5, 1.0, 2.0, 4.0])
    coupling = integrate_moffat_over_apertures(
        distances, np.zeros_like(distances), radius, 0.0, 0.0, fwhm, grid_half_points=24
    )
    assert np.all(coupling >= 0.0)
    assert np.all(np.diff(coupling) < 0.0)


def test_integrate_moffat_over_apertures_large_aperture_approaches_unity():
    fwhm = 1.2
    coupling = integrate_moffat_over_apertures(
        np.array([0.0]), np.array([0.0]), 25.0, 0.0, 0.0, fwhm, grid_half_points=80
    )
    assert coupling[0] > 0.999


def _hex_like_fiber_grid(spacing=1.5, half_extent=4.5):
    coordinates = np.arange(-half_extent, half_extent + 1e-6, spacing)
    grid_x, grid_y = np.meshgrid(coordinates, coordinates)
    return grid_x.ravel(), grid_y.ravel()


def test_fit_wavelength_interval_psf_recovers_injected_parameters():
    fiber_x, fiber_y = _hex_like_fiber_grid()
    fiber_radius = 0.75
    true_centroid_x, true_centroid_y, true_fwhm, true_amplitude = 0.3, -0.2, 2.0, 100.0

    coupling = integrate_moffat_over_apertures(
        fiber_x, fiber_y, fiber_radius, true_centroid_x, true_centroid_y, true_fwhm, grid_half_points=24
    )
    noiseless_flux = true_amplitude * coupling
    rng = np.random.default_rng(0)
    sigma = np.full_like(noiseless_flux, 0.05)
    flux = noiseless_flux + rng.normal(scale=sigma)

    result = fit_wavelength_interval_psf(
        fiber_x, fiber_y, fiber_radius, flux, sigma,
        seed_centroid_x=0.0, seed_centroid_y=0.0,
        wavelength_interval=(4900.0, 5100.0), reference_wavelength=5000.0,
    )
    assert result.scalars["valid"] is True
    assert result.scalars["status"] == "measured"
    assert abs(float(result.get_array("centroid_x")) - true_centroid_x) < 0.05
    assert abs(float(result.get_array("centroid_y")) - true_centroid_y) < 0.05
    assert abs(float(result.get_array("fwhm")) - true_fwhm) < 0.3


def test_fit_wavelength_interval_psf_falls_back_to_seed_when_underconstrained():
    fiber_x, fiber_y = _hex_like_fiber_grid()
    flux = np.zeros_like(fiber_x)
    sigma = np.ones_like(fiber_x)
    fiber_mask = np.ones_like(fiber_x, dtype=bool)
    fiber_mask[:2] = False  # only two usable fibers, fewer than the four free parameters

    result = fit_wavelength_interval_psf(
        fiber_x, fiber_y, 0.75, flux, sigma,
        seed_centroid_x=0.1, seed_centroid_y=0.2,
        wavelength_interval=(4900.0, 5100.0), reference_wavelength=5000.0,
        fiber_mask=fiber_mask,
    )
    assert result.scalars["valid"] is False
    assert result.scalars["status"] == "degraded"
    np.testing.assert_allclose(float(result.get_array("centroid_x")), 0.1, atol=1e-6)
    np.testing.assert_allclose(float(result.get_array("centroid_y")), 0.2, atol=1e-6)


def test_chromatic_psf_model_interpolates_inside_and_marks_continuous_extrapolation():
    reference_wavelength = np.array([4000.0, 4500.0, 5000.0, 5500.0, 6000.0])
    seed_delta_x = np.zeros_like(reference_wavelength)
    seed_delta_y = np.zeros_like(reference_wavelength)
    true_residual_x = 0.01 * (reference_wavelength - 5000.0) / 1000.0
    centroid_x = seed_delta_x + true_residual_x
    centroid_y = seed_delta_y.copy()
    fwhm = np.full_like(reference_wavelength, 1.8)
    valid = np.ones_like(reference_wavelength, dtype=bool)
    weight = np.ones_like(reference_wavelength)

    result = fit_chromatic_psf_model(
        reference_wavelength, seed_delta_x, seed_delta_y, centroid_x, centroid_y, fwhm, valid, weight,
        centroid_degree=1, fwhm_degree=0,
    )
    model: ChromaticPSFModel = result.meta["model"]

    inside_wavelength = np.array([4200.0, 5500.0])
    fitted_x, fitted_y, fitted_fwhm, status = model.evaluate(inside_wavelength, 0.0, 0.0)
    expected_x = 0.01 * (inside_wavelength - 5000.0) / 1000.0
    np.testing.assert_allclose(fitted_x, expected_x, atol=1e-6)
    np.testing.assert_allclose(fitted_y, 0.0, atol=1e-6)
    np.testing.assert_allclose(fitted_fwhm, 1.8, atol=1e-6)
    assert np.all(status == 0)

    outside_wavelength = np.array([3000.0])
    fitted_x, fitted_y, _, status = model.evaluate(outside_wavelength, seed_delta_x=5.0, seed_delta_y=-3.0)
    assert fitted_x[0] == 4.98
    assert fitted_y[0] == -3.0
    assert status[0] == 1


def test_extract_source_spectrum_recovers_injected_amplitude_without_bias():
    fiber_x, fiber_y = _hex_like_fiber_grid()
    fiber_radius = 0.75
    n_wave = 5
    true_amplitude = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    coupling = np.stack(
        [
            integrate_moffat_over_apertures(fiber_x, fiber_y, fiber_radius, 0.0, 0.0, 1.8)
            for _ in range(n_wave)
        ],
        axis=1,
    )
    noiseless_flux = coupling * true_amplitude[None, :]
    rng = np.random.default_rng(1)
    sigma = 0.02
    variance = np.full_like(noiseless_flux, sigma ** 2)
    flux = noiseless_flux + rng.normal(scale=sigma, size=noiseless_flux.shape)

    result = extract_source_spectrum(flux, variance, coupling)
    recovered = result.get_array("amplitude")
    np.testing.assert_allclose(recovered, true_amplitude, atol=0.5)
    np.testing.assert_allclose(
        result.get_array("captured_fraction"), coupling.sum(axis=0), atol=1e-9
    )


def test_extract_source_spectrum_background_column_and_missing_fibers():
    fiber_x, fiber_y = _hex_like_fiber_grid()
    fiber_radius = 0.75
    coupling = integrate_moffat_over_apertures(fiber_x, fiber_y, fiber_radius, 0.0, 0.0, 1.8)[:, None]
    true_amplitude = 10.0
    true_background = 0.3
    flux = true_amplitude * coupling + true_background
    variance = np.full_like(flux, 1e-6)

    result = extract_source_spectrum(flux, variance, coupling, background=True)
    np.testing.assert_allclose(result.get_array("amplitude"), [true_amplitude], atol=1e-3)
    np.testing.assert_allclose(result.get_array("background"), [true_background], atol=1e-3)

    full_fraction = result.get_array("captured_fraction")[0]
    fiber_mask = np.zeros(fiber_x.shape, dtype=bool)
    fiber_mask[0] = True
    masked_result = extract_source_spectrum(flux, variance, coupling, background=True, fiber_mask=fiber_mask)
    masked_fraction = masked_result.get_array("captured_fraction")[0]
    assert masked_fraction < full_fraction
    np.testing.assert_allclose(masked_fraction, full_fraction - coupling[0, 0], atol=1e-9)


def test_solve_source_design_matrix_reports_failure_without_raising():
    flux = np.array([1.0, 2.0])
    variance = np.array([1.0, 1.0])
    design_matrix = np.array([[1.0, 0.0], [0.0, 1.0]])
    fiber_mask = np.array([True, True])  # every fiber excluded

    result = solve_source_design_matrix(flux, variance, design_matrix, fiber_mask=fiber_mask)
    assert result.scalars["success"] is False
    assert np.all(np.isnan(result.get_array("amplitude")))


def test_moffat_psf_value_rejects_nonpositive_fwhm():
    try:
        moffat_psf_value(0.0, 0.0, 0.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for non-positive FWHM")


def test_sky_to_focal_plane_round_trips_through_tan_fiber_coordinates():
    ra0, dec0, pa = 150.0, 30.0, 12.0
    focal_x = np.array([0.0, 1.5, -2.0, 3.3])
    focal_y = np.array([0.0, -1.0, 2.5, -0.7])

    sky = tan_fiber_coordinates(ra0, dec0, pa, focal_x, focal_y)
    recovered = sky_to_focal_plane(ra0, dec0, pa, sky.get_array("ra"), sky.get_array("dec"))

    np.testing.assert_allclose(recovered.get_array("focal_x"), focal_x, atol=1e-6)
    np.testing.assert_allclose(recovered.get_array("focal_y"), focal_y, atol=1e-6)


def test_build_wavelength_intervals_returns_contiguous_equal_width_edges():
    intervals = build_wavelength_intervals(3500.0, 5500.0, 4)
    assert intervals.shape == (4, 2)
    np.testing.assert_allclose(intervals[0, 0], 3500.0)
    np.testing.assert_allclose(intervals[-1, 1], 5500.0)
    np.testing.assert_allclose(intervals[:-1, 1], intervals[1:, 0])
    np.testing.assert_allclose(np.diff(intervals, axis=1).ravel(), 500.0)


def test_bin_flux_by_wavelength_interval_inverse_variance_weights_and_excludes_masked():
    wavelength = np.tile(np.array([4990.0, 5000.0, 5010.0]), (2, 1))
    flux = np.array([[1.0, 3.0, 5.0], [2.0, 4.0, 6.0]])
    variance = np.array([[1.0, 1.0, 1.0], [1.0, 4.0, 1.0]])
    mask = np.zeros_like(flux, dtype=np.uint16)
    mask[1, 1] = 1  # exclude the 4.0 sample for the second fiber

    binned_flux, binned_uncertainty = bin_flux_by_wavelength_interval(
        wavelength, flux, variance, mask, (4900.0, 5100.0)
    )
    weight_row0 = 1.0 / variance[0]
    expected_row0 = np.average(flux[0], weights=weight_row0)
    np.testing.assert_allclose(binned_flux[0], expected_row0)
    expected_row1 = (2.0 / 1.0 + 6.0 / 1.0) / (1.0 / 1.0 + 1.0 / 1.0)
    np.testing.assert_allclose(binned_flux[1], expected_row1)
    assert np.all(np.isfinite(binned_uncertainty))


def test_bin_flux_by_wavelength_interval_returns_nan_when_no_valid_samples():
    wavelength = np.array([[4990.0, 5000.0]])
    flux = np.array([[1.0, 2.0]])
    variance = np.array([[1.0, 1.0]])
    mask = np.array([[1, 1]], dtype=np.uint16)

    binned_flux, binned_uncertainty = bin_flux_by_wavelength_interval(
        wavelength, flux, variance, mask, (4900.0, 5100.0)
    )
    assert np.isnan(binned_flux[0])
    assert np.isnan(binned_uncertainty[0])


def test_select_source_fibers_excludes_by_distance_and_preexisting_mask():
    fiber_x = np.array([0.0, 1.0, 10.0])
    fiber_y = np.array([0.0, 0.0, 0.0])
    exclusion = select_source_fibers(fiber_x, fiber_y, 0.0, 0.0, max_distance_arcsec=5.0)
    np.testing.assert_array_equal(exclusion, [False, False, True])

    exclusion_with_mask = select_source_fibers(
        fiber_x, fiber_y, 0.0, 0.0, max_distance_arcsec=5.0,
        fiber_mask=np.array([False, True, False]),
    )
    np.testing.assert_array_equal(exclusion_with_mask, [False, True, True])


def test_sum_aperture_flux_matches_unweighted_sum_and_excludes_masked_fibers():
    flux = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    variance = np.full_like(flux, 1.0)

    full = sum_aperture_flux(flux, variance)
    np.testing.assert_allclose(full.get_array("amplitude"), flux.sum(axis=0))

    fiber_mask = np.array([False, True, False])
    partial = sum_aperture_flux(flux, variance, fiber_mask=fiber_mask)
    np.testing.assert_allclose(partial.get_array("amplitude"), flux[[0, 2]].sum(axis=0))


def test_combine_observation_source_spectra_inverse_variance_weights_and_flags_inconsistent_wavelength():
    wavelength = np.array([5000.0, 5010.0, 5020.0])
    spectrum_a = {
        "wavelength": wavelength,
        "amplitude": np.array([1.0, 2.0, 3.0]),
        "variance": np.array([1.0, 1.0, 1.0]),
        "mask": np.zeros(3, dtype=np.uint16),
        "captured_fraction": np.array([0.9, 0.9, 0.9]),
    }
    spectrum_b = {
        "wavelength": wavelength,
        "amplitude": np.array([3.0, 4.0, 5.0]),
        "variance": np.array([4.0, 4.0, 4.0]),
        "mask": np.zeros(3, dtype=np.uint16),
        "captured_fraction": np.array([0.5, 0.5, 0.5]),
    }
    combined = combine_observation_source_spectra([spectrum_a, spectrum_b])
    expected_weight_a, expected_weight_b = 1.0, 0.25
    expected_amplitude = (
        expected_weight_a * spectrum_a["amplitude"] + expected_weight_b * spectrum_b["amplitude"]
    ) / (expected_weight_a + expected_weight_b)
    np.testing.assert_allclose(combined.get_array("amplitude"), expected_amplitude)
    np.testing.assert_allclose(combined.get_array("variance"), 1.0 / (expected_weight_a + expected_weight_b))
    assert combined.scalars["status"] == "combined"
    assert combined.scalars["wavelength_consistent"] is True
    assert combined.scalars["exposure_count"] == 2

    inconsistent_spectrum = dict(spectrum_b)
    inconsistent_spectrum["wavelength"] = wavelength + 5.0
    degraded = combine_observation_source_spectra([spectrum_a, inconsistent_spectrum])
    assert degraded.scalars["status"] == "degraded"
    assert degraded.scalars["wavelength_consistent"] is False


def test_combine_observation_source_spectra_requires_at_least_one_exposure():
    try:
        combine_observation_source_spectra([])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty exposure list")
