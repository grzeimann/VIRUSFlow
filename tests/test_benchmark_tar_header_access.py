from __future__ import annotations

import io
from pathlib import Path
import tarfile

import numpy as np
import pytest
from astropy.io import fits

import scripts.benchmark_tar_header_access as benchmark


def _result(strategy: str, total: float) -> benchmark.RunResult:
    record = benchmark.MemberRecord("sample.fits", 512, 2880)
    headers = [{card: None for card in benchmark.REPRESENTATIVE_CARDS}]
    stats = benchmark.IOStats(
        archive_opens=1,
        header_file_opens=1 if strategy == "A" else 0,
        member_scans=1,
        member_list_scan_passes=1,
        header_reads=1,
        header_bytes=2880,
        archive_bytes_read=4096 if strategy == "C" else 0,
        payload_bytes_discarded=1024 if strategy == "C" else 0,
    )
    return benchmark.RunResult(
        strategy=strategy,
        total_seconds=total,
        discovery_seconds=1.0 if strategy == "A" else None,
        header_seconds=2.0 if strategy == "A" else None,
        records=[record],
        headers=headers,
        stats=stats,
        logical_stream_bytes=4096 if strategy == "C" else None,
    )


def _patch_strategies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def run_a(path: Path) -> benchmark.RunResult:
        calls.append("A")
        return _result("A", 3.0)

    def run_b(path: Path) -> benchmark.RunResult:
        calls.append("B")
        return _result("B", 4.0)

    def run_c(path: Path) -> benchmark.RunResult:
        calls.append("C")
        return _result("C", 5.0)

    monkeypatch.setattr(benchmark, "_strategy_a", run_a)
    monkeypatch.setattr(benchmark, "_strategy_b", run_b)
    monkeypatch.setattr(benchmark, "_strategy_c", run_c)
    return calls


def _tar_path(tmp_path: Path, name: str = "virus0000001.tar") -> Path:
    path = tmp_path / name
    path.write_bytes(b"test")
    return path


def _build_small_fits_tar(tmp_path: Path) -> tuple[Path, bytes]:
    hdu = fits.PrimaryHDU(data=np.arange(16, dtype=np.int16).reshape(4, 4))
    hdu.header["DATE"] = "2026-08-21T00:00:00"
    hdu.header["EXPTIME"] = 12.5
    hdu.header["PEXPTIME"] = 13.5
    hdu.header["QPROG"] = "fixture-program"
    hdu.header["OBJECT"] = "fixture-object"
    hdu.header["QOBJECT"] = "fixture-qobject"
    hdu.header["IFUSLOT"] = 13
    hdu.header["IFUID"] = "043"
    hdu.header["SPECID"] = 412
    hdu.header["CONTID"] = "S/N 0021"
    hdu.header["AMPNAME"] = "LR"
    hdu.header["CCDPOS"] = "L"
    hdu.header["CCDHALF"] = "R"
    fits_bytes = io.BytesIO()
    hdu.writeto(fits_bytes)

    tar_path = tmp_path / "virus0000001.tar"
    member_name = "nested/" + ("long-name-" * 12) + ".fits"
    info = tarfile.TarInfo(member_name)
    info.size = len(fits_bytes.getvalue())
    with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as archive:
        note = tarfile.TarInfo("notes.txt")
        note_bytes = b"non-FITS member\n"
        note.size = len(note_bytes)
        archive.addfile(note, io.BytesIO(note_bytes))
        archive.addfile(info, io.BytesIO(fits_bytes.getvalue()))
    return tar_path, fits_bytes.getvalue()


def _build_small_corral_tar(tmp_path: Path) -> tuple[Path, Path, str]:
    inner_path, _ = _build_small_fits_tar(tmp_path)
    outer_path = tmp_path / "20260604.tar"
    inner_name = "virus/virus0000001.tar"
    unrelated_name = "virus/virus0000002.tar"
    inner_bytes = inner_path.read_bytes()
    with tarfile.open(outer_path, "w") as outer:
        unrelated = tarfile.TarInfo(unrelated_name)
        unrelated_bytes = b"not an inner tar"
        unrelated.size = len(unrelated_bytes)
        outer.addfile(unrelated, io.BytesIO(unrelated_bytes))
        selected = tarfile.TarInfo(inner_name)
        selected.size = len(inner_bytes)
        outer.addfile(selected, io.BytesIO(inner_bytes))
    return outer_path, inner_path, inner_name


def test_strategy_a_runs_a_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    calls = _patch_strategies(monkeypatch)

    assert benchmark.main(["--strategy", "A", str(_tar_path(tmp_path))]) == 0

    output = capsys.readouterr().out
    assert calls == ["A"]
    assert "Execution mode: Strategy A only" in output
    assert "Strategy A: two-phase" in output
    assert "B/A ratio" not in output
    assert "speedup" not in output


def test_strategy_b_runs_b_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    calls = _patch_strategies(monkeypatch)

    assert benchmark.main(["--strategy", "B", str(_tar_path(tmp_path))]) == 0

    output = capsys.readouterr().out
    assert calls == ["B"]
    assert "Execution mode: Strategy B only" in output
    assert "Strategy B: ordered acquisition" in output
    assert "B/A ratio" not in output
    assert "speedup" not in output


def test_strategy_c_runs_c_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    calls = _patch_strategies(monkeypatch)

    assert benchmark.main(["--strategy", "C", str(_tar_path(tmp_path))]) == 0

    output = capsys.readouterr().out
    assert calls == ["C"]
    assert "Execution mode: Strategy C only" in output
    assert "Strategy C: sequential stream" in output
    assert "speedup" not in output
    assert "B/A ratio" not in output
    assert "C/B ratio" not in output


