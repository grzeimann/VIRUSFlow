"""Contracts for the standalone LDLS forward-response development solver."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np


def _solver_module():
    path = Path(__file__).parents[1] / "scripts" / "develop_ldls_forward_response.py"
    spec = importlib.util.spec_from_file_location("develop_ldls_forward_response_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _synthetic_problem():
    solver = _solver_module()
    fiber_count, columns, rows = 3, 24, 48
    base_trace = np.repeat(np.asarray([[10.2], [21.0], [31.8]]), columns, axis=1)
    aperture_rows, aperture_weights, _ = solver.fractional_aperture_geometry(base_trace, rows)
    valid = np.ones((rows, columns), dtype=bool)
    pixel_y, pixel_x = np.nonzero(valid)
    evidence = solver.LDLSEvidence(
        image=np.zeros((rows, columns)), variance=np.ones((rows, columns)), valid_mask=valid,
        five_pixel_flux=np.full((fiber_count, columns), 100.0), base_trace=base_trace,
        aperture_rows=aperture_rows, aperture_weights=aperture_weights,
        fiber_ids=np.arange(fiber_count), amplifier_ids=np.zeros(fiber_count, dtype=int),
        amplifier_bounds=(0.0, 100.0), pixel_x=pixel_x, pixel_y=pixel_y,
        pixel_value=np.zeros(pixel_x.size), pixel_variance=np.ones(pixel_x.size),
    )
    geometry = solver.build_ldls_geometry(
        evidence, support=7.0, trace_degree=1, response_degree=1,
        amplifier_boundary=100.0, reference_W=np.full((fiber_count, columns), 1.62),
        reference_f_sigma=np.full((fiber_count, columns), 0.26),
    )
    sampling = solver.build_ldls_sampling(evidence, geometry)
    terms = geometry.response_basis_W.shape[-1]
    truth = solver.ProfileTraceState(
        trace_coeff=np.asarray([[0.05, 0.0], [0.0, 0.0], [0.0, 0.0]]),
        W_coeff=np.r_[0.02, np.zeros(terms - 1)],
        f_sigma_coeff=np.r_[0.01, np.zeros(terms - 1)],
    )
    cache = solver.ProfileCache(0.002)
    rendered = solver.evaluate_state(evidence, geometry, sampling, truth, cache=cache)
    image = np.zeros_like(evidence.image)
    image[geometry.sample_y, geometry.sample_x] = rendered.model_samples
    evidence = replace(evidence, image=image, pixel_value=image[pixel_y, pixel_x])
    return solver, evidence, geometry, sampling, truth, cache


def test_evaluate_state_has_fresh_deterministic_normalization_closure():
    solver, evidence, geometry, sampling, truth, cache = _synthetic_problem()

    evaluation = solver.evaluate_state(evidence, geometry, sampling, truth, cache=cache)
    solver.assert_evaluation_invariants(evidence, geometry, sampling, truth, cache)

    assert evaluation.robust_loss == 0.0
    np.testing.assert_allclose(evaluation.total_amplitude * evaluation.C5, evidence.five_pixel_flux)


def test_response_and_trace_steps_are_proposals_and_reduce_forward_loss():
    solver, evidence, geometry, sampling, _truth, cache = _synthetic_problem()
    initial = solver.initial_profile_trace_state(geometry)
    before = solver.evaluate_state(evidence, geometry, sampling, initial, cache=cache, derivatives=True)
    response_step = solver.build_response_newton_step(evidence, geometry, sampling, initial, before)
    response_state = solver.apply_response_step(initial, response_step)
    after_response = solver.evaluate_state(evidence, geometry, sampling, response_state, cache=cache)
    trace_step = solver.build_trace_step(evidence, geometry, sampling, response_state, after_response)
    after_trace = solver.evaluate_state(
        evidence, geometry, sampling, solver.apply_trace_step(response_state, trace_step), cache=cache
    )

    assert initial.generation == 0
    assert response_state.generation == 1
    assert after_response.robust_loss < before.robust_loss
    assert after_trace.robust_loss < after_response.robust_loss


def test_trace_normal_equations_retain_overlapping_fiber_couplings_as_sparse_blocks():
    solver, evidence, geometry, sampling, _truth, cache = _synthetic_problem()
    initial = solver.initial_profile_trace_state(geometry)
    current = solver.evaluate_state(evidence, geometry, sampling, initial, cache=cache)
    step = solver.build_trace_step(evidence, geometry, sampling, initial, current)
    terms = initial.trace_coeff.shape[1]

    assert solver.sparse.isspmatrix_csr(step.hessian)
    assert geometry.trace_neighbor_pairs.size > 0
    first, second = geometry.trace_neighbor_pairs[0]
    coupling = step.hessian[
        first * terms:(first + 1) * terms, second * terms:(second + 1) * terms
    ].toarray()
    assert np.linalg.norm(coupling) > 0.0


def test_accepted_response_step_commits_before_physical_displacement_convergence():
    solver, evidence, geometry, sampling, _truth, cache = _synthetic_problem()
    initial = solver.initial_profile_trace_state(geometry)
    before = solver.evaluate_state(evidence, geometry, sampling, initial, cache=cache)

    accepted, history = solver.solve_response_state(
        evidence, geometry, sampling, initial, cache=cache, max_iterations=1, tolerance=1.0
    )
    after = solver.evaluate_state(evidence, geometry, sampling, accepted, cache=cache)

    assert history[0]["damping"] > 0.0
    assert accepted.generation == initial.generation + 1
    assert history[0]["max_delta_W"] == np.max(np.abs(after.W - before.W))
    assert history[0]["max_delta_f_sigma"] == np.max(np.abs(after.f_sigma - before.f_sigma))


def test_accepted_trace_step_commits_before_physical_displacement_convergence():
    solver, evidence, geometry, sampling, _truth, cache = _synthetic_problem()
    initial = solver.initial_profile_trace_state(geometry)
    response_current = solver.evaluate_state(evidence, geometry, sampling, initial, cache=cache, derivatives=True)
    response_state = solver.apply_response_step(
        initial, solver.build_response_newton_step(evidence, geometry, sampling, initial, response_current)
    )
    before = solver.evaluate_state(evidence, geometry, sampling, response_state, cache=cache)

    accepted, history = solver.solve_trace_state(
        evidence, geometry, sampling, response_state, cache=cache, max_iterations=1, tolerance=1.0
    )
    after = solver.evaluate_state(evidence, geometry, sampling, accepted, cache=cache)

    assert history[0]["damping"] > 0.0
    assert accepted.generation == response_state.generation + 1
    assert history[0]["max_delta_trace"] == np.max(np.abs(after.trace - before.trace))


def test_zero_steps_preserve_state_identity():
    solver, _evidence, geometry, _sampling, _truth, _cache = _synthetic_problem()
    state = solver.initial_profile_trace_state(geometry)
    response = solver.ResponseStep(
        np.zeros_like(state.W_coeff), np.zeros_like(state.f_sigma_coeff),
        np.empty((0, 0)), np.empty(0), 0.0,
    )
    trace = solver.TraceStep(
        np.zeros_like(state.trace_coeff), solver.sparse.csr_matrix((0, 0)), np.empty(0), 0.0,
    )

    assert solver.apply_response_step(state, response) is state
    assert solver.apply_trace_step(state, trace) is state


def _legacy_profile_evaluate(solver, cache, u, R, sigma):
    """The pre-grouping reference used only to protect exact cache selection."""
    u, R, sigma = np.broadcast_arrays(np.asarray(u, float), np.asarray(R, float), np.asarray(sigma, float))
    key_r = np.rint(R / cache.quantization).astype(np.int32).ravel()
    key_s = np.rint(sigma / cache.quantization).astype(np.int32).ravel()
    keys, inverse = np.unique(np.column_stack((key_r, key_s)), axis=0, return_inverse=True)
    values = np.empty(u.size, float)
    derivatives = np.empty(u.size, float)
    flat_u = u.ravel()
    for index, (qr, qs) in enumerate(keys):
        selected = inverse == index
        key = int(qr), int(qs), 0.0
        template = cache._templates.get(key)
        if template is None:
            template = solver.fourier_compact_profile(qr * cache.quantization, qs * cache.quantization, alpha=0.0)
            cache._templates[key] = template
        values[selected], derivatives[selected] = template.evaluate(flat_u[selected])
    return values.reshape(u.shape), derivatives.reshape(u.shape)


def test_contiguous_profile_grouping_is_bitwise_equivalent_to_previous_key_selection():
    solver = _solver_module()
    u = np.linspace(-5.0, 5.0, 231).reshape(21, 11)
    row, column = np.indices(u.shape)
    R = 1.25 + 0.013 * (row % 7) + 0.004 * (column % 3)
    sigma = 0.18 + 0.011 * (row % 5) + 0.003 * (column % 4)
    grouped_cache = solver.ProfileCache(0.002)
    legacy_cache = solver.ProfileCache(0.002)

    grouped = grouped_cache.evaluate(u, R, sigma)
    legacy = _legacy_profile_evaluate(solver, legacy_cache, u, R, sigma)

    assert grouped_cache._templates.keys() == legacy_cache._templates.keys()
    np.testing.assert_array_equal(grouped[0], legacy[0])
    np.testing.assert_array_equal(grouped[1], legacy[1])


def test_uniform_illumination_alpha_zero_is_a_bitwise_profile_identity():
    solver = _solver_module()
    original = solver.fourier_compact_profile(1.43, 0.31)
    alpha_zero = solver.fourier_compact_profile(1.43, 0.31, alpha=0.0)

    np.testing.assert_array_equal(alpha_zero.coordinate, original.coordinate)
    np.testing.assert_array_equal(alpha_zero.density, original.density)
    np.testing.assert_array_equal(alpha_zero.derivative, original.derivative)


def test_normal_response_path_is_uniform_and_remains_a_36_parameter_system():
    solver, evidence, geometry, sampling, _truth, cache = _synthetic_problem()
    state = solver.initial_profile_trace_state(geometry)
    current = solver.evaluate_state(evidence, geometry, sampling, state, cache=cache, derivatives=True)
    step = solver.build_response_newton_step(evidence, geometry, sampling, state, current)

    assert not hasattr(state, "alpha")
    assert step.hessian.shape == (2 * state.W_coeff.size,) * 2
    assert current.Palpha is None
    explicit_uniform = solver.evaluate_state(
        evidence, geometry, sampling, state, cache=cache, derivatives=True,
        experimental_aperture_alpha=0.0,
    )
    for name in ("C5", "total_amplitude", "model_samples", "residuals", "P", "Pprime", "PW", "Pf"):
        np.testing.assert_array_equal(getattr(current, name), getattr(explicit_uniform, name))


def test_experimental_radial_illumination_remains_explicit_and_two_sided():
    solver, evidence, geometry, sampling, _truth, cache = _synthetic_problem()
    state = solver.initial_profile_trace_state(geometry)
    edge_brightened = solver.evaluate_state(
        evidence, geometry, sampling, state, cache=cache, derivatives=True,
        experimental_aperture_alpha=-0.17, experimental_alpha_derivative=True,
    )
    assert edge_brightened.Palpha is not None
    with np.testing.assert_raises(ValueError):
        solver.evaluate_state(
            evidence, geometry, sampling, state, cache=cache, experimental_aperture_alpha=-0.51,
        )


def test_zero_detector_displacement_is_a_bitwise_forward_model_identity():
    solver, evidence, geometry, sampling, truth, cache = _synthetic_problem()
    zero_field = solver.DetectorDisplacementField(
        np.zeros_like(evidence.image), *(np.empty(0) for _ in range(6)),
        amplifier_boundary=100, x_knot_spacing=32.0, y_knot_spacing=32.0,
    )

    baseline = solver.evaluate_state(evidence, geometry, sampling, truth, cache=cache, derivatives=True)
    displaced = solver.evaluate_state(
        evidence, geometry, sampling, truth, cache=cache, derivatives=True,
        detector_displacement=zero_field,
    )

    for name in ("C5", "total_amplitude", "model_samples", "residuals", "P", "Pprime", "PW", "Pf", "Palpha"):
        np.testing.assert_array_equal(getattr(displaced, name), getattr(baseline, name))
    assert displaced.robust_loss == baseline.robust_loss


def test_local_residual_mode_projection_solves_batched_four_mode_systems():
    solver, evidence, geometry, sampling, _truth, cache = _synthetic_problem()
    current = solver.evaluate_state(
        evidence, geometry, sampling, solver.initial_profile_trace_state(geometry), cache=cache, derivatives=True,
    )

    projection = solver._local_residual_mode_projection(evidence, geometry, sampling, current)

    assert projection["coefficients"].shape == (*evidence.base_trace.shape, 4)
    assert projection["joint_fraction"].shape == evidence.base_trace.shape
    assert np.isfinite(projection["joint_fraction"]).any()
