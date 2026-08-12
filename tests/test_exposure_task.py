from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from astropy.io import fits

from virusflow.algorithms.ccd import orient_amplifier_image
from virusflow.algorithms.astrometry import tan_fiber_coordinates
from virusflow.artifacts import ArtifactService, Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.config import ConfigurationService
from virusflow.core.identity import ZipCode
from virusflow.io.catalogs import FixtureCatalogProvider
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.planning.targets import ExposureTarget
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.exposure import ExposureTask
from virusflow.tasks.science import ReducedScienceAmplifierTask


def _publish(
    service,
    root,
    kind,
    zipcode,
    components,
    at,
    *,
    parents=(),
    metadata=None,
    summaries=None,
):
    request = ArtifactRequest(
        kind=kind,
        components={
            name: LogicalComponent(
                name, "array1d" if np.asarray(value).ndim == 1 else "array2d", value
            )
            for name, value in components.items()
        },
        scope=Scope(zipcode=zipcode),
        validity=Validity(at, at, "fixture"),
        parents=list(parents),
        metadata=dict(metadata or {}),
        summaries=dict(summaries or {}),
    )
    publication = DefaultPublicationService(
        svc=service, policy=DefaultPersistencePolicy(), base_dir=str(root)
    )
    context = PublicationContext("fixture", "1", "fixture", "1", {}, [], {})
    return publication.publish([request], context)[0]


def _traces(nx):
    positions = np.concatenate(
        (20 + 7 * np.arange(38), 330 + 7 * np.arange(37), 640 + 7 * np.arange(37))
    )
    return np.broadcast_to(positions[:, None], (112, nx)).copy()


def test_exposure_object_mask_includes_accepted_catalog_positions():
    focal = np.array([[0.0, 0.0], [3.0, 0.0], [10.0, 0.0]])
    catalog = np.array([[200.0, 30.0, 18.0]])
    # No detected source is near the first two fibers; the accepted catalog row is.
    mask = ExposureTask._build_exposure_object_mask(
        np.empty((0, 8)),
        np.array([[0, 0, 0, 0, 0, 1, 1, 0, 18]], dtype=float),
        catalog,
        200.0,
        30.0,
        180.0,
        focal,
        radius=4.0,
    )
    assert mask.tolist() == [True, True, False]


def test_science_dark_scaling_uses_selected_product_without_raw_dark_lookup(
    tmp_path: Path,
):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    service = ArtifactService(str(database))
    exposure_id = "20260609T031649.6"
    at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
    zipcode = ZipCode("060", "003", "206", "LL", "S/N 0039")
    path = tmp_path / "science.fits"
    header = fits.Header(
        {
            "IFUSLOT": 60,
            "IFUID": "003",
            "SPECID": 206,
            "CCDPOS": "L",
            "CCDHALF": "L",
            "AMPNAME": "XX",
            "CONTID": "S/N 0039",
            "GAIN": 1.0,
            "RDNOISE": 2.0,
            "EXPTIME": 40.0,
        }
    )
    fits.PrimaryHDU(np.full((4, 4), 100.0), header=header).writeto(path)
    with db.connect(str(database)) as connection:
        connection.execute(
            "INSERT INTO exposures(id, when_utc, frame_type) VALUES(?,?,?)",
            (exposure_id, "20260609", "sci"),
        )
        connection.execute(
            "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend,amp_key) "
            "VALUES(?,?,?,?,?,?)",
            (exposure_id, "sci", str(path), None, "filesystem", zipcode.key()),
        )

    bias = np.full((4, 4), 10.0, dtype=np.float32)
    dark = np.full((4, 4), 14.0, dtype=np.float32)
    bias_product = _publish(
        service,
        tmp_path,
        "master_bias",
        zipcode,
        {"master": bias, "per_pixel_bias_scatter": np.full_like(bias, 2.0)},
        at,
    )
    dark_mask = np.zeros_like(dark, dtype=np.uint8)
    dark_mask[1, 2] = 1
    dark_product = _publish(
        service,
        tmp_path,
        "master_dark",
        zipcode,
        {"master_dark": dark, "dark_pixel_mask": dark_mask},
        at,
        summaries={
            "reference_exposure_time_seconds": 20.0,
            "bias_convention": "included_in_electron_master",
        },
    )

    state = ReducedScienceAmplifierTask(
        TaskContext(str(database), str(tmp_path / "artifacts")),
        target=SimpleNamespace(zipcode=zipcode, exposure_id=exposure_id),
    ).run({"master_bias": bias_product, "master_dark": dark_product})[
        "reduced_science_state"
    ]

    np.testing.assert_allclose(state.image, 82.0)
    np.testing.assert_allclose(state.variance, 108.0)
    assert state.pixel_mask[1, 2] == 1
    assert state.summaries["dark_reference_exposure_time_seconds"] == 20.0
    assert state.summaries["dark_bias_convention"] == "included_in_electron_master"
    assert state.summaries["dark_scale"] == 2.0


