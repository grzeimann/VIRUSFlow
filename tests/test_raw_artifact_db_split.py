from __future__ import annotations

import io
import tarfile
from pathlib import Path

import numpy as np
from astropy.io import fits

from virusflow.artifacts import ArtifactService
from virusflow.artifacts.models import Scope
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.cli.virusflow import build_parser
from virusflow.core.identity import ZipCode
from virusflow.io.raw import RawFrameLoader
from virusflow.persistence.policy import DefaultPersistencePolicy
from virusflow.publication.context import PublicationContext
from virusflow.publication.service import DefaultPublicationService
from virusflow.registry import database as db
from virusflow.storage.filesystem import FileSystemStorage, read_member_bytes
from virusflow.tasks.base import TaskContext


def _write_raw(path: Path, **header_values) -> None:
    header = fits.Header({
        "IFUID": "043", "SPECID": "412", "CONTID": "S/N 0021", **header_values,
    })
    fits.PrimaryHDU(np.arange(4, dtype=float).reshape(2, 2), header=header).writeto(path)


def _tables(db_path: Path) -> set[str]:
    with db.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_init_creates_only_the_raw_catalog_database(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw_db = tmp_path / "raw.sqlite3"
    default_artifact_db = tmp_path / "virusflow.sqlite3"
    parser = build_parser()
    args = parser.parse_args(["init", "--raw-db", str(raw_db)])
    args.func(args)
    assert raw_db.exists()
    assert not default_artifact_db.exists()
    tables = _tables(raw_db)
    assert {"exposures", "raw_files", "amplifiers"} <= tables
    assert "artifacts" not in tables


def test_scan_populates_only_the_raw_database(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _write_raw(
        data_root / "20260501T010000.0_074LL_sci.fits",
        OBJECT="Target_082_W", QOBJECT="Target", AIRMASS=1.22,
    )
    raw_db = tmp_path / "raw.sqlite3"
    artifact_db = tmp_path / "artifact.sqlite3"

    parser = build_parser()
    args = parser.parse_args(["scan", "--raw-db", str(raw_db), str(data_root)])
    args.func(args)

    assert raw_db.exists()
    rows = db.list_raw_files(db_path=str(raw_db))
    assert len(rows) == 1
    raw_id = db.list_raw_file_rows(
        "20260501T010000.0", db_path=str(raw_db)
    )[0][0]
    assert db.list_raw_scientific_metadata(
        [raw_id], db_path=str(raw_db)
    )[0]["airmass"] == 1.22
    assert db.get_exposure_metadata(
        "20260501T010000.0", db_path=str(raw_db)
    )["airmass"] == 1.22

    svc = ArtifactService(str(artifact_db))
    assert svc.select_best(kind="master_bias", scope=Scope(zipcode=None)) is None
    assert not artifact_db.exists() or "raw_files" not in _tables(artifact_db)


def test_bounded_scan_uses_work_night_and_keeps_actual_exposure_date(
    tmp_path: Path, capsys,
):
    """Night-container membership is independent of the FITS exposure date."""
    root = tmp_path / "maverick"
    source = tmp_path / "source.fits"
    _write_raw(source)
    for night, exposure_date in (("20260601", "20260602"), ("20260602", "20260603")):
        archive = root / night / "virus" / "virus0000001.tar"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, mode="w") as tf:
            tf.add(source, arcname=f"{exposure_date}T010000.0_074LL_sci.fits")
        acm = root / night / "acm" / "acm0000001.tar"
        acm.parent.mkdir(parents=True)
        with tarfile.open(acm, mode="w") as tf:
            tf.add(source, arcname=f"{exposure_date}T010000.0_074LL_sci.fits")

    raw_db = tmp_path / "raw.sqlite3"
    args = build_parser().parse_args([
        "scan", "--raw-db", str(raw_db), "--first-night", "20260601",
        "--last-night", "20260601", str(root),
    ])
    args.func(args)

    rows = db.list_raw_files(db_path=str(raw_db))
    assert [row.exposure_id for row in rows] == ["20260602T010000.0"]
    assert "/20260601/virus/" in rows[0].path
    output = capsys.readouterr().out
    assert "Scanning night 20260601" in output
    assert "/acm/" not in output


def test_bounded_scan_prunes_corral_date_tars_and_nonvirus_nested_archives(
    tmp_path: Path, monkeypatch,
):
    """Only selected Corral outer nights and nested VIRUS archives are opened."""
    root = tmp_path / "date_tars"
    root.mkdir()
    config_fits = root / "virus_config" / "labflats" / "pixelflat_cam004_LL.fits"
    config_fits.parent.mkdir(parents=True)
    _write_raw(config_fits)
    source = tmp_path / "source.fits"
    _write_raw(source)
    for date in ("20260501", "20260502", "20260503"):
        with tarfile.open(root / f"{date}.tar", mode="w") as outer:
            for instrument, archive_name in (("virus", "virus0000001.tar"), ("acm", "acm0000001.tar")):
                inner_bytes = io.BytesIO()
                with tarfile.open(fileobj=inner_bytes, mode="w") as inner:
                    inner.add(source, arcname=f"{date}T010000.0_074LL_sci.fits")
                info = tarfile.TarInfo(name=f"{instrument}/{archive_name}")
                info.size = len(inner_bytes.getvalue())
                outer.addfile(info, io.BytesIO(inner_bytes.getvalue()))

    opened = []
    original_open = tarfile.open

    def recording_open(name=None, *args, **kwargs):
        if isinstance(name, (str, Path)):
            opened.append(Path(name).name)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr("virusflow.storage.filesystem.tarfile.open", recording_open)
    sources = list(FileSystemStorage(root).iter_raw_sources(
        first_night="20260501", last_night="20260502",
    ))

    assert {source.path.name for source in sources} == {"20260501.tar", "20260502.tar"}
    assert {source.outer_tar_member for source in sources} == {"virus/virus0000001.tar"}
    assert "20260503.tar" not in opened
    assert "acm0000001.tar" not in opened


def test_bounded_work_scan_never_walks_unrelated_night_subtrees(tmp_path: Path):
    root = tmp_path / "maverick"
    root.mkdir()
    config_fits = root / "virus_config" / "labflats" / "pixelflat_cam004_LL.fits"
    config_fits.parent.mkdir(parents=True)
    _write_raw(config_fits)
    source = tmp_path / "source.fits"
    _write_raw(source)
    for date in ("20260429", "20260430", "20260501", "20260502", "20260503", "20260504"):
        archive = root / date / "virus" / "virus0000001.tar"
        archive.parent.mkdir(parents=True)
        with tarfile.open(archive, mode="w") as tf:
            tf.add(source, arcname=f"{date}T010000.0_074LL_sci.fits")
        acm_archive = root / date / "acm" / "acm0000001.tar"
        acm_archive.parent.mkdir(parents=True)
        with tarfile.open(acm_archive, mode="w") as tf:
            tf.add(source, arcname=f"{date}T010000.0_074LL_sci.fits")

    sources = list(FileSystemStorage(root).iter_raw_sources(
        first_night="20260501", last_night="20260502",
    ))

    assert {source.path.parent.parent.name for source in sources} == {
        "20260501", "20260502",
    }


def test_bounded_scan_keeps_generic_fallback_for_unfamiliar_roots(tmp_path: Path):
    root = tmp_path / "unfamiliar"
    root.mkdir()
    raw_path = root / "frame_074LL_sci.fits"
    _write_raw(raw_path)

    sources = list(FileSystemStorage(root).iter_raw_sources(
        first_night="20260501", last_night="20260501",
    ))

    assert [source.path for source in sources] == [raw_path]


def test_artifact_publication_writes_only_to_the_artifact_database(tmp_path: Path):
    raw_db = tmp_path / "raw.sqlite3"
    artifact_db = tmp_path / "artifact.sqlite3"
    db.init_raw_db(str(raw_db))

    svc = ArtifactService(str(artifact_db))
    publisher = DefaultPublicationService(
        svc=svc, policy=DefaultPersistencePolicy(), base_dir=str(tmp_path / "products"),
    )
    data = np.ones((4, 4), dtype=float)
    publisher.publish([ArtifactRequest(
        kind="master_ldls",
        components={
            "master_ldls": LogicalComponent("master_ldls", "array2d", data),
            "flat_response_mask": LogicalComponent(
                "flat_response_mask", "array2d", np.zeros_like(data, dtype=np.uint8)
            ),
        },
        scope=Scope(zipcode=None),
    )], PublicationContext("flat", "v2", "flat", "1", {}, [], {}))

    artifact_tables = _tables(artifact_db)
    assert "artifacts" in artifact_tables
    raw_tables = _tables(raw_db)
    assert "raw_files" in raw_tables
    with db.connect(str(raw_db)) as conn:
        count = conn.execute("SELECT count(*) FROM raw_files").fetchone()[0]
    assert count == 0


def test_task_context_reads_raw_db_and_publishes_to_artifact_db(tmp_path: Path):
    from virusflow.tasks.calibs import BiasTask

    data_root = tmp_path / "data"
    data_root.mkdir()
    for amp in ("074LL", "074LU"):
        _write_raw(data_root / f"20260501T010000.0_{amp}_zro.fits")

    raw_db = tmp_path / "raw.sqlite3"
    artifact_db = tmp_path / "artifact.sqlite3"
    db.init_raw_db(str(raw_db))
    with db.connect(str(raw_db)) as conn:
        for source in FileSystemStorage(data_root).iter_raw_sources():
            db.register_raw_file(str(source.path), db_path=str(raw_db), conn=conn)

    ctx = TaskContext(str(artifact_db), str(tmp_path / "work"), {}, raw_db_path=str(raw_db))
    zipcode = ZipCode(ifuslot="074", ifuid="043", specid="412", amp="LL", controller="S/N 0021")
    target = type(
        "Target", (), {
            "zipcode": zipcode, "start_date": "20260501", "end_date": "20260501",
            "raw_ids": (),
        },
    )()
    task = BiasTask(ctx, target=target)
    raw_inputs, parent_ids = task.query_inputs()
    assert len(raw_inputs) == 1
    assert not Path(artifact_db).exists() or "raw_files" not in _tables(artifact_db)


def test_filesystem_and_tar_backends_still_enumerate_correctly(tmp_path: Path, capsys):
    fs_root = tmp_path / "fs"
    fs_root.mkdir()
    _write_raw(fs_root / "20260501T010000.0_074LL_sci.fits")
    fs_sources = list(FileSystemStorage(fs_root).iter_raw_sources())
    assert len(fs_sources) == 1
    assert fs_sources[0].backend == "filesystem"

    tar_root = tmp_path / "tar"
    tar_root.mkdir()
    fits_path = tmp_path / "20260501T010000.0_074LL_sci.fits"
    _write_raw(fits_path)
    tar_path = tar_root / "virus0000001.tar"
    with tarfile.open(tar_path, "w") as tf:
        tf.add(fits_path, arcname="20260501T010000.0_074LL_sci.fits")
    tar_sources = list(FileSystemStorage(tar_root).iter_raw_sources())
    assert len(tar_sources) == 1
    assert tar_sources[0].backend == "tar"
    assert tar_sources[0].tar_member == "20260501T010000.0_074LL_sci.fits"

    raw_db = tmp_path / "tar_raw.sqlite3"
    parser = build_parser()
    args = parser.parse_args(["scan", "--raw-db", str(raw_db), str(tar_root)])
    args.func(args)
    output = capsys.readouterr().out
    assert f"Ingesting tar {tar_path.resolve()}" in output
    assert f"Finished tar {tar_path.resolve()}: 1 raw sources, 1 registered" in output


def test_date_tar_backend_reads_nested_virus_tar(tmp_path: Path):
    fits_path = tmp_path / "20260501T010000.0_074LL_sci.fits"
    _write_raw(fits_path, OBJECT="Target_082_W", QOBJECT="Target")
    original_bytes = fits_path.read_bytes()

    virus_tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=virus_tar_bytes, mode="w") as inner:
        inner.add(fits_path, arcname="20260501T010000.0_074LL_sci.fits")
    virus_tar_bytes.seek(0)

    date_root = tmp_path / "date_root"
    date_root.mkdir()
    date_tar_path = date_root / "20260501.tar"
    virus_tar_info = tarfile.TarInfo(name="virus/virus0000001.tar")
    virus_tar_info.size = len(virus_tar_bytes.getvalue())
    with tarfile.open(date_tar_path, "w") as outer:
        virus_tar_bytes.seek(0)
        outer.addfile(virus_tar_info, virus_tar_bytes)

    sources = list(FileSystemStorage(date_root).iter_raw_sources())
    assert len(sources) == 1
    source = sources[0]
    assert source.backend == "date_tar"
    assert source.outer_tar_member == "virus/virus0000001.tar"
    assert source.tar_member == "20260501T010000.0_074LL_sci.fits"

    blob = read_member_bytes(source)
    assert blob == original_bytes

    raw_db = tmp_path / "raw.sqlite3"
    db.init_raw_db(str(raw_db))
    with db.connect(str(raw_db)) as conn:
        raw_id = db.register_raw_file(
            str(source.path), db_path=str(raw_db), tar_member=source.tar_member,
            outer_tar_member=source.outer_tar_member, conn=conn,
        )
    assert raw_id is not None
    assert raw_id.storage_backend == "date_tar"
    assert raw_id.outer_tar_member == "virus/virus0000001.tar"

    loader = RawFrameLoader()
    frame = loader.load_ref(raw_id)
    with fits.open(io.BytesIO(original_bytes)) as hdul:
        expected = np.asarray(hdul[0].data)
    assert np.array_equal(frame.data, expected)


