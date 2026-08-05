from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits

from virusflow.artifacts import ArtifactService, Scope
from virusflow.artifacts.models import ConfigurationReference
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.config import ConfigurationService
from virusflow.core.algo_result import AlgoResult
from virusflow.core.identity import ZipCode
from virusflow.core.scientific_metadata import aggregate_scientific_metadata
from virusflow.io import RawFrameData
from virusflow.performance import PerformanceRun
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.calibs import BiasTask, TraceTask, WaveTask


ZIP = ZipCode("020", "001", "001", "LL", "A")


def _publication(service: ArtifactService, root: Path) -> DefaultPublicationService:
    return DefaultPublicationService(
        svc=service,
        policy=DefaultPersistencePolicy(),
        base_dir=str(root / "products"),
    )


def _publish(
    service: ArtifactService,
    root: Path,
    *,
    kind: str,
    components: dict[str, np.ndarray],
    scientific_metadata: dict,
    parents=(),
):
    request = ArtifactRequest(
        kind=kind,
        components={
            name: LogicalComponent(
                name,
                "array1d" if np.asarray(value).ndim == 1 else "array2d",
                value,
            )
            for name, value in components.items()
        },
        scientific_metadata=scientific_metadata,
        scope=Scope(ZIP),
        parents=list(parents),
    )
    context = PublicationContext("fixture", "1", "fixture", "1", {}, [], {})
    return _publication(service, root).publish([request], context)[0]


def test_raw_ingestion_normalizes_all_scientific_header_fields(tmp_path: Path):
    database = tmp_path / "raw.sqlite3"
    valid_path = tmp_path / "20260724T010000.0_020LL_sci.fits"
    valid_header = fits.Header({
        "IFUID": "001",
        "SPECID": "001",
        "CONTID": "A",
        "DATE": "2026-07-24T01:00:00.250000",
        "AMBTEMP": 12.5,
        "HUMIDITY": 43.2,
        "PRESSURE": 798.4,
        "QPROG": "P-001",
        "OBJECT": "science_target",
        "RHO_STRT": 1.0,
        "THE_STRT": 2.0,
        "PHI_STRT": 3.0,
        "X_STRT": 4.0,
        "Y_STRT": 5.0,
    })
    fits.PrimaryHDU(np.ones((2, 2)), header=valid_header).writeto(valid_path)
    db.register_raw_file(str(valid_path), db_path=str(database))
    raw_id = db.list_raw_file_rows(
        "20260724T010000.0", db_path=str(database)
    )[0][0]
    values = db.list_raw_scientific_metadata([raw_id], db_path=str(database))[0]
    assert values["observation_time"] == "2026-07-24T01:00:00.250000"
    assert values["ambient_temperature"] == 12.5
    assert values["humidity"] == 43.2
    assert values["pressure"] == 798.4
    assert values["program_id"] == "P-001"
    assert values["object"] == "science_target"
    assert [values[field] for field in (
        "rho_start", "theta_start", "phi_start", "x_start", "y_start"
    )] == [1.0, 2.0, 3.0, 4.0, 5.0]

    invalid_path = tmp_path / "20260724T020000.0_020LL_sci.fits"
    invalid_header = fits.Header({
        "IFUID": "001",
        "SPECID": "001",
        "CONTID": "A",
        "DATE": "not-a-time",
        "AMBTEMP": "bad",
        "HUMIDITY": "nan",
        "PRESSURE": "inf",
        "QPROG": " ",
        "OBJECT": "",
        "RHO_STRT": "bad",
        "THE_STRT": "nan",
        "PHI_STRT": "-inf",
        "X_STRT": "",
    })
    fits.PrimaryHDU(np.ones((2, 2)), header=invalid_header).writeto(invalid_path)
    db.register_raw_file(str(invalid_path), db_path=str(database))
    invalid_id = db.list_raw_file_rows(
        "20260724T020000.0", db_path=str(database)
    )[0][0]
    invalid = db.list_raw_scientific_metadata(
        [invalid_id], db_path=str(database)
    )[0]
    assert all(invalid[field] is None for field in (
        "observation_time", "ambient_temperature", "humidity", "pressure",
        "program_id", "object", "rho_start", "theta_start", "phi_start",
        "x_start", "y_start",
    ))