def test_full_exposure_task_fixture_produces_baseline_products_and_refined_catalog_astrometry(
    tmp_path: Path,
):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    service = ArtifactService(str(database))
    exposure_id = "20260609T031649.6"
    at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
    nx = 39
    trace = _traces(nx)
    wavelength = np.broadcast_to(np.linspace(3500, 5500, nx), trace.shape).copy()
    zipcodes = [
        ZipCode("060", "003", "206", amp, "S/N 0039")
        for amp in ("LL", "LU", "RU", "RL")
    ]
    with db.connect(str(database)) as connection:
        connection.execute(
            "INSERT INTO exposures(id, when_utc, frame_type) VALUES(?,?,?)",
            (exposure_id, "20260609", "sci"),
        )
        connection.execute(
            "INSERT INTO exposure_details(exposure_id,airmass) VALUES(?,?)",
            (exposure_id, 1.22),
        )
        for zipcode in zipcodes:
            oriented = np.full((1032, nx), 20.0, dtype=np.float32)
            for y in trace[[5, 45, 80, 105], 0].astype(int):
                oriented[y, :] += 200.0
            raw_science = orient_amplifier_image(oriented, zipcode.amp, "XX")
            raw = np.column_stack((raw_science, np.zeros(1032, dtype=np.float32)))
            path = tmp_path / f"{exposure_id}_{zipcode.ifuslot}{zipcode.amp}_sci.fits"
            header = fits.Header(
                {
                    "IFUSLOT": 60,
                    "IFUID": "003",
                    "SPECID": 206,
                    "CCDPOS": zipcode.amp[0],
                    "CCDHALF": zipcode.amp[1],
                    "AMPNAME": "XX",
                    "CONTID": "S/N 0039",
                    "GAIN": 1.0,
                    "RDNOISE": 2.0,
                    "EXPTIME": 67.4,
                    "PEXPTIME": 75.5,
                    "AIRMASS": 1.22,
                    "TRANSPAR": 0.8,
                    "OBJECT": "WD1327-083_052_E",
                    "QOBJECT": "WD1327-083",
                    "QRA": "13:30:13.64",
                    "QDEC": "-08:34:29.47",
                    "QPROG": "SCIENCE-1",
                    "PARANGLE": 180.0,
                    "OBSID": 6,
                    "DATE": "2026-06-09T03:16:49.600000",
                    "AMBTEMP": 12.5,
                    "HUMIDITY": 44.0,
                    "PRESSURE": 798.2,
                    "RHO_STRT": 1.1,
                    "THE_STRT": 2.2,
                    "PHI_STRT": 3.3,
                    "X_STRT": 4.4,
                    "Y_STRT": 5.5,
                }
            )
            fits.PrimaryHDU(raw, header=header).writeto(path)
            connection.execute(
                "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend,amp_key) VALUES(?,?,?,?,?,?)",
                (exposure_id, "sci", str(path), None, "filesystem", zipcode.key()),
            )

    response_rows = []
    for zipcode in zipcodes:
        zero = np.zeros((1032, nx), dtype=np.float32)
        one = np.ones((1032, nx), dtype=np.float32)
        twilight = np.full((1032, nx), 100.0, dtype=np.float32)
        for y in trace[:, 0].astype(int):
            twilight[y, :] += 1000.0
        _publish(
            service,
            tmp_path,
            "master_bias",
            zipcode,
            {"master": zero, "per_pixel_bias_scatter": one},
            at,
        )
        _publish(
            service,
            tmp_path,
            "master_dark",
            zipcode,
            {"master_dark": zero, "dark_pixel_mask": zero.astype(np.uint8)},
            at,
            summaries={
                "reference_exposure_time_seconds": 600.0,
                "bias_convention": "included_in_electron_master",
            },
        )
        _publish(
            service,
            tmp_path,
            "master_ldls",
            zipcode,
            {"master_ldls": twilight, "flat_response_mask": zero.astype(np.uint8)},
            at,
        )
        _publish(service, tmp_path, "master_arc", zipcode, {"master_arc": one}, at)
        _publish(
            service,
            tmp_path,
            "master_twilight",
            zipcode,
            {"master_twilight": twilight},
            at,
        )
        sample_columns = np.asarray([0, nx - 1], dtype=float)
        _publish(
            service,
            tmp_path,
            "trace_map",
            zipcode,
            {
                "fiber_trace_map": trace,
                "trace_sample_columns": sample_columns,
                "sampled_trace_positions": trace[:, [0, nx - 1]],
                "per_fiber_trace_residual_rms": np.zeros(trace.shape[0]),
                "trace_sample_valid_mask": np.ones((trace.shape[0], 2), dtype=np.uint8),
                "trace_fit_residuals": np.zeros((trace.shape[0], 2)),
                "per_fiber_valid_sample_count": np.full(trace.shape[0], 2),
                "trace_interpolated_fiber_mask": np.zeros(
                    trace.shape[0], dtype=np.uint8
                ),
            },
            at,
        )
        amplifier_wavelength = wavelength.copy()
        if zipcode.amp == "LL":
            amplifier_wavelength[7, 20] = amplifier_wavelength[7, 19] - 0.25
        _publish(
            service,
            tmp_path,
            "wavelength_map",
            zipcode,
            {
                "wavelength_map": amplifier_wavelength,
                "per_fiber_wavelength_residual_rms": np.zeros(trace.shape[0]),
                "arc_identification": np.asarray(
                    [[0.0, 3500.0, 3500.0, 0.0, 0.0, 0.0]]
                ),
                "arc_candidate_evidence": np.asarray(
                    [[0.0, 0.0, 1.0, 1.0, 3500.0, 0.0, 0.0]]
                ),
                "arc_line_evidence": np.asarray(
                    [[0.0, 0.0, 3500.0, 3500.0, 0.0, 0.0, 0.0]]
                ),
                "seed_region_attempted_mask": np.ones(1, dtype=np.uint8),
                "seed_region_success_mask": np.ones(1, dtype=np.uint8),
                "seed_region_failure_code": np.zeros(1, dtype=np.uint8),
                "seed_fit_coefficients": np.asarray([[3500.0, 1.0]]),
                "interpolated_fiber_mask": np.zeros(trace.shape[0], dtype=np.uint8),
                "extrapolated_fiber_mask": np.zeros(trace.shape[0], dtype=np.uint8),
                "input_mask_indices": np.asarray([], dtype=np.int32),
                "input_mask_shape": np.asarray([1032, nx], dtype=np.int32),
            },
            at,
        )
    total_fibers = len(zipcodes) * trace.shape[0]
    exposure_response = _publish(
        service,
        tmp_path,
        "exposure_fiber_response",
        None,
        {
            "raw_ratio": np.ones((total_fibers, nx), dtype=np.float32),
            "normalization": np.ones((total_fibers, nx), dtype=np.float32),
            "valid_mask": np.ones((total_fibers, nx), dtype=np.uint8),
            "common_ldls": np.full((total_fibers, nx), 1000.0, dtype=np.float32),
            "common_twilight": np.full((total_fibers, nx), 1000.0, dtype=np.float32),
            "within_amplifier_response": np.ones((total_fibers, nx), dtype=np.float32),
            "amplifier_response": np.ones((len(zipcodes), nx), dtype=np.float32),
            "amplifier_scalar": np.ones(len(zipcodes), dtype=np.float32),
            "amplifier_common_response": np.ones((len(zipcodes), nx), dtype=np.float32),
            "fiber_amplifier_index": np.repeat(np.arange(len(zipcodes)), trace.shape[0]),
            "amplifier_identity": np.asarray(
                [
                    [int(item.ifuslot), int(item.ifuid), int(item.specid), index]
                    for index, item in enumerate(zipcodes)
                ],
                dtype=np.int32,
            ),
            "wavelength": np.tile(np.linspace(3500, 5500, nx, dtype=np.float32), (total_fibers, 1)),
        },
        at,
        metadata={
            "algorithm_metadata": {
                "amplifier_keys": [item.key() for item in zipcodes],
            }
        },
        summaries={"fibers_per_amplifier": trace.shape[0]},
    )

    config = ConfigurationService(root=Path.cwd())
    fplane, _ = config.resolve_fplane(Path.cwd() / "fplaneall.txt")
    offsets, _ = config.fiber_offsets("003")
    ra0 = (13 + 30 / 60 + 13.64 / 3600) * 15
    dec0 = -(8 + 34 / 60 + 29.47 / 3600)
    catalog = []
    for zipcode, fiber_index in zip(zipcodes, (5, 45, 80, 105)):
        fx, fy = fplane["060"]
        local = offsets[zipcode.amp][fiber_index]
        tan_result = tan_fiber_coordinates(
            ra0, dec0, 180.0, [fx + local[0]], [fy + local[1]]
        )
        ra = tan_result.get_array("ra")
        dec = tan_result.get_array("dec")
        catalog.append((ra[0], dec[0], 18.0))
    context = TaskContext(
        str(database),
        str(tmp_path / "artifacts"),
        {
            "configuration_root": str(Path.cwd()),
            "fplane_path": str(Path.cwd() / "fplaneall.txt"),
            "catalog_provider": FixtureCatalogProvider(catalog),
        },
    )
    result = ExposureTask(context, target=ExposureTarget(exposure_id, at)).run({})
    required = {
        "exposure_completion_manifest",
        "initial_astrometry",
        "catalog_match_table",
        "final_astrometry",
        "fiber_sky_coordinates",
        "sky_fiber_mask",
        "sky_model",
        "fiber_response_model",
        "calibrated_fiber_state",
        "effective_exposure_time",
        "exposure_fiber_response",
    }
    assert required <= set(result)
    manifest = service.describe(result["exposure_completion_manifest"].id)
    assert manifest["summary"]["raw_amplifier_count"] == 4
    assert manifest["summary"]["extracted_amplifier_count"] == 4
    assert manifest["summary"]["excluded_wavelength_fiber_count"] == 1
    exclusions = manifest["summary"]["wavelength_fiber_exclusions"]
    assert exclusions[zipcodes[0].key()]["fiber_indices"] == [7]
    assert service.describe(result["final_astrometry"].id)["summary"]["refined"] == 1
    inference_metadata = service.describe(result["sky_model"].id)["summary"]
    assert 1 <= inference_metadata["exposure_inference_iteration"] <= 3
    assert inference_metadata["exposure_inference_history"]
    mode = service.describe(result["exposure_mode_classification"].id)["summary"]
    assert mode["OBJECT"] == "WD1327-083_052_E"
    assert mode["QOBJECT"] == mode["requested_target"] == "WD1327-083"
    assert mode["requested_ifuslot"] == "052" and mode["het_track"] == "E"
    assert mode["virus_primary"] is True and mode["q_metadata_complete"] is True
    scientific = service.get_scientific_metadata(result["final_astrometry"].id)
    assert scientific["observation_time"] == at
    assert scientific["airmass"] == 1.22
    assert {
        field: scientific[field]
        for field in ("rho_start", "theta_start", "phi_start", "x_start", "y_start")
    } == {
        "rho_start": 1.1,
        "theta_start": 2.2,
        "phi_start": 3.3,
        "x_start": 4.4,
        "y_start": 5.5,
    }
    forbidden = {
        "reduced_science_image",
        "scatter_subtracted_image",
        "aperture_extracted_spectrum",
        "extracted_variance",
        "fiber_sky_prediction",
        "sky_subtracted_spectrum",
        "final_exposure_response",
    }
    assert not any(service.adapter.list_all(kind=kind) for kind in forbidden)
    assert result["exposure_fiber_response"].id == exposure_response.id
    assert service.adapter.get_row(exposure_response.id)["exposure_id"] is None
    assert len(service.adapter.list_all(kind="exposure_fiber_response")) == 1
    assert result["calibrated_fiber_state"].flux.dtype == np.float32
    baseline = service.describe(result["baseline_relative_response"].id)
    baseline_components = {component["name"] for component in baseline["components"]}
    assert baseline_components == {"wavelength", "response", "uncertainty", "mask"}
    assert baseline["summary"]["response_definition"] == "throughput / normalization"
    assert baseline["summary"]["atmospheric_content"] == "removed_with_model"
    assert (
        baseline["summary"]["construction_extinction_model"]
        == "mcdonald_extinction.dat"
    )
    assert baseline["summary"]["construction_airmass"] == 1.22
    assert baseline["summary"]["construction_airmass_basis"] == "HET fixed altitude"
    assert (
        baseline["summary"]["source_baseline"]
        == "legacy Remedy throughput / normalization"
    )
    assert baseline["summary"]["absolute_flux_calibration"] is False
    assert baseline["summary"]["atmospheric_correction_applied"] is False
    assert baseline["summary"]["isolated_instrumental_throughput"] is False
    state_metadata = result["calibrated_fiber_state"].metadata
    assert state_metadata["baseline_applied_count"] == 1
    assert state_metadata["exposure_illumination_applied_count"] == 1
    assert state_metadata["exposure_transparency_measurement"] == 0.8
    assert state_metadata["exposure_transparency_application"] == (
        "applied once as a separate gray factor"
    )
    assert state_metadata["atmospheric_extinction_applied_count"] == 1
    assert (
        state_metadata["atmospheric_extinction_model_artifact_id"]
        == result["atmospheric_extinction_model"].id
    )
    assert state_metadata["exposure_airmass_measurement"] == 1.22
    assert state_metadata["applied_airmass"] == 1.22
    assert state_metadata["atmospheric_correction_applied"] is True
    response_state = service.describe(result["fiber_response_model"].id)["summary"]
    assert response_state["baseline_atmospheric_content"] == "removed_with_model"
    assert (
        response_state["atmospheric_extinction_model_artifact_id"]
        == result["atmospheric_extinction_model"].id
    )
    assert response_state["exposure_airmass"] == 1.22
    assert response_state["applied_airmass"] == 1.22
    assert response_state["exposure_airmass_source"] == (
        "canonical raw scientific metadata AIRMASS"
    )
    assert response_state["gray_factors"] == {
        "fiber_illumination_artifact_id": result["exposure_illumination_correction"].id,
        "transparency": 0.8,
        "mirror_illumination": None,
    }
    identity = result["calibrated_fiber_state"].fiber_identity
    assert identity.shape[0] == 4 * 112 - 1
    assert not np.any((identity[:, 4] == 0) & (identity[:, 1] == 7))
    assert np.any((identity[:, 4] == 0) & (identity[:, 1] == 8))
    for name, artifact in result.items():
        if name == "calibrated_fiber_state":
            continue
        artifacts = artifact if isinstance(artifact, tuple) else (artifact,)
        for one in artifacts:
            for component in service.describe(one.id)["components"]:
                service.load_component(one.id, component["name"], verify_checksum=True)