def _build_date_tar(tmp_path: Path, *, amp_names: list[str], ifuslot: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    fits_paths = []
    for amp in amp_names:
        p = tmp_path / f"20260501T010000.0_{ifuslot}{amp}_sci.fits"
        _write_raw(p, OBJECT="Target_082_W", QOBJECT="Target")
        fits_paths.append(p)

    virus_tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=virus_tar_bytes, mode="w") as inner:
        for p in fits_paths:
            inner.add(p, arcname=p.name)
    virus_tar_bytes.seek(0)

    date_root = tmp_path / "date_root"
    date_root.mkdir()
    date_tar_path = date_root / "20260501.tar"
    virus_tar_info = tarfile.TarInfo(name="virus/virus0000001.tar")
    virus_tar_info.size = len(virus_tar_bytes.getvalue())
    with tarfile.open(date_tar_path, "w") as outer:
        virus_tar_bytes.seek(0)
        outer.addfile(virus_tar_info, virus_tar_bytes)
    return date_root


def test_date_tar_offset_index_matches_unindexed_metadata(tmp_path: Path):
    unindexed_root = _build_date_tar(tmp_path / "unindexed", amp_names=["LL", "LU"], ifuslot="074")
    indexed_root = _build_date_tar(tmp_path / "indexed", amp_names=["LL", "LU"], ifuslot="075")

    db._IFUSLOT_META_CACHE.clear()
    unindexed_db = tmp_path / "unindexed.sqlite3"
    db.init_raw_db(str(unindexed_db))
    unindexed_ids = []
    with db.connect(str(unindexed_db)) as conn:
        for source in FileSystemStorage(unindexed_root).iter_raw_sources():
            unindexed_ids.append(db.register_raw_file(
                str(source.path), db_path=str(unindexed_db), tar_member=source.tar_member,
                outer_tar_member=source.outer_tar_member, conn=conn,
            ))

    db._IFUSLOT_META_CACHE.clear()
    indexed_db = tmp_path / "indexed.sqlite3"
    db.init_raw_db(str(indexed_db))
    indexed_ids = []
    with db.connect(str(indexed_db)) as conn:
        for source in FileSystemStorage(indexed_root).iter_raw_sources():
            db.ensure_date_tar_index(
                str(source.path), source.outer_tar_member, conn=conn,
            )
            indexed_ids.append(db.register_raw_file(
                str(source.path), db_path=str(indexed_db), tar_member=source.tar_member,
                outer_tar_member=source.outer_tar_member, conn=conn,
            ))

    assert len(unindexed_ids) == len(indexed_ids) == 2
    for unindexed_id, indexed_id in zip(
        sorted(unindexed_ids, key=lambda r: r.tar_member),
        sorted(indexed_ids, key=lambda r: r.tar_member),
    ):
        assert unindexed_id.zipcode.ifuid == indexed_id.zipcode.ifuid
        assert unindexed_id.zipcode.specid == indexed_id.zipcode.specid
        assert unindexed_id.zipcode.controller == indexed_id.zipcode.controller
        assert unindexed_id.zipcode.amp == indexed_id.zipcode.amp

    with db.connect(str(indexed_db)) as conn:
        rows = conn.execute(
            "SELECT member, offset, size FROM date_tar_members WHERE date_tar_path=? AND outer_member=?",
            (str((indexed_root / "20260501.tar").resolve()), "virus/virus0000001.tar"),
        ).fetchall()
    assert len(rows) == 2
    for member, offset, size in rows:
        hdr = db._read_header_via_tar_offset(str(indexed_root / "20260501.tar"), offset)
        assert hdr is not None
        assert hdr.get("IFUID") == "043"


