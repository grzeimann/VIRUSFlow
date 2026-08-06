from __future__ import annotations

from datetime import datetime

from virusflow.artifacts import Scope
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.core.identity import ZipCode
from virusflow.planning.cadence import pair_lamp_groups, resolve_calibration_groups
from virusflow.planning.targets import PurposeCadence
from virusflow.io import RawFrameData
from virusflow.planning import ReductionGraph, adapt_target, default_calibration_graph
from virusflow.planning.config import load_planning_config_from_dict
from virusflow.registry import database as db
from virusflow.tasks.base import TaskContext
from virusflow.tasks.calibs import MasterSciTask
from virusflow.artifacts import ArtifactService
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
import numpy as np


ZIP = ZipCode("013", "043", "412", "LL", "A")


def _catalog(tmp_path, rows):
    path = str(tmp_path / "cadence.sqlite3")
    db.init_db(path)
    with db.connect(path) as connection:
        connection.execute(
            "INSERT INTO amplifiers(key,ifuslot,ifuid,specid,amp,controller) VALUES(?,?,?,?,?,?)",
            (ZIP.key(), *ZIP.as_tuple()),
        )
        for index, row in enumerate(rows):
            exposure_id, frame_type = row[0], row[1]
            connection.execute(
                "INSERT INTO exposures(id,when_utc,frame_type) VALUES(?,?,?)",
                (exposure_id, exposure_id[:8], frame_type),
            )
            connection.execute(
                "INSERT INTO exposure_details(exposure_id,exptime,pexptime,ambient_temperature,lamp,observing_block) "
                "VALUES(?,?,?,?,?,?)",
                (exposure_id, row[2], row[2], row[3], row[4], row[5]),
            )
            connection.execute(
                "INSERT INTO raw_files(exposure_id,frame_type,path,storage_backend,amp_key) VALUES(?,?,?,?,?)",
                (exposure_id, frame_type, f"/raw/{index}.fits", "filesystem", ZIP.key()),
            )
    return path


def _groups(path, kind, policy, **options):
    return resolve_calibration_groups(
        kind=kind, cadence=PurposeCadence(policy, **options),
        scope=Scope(ZIP), db_path=path,
    )


def test_purpose_cadence_configuration_is_explicit():
    configured = load_planning_config_from_dict({
        "nodes": {"master_dark": {"cadence": {
            "type": "weekly", "minimum_exposures": 2,
        }}}
    })
    cadence = configured.nodes["master_dark"].cadence
    assert cadence.policy == "weekly"
    assert cadence.options == {"minimum_exposures": 2}


def test_nightly_bias_preserves_fractional_boundaries_and_zip_membership(tmp_path):
    path = _catalog(tmp_path, [
        ("20260609T235959.125", "zro", 0.0, None, None, None),
        ("20260610T000000.250", "zro", 0.0, None, None, None),
    ])
    result = _groups(path, "master_bias", "nightly", minimum_exposures=1)
    assert len(result.groups) == 2
    assert result.groups[0].timestamps == (datetime(2026, 6, 9, 23, 59, 59, 125000),)
    assert result.groups[1].timestamps[0].microsecond == 250000


def test_dark_monthly_and_configurable_weekly_are_not_nightly(tmp_path):
    path = _catalog(tmp_path, [
        ("20260601T010000.0", "drk", 360.0, 11.0, None, None),
        ("20260620T010000.0", "drk", 360.0, 12.0, None, None),
        ("20260701T010000.0", "drk", 360.0, 13.0, None, None),
    ])
    monthly = _groups(path, "master_dark", "monthly", minimum_exposures=1)
    weekly = _groups(path, "master_dark", "weekly", minimum_exposures=1)
    assert [len(group.raw_ids) for group in monthly.groups] == [2, 1]
    assert monthly.groups[0].applicability["start"] == "2026-06-01T00:00:00.000000"
    assert monthly.groups[0].applicability["end"] == "2026-07-01T00:00:00.000000"
    assert len(weekly.groups) == 3