def test_exposure_without_calibrations_fails_before_publishing_empty_products(
    tmp_path: Path,
):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    exposure_id = "20260609T031649.6"
    at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
    zipcode = ZipCode("060", "003", "206", "LL", "S/N 0039")
    path = tmp_path / f"{exposure_id}_060LL_sci.fits"
    fits.PrimaryHDU(
        np.ones((4, 4)),
        header=fits.Header(
            {
                "IFUSLOT": 60,
                "IFUID": "003",
                "SPECID": 206,
                "CCDPOS": "L",
                "CCDHALF": "L",
                "AMPNAME": "XX",
                "CONTID": "S/N 0039",
            }
        ),
    ).writeto(path)
    with db.connect(str(database)) as connection:
        connection.execute(
            "INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)",
            (exposure_id, exposure_id, "sci"),
        )
        connection.execute(
            "INSERT INTO amplifiers(key,ifuslot,ifuid,specid,amp,controller) VALUES(?,?,?,?,?,?)",
            (
                zipcode.key(),
                zipcode.ifuslot,
                zipcode.ifuid,
                zipcode.specid,
                zipcode.amp,
                zipcode.controller,
            ),
        )
        connection.execute(
            "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend,amp_key) "
            "VALUES(?,?,?,?,?,?)",
            (exposure_id, "sci", str(path), None, "filesystem", zipcode.key()),
        )

    context = TaskContext(
        str(database),
        str(tmp_path / "artifacts"),
        {
            "configuration_root": str(Path.cwd()),
            "fplane_path": str(Path.cwd() / "fplaneall.txt"),
        },
    )
    with np.testing.assert_raises_regex(
        RuntimeError,
        "no amplifier has complete calibration coverage.*master_bias: missing published calibration.*1 amplifier",
    ):
        ExposureTask(context, target=ExposureTarget(exposure_id, at)).run({})

    assert ArtifactService(str(database)).adapter.list_all() == []


