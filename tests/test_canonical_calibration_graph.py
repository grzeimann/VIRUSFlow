from __future__ import annotations

import numpy as np

from virusflow.artifacts import ArtifactService, Scope
from virusflow.core.algo_result import AlgoResult
from virusflow.core.identity import ZipCode
from virusflow.executors.planning_executor import PlanningExecutor
from virusflow.io import RawFrameData
from virusflow.planning import ReductionGraph, adapt_target, default_calibration_graph, schedule
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.mapping import default_kind_to_task


def _seed(conn, zipcode, frame_type, count, *, lamp=None, exptime=30.0):
    hour = {"zro": 0, "drk": 1, "flt": 2, "hg": 3, "cd": 4, "twi": 5, "sci": 6}[frame_type]
    for index in range(count):
        exposure_id = f"20260609T{hour:02d}{index // 60:02d}{index % 60:02d}.{index % 10}"
        conn.execute(
            "INSERT OR IGNORE INTO exposures(id, when_utc, frame_type) VALUES(?,?,?)",
            (exposure_id, "20260609", frame_type),
        )
        conn.execute(
            "INSERT INTO raw_files(exposure_id, frame_type, path, tar_member, storage_backend, amp_key) VALUES(?,?,?,?,?,?)",
            (exposure_id, frame_type, f"/{exposure_id}.fits", None, "filesystem", zipcode.key()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO exposure_details(exposure_id,exptime,pexptime,lamp) VALUES(?,?,?,?)",
            (exposure_id, exptime, exptime, lamp),
        )


class _Loader:
    def load(self, path, tar_member=None):
        yy, xx = np.indices((1032, 32))
        data = (100.0 + xx + yy).astype(float)
        header = {
            "GAIN": 1.0, "RDNOISE": 3.0, "CCDPOS": "L", "CCDHALF": "L",
            "EXPTIME": 30.0,
        }
        return RawFrameData(data, header, path, tar_member)


def test_canonical_graph_declares_separate_lamps_composed_arc_and_master_sci():
    nodes, edges = default_calibration_graph()
    by_kind = {node.kind: node for node in nodes}
    assert set(by_kind) == {
        "master_bias", "master_dark", "master_ldls", "master_hg", "master_cd",
        "master_arc", "master_twilight", "master_sci", "trace_map", "wavelength_map",
        "extracted_master_ldls_spectrum", "extracted_master_twilight_spectrum",
        "extracted_master_sci_spectrum", "within_amp_fiber_normalization",
        "amp_to_amp_normalization", "fiber_wavelength_spectral_mask",
    }
    assert by_kind["master_hg"].inputs_raw == ["cmp", "hg"]
    assert by_kind["master_cd"].inputs_raw == ["cmp", "cd"]
    assert by_kind["master_arc"].inputs_raw is None
    assert by_kind["master_arc"].inputs_artifacts == ["master_hg", "master_cd"]
    assert by_kind["master_sci"].inputs_raw == ["sci"]
    assert by_kind["extracted_master_sci_spectrum"].inputs_artifacts == [
        "master_sci", "trace_map",
    ]
    assert by_kind["extracted_master_ldls_spectrum"].inputs_artifacts == [
        "master_ldls", "trace_map",
    ]
    assert by_kind["extracted_master_twilight_spectrum"].inputs_artifacts == [
        "master_twilight", "trace_map",
    ]
    assert by_kind["within_amp_fiber_normalization"].inputs_artifacts == [
        "extracted_master_twilight_spectrum", "extracted_master_ldls_spectrum",
        "wavelength_map", "extracted_master_sci_spectrum",
    ]
    assert by_kind["amp_to_amp_normalization"].inputs_artifacts == [
        "within_amp_fiber_normalization"
    ]
    assert by_kind["amp_to_amp_normalization"].scope_mode == "calibration_build"
    assert by_kind["fiber_wavelength_spectral_mask"].inputs_artifacts == [
        "extracted_master_sci_spectrum", "wavelength_map",
    ]
    assert {(edge.src.kind, edge.dst.kind) for edge in edges} >= {
        ("master_bias", "master_dark"),
        ("master_bias", "master_ldls"),
        ("master_bias", "master_hg"),
        ("master_bias", "master_cd"),
        ("master_bias", "master_twilight"),
        ("master_bias", "master_sci"),
        ("master_bias", "trace_map"),
        ("master_bias", "wavelength_map"),
        ("master_bias", "extracted_master_sci_spectrum"),
        ("master_bias", "extracted_master_ldls_spectrum"),
        ("master_bias", "extracted_master_twilight_spectrum"),
        ("master_bias", "within_amp_fiber_normalization"),
        ("master_bias", "fiber_wavelength_spectral_mask"),
        ("master_ldls", "trace_map"),
        ("master_hg", "master_arc"),
        ("master_cd", "master_arc"),
        ("master_arc", "wavelength_map"),
        ("trace_map", "wavelength_map"),
        ("master_sci", "extracted_master_sci_spectrum"),
        ("trace_map", "extracted_master_sci_spectrum"),
        ("master_ldls", "extracted_master_ldls_spectrum"),
        ("trace_map", "extracted_master_ldls_spectrum"),
        ("master_twilight", "extracted_master_twilight_spectrum"),
        ("trace_map", "extracted_master_twilight_spectrum"),
        ("extracted_master_twilight_spectrum", "within_amp_fiber_normalization"),
        ("extracted_master_ldls_spectrum", "within_amp_fiber_normalization"),
        ("wavelength_map", "within_amp_fiber_normalization"),
        ("extracted_master_sci_spectrum", "within_amp_fiber_normalization"),
        ("within_amp_fiber_normalization", "amp_to_amp_normalization"),
        ("extracted_master_sci_spectrum", "fiber_wavelength_spectral_mask"),
        ("wavelength_map", "fiber_wavelength_spectral_mask"),
    }
    assert set(default_kind_to_task()) == set(by_kind)


def test_physical_ccd_graph_persists_response_products_components_and_lineage(tmp_path, monkeypatch):
    zipcodes = [
        ZipCode(ifuslot="020", ifuid="001", specid="001", amp=amp, controller="A")
        for amp in ("LL", "LU")
    ]
    zipcode = zipcodes[0]
    database = str(tmp_path / "canonical.sqlite3")
    db.init_db(database)
    with db.connect(database) as conn:
        for current in zipcodes:
            conn.execute(
                "INSERT INTO amplifiers(key, ifuslot, ifuid, specid, amp, controller) VALUES(?,?,?,?,?,?)",
                (current.key(), current.ifuslot, current.ifuid, current.specid, current.amp, current.controller),
            )
            for frame_type, count in (("zro", 25), ("drk", 20), ("flt", 30), ("hg", 1), ("cd", 1), ("twi", 1)):
                _seed(conn, current, frame_type, count, lamp=frame_type if frame_type in {"hg", "cd"} else None)
            _seed(conn, current, "sci", 3, exptime=700.0)

    reference_dir = tmp_path / "Fiber_Locations" / "20260609"
    reference_dir.mkdir(parents=True)
    for current in zipcodes:
        np.savetxt(
            reference_dir / f"fiber_loc_001_020_001_{current.amp}.txt",
            np.array([[5.0, 0.0], [12.0, 0.0]]),
        )

    import virusflow.tasks.calibs as calibs

    def fake_trace(*, master_ldls_array, trace_reference, zipcode):
        nx = master_ldls_array.shape[1]
        return AlgoResult(
            kind="trace", version="test-trace",
            arrays={
                "fiber_trace_map": np.repeat(np.array([[6.0], [14.0]]), nx, axis=1),
                "trace_sample_columns": np.array([4.0, 16.0, 28.0]),
                "sampled_trace_positions": np.array([[6.0, 6.0, 6.0], [14.0, 14.0, 14.0]]),
                "per_fiber_trace_residual_rms": np.array([0.0, 0.0]),
                "trace_sample_valid_mask": np.ones((2, 3), dtype=np.uint8),
                "trace_fit_residuals": np.zeros((2, 3), dtype=float),
                "per_fiber_valid_sample_count": np.full(2, 3, dtype=np.int16),
                "trace_interpolated_fiber_mask": np.zeros(2, dtype=np.uint8),
            },
        )

    def fake_wave(**kwargs):
        trace = kwargs["fiber_trace_map"]
        return AlgoResult(
            kind="wave", version="test-wave",
            arrays={
                "wavelength_map": np.broadcast_to(
                    np.linspace(3500.0, 5500.0, trace.shape[1]), trace.shape
                ).copy(),
                "per_fiber_wavelength_residual_rms": np.array([0.1, 0.2]),
                "arc_identification": np.array([[100.0, 4358.3, 4358.4, 0.1, 0.05, 0.0]]),
                "arc_candidate_evidence": np.array(
                    [[0.0, 100.0, 10.0, 1.0, 4358.3, 0.1, 0.05]]
                ),
                "arc_line_evidence": np.array(
                    [[0.0, 100.0, 4358.3, 4358.4, 0.1, 0.05, 0.0]]
                ),
                "seed_region_attempted_mask": np.ones(2, dtype=np.uint8),
                "seed_region_success_mask": np.ones(2, dtype=np.uint8),
                "seed_region_failure_code": np.ones(2, dtype=np.uint8),
                "seed_fit_coefficients": np.ones((2, 5), dtype=float),
                "interpolated_fiber_mask": np.zeros(2, dtype=np.uint8),
                "extrapolated_fiber_mask": np.zeros(2, dtype=np.uint8),
                "input_mask_indices": np.empty(0, dtype=np.int32),
                "input_mask_shape": np.asarray([1032, trace.shape[1]], dtype=np.int32),
            },
            scalars={"best_nmatch": 1, "best_rms": 0.1},
        )

    def fake_dark(*, raw_inputs, params):
        shape = np.asarray(raw_inputs[0]["data"]).shape
        return AlgoResult(
            kind="dark", version="test-dark",
            arrays={
                "master_dark": np.zeros(shape, dtype=float),
                "dark_pixel_mask": np.zeros(shape, dtype=np.uint8),
            },
            scalars={"bad_fraction": 0.0, "n_inputs": len(raw_inputs)},
        )

    monkeypatch.setattr(calibs, "fit_fiber_traces", fake_trace)
    monkeypatch.setattr(calibs, "fit_wavelength_solution", fake_wave)
    monkeypatch.setattr(calibs.DarkTask, "algorithm", staticmethod(fake_dark))

    nodes, edges = default_calibration_graph()
    graph = ReductionGraph(nodes, edges)
    scopes = [Scope(zipcode=current) for current in zipcodes]
    _, report = graph.plan(db_path=database, scopes=scopes)
    assert {target.kind for target in report.planned} == {node.kind for node in nodes}

    context = TaskContext(
        database,
        str(tmp_path / "products"),
        {"raw_frame_loader": _Loader(), "configuration_root": str(tmp_path)},
    )
    scheduled = schedule(
        targets=report.planned,
        nodes=nodes,
        edges=edges,
        kind_to_task=default_kind_to_task(),
        task_context_factory=lambda: context,
        target_adapter=adapt_target,
    )
    executor = PlanningExecutor(max_workers=1, debug=False)
    for item in scheduled:
        executor.add_task(item.id, item.task, kind=item.kind, depends_on=item.depends_on)
    results = executor.run()
    for item in scheduled:
        if item.kind.startswith("extracted_master_"):
            assert set(results[item.id]) == {item.kind, "ccd_scattered_light_model"}
        else:
            assert set(results[item.id]) == {item.kind}

    master_timing = next(
        item for item in executor.performance_report["tasks"]
        if item["task_kind"] == "master_bias"
    )
    assert master_timing["phases"]["load_raw_frames"]["count"] == 25
    assert master_timing["phases"]["base_reduction"]["count"] == 25
    assert master_timing["phases"]["combine_frames"]["count"] == 1
    assert master_timing["counters"]["frame_count"] == 25
    assert master_timing["identities"]["array_shape"] == ["1032x32"]
    assert master_timing["identities"]["combine_method"] == [
        "chunked fixed-center biweight_location + MAD"
    ]
    assert master_timing["counters"]["base_reduction_calls"] == 25
    assert master_timing["counters"]["combine_calls"] == 1

    service = ArtifactService(database)
    rows = {
        kind: service.select_best(kind=kind, scope=Scope(zipcode=zipcode), policy="latest")
        for kind in {node.kind for node in nodes} - {"amp_to_amp_normalization"}
    }
    rows["amp_to_amp_normalization"] = service.select_best(
        kind="amp_to_amp_normalization", scope=Scope(zipcode=None), policy="latest"
    )
    assert all(rows.values())
    assert {item["name"] for item in service.describe(rows["master_bias"])["components"]} == {
        "master", "per_pixel_bias_scatter"
    }
    assert {item["name"] for item in service.describe(rows["trace_map"])["components"]} == {
        "fiber_trace_map", "trace_sample_columns", "sampled_trace_positions",
        "per_fiber_trace_residual_rms", "trace_sample_valid_mask",
        "trace_fit_residuals", "per_fiber_valid_sample_count",
        "trace_interpolated_fiber_mask",
    }
    assert {item["name"] for item in service.describe(rows["wavelength_map"])["components"]} == {
        "wavelength_map", "per_fiber_wavelength_residual_rms", "arc_identification",
        "arc_candidate_evidence", "arc_line_evidence",
        "seed_region_attempted_mask", "seed_region_success_mask",
        "seed_region_failure_code", "seed_fit_coefficients",
        "interpolated_fiber_mask", "extrapolated_fiber_mask",
        "input_mask_indices", "input_mask_shape",
    }
    assert {item["name"] for item in service.describe(rows["master_sci"])["components"]} == {"master_sci"}
    for kind in ("master_ldls", "master_twilight", "master_sci"):
        description = service.describe(rows[kind])
        assert description["summary"]["algorithm_metadata"]["detector_correction_policy"] == (
            "bias_plus_exptime_scaled_dark_residual-1"
        )
        assert {item["parent_id"] for item in description["relations"]} == {
            int(rows["master_bias"]["id"]), int(rows["master_dark"]["id"]),
        }
    assert {item["name"] for item in service.describe(
        rows["extracted_master_sci_spectrum"]
    )["components"]} == {
        "spectrum", "valid_pixel_fraction", "effective_aperture_width",
        "extraction_valid", "aperture_start_row", "aperture_first_weight",
        "aperture_last_weight", "aperture_sample_mask_bits",
    }
    for kind in (
        "extracted_master_ldls_spectrum", "extracted_master_twilight_spectrum",
    ):
        assert {item["name"] for item in service.describe(rows[kind])["components"]} == {
            "spectrum", "valid_pixel_fraction", "effective_aperture_width",
            "extraction_valid", "aperture_start_row", "aperture_first_weight",
            "aperture_last_weight", "aperture_sample_mask_bits",
        }
    response_description = service.describe(rows["within_amp_fiber_normalization"])
    assert {item["name"] for item in response_description["components"]} >= {
        "raw_ratio", "normalization", "valid_mask", "common_twilight",
        "ftf_ldls", "twilight_broad_correction",
        "twilight_residual_correction", "wavelength",
        "amplifier_twilight_level",
    }
    assert response_description["summary"]["algorithm_metadata"]["fine_structure_source"] == (
        "master_ldls"
    )
    assert response_description["summary"]["algorithm_metadata"]["large_scale_anchor"] == (
        "master_twilight"
    )
    amp_description = service.describe(rows["amp_to_amp_normalization"])
    assert amp_description["scope"]["exposure_id"] is None
    assert amp_description["summary"]["algorithm_metadata"]["coverage_complete"] is True
    assert amp_description["summary"]["algorithm_metadata"]["amplifier_keys"] == [
        current.key() for current in zipcodes
    ]
    assert len(amp_description["relations"]) == len(zipcodes)
    calibration_scatter = [
        row for row in service.adapter.list_all(kind="ccd_scattered_light_model")
        if row.get("exposure_id") is None
    ]
    assert len(calibration_scatter) == 3 * len(zipcodes)
    assert {
        service.describe(row)["summary"]["calibration_input_kind"]
        for row in calibration_scatter
    } == {"master_ldls", "master_twilight", "master_sci"}
    assert {item["name"] for item in service.describe(
        rows["fiber_wavelength_spectral_mask"]
    )["components"]} == {
        "mask", "spectral_model", "normalization", "good_wavelength_solution",
    }
    extraction_description = service.describe(rows["extracted_master_sci_spectrum"])
    assert extraction_description["summary"]["algorithm_metadata"]["extraction_method"] == (
        "fractional_top_hat_aperture"
    )
    mask_description = service.describe(rows["fiber_wavelength_spectral_mask"])
    assert mask_description["summary"]["algorithm_metadata"]["normalization_mode"] == (
        "coarse_self_normalization"
    )
    assert {
        item["kind"]
        for item in mask_description["provenance"]["configuration_references"]
    } >= {
        "master_sci_spectral_mask"
    }
    arc_relations = service.describe(rows["master_arc"])["relations"]
    assert {item["parent_id"] for item in arc_relations} == {
        int(rows["master_hg"]["id"]), int(rows["master_cd"]["id"])
    }
    trace_relations = service.describe(rows["trace_map"])["relations"]
    wave_relations = service.describe(rows["wavelength_map"])["relations"]
    assert {item["parent_id"] for item in trace_relations} == {int(rows["master_ldls"]["id"])}
    assert {item["parent_id"] for item in wave_relations} == {
        int(rows["master_arc"]["id"]), int(rows["trace_map"]["id"]),
        int(rows["master_ldls"]["id"]), int(rows["master_dark"]["id"]),
    }
    extraction_relations = service.describe(
        rows["extracted_master_sci_spectrum"]
    )["relations"]
    assert {int(rows["master_sci"]["id"]), int(rows["trace_map"]["id"])} < {
        item["parent_id"] for item in extraction_relations
    }
    ldls_extraction_relations = service.describe(
        rows["extracted_master_ldls_spectrum"]
    )["relations"]
    twilight_extraction_relations = service.describe(
        rows["extracted_master_twilight_spectrum"]
    )["relations"]
    assert {int(rows["master_ldls"]["id"]), int(rows["trace_map"]["id"])} < {
        item["parent_id"] for item in ldls_extraction_relations
    }
    assert {int(rows["master_twilight"]["id"]), int(rows["trace_map"]["id"])} < {
        item["parent_id"] for item in twilight_extraction_relations
    }
    response_relations = response_description["relations"]
    assert {item["parent_id"] for item in response_relations} == {
        int(rows["extracted_master_ldls_spectrum"]["id"]),
        int(rows["extracted_master_twilight_spectrum"]["id"]),
        int(rows["extracted_master_sci_spectrum"]["id"]),
        int(rows["wavelength_map"]["id"]),
    }
    mask_relations = service.describe(
        rows["fiber_wavelength_spectral_mask"]
    )["relations"]
    assert {item["parent_id"] for item in mask_relations} == {
        int(rows["extracted_master_sci_spectrum"]["id"]),
        int(rows["wavelength_map"]["id"]),
    }

    rerun, rerun_report = graph.plan(db_path=database, scopes=scopes)
    assert rerun == []
    assert len(rerun_report.existing) == len(scheduled)

    with db.connect(database) as connection:
        connection.execute(
            """
            UPDATE qa_decisions
            SET policy_version='1'
            WHERE artifact_id=?
            """,
            (int(rows["master_bias"]["id"]),),
        )
    stale_policy_rerun, _ = graph.plan(
        db_path=database, scopes=scopes
    )
    assert [target.kind for target in stale_policy_rerun] == ["master_bias"]


def test_amp_to_amp_normalization_builds_from_majority_when_one_amplifier_is_terminal(
    tmp_path, monkeypatch
):
    zipcodes = [
        ZipCode(ifuslot="020", ifuid=f"{index:03d}", specid="001", amp=amp, controller="A")
        for index in range(1, 6)
        for amp in ("LL", "LU")
    ]
    database = str(tmp_path / "degraded-coverage.sqlite3")
    db.init_db(database)
    with db.connect(database) as conn:
        for current in zipcodes:
            conn.execute(
                "INSERT INTO amplifiers(key, ifuslot, ifuid, specid, amp, controller) VALUES(?,?,?,?,?,?)",
                (current.key(), current.ifuslot, current.ifuid, current.specid, current.amp, current.controller),
            )
            for frame_type, count in (("zro", 10), ("drk", 5), ("flt", 10), ("hg", 1), ("cd", 1), ("twi", 1)):
                _seed(conn, current, frame_type, count, lamp=frame_type if frame_type in {"hg", "cd"} else None)
            _seed(conn, current, "sci", 3, exptime=700.0)

    reference_dir = tmp_path / "Fiber_Locations" / "20260609"
    reference_dir.mkdir(parents=True)
    for current in zipcodes:
        np.savetxt(
            reference_dir / f"fiber_loc_001_020_{current.ifuid}_{current.amp}.txt",
            np.array([[5.0, 0.0], [12.0, 0.0]]),
        )

    import virusflow.tasks.calibs as calibs

    def fake_trace(*, master_ldls_array, trace_reference, zipcode):
        nx = master_ldls_array.shape[1]
        return AlgoResult(
            kind="trace", version="test-trace",
            arrays={
                "fiber_trace_map": np.repeat(np.array([[6.0], [14.0]]), nx, axis=1),
                "trace_sample_columns": np.array([4.0, 16.0, 28.0]),
                "sampled_trace_positions": np.array([[6.0, 6.0, 6.0], [14.0, 14.0, 14.0]]),
                "per_fiber_trace_residual_rms": np.array([0.0, 0.0]),
                "trace_sample_valid_mask": np.ones((2, 3), dtype=np.uint8),
                "trace_fit_residuals": np.zeros((2, 3), dtype=float),
                "per_fiber_valid_sample_count": np.full(2, 3, dtype=np.int16),
                "trace_interpolated_fiber_mask": np.zeros(2, dtype=np.uint8),
            },
        )

    def fake_wave(**kwargs):
        trace = kwargs["fiber_trace_map"]
        return AlgoResult(
            kind="wave", version="test-wave",
            arrays={
                "wavelength_map": np.broadcast_to(
                    np.linspace(3500.0, 5500.0, trace.shape[1]), trace.shape
                ).copy(),
                "per_fiber_wavelength_residual_rms": np.array([0.1, 0.2]),
                "arc_identification": np.array([[100.0, 4358.3, 4358.4, 0.1, 0.05, 0.0]]),
                "arc_candidate_evidence": np.array(
                    [[0.0, 100.0, 10.0, 1.0, 4358.3, 0.1, 0.05]]
                ),
                "arc_line_evidence": np.array(
                    [[0.0, 100.0, 4358.3, 4358.4, 0.1, 0.05, 0.0]]
                ),
                "seed_region_attempted_mask": np.ones(2, dtype=np.uint8),
                "seed_region_success_mask": np.ones(2, dtype=np.uint8),
                "seed_region_failure_code": np.ones(2, dtype=np.uint8),
                "seed_fit_coefficients": np.ones((2, 5), dtype=float),
                "interpolated_fiber_mask": np.zeros(2, dtype=np.uint8),
                "extrapolated_fiber_mask": np.zeros(2, dtype=np.uint8),
                "input_mask_indices": np.empty(0, dtype=np.int32),
                "input_mask_shape": np.asarray([1032, trace.shape[1]], dtype=np.int32),
            },
            scalars={"best_nmatch": 1, "best_rms": 0.1},
        )

    def fake_dark(*, raw_inputs, params):
        shape = np.asarray(raw_inputs[0]["data"]).shape
        return AlgoResult(
            kind="dark", version="test-dark",
            arrays={
                "master_dark": np.zeros(shape, dtype=float),
                "dark_pixel_mask": np.zeros(shape, dtype=np.uint8),
            },
            scalars={"bad_fraction": 0.0, "n_inputs": len(raw_inputs)},
        )

    monkeypatch.setattr(calibs, "fit_fiber_traces", fake_trace)
    monkeypatch.setattr(calibs, "fit_wavelength_solution", fake_wave)
    monkeypatch.setattr(calibs.DarkTask, "algorithm", staticmethod(fake_dark))

    nodes, edges = default_calibration_graph()
    graph = ReductionGraph(nodes, edges)
    scopes = [Scope(zipcode=current) for current in zipcodes]
    _, report = graph.plan(db_path=database, scopes=scopes)

    context = TaskContext(
        database,
        str(tmp_path / "products"),
        {"raw_frame_loader": _Loader(), "configuration_root": str(tmp_path)},
    )
    scheduled = schedule(
        targets=report.planned,
        nodes=nodes,
        edges=edges,
        kind_to_task=default_kind_to_task(),
        task_context_factory=lambda: context,
        target_adapter=adapt_target,
    )
    executor = PlanningExecutor(max_workers=1, debug=False)
    for item in scheduled:
        executor.add_task(item.id, item.task, kind=item.kind, depends_on=item.depends_on)
    executor.run()

    service = ArtifactService(database)
    amp_row = service.select_best(
        kind="amp_to_amp_normalization", scope=Scope(zipcode=None), policy="latest"
    )
    assert amp_row is not None
    amp_description = service.describe(amp_row)
    assert amp_description["summary"]["algorithm_metadata"]["coverage_complete"] is True
    assert amp_description["summary"]["algorithm_metadata"]["amplifier_keys"] == [
        current.key() for current in zipcodes
    ]

    bad_zipcode = zipcodes[0]
    bad_row = service.select_best(
        kind="within_amp_fiber_normalization", scope=Scope(zipcode=bad_zipcode), policy="latest"
    )
    assert bad_row is not None
    policy_version = service.diagnostics.policy_version_for("within_amp_fiber_normalization")
    db.save_qa_bundle(
        int(bad_row["id"]),
        facts={"read_noise": {"value": 999.0}},
        status="fail",
        usability="unusable",
        policy_version=policy_version,
        db_path=database,
    )

    replanned, replan_report = graph.plan(db_path=database, scopes=scopes)
    degraded_target = next(
        target for target in replanned if target.kind == "amp_to_amp_normalization"
    )
    assert degraded_target.group.metadata["coverage_complete"] is False
    assert degraded_target.group.metadata["excluded_amplifier_keys"] == [bad_zipcode.key()]
    assert bad_zipcode.key() not in degraded_target.group.metadata["amplifier_keys"]
    assert len(degraded_target.group.metadata["amplifier_keys"]) == 9

    replan_context = TaskContext(
        database,
        str(tmp_path / "products"),
        {"raw_frame_loader": _Loader(), "configuration_root": str(tmp_path)},
    )
    replan_scheduled = schedule(
        targets=replanned,
        nodes=nodes,
        edges=edges,
        kind_to_task=default_kind_to_task(),
        task_context_factory=lambda: replan_context,
        target_adapter=adapt_target,
    )
    replan_executor = PlanningExecutor(max_workers=1, debug=False)
    for item in replan_scheduled:
        replan_executor.add_task(item.id, item.task, kind=item.kind, depends_on=item.depends_on)
    replan_executor.run()

    degraded_row = service.select_best(
        kind="amp_to_amp_normalization", scope=Scope(zipcode=None), policy="latest"
    )
    assert int(degraded_row["id"]) != int(amp_row["id"])
    degraded_description = service.describe(degraded_row)
    assert degraded_description["summary"]["algorithm_metadata"]["coverage_complete"] is False
    assert degraded_description["summary"]["algorithm_metadata"]["excluded_amplifier_keys"] == [
        bad_zipcode.key()
    ]
    assert len(degraded_description["relations"]) == 9


def test_critical_read_noise_blocks_calibration_branches_before_wavelength(
    tmp_path, monkeypatch
):
    zipcode = ZipCode(ifuslot="020", ifuid="001", specid="001", amp="LL", controller="A")
    database = str(tmp_path / "critical-read-noise.sqlite3")
    db.init_db(database)
    with db.connect(database) as conn:
        conn.execute(
            "INSERT INTO amplifiers(key, ifuslot, ifuid, specid, amp, controller) VALUES(?,?,?,?,?,?)",
            (
                zipcode.key(), zipcode.ifuslot, zipcode.ifuid,
                zipcode.specid, zipcode.amp, zipcode.controller,
            ),
        )
        for frame_type, count in (
            ("zro", 25), ("drk", 1), ("flt", 3), ("hg", 1),
            ("cd", 1), ("twi", 1),
        ):
            _seed(
                conn, zipcode, frame_type, count,
                lamp=frame_type if frame_type in {"hg", "cd"} else None,
            )
        _seed(conn, zipcode, "sci", 3, exptime=700.0)

    import virusflow.tasks.calibs as calibs

    def high_read_noise_bias(*, raw_inputs, params):
        shape = np.asarray(raw_inputs[0]["data"]).shape
        return AlgoResult(
            kind="bias",
            version="test-critical-read-noise",
            arrays={
                "master": np.zeros(shape, dtype=float),
                "per_pixel_bias_scatter": np.full(shape, 6.5, dtype=float),
            },
            scalars={"read_noise": 6.5, "n_inputs": len(raw_inputs)},
        )

    monkeypatch.setattr(calibs.BiasTask, "algorithm", staticmethod(high_read_noise_bias))

    nodes, edges = default_calibration_graph()
    graph = ReductionGraph(nodes, edges)
    _, report = graph.plan(db_path=database, scopes=[Scope(zipcode=zipcode)])
    context = TaskContext(
        database,
        str(tmp_path / "products"),
        {"raw_frame_loader": _Loader(), "configuration_root": str(tmp_path)},
    )
    scheduled = schedule(
        targets=report.planned,
        nodes=nodes,
        edges=edges,
        kind_to_task=default_kind_to_task(),
        task_context_factory=lambda: context,
        target_adapter=adapt_target,
    )
    executor = PlanningExecutor(
        max_workers=4, debug=False, progress=False, raise_on_failure=False
    )
    for item in scheduled:
        executor.add_task(item.id, item.task, kind=item.kind, depends_on=item.depends_on)
    executor.run()

    assert executor.execution_stats["per_kind"]["master_bias"]["failed"] == 1
    assert executor.execution_stats["per_kind"]["wavelength_map"]["failed"] == 0
    assert executor.execution_stats["per_kind"]["wavelength_map"]["blocked"] == 1
    assert executor.execution_stats["blocked"] == len(scheduled) - 1

    service = ArtifactService(database)
    bias = service.select_best(
        kind="master_bias", scope=Scope(zipcode=zipcode), policy="latest"
    )
    assert bias is not None
    qa = service.adapter.get_qa_bundle(int(bias["id"]))
    assert qa["status"] == "fail"
    assert qa["usability"] == "unusable"
    assert qa["metrics"]["read_noise"] == 6.5
    assert service.adapter.list_all(kind="wavelength_map") == []

    rerun, rerun_report = graph.plan(
        db_path=database, scopes=[Scope(zipcode=zipcode)]
    )
    assert rerun == []
    assert len(rerun_report.terminal) == len(scheduled)
    assert rerun_report.reasons[
        next(key for key in rerun_report.reasons if key.startswith("master_bias:"))
    ].startswith("already_recorded_terminal_task_failure:")
    assert all(
        reason == "already_registered_terminal_qa_failure"
        or reason.startswith("already_recorded_terminal_task_failure:")
        or reason.startswith("blocked_by_terminal_qa_failure:")
        for reason in rerun_report.reasons.values()
    )

    forced, forced_report = graph.plan(
        db_path=database, scopes=[Scope(zipcode=zipcode)], force_replan=True
    )
    assert len(forced) == len(scheduled)
    assert forced_report.terminal == []
