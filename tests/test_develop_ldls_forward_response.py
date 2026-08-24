"""Contracts for the standalone LDLS forward-response development solver."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from virusflow.artifacts import ArtifactService, Scope
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.core.identity import ZipCode
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService


def _solver_module():
    path = Path(__file__).parents[1] / "scripts" / "develop_ldls_forward_response.py"
    spec = importlib.util.spec_from_file_location("develop_ldls_forward_response_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPOSURES_A = (
    "20250418T232722.6", "20250418T232847.8", "20250418T233011.0",
)
EXPOSURES_B = (
    "20250419T232722.6", "20250419T232847.8", "20250419T233011.0",
)


def _publish_fixture(service, root: Path, request: ArtifactRequest):
    publication = DefaultPublicationService(
        svc=service, policy=DefaultPersistencePolicy(), base_dir=str(root)
    )
    context = PublicationContext(
        task_name="fixture", task_version="1", algorithm_name="fixture",
        algorithm_version="1", parameters={}, parent_ids=[], timings={},
    )
    return publication.publish([request], context)[0]


def _fixture_pair(tmp_path: Path, exposures: tuple[str, ...], value: float):
    solver = _solver_module()
    service = ArtifactService(str(tmp_path / "artifacts.sqlite3"))
    lower = ZipCode("106", "033", "506", "LL", "S/N 0013")
    upper = ZipCode("106", "033", "506", "LU", "S/N 0013")
    group = {
        "frame_membership": [{"exposure_id": exposure} for exposure in exposures],
        "n_exposures": len(exposures),
        "temporal_center": exposures[1],
    }
    trace = np.asarray([
        [100.0] * 24, [112.0] * 24, [400.0] * 24, [412.0] * 24,
    ])
    artifacts = {}
    for index, zipcode in enumerate((lower, upper)):
        image = np.full((1032, 24), value + index, dtype=np.float32)
        mask = np.zeros_like(image, dtype=np.uint8)
        master = _publish_fixture(service, tmp_path, ArtifactRequest(
            kind="master_ldls",
            components={
                "master_ldls": LogicalComponent("master_ldls", "array2d", image, "electron", "oriented_amplifier"),
                "flat_response_mask": LogicalComponent("flat_response_mask", "array2d", mask, "1", "oriented_amplifier"),
            },
            metadata={"calibration_group_id": f"master_ldls:{exposures[0]}:{zipcode.amp}", "calibration_group": group},
            scope=Scope(zipcode=zipcode),
        ))
        trace_components = {
            "fiber_trace_map": LogicalComponent("fiber_trace_map", "array2d", trace, "pixel", "fiber_by_dispersion_pixel"),
            "trace_sample_columns": LogicalComponent("trace_sample_columns", "array1d", np.asarray([0.0, 23.0]), "pixel", "dispersion_pixel"),
            "sampled_trace_positions": LogicalComponent("sampled_trace_positions", "array2d", trace[:, [0, -1]], "pixel", "fiber_by_sample"),
            "per_fiber_trace_residual_rms": LogicalComponent("per_fiber_trace_residual_rms", "array1d", np.zeros(4), "pixel", "fiber"),
            "trace_sample_valid_mask": LogicalComponent("trace_sample_valid_mask", "array2d", np.ones((4, 2), dtype=np.uint8), "1", "fiber_by_sample"),
            "trace_fit_residuals": LogicalComponent("trace_fit_residuals", "array2d", np.zeros((4, 2)), "pixel", "fiber_by_sample"),
            "per_fiber_valid_sample_count": LogicalComponent("per_fiber_valid_sample_count", "array1d", np.full(4, 2), "1", "fiber"),
            "trace_interpolated_fiber_mask": LogicalComponent("trace_interpolated_fiber_mask", "array1d", np.zeros(4, dtype=np.uint8), "1", "fiber"),
        }
        trace_artifact = _publish_fixture(service, tmp_path, ArtifactRequest(
            kind="trace_map", components=trace_components,
            metadata={"calibration_group_id": f"trace_map:{exposures[0]}:{zipcode.amp}", "calibration_group": group},
            scope=Scope(zipcode=zipcode), parents=[master.id],
        ))
        artifacts[zipcode.amp] = {"master_ldls": master, "trace_map": trace_artifact}
    return solver, service, lower, upper, artifacts, trace


def test_artifact_bridge_selects_exact_group_and_is_read_only(tmp_path: Path):
    solver, _service_a, lower, upper, artifacts_a, trace = _fixture_pair(tmp_path, EXPOSURES_A, 10.0)
    _solver_b, service, _lower_b, _upper_b, _artifacts_b, _trace_b = _fixture_pair(tmp_path, EXPOSURES_B, 20.0)
    before = {kind: len(service.adapter.list_all(kind=kind)) for kind in ("master_ldls", "trace_map")}

    evidence, selection = solver.load_ldls_evidence_pair(
        service, lower, upper, exposure_ids=EXPOSURES_A,
    )

    assert selection["artifact_db"] == service.db_path
    assert selection["selected_exposure_ids"] == sorted(EXPOSURES_A)
    assert selection["lower_master_ldls"]["id"] == artifacts_a["LL"]["master_ldls"].id
    assert selection["upper_master_ldls"]["id"] == artifacts_a["LU"]["master_ldls"].id
    assert selection["lower_trace_map"]["id"] == artifacts_a["LL"]["trace_map"].id
    assert selection["upper_trace_map"]["id"] == artifacts_a["LU"]["trace_map"].id
    assert selection["master_ldls_array_shapes"] == {"lower": [1032, 24], "upper": [1032, 24]}
    assert selection["trace_map_shapes"] == {"lower": [4, 24], "upper": [4, 24]}
    assert selection["assembled_physical_ccd_shape"] == [2064, 24]
    assert selection["read_only"] is True
    assert evidence.image.shape == (2064, 24)
    assert evidence.base_trace.shape == (8, 24)
    np.testing.assert_allclose(evidence.base_trace[:4], trace)
    np.testing.assert_allclose(evidence.base_trace[4:], trace + 1032.0)

    after = {kind: len(service.adapter.list_all(kind=kind)) for kind in ("master_ldls", "trace_map")}
    assert after == before


def test_artifact_bridge_rejects_mismatched_physical_ccd_pair(tmp_path: Path):
    solver, service, lower, _upper, _artifacts, _trace = _fixture_pair(tmp_path, EXPOSURES_A, 10.0)
    mismatched = ZipCode("999", lower.ifuid, lower.specid, "LU", lower.controller)
    with pytest.raises(ValueError, match="one physical CCD"):
        solver.load_ldls_evidence_pair(service, lower, mismatched, exposure_ids=EXPOSURES_A)


def test_artifact_bridge_can_pin_one_duplicate_group_by_master_ldls_id(tmp_path: Path):
    solver, _service_a, lower, upper, artifacts_a, _trace = _fixture_pair(tmp_path, EXPOSURES_A, 10.0)
    _solver_b, service, _lower_b, _upper_b, _artifacts_b, _trace_b = _fixture_pair(tmp_path, EXPOSURES_A, 20.0)

    evidence, selection = solver.load_ldls_evidence_pair(
        service, lower, upper, exposure_ids=EXPOSURES_A,
        lower_ldls_artifact_id=artifacts_a["LL"]["master_ldls"].id,
        upper_ldls_artifact_id=artifacts_a["LU"]["master_ldls"].id,
    )

    assert evidence.image.shape == (2064, 24)
    assert selection["lower_master_ldls"]["id"] == artifacts_a["LL"]["master_ldls"].id
    assert selection["upper_master_ldls"]["id"] == artifacts_a["LU"]["master_ldls"].id


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