def test_later_complete_baseline_supersedes_import_without_composition(tmp_path: Path):
    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    service = ArtifactService(str(database))
    at = datetime(2026, 6, 9)
    context = TaskContext(str(database), str(tmp_path / "artifacts"), {})
    task = ExposureTask(context, target=ExposureTarget("20260609T000000.0", at))
    config = ConfigurationService()

    imported = task._select_or_import_baseline(service, config, at)
    later = _publish(
        service,
        tmp_path,
        "baseline_relative_response",
        None,
        {
            "wavelength": np.asarray([3500.0, 5500.0]),
            "response": np.asarray([0.5, 0.6]),
            "uncertainty": np.asarray([0.01, 0.02]),
            "mask": np.zeros(2, dtype=np.uint8),
        },
        at,
        metadata={
            "derivation_method_identity": {"extraction": "later measured baseline"},
            "atmospheric_content": "removed_with_model",
            "atmospheric_separation": {
                "extinction_model_identity": "mcdonald-observatory-mean-extinction",
                "calibration_exposure_airmasses": [1.12, 1.31],
            },
            "applicability": {
                "instrument_epoch": "later measured epoch",
                "algorithm_versions": task._baseline_application_versions(),
            },
        },
    )
    incompatible = _publish(
        service,
        tmp_path,
        "baseline_relative_response",
        None,
        {
            "wavelength": np.asarray([3500.0, 5500.0]),
            "response": np.asarray([0.7, 0.8]),
            "uncertainty": np.asarray([0.01, 0.02]),
            "mask": np.zeros(2, dtype=np.uint8),
        },
        at,
        metadata={
            "derivation_method_identity": {"extraction": "incompatible future method"},
            "atmospheric_content": "absorbed_unknown",
            "atmospheric_separation": {
                "extinction_model_identity": None,
                "calibration_exposure_airmasses": [],
            },
            "applicability": {
                "instrument_epoch": "later measured epoch",
                "algorithm_versions": {
                    **task._baseline_application_versions(),
                    "extraction": "future-extraction-2.0",
                },
            },
        },
    )

    selected = task._select_or_import_baseline(service, config, at)
    assert selected.id == later.id
    assert selected.id != imported.id
    assert selected.id != incompatible.id
    assert len(service.adapter.list_all(kind="baseline_relative_response")) == 3

    extinction = task._select_or_import_extinction_model(
        service,
        config,
        at,
        required_identity="mcdonald-observatory-mean-extinction",
    )
    extinction_description = service.describe(extinction.id)
    assert {
        component["name"] for component in extinction_description["components"]
    } == {"wavelength", "extinction_coefficient", "uncertainty", "mask"}
    assert extinction_description["summary"]["site"] == "McDonald Observatory"
    assert extinction_description["summary"]["coefficient_units"] == "mag / airmass"
    assert extinction_description["summary"]["applicability"] == {
        "wavelength_min_angstrom": 3400.0,
        "wavelength_max_angstrom": 7000.0,
        "site": "McDonald Observatory",
        "interpolation": "linear within valid range; no extrapolation",
    }
    assert (
        "NaN with mask bit 2" in extinction_description["summary"]["uncertainty_state"]
    )
    np.testing.assert_array_equal(
        service.load_component(extinction.id, "mask")["data"],
        np.full(37, 2, dtype=np.uint16),
    )
