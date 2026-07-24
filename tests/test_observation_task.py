from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits

from virusflow.artifacts import ArtifactService, Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.planning.targets import ObservationTarget
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.observation import ObservationTask
from virusflow.algorithms.exposure import CalibratedFiberState
from virusflow.ontology.scopes import PhysicalScope


def _publish(service, root, kind, exposure_id, components, at, summaries=None):
    request = ArtifactRequest(
        kind=kind,
        components={
            name: LogicalComponent(name, "array1d" if np.asarray(value).ndim == 1 else "array2d", value)
            for name, value in components.items()
        },
        summaries=dict(summaries or {}),
        scope=Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.EXPOSURE),
        validity=Validity(at, at, "fixture"),
    )
    publisher = DefaultPublicationService(svc=service, policy=DefaultPersistencePolicy(), base_dir=str(root))
    return publisher.publish([request], PublicationContext("fixture", "1", "fixture", "1", {}, [], {}))[0]


def test_observation_task_preserves_atomic_exposures_registration_coverage_and_queries(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    service = ArtifactService(str(database))
    exposure_ids = ("20260609T031649.6", "20260609T031859.3", "20260609T032112.2")
    dec0 = -8.5
    nominal = np.asarray([[0.0, 0.0], [1.27, 0.73], [0.0, 1.46]])
    calibrated_states = []
    for index, exposure_id in enumerate(exposure_ids):
        path = tmp_path / f"{exposure_id}.fits"
        header = fits.Header({
            "IFUSLOT": 60, "IFUID": "003", "SPECID": 206, "CCDPOS": "L", "CCDHALF": "L",
            "AMPNAME": "XX", "CONTID": "S/N 0039", "OBJECT": "target", "OBSID": 6,
            "QRA": "13:30:00", "QDEC": "-08:30:00", "PARANGLE": 180.0, "EXPTIME": 67.4,
            "SEEING": 1.5 + 0.1 * index, "TRANSPAR": 0.9 - 0.05 * index,
        })
        fits.PrimaryHDU(np.ones((4, 4), dtype=np.float32), header=header).writeto(path)
        with db.connect(str(database)) as connection:
            connection.execute("INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)", (exposure_id, exposure_id, "sci"))
            connection.execute(
                "INSERT OR IGNORE INTO amplifiers(key,ifuslot,ifuid,specid,amp,controller) VALUES(?,?,?,?,?,?)",
                ("060+003+206+LL+S/N 0039", "060", "003", "206", "LL", "S/N 0039"),
            )
            connection.execute(
                "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend,amp_key) VALUES(?,?,?,?,?,?)",
                (exposure_id, "sci", str(path), None, "filesystem", "060+003+206+LL+S/N 0039"),
            )
        at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
        failed = 1 if index == 1 else 0
        _publish(
            service, tmp_path, "exposure_completion_manifest", exposure_id,
            {"coverage": np.asarray([[1, 1, 1, int(not failed), int(not failed)]]), "amplifier_identity": np.asarray([[60, 206, 0]])},
            at,
            {"raw_amplifier_count": 1, "reduced_amplifier_count": 1, "extracted_amplifier_count": int(not failed),
             "failed_or_missing_amplifier_count": failed},
        )
        ra = 202.5 + nominal[index, 0] / np.cos(np.deg2rad(dec0)) / 3600
        dec = dec0 + nominal[index, 1] / 3600
        astrometry_product = _publish(
            service, tmp_path, "final_astrometry", exposure_id,
            {"parameters": np.asarray([ra, dec, 180.0, 88.45]), "fit_evidence": np.asarray([0.0, 0.0, 0.0])},
            at, {"refined": 1, "accepted_match_count": 5},
        )
        calibrated_states.append(CalibratedFiberState(
            exposure_id=exposure_id,
            flux=np.full((2, 4), (index + 1) * 1e-17, dtype=np.float32),
            variance=np.full((2, 4), 4e-34, dtype=np.float32),
            mask=np.zeros((2, 4), dtype=np.uint16),
            wavelength=np.tile(np.linspace(3500, 3503, 4, dtype=np.float32), (2, 1)),
            fiber_identity=np.asarray([[index, 0, 60, 206, 0], [index, 1, 60, 206, 0]], dtype=np.int32),
            sky_coordinates=np.asarray([[202.5, -8.5], [202.5001, -8.5001]], dtype=np.float64),
            focal_plane_coordinates=np.asarray([[0, 0], [1, 1]], dtype=np.float32),
            model_artifact_ids=(int(astrometry_product.id),),
            metadata={"fixture": True},
        ))

    context = TaskContext(str(database), str(tmp_path / "artifacts"), {
        "configuration_root": str(Path.cwd()), "calibrated_fiber_states": calibrated_states,
    })
    target = ObservationTarget("20260609-OBSID6", "20260609-OBSID6-DITHER", exposure_ids)
    result = ObservationTask(context, target=target).run({})
    assert len(result["exposure_states"]) == 3
    registration = service.describe(result["dither_registration"].id)
    assert registration["summary"]["registered_exposure_count"] == 3
    residual = service.load_component(result["dither_registration"].id, "registration_residuals")["data"]
    np.testing.assert_allclose(residual, 0.0, atol=2e-4)
    coverage = service.load_component(result["dither_coverage_map"].id, "coverage")["data"]
    assert np.any(coverage == 0) and np.any(coverage > 1)
    assert "calibrated_fiber_observation" in result
    final = service.describe(result["calibrated_fiber_observation"].id)
    assert final["summary"]["exposure_count"] == 3
    assert final["summary"]["fiber_count"] == 6
    stored_flux = service.load_component(result["calibrated_fiber_observation"].id, "flux")
    assert stored_flux["data"].dtype == np.float32
    assert np.nanmedian(stored_flux["stored_data"]) == 2.0
    assert stored_flux["header"]["BUNIT"].startswith("1e-17")
    assert len(service.query_observation(target.observation_id)) == 9
    assert len(service.query_dither_set(target.dither_set_id)) == 9
    assert len(service.query_observation_set([target.observation_id], kind="observation_summary")) == 1
    for artifact in (*result["exposure_states"], *(value for key, value in result.items() if key != "exposure_states")):
        for component in service.describe(artifact.id)["components"]:
            service.load_component(artifact.id, component["name"], verify_checksum=True)


def test_observation_task_repeated_identity_is_retained_as_degraded(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    exposure_id = "20260609T031649.6"
    path = tmp_path / "repeat.fits"
    fits.PrimaryHDU(np.ones((4, 4)), header=fits.Header({
        "IFUSLOT": 60, "IFUID": "003", "SPECID": 206, "CCDPOS": "L", "CCDHALF": "L", "AMPNAME": "XX",
        "CONTID": "S/N 0039", "OBJECT": "target", "OBSID": 6, "QRA": "13:30:00", "QDEC": "-08:30:00", "PARANGLE": 180.0,
    })).writeto(path)
    with db.connect(str(database)) as connection:
        connection.execute("INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)", (exposure_id, exposure_id, "sci"))
        connection.execute(
            "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend) VALUES(?,?,?,?,?)",
            (exposure_id, "sci", str(path), None, "filesystem"),
        )
    context = TaskContext(str(database), str(tmp_path / "artifacts"), {"configuration_root": str(Path.cwd())})
    result = ObservationTask(
        context, target=ObservationTarget("obs", "dither", (exposure_id, exposure_id, exposure_id))
    ).run({})
    summary = ArtifactService(str(database)).describe(result["dither_assignment"].id)["summary"]
    assert summary["duplicate_count"] == 2
    assert summary["complete"] == 0


def test_parallel_virus_observation_retains_target_but_has_no_standard_dither(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    exposure_id = "20260724T031649.6"
    path = tmp_path / "parallel.fits"
    fits.PrimaryHDU(np.ones((4, 4)), header=fits.Header({
        "IFUSLOT": 60, "IFUID": "003", "SPECID": 206,
        "CCDPOS": "L", "CCDHALF": "L", "AMPNAME": "XX", "CONTID": "S/N 0039",
        "OBJECT": "parallel", "QOBJECT": "Target_Name", "QRA": "13:30:00",
        "QDEC": "-08:30:00", "QPROG": "P001", "OBSID": 8,
        "PARANGLE": 180.0, "EXPTIME": 2007.5, "PEXPTIME": 2000.0,
    })).writeto(path)
    with db.connect(str(database)) as connection:
        connection.execute(
            "INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)",
            (exposure_id, exposure_id, "sci"),
        )
        connection.execute(
            "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend) "
            "VALUES(?,?,?,?,?)",
            (exposure_id, "sci", str(path), None, "filesystem"),
        )
    context = TaskContext(
        str(database), str(tmp_path / "artifacts"), {"configuration_root": str(Path.cwd())}
    )
    result = ObservationTask(
        context,
        target=ObservationTarget("20260724-OBSID8", "20260724-OBSID8-DITHER", (exposure_id,)),
    ).run({})
    service = ArtifactService(str(database))
    assignment = service.describe(result["dither_assignment"].id)["summary"]
    assert assignment["observing_mode"] == "parallel"
    assert assignment["virus_primary"] is False
    assert assignment["dither_mode"] == "none"
    assert assignment["coverage_mode"] == "sparse"
    assert assignment["complete"] == 0 and assignment["valid"] == 1
    values = service.load_component(result["dither_assignment"].id, "assignments")["data"]
    assert values[0, 2] == -1
    np.testing.assert_array_equal(values[0, 3:5], 0.0)
    exposure_state = service.describe(result["exposure_states"][0].id)["summary"]
    assert exposure_state["exposure_context"]["requested_target"] == "Target_Name"
    assert exposure_state["exposure_context"]["virus_object"] == "parallel"
