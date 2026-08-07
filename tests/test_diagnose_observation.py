from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits

from virusflow.algorithms.exposure import CalibratedFiberState
from virusflow.config.defaults import DITHER_POLICY
from virusflow.ontology.artifact_kinds import ARTIFACT_KINDS
from virusflow.planning.targets import ObservationTarget
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.observation import ObservationTask

ROOT = Path(__file__).resolve().parents[1]


def _load_diagnose_module():
    spec = importlib.util.spec_from_file_location(
        "diagnose_observation", ROOT / "scripts" / "diagnose_observation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnose = _load_diagnose_module()


def _build_observation_fixture(tmp_path: Path):
    """A completed 3-exposure observation, without any source/PSF evidence.

    Mirrors the fixture in test_observation_task.py so ObservationTask
    publishes the same real observation-scoped Artifacts a normal
    ``virusflow run observation`` would.
    """

    database = tmp_path / "registry.sqlite3"
    db.init_db(str(database))
    exposure_ids = ("20260609T031649.6", "20260609T031859.3", "20260609T032112.2")
    dec0 = -8.5
    nominal = np.asarray(DITHER_POLICY.nominal_pattern_arcsec, dtype=float)
    calibrated_states = []
    for index, exposure_id in enumerate(exposure_ids):
        path = tmp_path / f"{exposure_id}.fits"
        header = fits.Header({
            "IFUSLOT": 60, "IFUID": "003", "SPECID": 206, "CCDPOS": "L", "CCDHALF": "L",
            "AMPNAME": "XX", "CONTID": "S/N 0039", "OBJECT": "target", "OBSID": 6,
            "QRA": "13:30:00", "QDEC": "-08:30:00", "PARANGLE": 180.0, "EXPTIME": 67.4,
            "AIRMASS": 1.22 + 0.01 * index,
        })
        fits.PrimaryHDU(np.ones((4, 4), dtype=np.float32), header=header).writeto(path)
        with db.connect(str(database)) as connection:
            connection.execute(
                "INSERT INTO exposures(id, when_utc, frame_type) VALUES(?,?,?)",
                (exposure_id, exposure_id, "sci"),
            )
            connection.execute(
                "INSERT INTO exposure_details(exposure_id,airmass) VALUES(?,?)",
                (exposure_id, 1.22 + 0.01 * index),
            )
            connection.execute(
                "INSERT INTO raw_files(exposure_id,frame_type,path,tar_member,storage_backend) VALUES(?,?,?,?,?)",
                (exposure_id, "sci", str(path), None, "filesystem"),
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
            model_artifact_ids=(),
            metadata={"fixture": True},
        ))

    context = TaskContext(str(database), str(tmp_path / "artifacts"), {
        "configuration_root": str(ROOT), "calibrated_fiber_states": calibrated_states,
    })
    observation_id = "20260609-OBSID6"
    target = ObservationTarget(observation_id, observation_id + "-DITHER", exposure_ids)
    ObservationTask(context, target=target).run({})
    return database, tmp_path / "artifacts", observation_id, exposure_ids


def _run_diagnose(database, workdir, output_dir, observation_id):
    return diagnose.main([
        observation_id, "--db", str(database), "--workdir", str(workdir),
        "--configuration-root", str(ROOT), "--output-dir", str(output_dir),
    ])


def test_documented_artifact_kinds_match_registry():
    text = (ROOT / "docs/architecture/artifact-kinds-and-equation-inversion.md").read_text()
    start = text.index("## Complete registered-kind inventory")
    section = text[start:text.index("## Retention boundaries", start)]
    documented = set(re.findall(r"^\|\s*`([a-z0-9_]+)`\s*\|", section, re.M))
    assert documented == set(ARTIFACT_KINDS)


def test_load_observation_context_discovers_observation_and_exposures(tmp_path):
    database, _workdir, observation_id, exposure_ids = _build_observation_fixture(tmp_path)
    service = diagnose.ArtifactService(str(database))
    ctx = diagnose.load_observation_context(service, observation_id)
    assert ctx is not None
    assert ctx.exposure_ids == list(exposure_ids)
    assert ctx.registration_row is not None
    assert ctx.coverage_row is not None
    assert ctx.summary_row is not None
    assert ctx.calibrated_observation_row is not None
    assert diagnose.load_observation_context(service, "no-such-observation") is None


def test_representative_fixture_generates_core_figures(tmp_path):
    database, workdir, observation_id, exposure_ids = _build_observation_fixture(tmp_path)
    out_dir = tmp_path / "diagnostics"
    exit_code = _run_diagnose(database, workdir, out_dir, observation_id)
    assert exit_code == 0
    assert (out_dir / "index.md").exists()
    assert (out_dir / "observation_summary.png").exists()
    assert (out_dir / "dither_pattern.png").exists()
    for exposure_id in exposure_ids:
        assert (out_dir / exposure_id / "collapsed_focal_plane.png").exists()


def test_missing_optional_products_are_skipped_not_failed(tmp_path):
    database, workdir, observation_id, exposure_ids = _build_observation_fixture(tmp_path)
    out_dir = tmp_path / "diagnostics"
    exit_code = _run_diagnose(database, workdir, out_dir, observation_id)
    assert exit_code == 0
    index_text = (out_dir / "index.md").read_text()
    assert "## Failed while generating" not in index_text
    for exposure_id in exposure_ids:
        # No source/PSF/catalog evidence was ever published for this fixture,
        # so these are expected to be skipped, not to raise.
        assert not (out_dir / exposure_id / "astrometry.png").exists()
        assert not (out_dir / exposure_id / "illumination.png").exists()
        assert not (out_dir / exposure_id / "source_geometry.png").exists()
        assert not (out_dir / exposure_id / "psf_dar.png").exists()
        assert not (out_dir / exposure_id / "source_spectrum.png").exists()
    assert f"{exposure_ids[0]}/astrometry" in index_text
    assert f"{exposure_ids[0]}/psf_dar" in index_text


def test_running_diagnostics_does_not_modify_database(tmp_path):
    database, workdir, observation_id, _exposure_ids = _build_observation_fixture(tmp_path)
    before = hashlib.md5(database.read_bytes()).hexdigest()
    _run_diagnose(database, workdir, tmp_path / "diagnostics", observation_id)
    after = hashlib.md5(database.read_bytes()).hexdigest()
    assert before == after
