from __future__ import annotations

import numpy as np

from virusflow.algorithms.observation import (
    assign_nominal_dithers,
    dither_coverage_map,
    refine_relative_offsets,
)


PATTERN = np.asarray([[0.0, 0.0], [1.27, 0.73], [0.0, 1.46]])


def test_incomplete_extra_ambiguous_and_repeated_members_remain_explicit():
    incomplete = assign_nominal_dithers(["a", "b"], [0, 1], PATTERN)
    assert not incomplete.complete and incomplete.extra_count == 0
    assert incomplete.assignments[:, 2].tolist() == [0, 1]

    extra = assign_nominal_dithers(["a", "b", "c", "d"], [0, 1, 2, 3], PATTERN)
    assert extra.extra_count == 1
    assert extra.assignments[3, 2] == -1
    assert np.isnan(extra.assignments[3, 3:5]).all()

    ambiguous = assign_nominal_dithers(["a", "b", "c"], [0, 0, np.nan], PATTERN)
    assert ambiguous.ambiguous
    assert np.all(ambiguous.assignments[:, 7] == 1)

    repeated = assign_nominal_dithers(["a", "a", "c"], [0, 1, 2], PATTERN)
    assert repeated.duplicate_count == 1
    assert repeated.assignments[:2, 5].tolist() == [1, 1]


def test_parallel_members_are_not_assigned_a_standard_dither_from_count_alone():
    parallel = assign_nominal_dithers(
        ["a", "b", "c"], [0, 1, 2], PATTERN, dither_mode="none"
    )
    assert parallel.valid and not parallel.complete and parallel.dither_mode == "none"
    assert parallel.assignments[:, 2].tolist() == [-1, -1, -1]
    np.testing.assert_array_equal(parallel.assignments[:, 3:5], 0.0)


def test_nominal_and_refined_offsets_are_separate_with_explicit_fallback():
    dec0 = 30.0
    params = np.asarray([
        [200.0, dec0, 180.0],
        [200.0 + 1.5 / np.cos(np.deg2rad(dec0)) / 3600, dec0 + 0.5 / 3600, 180.0],
        [201.0, 31.0, 180.0],
    ])
    measured, residual, success = refine_relative_offsets(PATTERN, params, [1, 1, 0])
    np.testing.assert_allclose(measured[1], [1.5, 0.5], atol=1e-6)
    np.testing.assert_allclose(residual[1], [0.23, -0.23], atol=1e-6)
    np.testing.assert_allclose(measured[2], PATTERN[2])
    assert np.isnan(residual[2]).all()
    assert success.tolist() == [1, 1, 0]


def test_coverage_retains_holes_and_duplicated_samples():
    fibers = np.asarray([[0.0, 0.0], [2.0, 0.0]])
    coverage, x, y = dither_coverage_map(fibers, [[0, 0], [0.5, 0]], grid_step_arcsec=0.25)
    assert coverage.shape == (y.size, x.size)
    assert np.any(coverage == 0)
    assert np.any(coverage > 1)
