"""Focused contract and synthetic tests for the small development reference."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np


def _reference():
    path = Path(__file__).parents[1] / "scripts" / "develop_compact_profile_trace.py"
    spec = importlib.util.spec_from_file_location("compact_profile_trace_reference_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _problem():
    ref = _reference()
    fibers, columns, rows = 3, 64, 80
    x = np.linspace(-1.0, 1.0, columns)
    trace = np.repeat(np.asarray([[12.0], [19.0], [26.0]]), columns, axis=1)
    valid = np.ones((rows, columns), bool)
    yy, xx = np.nonzero(valid)
    aperture_rows, aperture_weights, _ = ref.fractional_aperture_geometry(trace, rows)
    evidence = ref.LDLSEvidence(
        image=np.zeros((rows, columns)), variance=np.ones((rows, columns)), valid_mask=valid,
        five_pixel_flux=np.zeros((fibers, columns)), base_trace=trace,
        aperture_rows=aperture_rows, aperture_weights=aperture_weights,
        fiber_ids=np.arange(fibers), amplifier_ids=np.zeros(fibers, int),
        amplifier_bounds=(0.0, float(rows)), pixel_x=xx, pixel_y=yy,
        pixel_value=np.zeros(xx.size), pixel_variance=np.ones(xx.size),
    )
    config = ref.RunConfig(max_initial_profile_iterations=0, max_trace_iterations=1)
    geometry, inference, validation, full, cache = ref.build_sparse_views(evidence, config)
    coefficients = ref._initial_sigma_coefficients(0.7)
    blank = ref.evaluate_compact_state(evidence, geometry, full, trace, 0.975, coefficients, config, cache)
    source = replace(evidence, five_pixel_flux=100.0 * blank.C5)
    truth = ref.evaluate_compact_state(source, geometry, full, trace, 0.975, coefficients, config, cache)
    image = np.zeros_like(source.image)
    image[geometry.sample_y, geometry.sample_x] = truth.model_samples
    source = replace(source, image=image, pixel_value=image[yy, xx])
    return ref, source, geometry, inference, validation, full, cache, trace, coefficients, config


def test_resolved_knobs_and_default_phases_are_explicit():
    ref = _reference()
    config = ref.RunConfig(q_r_initial=0.971, sigma_initial=0.73, stride=8, inference_phase=0, validation_phase=4)
    resolved = config.resolved()
    assert resolved["q_r_initial"] == 0.971
    assert resolved["sigma_initial"] == 0.73
    assert resolved["stride"] == 8
    assert resolved["inference_phase"] == 0
    assert resolved["validation_phase"] == 4
    defaults = ref.RunConfig().resolved()
    assert defaults["inference_phase"] == 0 and defaults["validation_phase"] == 4


def test_sparse_views_are_disjoint_full_cross_dispersion_and_keep_overlap():
    ref, evidence, geometry, inference, validation, full, cache, trace, coefficients, config = _problem()
    assert np.all(inference.selected_detector_x % 8 == 0)
    assert np.all(validation.selected_detector_x % 8 == 4)
    assert np.intersect1d(inference.sample_indices, validation.sample_indices).size == 0
    assert inference.sample_indices.size < full.sample_indices.size
    prediction = ref.evaluate_compact_state(evidence, geometry, inference, trace, 0.975, coefficients, config, cache)
    assert prediction.contribution_indices is not None
    assert prediction.contribution_indices.size < geometry.contribution_sample_index.size
    overlap = np.flatnonzero(np.bincount(
        geometry.contribution_sample_index[prediction.contribution_indices],
        minlength=geometry.sample_x.size,
    ) > 1)
    assert overlap.size


def test_frozen_aperture_and_radius_are_recomputed_after_trace_movement():
    ref, evidence, geometry, _inference, _validation, full, cache, trace, coefficients, config = _problem()
    rows_before, weights_before = evidence.aperture_rows.copy(), evidence.aperture_weights.copy()
    before = ref.evaluate_compact_state(evidence, geometry, full, trace, 0.975, coefficients, config, cache)
    moved_trace = trace.copy(); moved_trace[0] += 0.1
    after = ref.evaluate_compact_state(evidence, geometry, full, moved_trace, 0.975, coefficients, config, cache)
    assert not np.array_equal(before.C5, after.C5)
    assert not np.array_equal(before.R_geom, after.R_geom)
    np.testing.assert_array_equal(evidence.aperture_rows, rows_before)
    np.testing.assert_array_equal(evidence.aperture_weights, weights_before)


def test_sparse_profile_derivatives_and_normal_equations_equal_full_subset():
    ref, evidence, geometry, inference, _validation, full, cache, trace, coefficients, config = _problem()
    full_prediction = ref.evaluate_compact_state(evidence, geometry, full, trace, 0.975, coefficients, config, cache)
    sparse_prediction = ref.evaluate_compact_state(evidence, geometry, inference, trace, 0.975, coefficients, config, cache)
    indices = sparse_prediction.contribution_indices
    assert indices is not None
    full_d_radius, full_d_sigma = ref._compact_profile_parameter_derivatives(
        evidence, geometry, full_prediction, cache=cache,
    )
    sparse_d_radius, sparse_d_sigma = ref._compact_profile_parameter_derivatives(
        evidence, geometry, sparse_prediction, cache=cache,
    )
    assert sparse_d_radius.shape == (indices.size,)
    assert indices.size == np.count_nonzero(np.isin(geometry.contribution_sample_index, inference.sample_indices))
    np.testing.assert_allclose(sparse_d_radius, full_d_radius[indices], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(sparse_d_sigma, full_d_sigma[indices], rtol=0.0, atol=0.0)
    basis = ref._fixed_r_sigma_basis(evidence, geometry, 3, include_x=True)
    full_hessian, full_gradient = ref._build_compact_profile_normal_equations(
        evidence, geometry, inference, full_prediction, basis, full_d_radius, full_d_sigma,
        fit_q_R=True, ridge=config.ridge,
    )
    sparse_hessian, sparse_gradient = ref._build_compact_profile_normal_equations(
        evidence, geometry, inference, sparse_prediction, basis, sparse_d_radius, sparse_d_sigma,
        fit_q_R=True, ridge=config.ridge, contribution_indices=indices,
    )
    np.testing.assert_allclose(sparse_hessian, full_hessian, rtol=0.0, atol=1e-12)
    np.testing.assert_allclose(sparse_gradient, full_gradient, rtol=0.0, atol=1e-12)


def test_synthetic_degree_four_trace_perturbation_is_recovered():
    ref, evidence, geometry, inference, validation, full, cache, base_trace, coefficients, config = _problem()
    x = np.linspace(-1.0, 1.0, base_trace.shape[1])
    basis = np.polynomial.legendre.legvander(x, 4)
    injected = np.zeros((base_trace.shape[0], 5)); injected[:, 0] = 0.11; injected[:, 1] = -0.035; injected[:, 2] = 0.014
    true_trace = base_trace + np.einsum("xt,ft->fx", basis, injected)
    truth = ref.evaluate_compact_state(evidence, geometry, full, true_trace, 0.975, coefficients, config, cache)
    image = np.zeros_like(evidence.image); image[geometry.sample_y, geometry.sample_x] = truth.model_samples
    source = replace(evidence, image=image, pixel_value=image[evidence.pixel_y, evidence.pixel_x], five_pixel_flux=100.0 * truth.C5)
    result = ref.refine_trace(source, geometry, inference, validation, base_trace, np.zeros_like(injected), 0.975, coefficients, config, cache, ref.TimingReport({}))
    recovered = result[0] - base_trace
    assert np.max(np.abs(recovered - np.einsum("xt,ft->fx", basis, injected))) < 0.03


def test_reference_has_one_full_closure_and_excludes_unimplemented_branches():
    ref = _reference()
    source = Path(ref.__file__).read_text()
    assert source.count("evaluate_full_closure(") == 2  # definition and one call
    assert "max_trace_iterations: int = 2" in source
    assert "q_R_grid" not in source
    assert "detector dy" in source
    assert "power-law fiber halo" in source
    assert "run_detector_displacement_experiment" not in source
    assert "build_response_newton_step" not in source
    assert "service.publish" not in source


def test_resolved_configuration_is_persisted_exactly(tmp_path):
    ref, evidence, geometry, inference, validation, full, cache, trace, coefficients, config = _problem()
    initial = ref.evaluate_compact_state(evidence, geometry, inference, trace, 0.975, coefficients, config, cache)
    final = ref.evaluate_compact_state(evidence, geometry, full, trace, 0.975, coefficients, config, cache)
    residual_image = np.zeros_like(evidence.image)
    report = ref.save_outputs(
        tmp_path, evidence, geometry, inference, validation, trace,
        np.zeros((trace.shape[0], config.trace_degree + 1)), 0.975, coefficients,
        initial, final, final,
        {"robust_loss": final.robust_loss, "RMS": 0.0, "median": 0.0, "MAD": 0.0, "sample_count": int(final.residuals.size), "residual_image": residual_image},
        {}, config,
        {"initial_profile": [], "trace": [], "closure_summary": {"history": []}},
        ref.TimingReport({}), 0.0, 0.0, 0.0,
    )
    import json
    persisted = json.loads((tmp_path / "compact_profile_trace.json").read_text())
    assert persisted["run_config"] == config.resolved()
    assert report["run_config"] == config.resolved()