def test_date_tar_index_powers_raw_frame_loader_fast_path(tmp_path: Path):
    root = _build_date_tar(tmp_path, amp_names=["LL", "LU"], ifuslot="074")
    raw_db = tmp_path / "raw.sqlite3"
    db.init_raw_db(str(raw_db))
    with db.connect(str(raw_db)) as conn:
        for source in FileSystemStorage(root).iter_raw_sources():
            db.ensure_date_tar_index(str(source.path), source.outer_tar_member, conn=conn)
            db.register_raw_file(
                str(source.path), db_path=str(raw_db), tar_member=source.tar_member,
                outer_tar_member=source.outer_tar_member, conn=conn,
            )

    resolved = db.list_raw_files_scoped(
        "sci", "20260501", "20260501", db_path=str(raw_db),
    )
    assert len(resolved) == 2
    for _, raw_id in resolved:
        assert raw_id.storage_backend == "date_tar"
        assert raw_id.outer_tar_member == "virus/virus0000001.tar"
        assert raw_id.archive_offset is not None
        assert raw_id.archive_size is not None

    loader = RawFrameLoader()
    for _, raw_id in resolved:
        frame = loader.load_ref(raw_id)
        assert frame.data.shape == (2, 2)
    # No fallback tarfile.open() should have run: the loader's archive handle
    # cache should hold exactly one open handle (shared across both amps).
    assert len(loader._archive_handles) == 1
    loader.close()