def test_twilight_week_and_ldls_isolated_temperature_metadata(tmp_path):
    rows = [
        ("20260608T010000.0", "twi", 30.0, None, None, None),
        ("20260614T010000.0", "twi", 30.0, None, None, None),
        ("20260615T010000.0", "twi", 30.0, None, None, None),
        ("20260609T010000.0", "flt", 30.0, 10.0, None, None),
        ("20260609T020000.0", "flt", 30.0, 12.0, None, None),
        ("20260609T035959.5", "flt", 30.0, None, None, None),
        ("20260609T080000.0", "flt", 30.0, 20.0, None, None),
        ("20260609T090000.0", "flt", 30.0, 21.0, None, None),
    ]
    path = _catalog(tmp_path, rows)
    twilight = _groups(path, "master_twilight", "weekly", minimum_exposures=1)
    assert [len(group.raw_ids) for group in twilight.groups] == [2, 1]
    ldls = _groups(
        path, "master_ldls", "isolated", maximum_span_hours=3,
        minimum_exposures=3,
    )
    assert len(ldls.groups) == 1
    stats = ldls.groups[0].metadata["ambient_temperature"]
    assert stats == {"count": 2, "mean": 11.0, "median": 11.0,
                     "minimum": 10.0, "maximum": 12.0, "spread": 2.0}
    assert ldls.groups[0].metadata["missing_temperature"] is True
    assert any(item["reason"] == "minimum_exposures_not_met" for item in ldls.exclusions)


def test_hg_cd_are_separate_and_pair_nearest_deterministically(tmp_path):
    path = _catalog(tmp_path, [
        ("20260609T010000.0", "cmp", 30.0, 10.0, "Hg", None),
        ("20260609T011000.0", "cmp", 30.0, 10.1, "mercury", None),
        ("20260609T013000.0", "cmp", 30.0, 10.2, "Cd", None),
        ("20260609T014000.0", "cmp", 30.0, 10.3, "cadmium", None),
    ])
    hg = _groups(path, "master_hg", "isolated", maximum_span_hours=3, minimum_exposures=1)
    cd = _groups(path, "master_cd", "isolated", maximum_span_hours=3, minimum_exposures=1)
    assert hg.groups[0].exposure_ids == ("20260609T010000.0", "20260609T011000.0")
    assert cd.groups[0].exposure_ids == ("20260609T013000.0", "20260609T014000.0")
    pairs, unresolved = pair_lamp_groups(hg.groups, cd.groups)
    assert len(pairs) == 1 and not unresolved
    assert pairs[0][2] == 1800.0
    no_pairs, unresolved = pair_lamp_groups(hg.groups, (), maximum_separation_hours=3)
    assert no_pairs == []
    assert unresolved[0]["reason"] == "no_cd_group_within_pairing_tolerance"


def test_master_sci_strict_eligibility_sufficiency_and_identity_vs_applicability(tmp_path):
    path = _catalog(tmp_path, [
        ("20260609T010000.0", "sci", 300.0, 10.0, None, "dark-a"),
        ("20260609T020000.1", "sci", 301.0, 10.1, None, "dark-a"),
        ("20260609T030000.2", "sci", 700.0, 10.2, None, "dark-a"),
        ("20260609T040000.3", "sci", 900.0, 10.3, None, "dark-a"),
    ])
    monthly = _groups(
        path, "master_sci", "monthly", minimum_exposure_seconds=300,
        minimum_exposures=3, minimum_total_exposure_seconds=1800,
    )
    assert len(monthly.groups) == 1
    group = monthly.groups[0]
    assert group.metadata["total_exposure_seconds"] == 1901.0
    assert group.metadata["sufficiency"]["sufficient"] is True
    assert any(item["exposure_time_seconds"] == 300.0 for item in monthly.exclusions)

    block = _groups(
        path, "master_sci", "observing_block", minimum_exposure_seconds=300,
        minimum_exposures=3, minimum_total_exposure_seconds=1800,
        intervals=[{"name": "explicit-dark", "start": "2026-06-09T00:00:00",
                    "end": "2026-06-10T00:00:00"}],
    )
    assert block.groups[0].raw_ids == group.raw_ids
    assert block.groups[0].computation_id == group.computation_id


