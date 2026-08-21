from __future__ import annotations

from pathlib import Path

import pytest

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
    )
    return benchmark.RunResult(
        strategy=strategy,
        total_seconds=total,
        discovery_seconds=1.0 if strategy == "A" else None,
        header_seconds=2.0 if strategy == "A" else None,
        records=[record],
        headers=headers,
        stats=stats,
    )


def _patch_strategies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def run_a(path: Path) -> benchmark.RunResult:
        calls.append("A")
        return _result("A", 3.0)

    def run_b(path: Path) -> benchmark.RunResult:
        calls.append("B")
        return _result("B", 4.0)

    monkeypatch.setattr(benchmark, "_strategy_a", run_a)
    monkeypatch.setattr(benchmark, "_strategy_b", run_b)
    return calls


def _tar_path(tmp_path: Path, name: str = "virus0000001.tar") -> Path:
    path = tmp_path / name
    path.write_bytes(b"test")
    return path


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


def test_single_strategy_aggregate_has_no_comparison_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys,
):
    calls = _patch_strategies(monkeypatch)
    paths = [_tar_path(tmp_path, "virus0000001.tar"), _tar_path(tmp_path, "virus0000002.tar")]

    assert benchmark.main(["--strategy", "A", *(str(path) for path in paths)]) == 0

    output = capsys.readouterr().out
    assert calls == ["A", "A"]
    assert "Aggregate" in output
    assert "Strategy A total:" in output
    assert "Strategy B total:" not in output
    assert "B/A ratio" not in output
    assert "speedup" not in output
    assert "seconds saved" not in output
    assert "percent saved" not in output
