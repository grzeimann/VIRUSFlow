from __future__ import annotations

import yaml

from virusflow.cli.virusflow import (
    _print_execution_summary,
    _write_execution_report,
    build_parser,
)


def _partial_stats():
    return {
        "total": 5700,
        "succeeded": 5677,
        "failed": 3,
        "blocked": 20,
        "cached": 0,
        "skipped": 0,
        "retried": 0,
        "elapsed_seconds": 1540.0,
        "per_kind": {
            "master_bias": {
                "total": 300, "succeeded": 297, "failed": 3,
                "blocked": 0, "retried": 0,
            },
        },
        "failures": [{
            "id": "master_bias:028:042:413:RL",
            "kind": "master_bias",
            "reason": "RuntimeError: QA hard-fail; read_noise=146.99 electron",
            "exception_type": "RuntimeError",
            "traceback": "Traceback (most recent call last):\n  full diagnostic\n",
            "attempts": 1,
        }],
        "blocked_tasks": [{
            "id": "master_dark:028:042:413:RL",
            "kind": "master_dark",
            "reason": "blocked by master_bias:028:042:413:RL",
        }],
    }


def test_partial_calibration_completion_is_reported_without_console_traceback(
    tmp_path, capsys,
):
    stats = _partial_stats()
    report_path = _write_execution_report(stats, tmp_path)
    _print_execution_summary(stats, report_path)

    output = capsys.readouterr().out
    assert "Calibration run completed: 5677/5700 tasks succeeded" in output
    assert "Recorded 3 task error(s)" in output
    assert "20 dependent task(s) were not run" in output
    assert "Traceback (most recent call last)" not in output
    assert str(report_path) in output

    report = yaml.safe_load(report_path.read_text())
    assert report["outcome"] == "completed_with_task_errors"
    assert report["summary"]["graph_reached_terminal_state"] is True
    assert report["summary"]["task_errors"] == 3
    assert report["task_errors"][0]["traceback"].startswith("Traceback")
    assert report["blocked_tasks"][0]["kind"] == "master_dark"


def test_strict_task_failure_mode_is_explicit_opt_in():
    parser = build_parser()
    normal = parser.parse_args(["run", "calibrations"])
    strict = parser.parse_args([
        "run", "calibrations", "--strict-task-failures",
    ])

    assert normal.strict_task_failures is False
    assert strict.strict_task_failures is True


def test_terminal_qa_rerun_is_described_as_reused_evidence(tmp_path, capsys):
    stats = {
        "total": 5700,
        "succeeded": 0,
        "failed": 0,
        "blocked": 0,
        "cached": 5677,
        "skipped": 23,
        "terminal_qa": 23,
    }
    report_path = _write_execution_report(stats, tmp_path)
    _print_execution_summary(stats, report_path)

    output = capsys.readouterr().out
    assert "0 task(s) executed successfully" in output
    assert "Reused 5677 existing task result(s)" in output
    assert "23 task(s) have recorded terminal QA outcomes" in output
    assert "--force-replan" in output
    report = yaml.safe_load(report_path.read_text())
    assert report["outcome"] == "completed_with_terminal_qa"
    assert report["summary"]["terminal_qa"] == 23