def test_master_sci_insufficient_group_is_unresolved(tmp_path):
    path = _catalog(tmp_path, [
        ("20260609T010000.0", "sci", 400.0, None, None, None),
        ("20260609T020000.0", "sci", 400.0, None, None, None),
    ])
    result = _groups(
        path, "master_sci", "monthly", minimum_exposure_seconds=300,
        minimum_exposures=3, minimum_total_exposure_seconds=1800,
    )
    assert result.groups == ()
    assert result.exclusions[-1]["reason"] == "master_sci_insufficient"


def test_master_sci_canonical_task_publication_loading_and_float32(tmp_path):
    path = _catalog(tmp_path, [
        ("20260609T010000.0", "sci", 601.0, 10.0, None, None),
        ("20260609T020000.0", "sci", 602.0, 10.1, None, None),
        ("20260609T030000.0", "sci", 603.0, 10.2, None, None),
    ])
    nodes, edges = default_calibration_graph()
    _, report = ReductionGraph(nodes, edges).plan(db_path=path, scopes=[Scope(ZIP)])
    target = next(item for item in report.planned if item.kind == "master_sci")

    class Loader:
        def load(self, path, tar_member=None, **kwargs):
            index = float(path.rsplit("/", 1)[-1].split(".", 1)[0])
            yy, xx = np.indices((24, 32))
            data = 1000.0 + xx + yy + index
            header = {
                "GAIN": 1.0, "RDNOISE": 3.0, "EXPTIME": 600.0,
                "CCDPOS": "L", "CCDHALF": "L",
            }
            return RawFrameData(data, header, path, tar_member)

    context = TaskContext(path, str(tmp_path / "products"), {"raw_frame_loader": Loader()})
    service = ArtifactService(path)
    publisher = DefaultPublicationService(
        svc=service, policy=DefaultPersistencePolicy(), base_dir=context.workdir,
    )
    shape = (24, 32)
    bias = publisher.publish([ArtifactRequest(
        kind="master_bias",
        components={
            "master": LogicalComponent("master", "array2d", np.zeros(shape)),
            "per_pixel_bias_scatter": LogicalComponent(
                "per_pixel_bias_scatter", "array2d", np.ones(shape),
            ),
        },
        scope=Scope(ZIP),
    )], PublicationContext("fixture", "1", "fixture", "1", {}, [], {}))[0]
    dark = publisher.publish([ArtifactRequest(
        kind="master_dark",
        components={
            "master_dark": LogicalComponent("master_dark", "array2d", np.zeros(shape)),
            "dark_pixel_mask": LogicalComponent(
                "dark_pixel_mask", "array2d", np.zeros(shape, dtype=np.uint8),
            ),
        },
        summaries={
            "reference_exposure_time_seconds": 600.0,
            "bias_convention": "included_in_electron_master",
        },
        scope=Scope(ZIP),
    )], PublicationContext("fixture", "1", "fixture", "1", {}, [], {}))[0]
    artifact = MasterSciTask(context, target=adapt_target(target)).run({
        "bias": {"master_bias": bias},
        "dark": {"master_dark": dark},
    })["master_sci"]
    description = service.describe(artifact.id)
    assert {item["name"] for item in description["components"]} == {"master_sci"}
    assert service.get(artifact.id).metadata["calibration_group"]["sufficiency"]["sufficient"] is True
    stored_dtype = service.load_component(artifact.id, "master_sci")["data"].dtype
    assert stored_dtype.kind == "f" and stored_dtype.itemsize == 4