def test_composite_aggregation_means_consensus_conflicts_and_null_tracker():
    common = aggregate_scientific_metadata([
        {
            "observation_time": "2026-07-24T01:00:00",
            "ambient_temperature": 10.0,
            "humidity": 40.0,
            "pressure": 790.0,
            "program_id": "P1",
            "object": "LDLS",
            "rho_start": 1.0,
        },
        {
            "observation_time": "2026-07-24T03:00:00",
            "ambient_temperature": float("nan"),
            "humidity": 44.0,
            "pressure": 798.0,
            "program_id": "P1",
            "object": "LDLS",
            "rho_start": 9.0,
        },
    ])
    assert common["observation_time"] == datetime(2026, 7, 24, 2)
    assert common["ambient_temperature"] == 10.0
    assert common["humidity"] == 42.0
    assert common["pressure"] == 794.0
    assert common["program_id"] == "P1"
    assert common["object"] == "LDLS"
    assert all(common[field] is None for field in (
        "rho_start", "theta_start", "phi_start", "x_start", "y_start"
    ))

    conflicting = aggregate_scientific_metadata([
        {"program_id": "P1", "object": "Hg"},
        {"program_id": "P2", "object": "Cd"},
        {"program_id": None, "object": None},
    ])
    assert conflicting["program_id"] is None
    assert conflicting["object"] is None