def test_strategy_c_matches_a_on_small_pax_tar_and_discards_payload(tmp_path: Path):
    tar_path, fits_bytes = _build_small_fits_tar(tmp_path)

    c = benchmark._strategy_c(tar_path)
    a = benchmark._strategy_a(tar_path)

    assert c.records == a.records
    assert c.headers == a.headers
    assert len(c.records) == 1
    assert c.stats.header_reads == 1
    assert c.stats.header_bytes < len(fits_bytes)
    assert c.stats.payload_bytes_discarded == len(fits_bytes) - c.stats.header_bytes
    assert c.stats.archive_opens == 1
    assert c.stats.seek_calls == 0
    assert c.stats.backward_seek_calls == 0
    assert c.stats.stream_forward_seek_calls >= 1
    assert c.stats.archive_bytes_read == tar_path.stat().st_size


def test_strategy_c_does_not_open_fits_image_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    tar_path, _ = _build_small_fits_tar(tmp_path)

    def fail_if_image_api_is_used(*args, **kwargs):
        raise AssertionError("Strategy C must not open FITS image HDUs")

    monkeypatch.setattr(benchmark.fits, "open", fail_if_image_api_is_used)
    result = benchmark._strategy_c(tar_path)

    assert result.stats.header_reads == 1
    assert result.stats.payload_bytes_discarded > 0


def test_corral_inner_strategy_b_matches_standalone_inner_tar(tmp_path: Path):
    outer_path, inner_path, inner_name = _build_small_corral_tar(tmp_path)

    nested = benchmark._strategy_b_corral(outer_path, inner_name)
    standalone = benchmark._strategy_a(inner_path)

    assert nested.records == standalone.records
    assert nested.headers == standalone.headers
    assert nested.outer_stats.archive_opens == 1
    assert nested.outer_members_examined == 2
    assert nested.inner_size == inner_path.stat().st_size
    assert nested.inner_stats.archive_opens == 1
    assert nested.inner_stats.header_reads == len(nested.records) == 1
    assert nested.inner_stats.payload_bytes_discarded == 0
    assert nested.inner_stats.payload_skip_seek_calls >= 1
    assert nested.outer_inner_seek_calls > 0
    assert nested.outer_stats.corral_outer_next_seek_calls >= 1
    assert nested.outer_stats.corral_outer_fits_next_seek_calls >= 1
    assert nested.outer_inner_backward_seeks == 0
    assert nested.inner_stats.backward_seek_calls == 0
    assert nested.outer_inner_payload_bytes_read < nested.records[0].size


def test_corral_inner_mode_is_exclusive_with_strategy(tmp_path: Path):
    with pytest.raises(SystemExit):
        benchmark._parser().parse_args([
            "--strategy", "B", "--corral-inner", "virus/virus0000001.tar",
            str(tmp_path / "20260604.tar"),
        ])


def test_corral_inner_cli_report_is_self_contained(tmp_path: Path, capsys):
    outer_path, _, inner_name = _build_small_corral_tar(tmp_path)

    assert benchmark.main(["--corral-inner", inner_name, str(outer_path)]) == 0

    output = capsys.readouterr().out
    assert "Execution mode: selected inner Strategy B only" in output
    assert "open + member-location time:" in output
    assert "setup + open time:" in output
    assert "traversal + headers time:" in output
    assert "outer-file seeks through ExFileObject/_FileInFile" in output
    assert "FITS image/payload bytes physically read from outer:" in output
    assert "FITS payload remainder read/discarded by benchmark: NO" in output
    assert "Total time:" in output
    assert "speedup" not in output


def test_strategy_and_order_are_mutually_exclusive(tmp_path: Path):
    with pytest.raises(SystemExit):
        benchmark._parser().parse_args([
            "--strategy", "A", "--order", "B-A", str(_tar_path(tmp_path)),
        ])


@pytest.mark.parametrize(
    ("order", "expected_calls"),
    [("A-B", ["A", "B"]), ("B-A", ["B", "A"])],
)
def test_existing_two_strategy_orders_remain(monkeypatch, tmp_path, capsys, order, expected_calls):
    calls = _patch_strategies(monkeypatch)

    assert benchmark.main(["--order", order, str(_tar_path(tmp_path))]) == 0

    output = capsys.readouterr().out
    assert calls == expected_calls
    assert f"Execution order for each tar: {order}" in output
    assert "Correctness:" in output
    assert "B/A ratio" in output


def test_default_two_strategy_order_remains_a_b(monkeypatch, tmp_path, capsys):
    calls = _patch_strategies(monkeypatch)

    assert benchmark.main([str(_tar_path(tmp_path))]) == 0

    output = capsys.readouterr().out
    assert calls == ["A", "B"]
    assert "Execution order for each tar: A-B" in output


@pytest.mark.parametrize("strategy", ["A", "C"])
def test_single_strategy_aggregate_has_no_comparison_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys,
    strategy: str,
):
    calls = _patch_strategies(monkeypatch)
    paths = [_tar_path(tmp_path, "virus0000001.tar"), _tar_path(tmp_path, "virus0000002.tar")]

    assert benchmark.main([
        "--strategy", strategy, *(str(path) for path in paths)
    ]) == 0

    output = capsys.readouterr().out
    assert calls == [strategy, strategy]
    assert "Aggregate" in output
    assert f"Strategy {strategy} total:" in output
    assert "Strategy A total:" not in output or strategy == "A"
    assert "Strategy B total:" not in output
    assert "B/A ratio" not in output
    assert "speedup" not in output
    assert "seconds saved" not in output
    assert "percent saved" not in output
