from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits

from virusflow.algorithms.ccd import orient_amplifier_image
from virusflow.algorithms.exposure import tan_fiber_coordinates
from virusflow.artifacts import ArtifactService, Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.config import ConfigurationService
from virusflow.core.identity import ZipCode
from virusflow.io.catalogs import FixtureCatalogProvider
from virusflow.ontology.scopes import PhysicalScope
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.planning.targets import ExposureTarget
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.exposure import ExposureTask


def _publish(service, root, kind, zipcode, components, at):
    request = ArtifactRequest(
        kind=kind, components={
            name: LogicalComponent(name, "array1d" if np.asarray(value).ndim == 1 else "array2d", value)
            for name, value in components.items()
        },
        scope=Scope(zipcode=zipcode), validity=Validity(at, at, "fixture"),
    )
    publication = DefaultPublicationService(svc=service, policy=DefaultPersistencePolicy(), base_dir=str(root))
    context = PublicationContext("fixture", "1", "fixture", "1", {}, [], {})
    return publication.publish([request], context)[0]


def _traces(nx):
    positions = np.concatenate((20 + 7 * np.arange(38), 330 + 7 * np.arange(37), 640 + 7 * np.arange(37)))
    return np.broadcast_to(positions[:, None], (112, nx)).copy()


def test_full_exposure_task_fixture_produces_baseline_products_and_refined_catalog_astrometry(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    service = ArtifactService(str(database))
    exposure_id = "20260609T031649.6"
    at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
    nx = 39
    trace = _traces(nx)
    wavelength = np.broadcast_to(np.linspace(3500, 5500, nx), trace.shape).copy()
    zipcodes = [ZipCode("060", "003", "206", amp, "S/N 0039") for amp in ("LL", "LU", "RU", "RL")]
    with db.connect(str(database)) as connection:
        connection.execute("INSERT INTO exposures(id, when_utc, frame_type) VALUES(?,?,?)", (exposure_id, "20260609", "sci"))
        for zipcode in zipcodes:
            oriented = np.full((1032, nx), 20.0, dtype=np.float32)
            for y in trace[[5, 45, 80, 105], 0].astype(int):
                oriented[y, :] += 200.0
            raw_science = orient_amplifier_image(oriented, zipcode.amp, "XX")
            raw = np.column_stack((raw_science, np.zeros(1032, dtype=np.float32)))
            path = tmp_path / f"{exposure_id}_{zipcode.ifuslot}{zipcode.amp}_sci.fits"
            header = fits.Header({
                "IFUSLOT": 60, "IFUID": "003", "SPECID": 206,
                "CCDPOS": zipcode.amp[0], "CCDHALF": zipcode.amp[1], "AMPNAME": "XX", "CONTID": "S/N 0039",
                "GAIN": 1.0, "RDNOISE": 2.0, "EXPTIME": 67.4, "PEXPTIME": 75.5,
                "OBJECT": "WD1327-083_052_E", "QOBJECT": "WD1327-083",
                "QRA": "13:30:13.64", "QDEC": "-08:34:29.47", "QPROG": "SCIENCE-1",
                "PARANGLE": 180.0, "OBSID": 6,
            })
            fits.PrimaryHDU(raw, header=header).writeto(path)
            connection.execute(
                "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend,amp_key) VALUES(?,?,?,?,?,?)",
                (exposure_id, "sci", str(path), None, "filesystem", zipcode.key()),
            )

    for zipcode in zipcodes:
        zero = np.zeros((1032, nx), dtype=np.float32)
        one = np.ones((1032, nx), dtype=np.float32)
        twilight = np.full((1032, nx), 100.0, dtype=np.float32)
        for y in trace[:, 0].astype(int):
            twilight[y, :] += 1000.0
        _publish(service, tmp_path, "master_bias", zipcode, {"master": zero, "per_pixel_bias_scatter": one}, at)
        _publish(service, tmp_path, "master_dark", zipcode, {"master_dark": zero, "dark_pixel_mask": zero.astype(np.uint8)}, at)
        _publish(service, tmp_path, "master_ldls", zipcode, {"master_ldls": twilight, "flat_response_mask": zero.astype(np.uint8)}, at)
        _publish(service, tmp_path, "master_arc", zipcode, {"master_arc": one}, at)
        _publish(service, tmp_path, "master_twilight", zipcode, {"master_twilight": twilight}, at)
        sample_columns = np.asarray([0, nx - 1], dtype=float)
        _publish(service, tmp_path, "trace_map", zipcode, {
            "fiber_trace_map": trace,
            "trace_sample_columns": sample_columns,
            "sampled_trace_positions": trace[:, [0, nx - 1]],
            "per_fiber_trace_residual_rms": np.zeros(trace.shape[0]),
        }, at)
        amplifier_wavelength = wavelength.copy()
        if zipcode.amp == "LL":
            amplifier_wavelength[7, 20] = amplifier_wavelength[7, 19] - 0.25
        _publish(service, tmp_path, "wavelength_map", zipcode, {
            "wavelength_map": amplifier_wavelength,
            "per_fiber_wavelength_residual_rms": np.zeros(trace.shape[0]),
        }, at)

    config = ConfigurationService(root=Path.cwd())
    fplane, _ = config.resolve_fplane(Path.cwd() / "fplaneall.txt")
    offsets, _ = config.fiber_offsets("003")
    ra0 = (13 + 30 / 60 + 13.64 / 3600) * 15
    dec0 = -(8 + 34 / 60 + 29.47 / 3600)
    catalog = []
    for zipcode, fiber_index in zip(zipcodes, (5, 45, 80, 105)):
        fx, fy = fplane["060"]
        local = offsets[zipcode.amp][fiber_index]
        ra, dec, _ = tan_fiber_coordinates(ra0, dec0, 180.0, [fx + local[0]], [fy + local[1]])
        catalog.append((ra[0], dec[0], 18.0))
    context = TaskContext(
        str(database), str(tmp_path / "artifacts"),
        {"configuration_root": str(Path.cwd()), "fplane_path": str(Path.cwd() / "fplaneall.txt"),
         "catalog_provider": FixtureCatalogProvider(catalog)},
    )
    result = ExposureTask(context, target=ExposureTarget(exposure_id, at)).run({})
    required = {
        "exposure_completion_manifest", "initial_astrometry", "catalog_match_table", "final_astrometry",
        "fiber_sky_coordinates", "sky_fiber_mask", "sky_model", "fiber_response_model",
        "calibrated_fiber_state", "effective_exposure_time",
        "amp_to_amp_normalization",
    }
    assert required <= set(result)
    manifest = service.describe(result["exposure_completion_manifest"].id)
    assert manifest["summary"]["raw_amplifier_count"] == 4
    assert manifest["summary"]["extracted_amplifier_count"] == 4
    assert manifest["summary"]["excluded_wavelength_fiber_count"] == 1
    exclusions = manifest["summary"]["wavelength_fiber_exclusions"]
    assert exclusions[zipcodes[0].key()]["fiber_indices"] == [7]
    assert service.describe(result["final_astrometry"].id)["summary"]["refined"] == 1
    mode = service.describe(result["exposure_mode_classification"].id)["summary"]
    assert mode["OBJECT"] == "WD1327-083_052_E"
    assert mode["QOBJECT"] == mode["requested_target"] == "WD1327-083"
    assert mode["requested_ifuslot"] == "052" and mode["het_track"] == "E"
    assert mode["virus_primary"] is True and mode["q_metadata_complete"] is True
    forbidden = {
        "reduced_science_image", "scatter_subtracted_image", "aperture_extracted_spectrum",
        "extracted_variance", "fiber_sky_prediction", "sky_subtracted_spectrum",
        "final_exposure_response",
    }
    assert not any(service.adapter.list_all(kind=kind) for kind in forbidden)
    assert result["calibrated_fiber_state"].flux.dtype == np.float32
    identity = result["calibrated_fiber_state"].fiber_identity
    assert identity.shape[0] == 4 * 112 - 1
    assert not np.any((identity[:, 4] == 0) & (identity[:, 1] == 7))
    assert np.any((identity[:, 4] == 0) & (identity[:, 1] == 8))
    for name, artifact in result.items():
        if name == "calibrated_fiber_state":
            continue
        for component in service.describe(artifact.id)["components"]:
            service.load_component(artifact.id, component["name"], verify_checksum=True)


def test_exposure_without_calibrations_fails_before_publishing_empty_products(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    exposure_id = "20260609T031649.6"
    at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
    zipcode = ZipCode("060", "003", "206", "LL", "S/N 0039")
    path = tmp_path / f"{exposure_id}_060LL_sci.fits"
    fits.PrimaryHDU(np.ones((4, 4)), header=fits.Header({
        "IFUSLOT": 60, "IFUID": "003", "SPECID": 206,
        "CCDPOS": "L", "CCDHALF": "L", "AMPNAME": "XX", "CONTID": "S/N 0039",
    })).writeto(path)
    with db.connect(str(database)) as connection:
        connection.execute(
            "INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)",
            (exposure_id, exposure_id, "sci"),
        )
        connection.execute(
            "INSERT INTO amplifiers(key,ifuslot,ifuid,specid,amp,controller) VALUES(?,?,?,?,?,?)",
            (zipcode.key(), zipcode.ifuslot, zipcode.ifuid, zipcode.specid, zipcode.amp, zipcode.controller),
        )
        connection.execute(
            "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend,amp_key) "
            "VALUES(?,?,?,?,?,?)",
            (exposure_id, "sci", str(path), None, "filesystem", zipcode.key()),
        )

    context = TaskContext(str(database), str(tmp_path / "artifacts"), {
        "configuration_root": str(Path.cwd()), "fplane_path": str(Path.cwd() / "fplaneall.txt"),
    })
    with np.testing.assert_raises_regex(
        RuntimeError,
        "no amplifier has complete calibration coverage.*master_bias: missing published calibration.*1 amplifier",
    ):
        ExposureTask(context, target=ExposureTarget(exposure_id, at)).run({})

    assert ArtifactService(str(database)).adapter.list_all() == []