def test_master_task_publishes_aggregate_selected_raw_state(tmp_path: Path):
    database = str(tmp_path / "master.sqlite3")
    db.init_db(database)
    with db.connect(database) as connection:
        connection.execute(
            "INSERT INTO amplifiers(key,ifuslot,ifuid,specid,amp,controller) "
            "VALUES(?,?,?,?,?,?)",
            (ZIP.key(), *ZIP.as_tuple()),
        )
        for index, (instant, temperature, humidity, pressure) in enumerate((
            ("2026-06-09T01:00:00", 10.0, 40.0, 790.0),
            ("2026-06-09T03:00:00", 14.0, 44.0, 798.0),
        )):
            exposure_id = f"20260609T0{index + 1}0000.0"
            connection.execute(
                "INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)",
                (exposure_id, "20260609", "zro"),
            )
            connection.execute(
                """
                INSERT INTO raw_files(
                    exposure_id,frame_type,path,storage_backend,amp_key,
                    observation_time,ambient_temperature,humidity,pressure,
                    program_id,object,rho_start,theta_start,phi_start,x_start,y_start
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    exposure_id, "zro", f"/raw/{index}.fits", "filesystem", ZIP.key(),
                    instant, temperature, humidity, pressure, "P1", "bias",
                    1.0, 2.0, 3.0, 4.0, 5.0,
                ),
            )

    class Loader:
        def load(self, path, tar_member=None, **kwargs):
            return RawFrameData(
                np.ones((8, 8), dtype=float),
                {"GAIN": 1.0, "RDNOISE": 3.0, "CCDPOS": "L", "CCDHALF": "L"},
                path,
                tar_member,
            )

    target = type("Target", (), {
        "zipcode": ZIP,
        "start_date": "20260609",
        "end_date": "20260609",
        "start_dt": datetime(2026, 6, 9),
        "end_dt": datetime(2026, 6, 10),
    })()
    context = TaskContext(
        database,
        str(tmp_path / "products"),
        {"raw_frame_loader": Loader()},
    )
    artifact = BiasTask(context, target=target).run({})["master_bias"]
    scientific = ArtifactService(database).get_scientific_metadata(artifact.id)
    assert scientific["observation_time"] == datetime(2026, 6, 9, 2)
    assert scientific["ambient_temperature"] == 12.0
    assert scientific["humidity"] == 42.0
    assert scientific["pressure"] == 794.0
    assert scientific["program_id"] == "P1"
    assert scientific["object"] == "bias"
    assert all(scientific[field] is None for field in (
        "rho_start", "theta_start", "phi_start", "x_start", "y_start"
    ))


def test_trace_and_wavelength_inherit_their_measurement_evidence_state(
    tmp_path: Path, monkeypatch
):
    database = str(tmp_path / "calibration.sqlite3")
    service = ArtifactService(database)
    image = np.ones((16, 20), dtype=float)
    state = {
        "observation_time": datetime(2026, 6, 9, 2),
        "ambient_temperature": 12.0,
        "humidity": 42.0,
        "pressure": 794.0,
        "program_id": "CAL",
        "object": "lamp",
    }
    ldls = _publish(
        service,
        tmp_path,
        kind="master_ldls",
        components={
            "master_ldls": image,
            "flat_response_mask": np.zeros_like(image, dtype=np.uint8),
        },
        scientific_metadata=state,
    )

    def fake_trace(**kwargs):
        nx = kwargs["master_ldls_array"].shape[1]
        return AlgoResult(
            kind="trace",
            version="test",
            arrays={
                "fiber_trace_map": np.broadcast_to(
                    np.asarray([[5.0], [10.0]]), (2, nx)
                ).copy(),
                "trace_sample_columns": np.asarray([0.0, nx - 1.0]),
                "sampled_trace_positions": np.asarray([[5.0, 5.0], [10.0, 10.0]]),
                "per_fiber_trace_residual_rms": np.zeros(2),
                "trace_sample_valid_mask": np.ones((2, 2), dtype=np.uint8),
                "trace_fit_residuals": np.zeros((2, 2)),
                "per_fiber_valid_sample_count": np.full(2, 2),
                "trace_interpolated_fiber_mask": np.zeros(2, dtype=np.uint8),
            },
        )

    monkeypatch.setattr("virusflow.tasks.calibs.fit_fiber_traces", fake_trace)
    monkeypatch.setattr(
        ConfigurationService,
        "resolve_trace_reference",
        lambda self, **kwargs: (
            np.asarray([[5.0, 0.0], [10.0, 0.0]]),
            ConfigurationReference("trace_reference", "test"),
        ),
    )
    target = type("Target", (), {
        "zipcode": ZIP,
        "start_date": "20260609",
        "end_date": "20260609",
    })()
    context = TaskContext(database, str(tmp_path / "products"), {})
    trace_task = TraceTask(context, target=target)
    trace_task.configuration_references = lambda: []
    trace = trace_task.run({"ldls": {"master_ldls": ldls}})["trace_map"]
    trace_state = service.get_scientific_metadata(trace.id)
    assert {
        field: trace_state[field]
        for field in (
            "observation_time", "ambient_temperature", "humidity", "pressure"
        )
    } == {
        "observation_time": state["observation_time"],
        "ambient_temperature": 12.0,
        "humidity": 42.0,
        "pressure": 794.0,
    }
    assert trace_state["rho_start"] is None

    arc = _publish(
        service,
        tmp_path,
        kind="master_arc",
        components={"master_arc": image},
        scientific_metadata=state,
    )

    def fake_wave(**kwargs):
        trace_map = kwargs["fiber_trace_map"]
        return AlgoResult(
            kind="wave",
            version="test",
            arrays={
                "wavelength_map": np.broadcast_to(
                    np.linspace(3500.0, 5500.0, trace_map.shape[1]), trace_map.shape
                ).copy(),
                "per_fiber_wavelength_residual_rms": np.asarray([0.1, 0.2]),
                "arc_identification": np.asarray(
                    [[1.0, 4358.3, 4358.4, 0.1, 0.05, 0.0]]
                ),
                "arc_candidate_evidence": np.asarray(
                    [[0.0, 1.0, 100.0, 1.0, 4358.3, 0.1, 0.05]]
                ),
                "arc_line_evidence": np.asarray(
                    [[0.0, 1.0, 4358.3, 4358.4, 0.1, 0.05, 0.0]]
                ),
                "seed_region_attempted_mask": np.asarray([1], dtype=np.uint8),
                "seed_region_success_mask": np.asarray([1], dtype=np.uint8),
                "seed_region_failure_code": np.asarray([0], dtype=np.uint8),
                "seed_fit_coefficients": np.asarray([[3500.0, 100.0]]),
                "interpolated_fiber_mask": np.zeros(2, dtype=np.uint8),
                "extrapolated_fiber_mask": np.zeros(2, dtype=np.uint8),
                "input_mask_indices": np.asarray([], dtype=np.int32),
                "input_mask_shape": np.asarray([0, 0], dtype=np.int32),
            },
            scalars={"best_nmatch": 1, "best_rms": 0.1},
        )

    monkeypatch.setattr("virusflow.tasks.calibs.fit_wavelength_solution", fake_wave)
    wave_task = WaveTask(context, target=target)
    wave_task.configuration_references = lambda: []
    wavelength = wave_task.run({
        "arc": {"master_arc": arc},
        "trace": {"trace_map": trace},
    })["wavelength_map"]
    wave_state = service.get_scientific_metadata(wavelength.id)
    assert {
        field: wave_state[field]
        for field in (
            "observation_time", "ambient_temperature", "humidity", "pressure"
        )
    } == {
        "observation_time": state["observation_time"],
        "ambient_temperature": 12.0,
        "humidity": 42.0,
        "pressure": 794.0,
    }
    assert wave_state["rho_start"] is None


def test_lightweight_bulk_query_filters_in_database_without_n_plus_one(
    tmp_path: Path, monkeypatch
):
    database = str(tmp_path / "query.sqlite3")
    service = ArtifactService(database)
    base = _publish(
        service,
        tmp_path,
        kind="master_ldls",
        components={
            "master_ldls": np.ones((3, 4)),
            "flat_response_mask": np.zeros((3, 4), dtype=np.uint8),
        },
        scientific_metadata={},
    )
    artifacts = []
    for day in range(1, 5):
        artifact = _publish(
            service,
            tmp_path,
            kind="trace_map",
            components={
                "fiber_trace_map": np.ones((2, 4)),
                "trace_sample_columns": np.asarray([0.0, 3.0]),
                "sampled_trace_positions": np.ones((2, 2)),
                "per_fiber_trace_residual_rms": np.zeros(2),
                "trace_sample_valid_mask": np.ones((2, 2), dtype=np.uint8),
                "trace_fit_residuals": np.zeros((2, 2)),
                "per_fiber_valid_sample_count": np.full(2, 2),
                "trace_interpolated_fiber_mask": np.zeros(2, dtype=np.uint8),
            },
            parents=[base.id],
            scientific_metadata={
                "observation_time": datetime(2026, 1, day, 12),
                "ambient_temperature": 9.0 + day,
                "humidity": 40.0 + day,
                "pressure": 790.0 + day,
                "program_id": "P1",
                "object": "LDLS",
            },
        )
        service.adapter.set_qa_bundle(
            artifact.id,
            facts={},
            status="pass",
            usability="usable",
            policy_version="test",
        )
        artifacts.append(artifact)

    monkeypatch.setattr(
        service,
        "load_component",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("lightweight query opened component storage")
        ),
    )
    monkeypatch.setattr(
        db,
        "get_artifact_details",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("lightweight query performed per-artifact detail lookup")
        ),
    )
    monkeypatch.setattr(
        db,
        "get_artifact_scientific_metadata",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("lightweight query performed per-artifact metadata lookup")
        ),
    )

    run = PerformanceRun(workers=1)
    run.mark_queued("query")
    timing, token = run.begin_task("query", "query", "summaries", "main", 1)
    found = service.find_artifacts(
        kind="trace_map",
        hardware_scope=ZIP,
        observation_time=(
            datetime(2026, 1, 2),
            datetime(2026, 1, 4),
        ),
        ambient_temperature=(11.5, 12.5),
    )
    run.end_task(timing, token, "succeeded")

    assert len(found) == 1
    summary = found[0]
    assert summary["artifact_id"] == artifacts[2].id
    assert summary["qa_status"] == "pass"
    assert summary["usability"] == "usable"
    assert summary["scope"]["hardware_scope"] == ZIP.key()
    assert summary["scope"]["hardware_identity"] == {
        "ifuslot": ZIP.ifuslot,
        "ifuid": ZIP.ifuid,
        "specid": ZIP.specid,
        "amp": ZIP.amp,
        "controller": ZIP.controller,
    }
    assert summary["scientific_metadata"]["humidity"] == 43.0
    assert summary["scientific_metadata"]["pressure"] == 793.0
    assert summary["scientific_metadata"]["program_id"] == "P1"
    assert summary["scientific_metadata"]["object"] == "LDLS"
    assert summary["parent_ids"] == [base.id]
    assert set(summary["component_names"]) == {
        "fiber_trace_map",
        "trace_sample_columns",
        "sampled_trace_positions",
        "per_fiber_trace_residual_rms",
        "trace_sample_valid_mask",
        "trace_fit_residuals",
        "per_fiber_valid_sample_count",
        "trace_interpolated_fiber_mask",
    }
    artifact_selects = [
        event for event in timing.database_queries
        if event["operation"] == "WITH"
    ]
    assert len(artifact_selects) == 1

    with db.connect(database) as connection:
        artifact_count = connection.execute(
            "SELECT count(*) FROM artifacts"
        ).fetchone()[0]
        scientific_count = connection.execute(
            "SELECT count(*) FROM artifact_scientific_metadata"
        ).fetchone()[0]
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(artifact_scientific_metadata)"
            )
        }
        indexes = {
            row[1] for row in connection.execute(
                "PRAGMA index_list(artifact_scientific_metadata)"
            )
        }
    assert scientific_count == artifact_count
    assert {
        "artifact_id", "observation_time", "ambient_temperature", "humidity",
        "pressure", "program_id", "object", "rho_start", "theta_start",
        "phi_start", "x_start", "y_start",
    } <= columns
    assert "artifact_scientific_metadata_observation_time" in indexes
    assert "artifact_scientific_metadata_ambient_temperature" in indexes
