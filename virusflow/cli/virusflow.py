from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml

# Prefer a non-interactive Matplotlib backend by default to avoid GUI warnings in workers
import os as _os_mplcfg
_os_mplcfg.environ.setdefault("MPLBACKEND", "Agg")

from ..registry import database as db
from ..storage.filesystem import FileSystemStorage
from ..core.identity import parse_zipcode_key
from .formatting import format_artifacts_table, format_exposures_table


def cmd_init(args: argparse.Namespace) -> None:
    db.init_raw_db(args.raw_db)
    print(f"Initialized registry at {args.raw_db}")


def cmd_scan(args: argparse.Namespace) -> None:
    storage = FileSystemStorage(args.root)
    db.init_raw_db(args.raw_db)
    start_date = getattr(args, "start_date", None)
    end_date = getattr(args, "end_date", None)
    for name, value in (("--start-date", start_date), ("--end-date", end_date)):
        if value:
            try:
                value = str(value).replace("-", "")
                if len(value) != 8 or not value.isdigit():
                    raise ValueError
            except ValueError:
                raise SystemExit(f"Invalid {name} value '{value}'. Use YYYYMMDD.")
            if name == "--start-date":
                start_date = value
            else:
                end_date = value
    if start_date and end_date and start_date > end_date:
        raise SystemExit("--start-date must be on or before --end-date.")
    count = 0
    skipped_outside_window = 0
    skipped_unparseable_date = 0
    zipcode_keys = set()
    indexed_tars = set()
    indexed_date_tars = set()
    reported_dates = set()
    # Unified iteration over both filesystem FITS and FITS inside tar archives
    with db.connect(args.raw_db) as conn:
        for src in storage.iter_raw_sources():
            if start_date or end_date:
                exposure_id, _, _ = db._parse_filename_meta(str(src.tar_member or src.path))
                source_date = exposure_id[:8]
                if len(source_date) != 8 or not source_date.isdigit():
                    skipped_unparseable_date += 1
                    continue
                if ((start_date and source_date < start_date)
                        or (end_date and source_date > end_date)):
                    skipped_outside_window += 1
                    continue
                if source_date not in reported_dates:
                    print(f"Ingesting exposure date {source_date}")
                    reported_dates.add(source_date)
            # For tar-backed members, ensure we have a DB tar index built once per tar
            if src.backend == "tar":
                p = os.path.abspath(str(src.path))
                if p not in indexed_tars:
                    try:
                        db.ensure_tar_index(p, conn=conn)
                    except Exception:
                        pass
                    indexed_tars.add(p)
            elif src.backend == "date_tar":
                p = os.path.abspath(str(src.path))
                key = (p, src.outer_tar_member)
                if key not in indexed_date_tars:
                    try:
                        db.ensure_date_tar_index(p, src.outer_tar_member, conn=conn)
                    except Exception:
                        pass
                    indexed_date_tars.add(key)
            rid = db.register_raw_file(
                str(src.path), db_path=args.raw_db, tar_member=src.tar_member,
                outer_tar_member=src.outer_tar_member, conn=conn,
            )
            if rid is not None:
                count += 1
                if rid.zipcode is not None:
                    zipcode_keys.add(rid.zipcode.key())
    print(f"Registered {count} raw FITS files from {args.root}")
    # Report unique ZipCodes discovered during this scan
    zc_count = len(zipcode_keys)
    print(f"Discovered {zc_count} unique zipcodes")
    if zc_count:
        examples = sorted(zipcode_keys)[: min(5, zc_count)]
        print("Example zipcodes:")
        for z in examples:
            print(f"  - {z}")
    # Report how many tar files were indexed during this scan (DB mode only)
    if indexed_tars:
        print(f"Indexed {len(indexed_tars)} tar files into registry (DB mode)")
    if indexed_date_tars:
        print(f"Indexed {len(indexed_date_tars)} nested date-tar members into registry (DB mode)")
    if start_date or end_date:
        print(
            f"Scan date window {start_date or 'open'}..{end_date or 'open'}: "
            f"skipped {skipped_outside_window} source(s) outside the inclusive bounds"
            + (f" and {skipped_unparseable_date} without a parseable exposure date" if skipped_unparseable_date else "")
        )


def cmd_exposures(args: argparse.Namespace) -> None:
    rows = db.list_exposure_table(
        db_path=args.raw_db,
        start_date=args.start_date,
        end_date=args.end_date,
        requested_target=args.requested_target,
        requested_program=args.requested_program,
        observing_mode=args.observing_mode,
        limit=args.limit,
    )
    if not rows:
        msg = "No exposures found"
        if args.start_date and args.end_date:
            msg += f" in date window {args.start_date}..{args.end_date}"
        print(msg)
        return
    table = format_exposures_table(rows, csv=bool(args.csv))
    if table:
        print(table)


def cmd_artifacts(args: argparse.Namespace) -> None:
    # Optional zipcode filter
    zc = None
    if args.zipcode:
        zc = parse_zipcode_key(args.zipcode)
    at_time = None
    if args.at:
        # Accept YYYYMMDD or full ISO datetime
        s = str(args.at)
        from datetime import datetime
        try:
            if len(s) == 8 and s.isdigit():
                at_time = datetime.strptime(s, "%Y%m%d")
            else:
                at_time = datetime.fromisoformat(s)
        except Exception:
            raise SystemExit(f"Invalid --at value '{args.at}'. Use YYYYMMDD or ISO datetime.")
    # Best-selection mode using policy
    if args.best:
        if not args.kind or not args.zipcode:
            raise SystemExit("--best requires --kind and --zipcode")
        from ..artifacts import ArtifactService, Scope
        svc = ArtifactService(args.db)
        row = svc.select_best(kind=args.kind, scope=Scope(zc), at_time=at_time, policy=(args.policy or "latest_valid"))
        if not row:
            print("No artifacts found")
            return
        rows = [row]
    else:
        rows = db.list_artifacts(kind=args.kind, zipcode=zc, at_time=at_time, db_path=args.db, limit=args.limit)
    if not rows:
        print("No artifacts found")
        return
    # Optionally enrich with sidecar summaries without opening FITS
    if args.summary:
        from ..artifacts import ArtifactService
        svc = ArtifactService(args.db)
        import json as _json
        for r in rows:
            d = svc.describe(r)
            s = d.get("summary") if isinstance(d, dict) else None
            r["summary"] = _json.dumps(s, sort_keys=True) if s is not None else None
    # Optional status filter (client-side) for convenience
    if args.status:
        rows = [r for r in rows if (str(r.get("qa_status") or "").lower() == str(args.status).lower())]
        if not rows:
            print("No artifacts found")
            return
    print(format_artifacts_table(rows, csv=bool(args.csv), include_summary=bool(args.summary)))


def cmd_storage_report(args: argparse.Namespace) -> None:
    from ..artifacts import ArtifactService

    report = ArtifactService(args.db).storage_summary(largest=args.largest)
    print(yaml.safe_dump(report, sort_keys=False))


# ------------------ QA subcommands ------------------

def cmd_qa_show(args: argparse.Namespace) -> None:
    from ..registry import database as _db
    qa = _db.get_qa_results(int(args.artifact_id), db_path=args.db)
    if not qa:
        print("No QA found")
        return
    import yaml as _yaml
    print(_yaml.safe_dump(qa, sort_keys=False))


def cmd_qa_list(args: argparse.Namespace) -> None:
    # Reuse artifacts listing with status filter
    zc = None
    if args.zipcode:
        zc = parse_zipcode_key(args.zipcode)
    rows = db.list_artifacts(kind=args.kind, zipcode=zc, db_path=args.db, limit=args.limit)
    if args.status:
        rows = [r for r in rows if (str(r.get("qa_status") or "").lower() == str(args.status).lower())]
    if not rows:
        print("No artifacts found")
        return
    print(format_artifacts_table(rows, csv=bool(args.csv)))


def cmd_qa_eval(args: argparse.Namespace) -> None:
    """Re-evaluate QA for a single artifact.

    Uses --meta JSON when provided; if --from-summary is set, uses ArtifactService.describe(...)["summary"].
    """
    import json as _json
    from ..artifacts import ArtifactService
    svc = ArtifactService(args.db)
    art_id = int(args.artifact_id)
    kind = args.kind
    meta = None
    if args.meta:
        try:
            meta = _json.loads(args.meta)
        except Exception as e:
            raise SystemExit(f"Invalid --meta JSON: {e}")
    elif args.from_summary:
        try:
            desc = svc.describe(art_id)
            meta = desc.get("summary") if isinstance(desc, dict) else None
        except Exception:
            meta = None
    status = svc.diagnostics.evaluate_and_save(artifact_id=art_id, kind=kind, meta=meta or {})
    print(f"QA evaluate: artifact_id={art_id} kind={kind} status={status}")


# ------------------ Analytics subcommands ------------------

def cmd_analyze(args: argparse.Namespace) -> None:
    from ..analytics.service import AnalyticsService, AnalyticsRequest
    svc = AnalyticsService(args.db)
    params = {
        "out": args.out,
        "zipcode": args.zipcode,
        "limit": args.limit,
        # Trace/Wavelength toggles
        "make_preview": (not getattr(args, "no_preview", False)),
        "make_row_dispersion": (not getattr(args, "no_row_dispersion", False)),
        "make_value_hist": (not getattr(args, "no_value_hist", False)),
        # Calibration study parameters
        "kinds": getattr(args, "kinds", None),
        "make_p95_hist": (not getattr(args, "no_p95_hist", False)),
        "make_badfrac_trend": (not getattr(args, "no_badfrac_trend", False)),
        "make_zero_map": (not getattr(args, "no_zero_map", False)),
        # Instrument health
        "make_throughput_trend": (not getattr(args, "no_throughput_trend", False)),
        "make_bad_fiber_map": (not getattr(args, "no_bad_fiber_map", False)),
        # Trending
        "trend_kind": getattr(args, "trend_kind", None),
        "metric": getattr(args, "metric", None),
        "since": getattr(args, "since", None),
        "until": getattr(args, "until", None),
        # Reports
        "report_kind": getattr(args, "report_kind", None),
    }
    try:
        res = svc.run(AnalyticsRequest(name=args.study, params=params))
    except Exception as e:
        raise SystemExit(f"analyze failed: {e}")
    try:
        print(yaml.safe_dump(res, sort_keys=False))
    except Exception:
        print(res)

# ------------------ Runner ------------------


def resolve_nworkers(*, cli_value=None, serial: bool = False, configured_value=None) -> int:
    """CLI > configuration > four-worker default, with an explicit serial override."""

    if serial:
        return 1
    value = cli_value if cli_value is not None else configured_value
    value = 4 if value is None else int(value)
    if value < 1:
        raise ValueError("nworkers must be at least one; use --serial for serial execution")
    return value


def resolve_progress_config(args: argparse.Namespace, configured=None) -> dict[str, Any]:
    """Resolve CLI overrides over planning configuration and built-in defaults."""

    enabled = getattr(args, "progress", None)
    if enabled is None:
        enabled = getattr(configured, "progress", True)
    mode = getattr(args, "progress_mode", None) or getattr(configured, "progress_mode", "auto")
    interval = getattr(args, "progress_interval", None)
    if interval is None:
        interval = getattr(configured, "progress_interval", 30.0)
    path = getattr(args, "progress_file", None) or getattr(configured, "progress_path", None)
    retries = getattr(args, "max_retries", None)
    if retries is None:
        retries = getattr(configured, "max_retries", 0)
    return {
        "progress": bool(enabled),
        "progress_mode": str(mode),
        "progress_interval": float(interval),
        "progress_path": str(path) if path else None,
        "max_retries": int(retries),
    }


def _write_execution_report(stats: dict[str, Any], workdir: str | Path) -> Path:
    """Persist terminal graph state, including complete task-error evidence."""

    failed = int(stats.get("failed", 0))
    blocked = int(stats.get("blocked", 0))
    terminal_qa = int(stats.get("terminal_qa", 0))
    report = {
        "schema": "virusflow.execution.v1",
        "outcome": (
            "completed_with_task_errors" if failed
            else "completed_with_terminal_qa" if terminal_qa
            else "completed"
        ),
        "summary": {
            "graph_reached_terminal_state": True,
            "total": int(stats.get("total", 0)),
            "succeeded": int(stats.get("succeeded", 0)),
            "task_errors": failed,
            "downstream_blocked": blocked,
            "terminal_qa": terminal_qa,
            "cached": int(stats.get("cached", 0)),
            "skipped": int(stats.get("skipped", 0)),
            "retried": int(stats.get("retried", 0)),
            "elapsed_seconds": float(stats.get("elapsed_seconds", 0.0)),
        },
        "per_kind": stats.get("per_kind", {}),
        # These records retain exception type, reason, and full traceback.
        "task_errors": list(stats.get("failures", [])),
        "blocked_tasks": list(stats.get("blocked_tasks", [])),
    }
    if stats.get("performance_files"):
        report["performance_files"] = stats["performance_files"]
    path = Path(workdir) / "execution_report.yml"
    path.write_text(yaml.safe_dump(report, sort_keys=False))
    return path


def _print_execution_summary(stats: dict[str, Any], report_path: Path) -> None:
    """Describe terminal graph completion without presenting task errors as a crash."""

    total = int(stats.get("total", 0))
    succeeded = int(stats.get("succeeded", 0))
    failed = int(stats.get("failed", 0))
    blocked = int(stats.get("blocked", 0))
    cached = int(stats.get("cached", 0))
    skipped = int(stats.get("skipped", 0))
    terminal_qa = int(stats.get("terminal_qa", 0))
    if failed:
        print(
            f"Calibration run completed: {succeeded}/{total} tasks succeeded."
        )
        print(
            f"Recorded {failed} task error(s); {blocked} dependent task(s) "
            "were not run."
        )
        print("Completed Products were retained and remain available.")
        print(f"Detailed task errors and tracebacks: {report_path}")
        print("Task error summary:")
        for failure in list(stats.get("failures", []))[:5]:
            print(
                f"  - {failure.get('kind', '?')} "
                f"{failure.get('id', '?')}: {failure.get('reason', '')}"
            )
        remaining = failed - min(failed, 5)
        if remaining > 0:
            print(f"  - ... {remaining} additional task error(s); see the report.")
        return
    print(f"Calibration run completed: {succeeded} task(s) executed successfully.")
    print(f"Reused {cached} existing task result(s); {skipped} task(s) were not run.")
    if terminal_qa:
        print(
            f"{terminal_qa} task(s) have recorded terminal QA outcomes; "
            "use --force-replan to recompute them."
        )
    print(f"Execution report: {report_path}")


def _run_planned(args: argparse.Namespace) -> None:
    from ..core.pathutils import ensure_dir
    from ..planning import (
        load_planning_config,
        PlanningConfig,
        default_calibration_graph,
    )
    from ..planning.graph import ReductionGraph
    from ..artifacts.models import Scope
    from ..registry import database as _db

    # Respect --qa-yaml by setting VF_QA_YAML for the QA engine
    try:
        if getattr(args, "qa_yaml", None):
            os.environ["VF_QA_YAML"] = str(args.qa_yaml)
    except Exception:
        pass

    # Load external planning YAML if provided
    cfg_obj: PlanningConfig | None = None
    if getattr(args, "planning_yaml", None):
        cfg_obj = load_planning_config(args.planning_yaml)
    nworkers = resolve_nworkers(
        cli_value=getattr(args, "nworkers", getattr(args, "workers", None)),
        serial=bool(getattr(args, "serial", False)),
        configured_value=getattr(cfg_obj, "nworkers", None),
    )
    # Build default graph and apply overrides
    nodes, edges = default_calibration_graph(cfg_obj)
    # Preflight: validate planning graph
    from ..planning import validate_graph
    validate_graph(nodes, edges)
    G = ReductionGraph(nodes, edges)
    # Determine scopes: list zipcodes that have any raw files in optional date window
    zcs = _db.list_zipcodes(db_path=args.raw_db, frame_type=None, start_date=getattr(args, "plan_start_date", None), end_date=getattr(args, "plan_end_date", None))
    # Optional developer filter: only specified zipcode keys
    only_keys = getattr(args, "only_zipcodes", None)
    if only_keys:
        try:
            want = {s.strip() for s in str(only_keys).split(",") if s.strip()}
            zcs = [zc for zc in zcs if zc.key() in want]
            if not zcs:
                print("Note: --only-zipcodes filter resulted in 0 scopes; nothing to plan.")
        except Exception:
            pass
    scopes = [Scope(zc) for zc in zcs]
    # Build an optional planning window from CLI args
    from ..planning.targets import TemporalWindow as _TemporalWindow
    def _parse_ymd(s: Optional[str]):
        if not s:
            return None
        try:
            from datetime import datetime as _dt
            return _dt.strptime(str(s), "%Y%m%d")
        except Exception:
            return None
    w_start = _parse_ymd(getattr(args, "plan_start_date", None))
    w_end = _parse_ymd(getattr(args, "plan_end_date", None))
    when_win = _TemporalWindow(start=w_start, end=w_end) if (w_start or w_end) else None

    planned, report = G.plan(
        db_path=args.db, raw_db_path=args.raw_db, scopes=scopes, when=when_win,
        force_replan=bool(getattr(args, "force_replan", False)),
    )
    # Persist a compact JSON report.  Large calibration plans serialize much
    # faster and occupy less space than the former human-oriented YAML dump.
    ensure_dir(args.workdir)
    rep_path = Path(args.workdir) / "planning_report.json"
    import json as _json

    def _tgt(t: object) -> dict:
        try:
            # Target from planning layer
            kind = getattr(t, "kind", None)
            scope = getattr(t, "scope", None)
            window = getattr(t, "window", None)
            z = getattr(scope, "zipcode", None)
            zd = {
                "ifuslot": getattr(z, "ifuslot", None),
                "ifuid": getattr(z, "ifuid", None),
                "specid": getattr(z, "specid", None),
                "amp": getattr(z, "amp", None),
                "controller": getattr(z, "controller", None),
            } if z is not None else None
            ws = getattr(window, "start", None)
            we = getattr(window, "end", None)
            return {
                "kind": kind,
                "zipcode": zd,
                "window": {
                    "start": (ws.isoformat() if ws else None),
                    "end": (we.isoformat() if we else None),
                } if window is not None else None,
                "group_id": getattr(getattr(t, "group", None), "group_id", None),
                "computation_identity": getattr(getattr(t, "group", None), "computation_id", None),
            }
        except Exception:
            return {"repr": repr(t)}

    rep = {
        "schema": "virusflow.planning.v2",
        "planned": [_tgt(t) for t in report.planned],
        "existing": [_tgt(t) for t in report.existing],
        "terminal": [_tgt(t) for t in report.terminal],
        "skipped": [_tgt(t) for t in report.skipped],
        "reasons": report.reasons,
        "cadence_groups": report.cadence_groups,
        "grouping_exclusions": report.exclusions,
        "lamp_pairs": report.lamp_pairs,
        "summary": {
            "n_planned": len(report.planned),
            "n_existing": len(report.existing),
            "n_terminal": len(report.terminal),
            "n_skipped": len(report.skipped),
            "n_scopes": len(scopes),
        },
    }
    with rep_path.open("w", encoding="utf-8") as stream:
        _json.dump(
            rep, stream, ensure_ascii=False, separators=(",", ":"), default=str,
        )
    # Print concise summary to stdout
    try:
        s = rep.get("summary", {})
        print(
            f"Planning summary: planned={s.get('n_planned', 0)} "
            f"existing={s.get('n_existing', 0)} terminal={s.get('n_terminal', 0)} "
            f"skipped={s.get('n_skipped', 0)} scopes={s.get('n_scopes', 0)}"
        )
    except Exception:
        pass
    print(f"Planning complete: wrote {rep_path}")
    # Automatic execution of planned calibrations unless --plan-only is passed
    if getattr(args, "plan_only", False):
        return
    # A completed plan should return before importing task implementations and
    # the scientific stack.  Those imports can dominate a true no-op rerun
    # (notably when Matplotlib must initialize its font cache).
    if not report.planned:
        stats = {
            "total": len(report.existing) + len(report.skipped) + len(report.terminal),
            "succeeded": 0,
            "failed": 0,
            "blocked": 0,
            "cached": len(report.existing),
            "skipped": len(report.skipped) + len(report.terminal),
            "retried": 0,
            "elapsed_seconds": 0.0,
            "per_kind": {},
            "failures": [],
            "blocked_tasks": [],
            "terminal_qa": len(report.terminal),
        }
        execution_report_path = _write_execution_report(stats, args.workdir)
        _print_execution_summary(stats, execution_report_path)
        if report.terminal and getattr(args, "strict_task_failures", False):
            raise SystemExit(1)
        return
    # Ensure algorithms I/O knows which registry DB to use for tar member reads during execution
    try:
        from ..algorithms.io import set_registry_db_path as _set_registry_db_path  # type: ignore
        _set_registry_db_path(args.raw_db)
    except Exception:
        pass
    # Execute scheduled tasks
    from ..planning import schedule as _schedule, adapt_target as _adapt_target
    from ..tasks.mapping import default_kind_to_task as _default_kind_to_task
    from ..tasks.base import TaskContext as _TaskContext
    # Build context and mapping
    cfg = {
        "nworkers": nworkers,
        "workers": nworkers,
        "inside_task_worker": False,
        "debug_timing": bool(args.debug_timing),
        "debug_inputs": bool(getattr(args, "debug_inputs", False)),
        "configuration_root": str(Path(args.configuration_root).resolve()),
    }
    ctx = _TaskContext(db_path=args.db, workdir=args.workdir, config=cfg, raw_db_path=args.raw_db)

    def _ctx_factory():
        return ctx

    kind_map = _default_kind_to_task()
    scheduled = _schedule(
        targets=report.planned,
        nodes=nodes,
        edges=edges,
        kind_to_task=kind_map,
        task_context_factory=_ctx_factory,
        target_adapter=_adapt_target,
    )
    ensure_dir(args.workdir)
    if not scheduled:
        stats = {
            "total": len(report.existing) + len(report.skipped) + len(report.terminal),
            "succeeded": 0,
            "failed": 0,
            "blocked": 0,
            "cached": len(report.existing),
            "skipped": len(report.skipped) + len(report.terminal),
            "retried": 0,
            "elapsed_seconds": 0.0,
            "per_kind": {},
            "failures": [],
            "blocked_tasks": [],
            "terminal_qa": len(report.terminal),
        }
        execution_report_path = _write_execution_report(stats, args.workdir)
        _print_execution_summary(stats, execution_report_path)
        if report.terminal and getattr(args, "strict_task_failures", False):
            raise SystemExit(1)
        return
    # Submit to the planning-native executor without the deprecated graph shim.
    from ..executors.planning_executor import (
        PlanningExecutor as _PlanningExecutor,
        WorkflowExecutionError as _WorkflowExecutionError,
    )
    progress_cfg = resolve_progress_config(args, cfg_obj)
    execp = _PlanningExecutor(
        max_workers=nworkers, debug=bool(args.debug_timing), **progress_cfg,
        performance_path=getattr(args, "performance_report", None),
    )
    # Add tasks preserving dependencies
    for st in scheduled:
        execp.add_task(
            st.id, st.task, kind=st.kind, depends_on=st.depends_on,
            success_tolerant_dependencies=st.success_tolerant_dependencies or [],
        )
    execution_error = None
    try:
        execp.run()
    except _WorkflowExecutionError as exc:
        # The graph has reached terminal state and the executor has already
        # retained every task traceback.  Present that as partial completion at
        # the CLI boundary instead of emitting a misleading process traceback.
        execution_error = exc
    stats = getattr(execp, "execution_stats", {}) or {}
    stats["total"] = (
        len(scheduled) + len(report.existing)
        + len(report.skipped) + len(report.terminal)
    )
    stats["cached"] = int(stats.get("cached", 0)) + len(report.existing)
    stats["skipped"] = (
        int(stats.get("skipped", 0)) + len(report.skipped) + len(report.terminal)
    )
    stats["terminal_qa"] = len(report.terminal)
    execution_report_path = _write_execution_report(stats, args.workdir)
    _print_execution_summary(stats, execution_report_path)
    if execution_error is not None and isinstance(
        execution_error.__cause__, (KeyboardInterrupt, SystemExit)
    ):
        raise SystemExit(130)
    if (
        int(stats.get("failed", 0)) or int(stats.get("terminal_qa", 0))
    ) and getattr(args, "strict_task_failures", False):
        raise SystemExit(1)


def cmd_run(args: argparse.Namespace) -> None:
    # Enforce DB mode: require an existing raw-catalog DB path
    if not os.path.exists(args.raw_db):
        raise SystemExit(
            f"Raw catalog DB not found at {args.raw_db}. Initialize and scan first: "
            f"'virusflow init --raw-db {args.raw_db}' then 'virusflow scan --raw-db {args.raw_db} <root>'."
        )
    # Planning-first is the only supported path
    _run_planned(args)


def _json_arg(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return parsed


def _science_context(args: argparse.Namespace):
    from ..tasks.base import TaskContext

    root = Path(getattr(args, "configuration_root", None) or Path.cwd()).resolve()
    cfg = {
        "configuration_root": str(root),
        "fplane_path": str(root / "fplaneall.txt"),
        "preserve_failed_scratch": bool(getattr(args, "preserve_failed_scratch", False)),
    }
    baseline_path = getattr(args, "baseline_response_file", None)
    baseline_artifact_id = getattr(args, "baseline_response_artifact_id", None)
    if baseline_path is not None:
        cfg["baseline_response_path"] = str(Path(baseline_path).resolve())
    if baseline_artifact_id is not None:
        cfg["baseline_response_artifact_id"] = int(baseline_artifact_id)
    extinction_path = getattr(args, "atmospheric_extinction_file", None)
    extinction_artifact_id = getattr(args, "atmospheric_extinction_artifact_id", None)
    if extinction_path is not None:
        cfg["atmospheric_extinction_path"] = str(Path(extinction_path).resolve())
    if extinction_artifact_id is not None:
        cfg["atmospheric_extinction_artifact_id"] = int(extinction_artifact_id)
    return TaskContext(str(args.db), str(Path(args.workdir).resolve()), cfg, raw_db_path=str(args.raw_db))


def _science_executor(args: argparse.Namespace):
    from ..executors.planning_executor import PlanningExecutor

    workers = resolve_nworkers(
        cli_value=getattr(args, "nworkers", None), serial=bool(getattr(args, "serial", False))
    )
    return PlanningExecutor(
        max_workers=workers, **resolve_progress_config(args),
        performance_path=getattr(args, "performance_report", None),
    ), workers


def _result_manifest(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _result_manifest(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_result_manifest(child) for child in value]
    if hasattr(value, "id"):
        return {"artifact_id": int(value.id), "kind": getattr(value, "kind", None)}
    if hasattr(value, "exposure_id"):
        return {"run_local_state": type(value).__name__, "exposure_id": value.exposure_id}
    return str(value)


def cmd_run_exposure(args: argparse.Namespace) -> None:
    from datetime import datetime
    from ..planning.targets import ExposureTarget
    from ..tasks.exposure import ExposureTask

    if not Path(args.raw_db).exists():
        raise SystemExit(f"Raw catalog DB not found: {args.raw_db}; run 'virusflow init' and 'virusflow scan' first")
    try:
        at = datetime.strptime(args.exposure_id, "%Y%m%dT%H%M%S.%f")
    except ValueError as exc:
        raise SystemExit("--exposure-id must use YYYYMMDDTHHMMSS.s") from exc
    executor, workers = _science_executor(args)
    executor.add_task(
        args.exposure_id, ExposureTask(_science_context(args), target=ExposureTarget(args.exposure_id, at)),
        kind="exposure", target=f"exposure_id={args.exposure_id}",
    )
    result = executor.run()[args.exposure_id]
    print(yaml.safe_dump({"workers": workers, "result": _result_manifest(result)}, sort_keys=False))


def cmd_run_observation(args: argparse.Namespace) -> None:
    from datetime import datetime
    from ..planning.targets import ExposureTarget, ObservationTarget
    from ..registry.database import observation_exposure_ids
    from ..tasks.exposure import ExposureTask
    from ..tasks.observation import ObservationTask

    explicit = tuple(args.exposure_id or ())
    exposure_ids = explicit or tuple(observation_exposure_ids(args.observation_id, db_path=args.raw_db))
    if not exposure_ids:
        raise SystemExit(f"No scanned science exposures found for {args.observation_id}")
    dither_set_id = args.dither_set_id or f"{args.observation_id}-DITHER"
    context = _science_context(args)
    executor, workers = _science_executor(args)
    for exposure_id in exposure_ids:
        try:
            at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
        except ValueError as exc:
            raise SystemExit(f"invalid exposure identity: {exposure_id}") from exc
        executor.add_task(
            exposure_id, ExposureTask(context, target=ExposureTarget(exposure_id, at)),
            kind="exposure", target=f"exposure_id={exposure_id}",
        )
    node_id = f"observation:{args.observation_id}"
    executor.add_task(
        node_id,
        ObservationTask(context, target=ObservationTarget(args.observation_id, dither_set_id, tuple(exposure_ids))),
        kind="observation", depends_on=list(exposure_ids), target=f"observation_id={args.observation_id}",
    )
    result = executor.run()[node_id]
    print(yaml.safe_dump({
        "workers": workers, "observation_id": args.observation_id,
        "exposure_ids": list(exposure_ids), "result": _result_manifest(result),
    }, sort_keys=False))


def cmd_artifact_show(args: argparse.Namespace) -> None:
    from ..artifacts import ArtifactService
    print(yaml.safe_dump(ArtifactService(args.db).describe(args.artifact_id), sort_keys=False))


def cmd_models(args: argparse.Namespace) -> None:
    from ..artifacts import ArtifactService
    from ..ontology.lifecycle import ArtifactLifecycle
    service = ArtifactService(args.db)
    rows = [
        row for row in service.adapter.list_all(kind=args.kind)
        if str(row.get("lifecycle") or "") == ArtifactLifecycle.MODEL.value
        or str(row.get("canonical_kind") or row.get("kind") or "").startswith("candidate_")
    ]
    if args.state:
        rows = [row for row in rows if str(row.get("state") or "active") == args.state]
    print(yaml.safe_dump([service.describe(row) for row in rows[: args.limit]], sort_keys=False))


def cmd_study_list(args: argparse.Namespace) -> None:
    from ..registry import database as _db
    with _db.connect(args.db) as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT study_id,scientific_question,retention_policy,expected_bytes,materialized_bytes,state,created_at,completed_at FROM analysis_studies ORDER BY created_at"
        ).fetchall()]
    print(yaml.safe_dump(rows, sort_keys=False))


def cmd_study_show(args: argparse.Namespace) -> None:
    from ..analytics.materialization import AnalysisStudyService
    print(yaml.safe_dump(AnalysisStudyService(args.db, args.output_dir).get(args.study_id).__dict__, sort_keys=False))


def cmd_study_create(args: argparse.Namespace) -> None:
    from ..analytics.materialization import AnalysisStudyService
    record = AnalysisStudyService(args.db, args.output_dir).create(
        scientific_question=args.question, selection=args.selection,
        selected_observations=args.observation, model_versions=args.model_versions,
        calibration_versions=args.calibration_versions, software_version=args.software_version,
        algorithm_versions=args.algorithm_versions, intermediate_kinds=args.intermediate_kind,
        retention_policy=args.retention, expected_bytes=args.expected_bytes, study_id=args.study_id,
    )
    print(yaml.safe_dump(record.__dict__, sort_keys=False))


def cmd_study_complete(args: argparse.Namespace) -> None:
    from ..analytics.materialization import AnalysisStudyService
    AnalysisStudyService(args.db, args.output_dir).complete(args.study_id, summary=args.summary)
    print(f"Completed analysis study {args.study_id}")


def cmd_study_validate(args: argparse.Namespace) -> None:
    from ..analytics.materialization import AnalysisStudyService
    AnalysisStudyService(args.db, args.output_dir).record_validation(
        args.study_id, candidate_artifact_id=args.candidate_artifact_id,
        metrics=args.metrics, comparison=args.comparison, decision=args.decision,
    )
    print(f"Recorded validation for candidate {args.candidate_artifact_id}; no promotion was performed")


def cmd_cleanup(args: argparse.Namespace) -> None:
    from ..storage.cleanup import cleanup_cache, cleanup_legacy, cleanup_scratch
    if args.cleanup_cmd == "scratch":
        report = cleanup_scratch(args.workdir, execute=args.execute)
    elif args.cleanup_cmd == "cache":
        report = cleanup_cache(args.db, execute=args.execute)
    else:
        report = cleanup_legacy(
            args.db, deactivate=args.deactivate, delete_payloads=args.delete_payloads,
            validation_succeeded=args.validation_succeeded,
        )
    print(yaml.safe_dump(report.as_dict(), sort_keys=False))


def cmd_config_show(args: argparse.Namespace) -> None:
    from ..planning import load_planning_config
    configured = load_planning_config(args.planning_yaml) if args.planning_yaml else None
    payload = {
        "db": str(Path(args.db).resolve()),
        "raw_db": str(Path(args.raw_db).resolve()),
        "workdir": str(Path(args.workdir).resolve()),
        "scratch_root": str((Path(args.workdir).resolve() / ".scratch")),
        "configuration_root": str(Path(args.configuration_root).resolve()),
        "workers": resolve_nworkers(cli_value=args.nworkers, serial=args.serial, configured_value=getattr(configured, "nworkers", None)),
        **resolve_progress_config(args, configured),
    }
    print(yaml.safe_dump(payload, sort_keys=False))


def cmd_performance_show(args: argparse.Namespace) -> None:
    report = json.loads(Path(args.report).read_text())
    summary = {
        "run_id": report.get("run_id"), "status": report.get("status"),
        "wall_seconds": report.get("wall_seconds"),
        "workers_configured": report.get("workers_configured"),
        "worker_utilization": report.get("worker_utilization"),
        "critical_path": report.get("critical_path"),
        "task_kind_summary": report.get("task_kind_summary"),
        "raw_io": report.get("raw_io"), "database": report.get("database"),
    }
    print(yaml.safe_dump(summary, sort_keys=False))


def cmd_performance_compare(args: argparse.Namespace) -> None:
    from ..performance import compare_artifact_registries, compare_performance_reports

    comparison = compare_performance_reports(
        json.loads(Path(args.before).read_text()), json.loads(Path(args.after).read_text())
    )
    if bool(args.before_db) != bool(args.after_db):
        raise SystemExit("--before-db and --after-db must be supplied together")
    if args.before_db:
        scientific = compare_artifact_registries(args.before_db, args.after_db)
        comparison["scientific_equivalence"] = scientific
        if args.scientific_output:
            Path(args.scientific_output).write_text(
                json.dumps(scientific, indent=2, sort_keys=True) + "\n"
            )
    text = json.dumps(comparison, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text)
        print(json.dumps({
            "output": str(Path(args.output).resolve()),
            "wall_seconds": comparison["wall_seconds"],
            "scientific_equivalence_passed": (
                comparison.get("scientific_equivalence") or {}
            ).get("passed"),
        }, indent=2, sort_keys=True))
    else:
        print(text, end="")


def cmd_performance_overhead(args: argparse.Namespace) -> None:
    from ..performance import measure_instrumentation_overhead

    result = measure_instrumentation_overhead(args.iterations)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text)
    print(text, end="")


def cmd_validate_observation(args: argparse.Namespace) -> None:
    from .verify_steps_8_10 import main as verify_main
    workers = resolve_nworkers(cli_value=args.nworkers, serial=args.serial)
    mode = args.progress_mode or "plain"
    forwarded = [
        "--data-root", args.data_root, "--workspace", args.workspace,
        "--output-dir", args.output_dir, "--workers", str(workers),
        "--progress-mode", mode,
    ]
    if args.serial:
        forwarded.append("--serial")
    if args.progress_file:
        forwarded.extend(["--progress-file", args.progress_file])
    if args.progress_interval is not None:
        forwarded.extend(["--progress-interval", str(args.progress_interval)])
    if args.reference_workspace:
        forwarded.extend(["--reference-workspace", args.reference_workspace])
    raise SystemExit(verify_main(forwarded))


def _add_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db", default=os.environ.get("VIRUSFLOW_DB", str(Path.cwd() / "virusflow.sqlite3")),
        help="Artifact/Product registry SQLite path (default: VIRUSFLOW_DB or ./virusflow.sqlite3)",
    )


def _add_raw_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--raw-db", default=os.environ.get("VIRUSFLOW_RAW_DB", str(Path.cwd() / "virusflow_raw.sqlite3")),
        help="Raw-frame catalog SQLite path (default: VIRUSFLOW_RAW_DB or ./virusflow_raw.sqlite3)",
    )


def _add_progress(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--nworkers", "--workers", dest="nworkers", type=int, help="Task workers (default: 4)")
    parser.add_argument("--serial", action="store_true", help="Force one worker")
    toggle = parser.add_mutually_exclusive_group()
    toggle.add_argument("--progress", dest="progress", action="store_true", default=None)
    toggle.add_argument("--no-progress", dest="progress", action="store_false")
    parser.add_argument("--progress-mode", choices=["auto", "tty", "plain", "json"], default=None)
    parser.add_argument("--progress-interval", type=float, default=None, help="Batch heartbeat seconds")
    parser.add_argument("--progress-file", help="Append structured JSONL progress")
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--performance-report", help="Write performance JSON and Markdown")


def _add_science_run(parser: argparse.ArgumentParser) -> None:
    _add_db(parser)
    _add_raw_db(parser)
    _add_progress(parser)
    parser.add_argument("--workdir", default=os.environ.get("VIRUSFLOW_WORKDIR", str(Path.cwd() / "work")))
    parser.add_argument("--configuration-root", default=os.environ.get("VIRUSFLOW_CONFIG_ROOT", str(Path.cwd())))
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument(
        "--baseline-response-file",
        help="Select and import one four-column baseline_relative_response payload",
    )
    baseline.add_argument(
        "--baseline-response-artifact-id", type=int,
        help="Select one existing baseline_relative_response Product by Artifact ID",
    )
    extinction = parser.add_mutually_exclusive_group()
    extinction.add_argument(
        "--atmospheric-extinction-file",
        help="Select and import one two- or four-column atmospheric_extinction_model payload",
    )
    extinction.add_argument(
        "--atmospheric-extinction-artifact-id", type=int,
        help="Select one existing atmospheric_extinction_model Product by Artifact ID",
    )
    parser.add_argument("--preserve-failed-scratch", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="virusflow", description="Artifact-driven VIRUS reduction and validation")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="Initialize a registry")
    _add_raw_db(sp); sp.set_defaults(func=cmd_init)
    sp = sub.add_parser("scan", help="Register raw FITS inputs")
    _add_raw_db(sp)
    sp.add_argument("--start-date", help="Inclusive UTC acquisition date (YYYYMMDD)")
    sp.add_argument("--end-date", help="Inclusive UTC acquisition date (YYYYMMDD)")
    sp.add_argument("root")
    sp.set_defaults(func=cmd_scan)
    sp = sub.add_parser("exposures", help="List scanned exposures")
    _add_raw_db(sp)
    sp.add_argument("--start-date")
    sp.add_argument("--end-date")
    sp.add_argument("--requested-target")
    sp.add_argument("--requested-program")
    sp.add_argument("--observing-mode", choices=["primary", "parallel", "calibration"])
    sp.add_argument("--limit", type=int)
    sp.add_argument("--csv", action="store_true")
    sp.set_defaults(func=cmd_exposures)
    run = sub.add_parser("run", help="Execute the task graph").add_subparsers(dest="run_cmd", required=True)
    cal = run.add_parser("calibrations", help="Plan and execute calibration products")
    _add_science_run(cal)
    cal.add_argument(
        "--planning-yaml",
        help="Canonical calibration node/edge overrides and execution defaults",
    ); cal.add_argument("--plan-only", action="store_true")
    cal.add_argument("--start-date", dest="plan_start_date"); cal.add_argument("--end-date", dest="plan_end_date")
    cal.add_argument("--only-zipcodes"); cal.add_argument("--force-replan", action="store_true")
    cal.add_argument("--qa-yaml"); cal.add_argument("--debug-timing", action="store_true"); cal.add_argument("--debug-inputs", action="store_true")
    cal.add_argument(
        "--strict-task-failures", action="store_true",
        help="Return status 1 after terminal graph completion when any task had an error",
    )
    cal.set_defaults(func=cmd_run)
    exp = run.add_parser("exposure", help="Reduce one atomic science exposure")
    _add_science_run(exp); exp.add_argument("--exposure-id", required=True); exp.set_defaults(func=cmd_run_exposure)
    obs = run.add_parser("observation", help="Reduce registry-derived observation membership")
    _add_science_run(obs); obs.add_argument("--observation-id", required=True); obs.add_argument("--dither-set-id"); obs.add_argument("--exposure-id", action="append", help="Explicit member override (repeatable)"); obs.set_defaults(func=cmd_run_observation)

    artifact = sub.add_parser("artifact", help="Inspect artifacts and provenance").add_subparsers(dest="artifact_cmd", required=True)
    al = artifact.add_parser("list"); _add_db(al)
    al.add_argument("--kind", help="Canonical kind; deprecated names are read-only lookup aliases"); al.add_argument("--zipcode"); al.add_argument("--at"); al.add_argument("--limit", type=int); al.add_argument("--csv", action="store_true"); al.add_argument("--summary", action="store_true"); al.add_argument("--best", action="store_true"); al.add_argument("--policy", choices=["latest_valid", "latest"]); al.add_argument("--status"); al.set_defaults(func=cmd_artifacts)
    ash = artifact.add_parser("show"); _add_db(ash); ash.add_argument("artifact_id", type=int); ash.set_defaults(func=cmd_artifact_show)

    model = sub.add_parser("model", help="Inspect accepted and candidate models").add_subparsers(dest="model_cmd", required=True)
    ml = model.add_parser("list"); _add_db(ml); ml.add_argument("--kind"); ml.add_argument("--state"); ml.add_argument("--limit", type=int, default=100); ml.set_defaults(func=cmd_models)
    ms = model.add_parser("show"); _add_db(ms); ms.add_argument("artifact_id", type=int); ms.set_defaults(func=cmd_artifact_show)

    storage = sub.add_parser("storage", help="Report persistent storage").add_subparsers(dest="storage_cmd", required=True)
    sr = storage.add_parser("report"); _add_db(sr); sr.add_argument("--largest", type=int, default=10); sr.set_defaults(func=cmd_storage_report)
    cleanup = sub.add_parser("cleanup", help="Inventory by default; mutate only with explicit flags").add_subparsers(dest="cleanup_cmd", required=True)
    cs = cleanup.add_parser("scratch"); cs.add_argument("--workdir", required=True); cs.add_argument("--execute", action="store_true"); cs.set_defaults(func=cmd_cleanup)
    cc = cleanup.add_parser("cache"); _add_db(cc); cc.add_argument("--execute", action="store_true"); cc.set_defaults(func=cmd_cleanup)
    cl = cleanup.add_parser("legacy", help="Inventory or explicitly retire superseded records"); _add_db(cl); cl.add_argument("--deactivate", action="store_true", help="Mark registry records obsolete but retain payloads"); cl.add_argument("--delete-payloads", action="store_true", help="Delete payloads (requires deactivation and validation gate)"); cl.add_argument("--validation-succeeded", action="store_true", help="Confirm representative validation before payload deletion"); cl.set_defaults(func=cmd_cleanup)

    qa = sub.add_parser("qa", help="Inspect and evaluate QA").add_subparsers(dest="qa_cmd", required=True)
    ql = qa.add_parser("list"); _add_db(ql); ql.add_argument("--kind"); ql.add_argument("--zipcode"); ql.add_argument("--status"); ql.add_argument("--limit", type=int); ql.add_argument("--csv", action="store_true"); ql.set_defaults(func=cmd_qa_list)
    qs = qa.add_parser("show"); _add_db(qs); qs.add_argument("--artifact-id", required=True); qs.set_defaults(func=cmd_qa_show)
    qe = qa.add_parser("evaluate"); _add_db(qe); qe.add_argument("--artifact-id", required=True); qe.add_argument("--kind", required=True); qe.add_argument("--meta"); qe.add_argument("--from-summary", action="store_true"); qe.set_defaults(func=cmd_qa_eval)

    study = sub.add_parser("study", help="Manage bounded analysis studies").add_subparsers(dest="study_cmd", required=True)
    sl = study.add_parser("list"); _add_db(sl); sl.set_defaults(func=cmd_study_list)
    ss = study.add_parser("show"); _add_db(ss); ss.add_argument("study_id"); ss.add_argument("--output-dir", default="analysis"); ss.set_defaults(func=cmd_study_show)
    sc = study.add_parser("create"); _add_db(sc); sc.add_argument("--study-id"); sc.add_argument("--question", required=True); sc.add_argument("--selection", type=_json_arg, required=True); sc.add_argument("--observation", action="append", default=[]); sc.add_argument("--model-versions", type=_json_arg, default={}); sc.add_argument("--calibration-versions", type=_json_arg, default={}); sc.add_argument("--software-version", default="unknown"); sc.add_argument("--algorithm-versions", type=_json_arg, default={}); sc.add_argument("--intermediate-kind", action="append", required=True); sc.add_argument("--retention", choices=["none", "selected", "outliers", "all", "until_study_completion", "permanent"], default="selected"); sc.add_argument("--expected-bytes", type=int, required=True); sc.add_argument("--output-dir", default="analysis"); sc.set_defaults(func=cmd_study_create)
    sx = study.add_parser("complete"); _add_db(sx); sx.add_argument("study_id"); sx.add_argument("--summary", type=_json_arg, required=True); sx.add_argument("--output-dir", default="analysis"); sx.set_defaults(func=cmd_study_complete)
    sv = study.add_parser("validate"); _add_db(sv); sv.add_argument("study_id"); sv.add_argument("--candidate-artifact-id", type=int, required=True); sv.add_argument("--metrics", type=_json_arg, required=True); sv.add_argument("--comparison", type=_json_arg, required=True); sv.add_argument("--decision", required=True); sv.add_argument("--output-dir", default="analysis"); sv.set_defaults(func=cmd_study_validate)

    analyze = sub.add_parser("analyze", help="Run read-only production analytics"); _add_db(analyze)
    analyze.add_argument("--study", required=True, choices=["trace", "wavelength", "calibration", "instrument_health", "trending", "reports"]); analyze.add_argument("--zipcode"); analyze.add_argument("--limit", type=int); analyze.add_argument("--out", required=True)
    for flag in ("no-preview", "no-row-dispersion", "no-value-hist", "no-p95-hist", "no-badfrac-trend", "no-zero-map", "no-throughput-trend", "no-bad-fiber-map"): analyze.add_argument(f"--{flag}", action="store_true")
    analyze.add_argument("--kinds"); analyze.add_argument("--kind", dest="trend_kind"); analyze.add_argument("--metric"); analyze.add_argument("--since"); analyze.add_argument("--until"); analyze.add_argument("--report-kind", choices=["daily_calib", "weekly_health"]); analyze.set_defaults(func=cmd_analyze)

    config = sub.add_parser("config", help="Show effective execution configuration").add_subparsers(dest="config_cmd", required=True)
    show = config.add_parser("show"); _add_science_run(show); show.add_argument("--planning-yaml"); show.set_defaults(func=cmd_config_show)
    performance = sub.add_parser("performance", help="Inspect or compare performance reports").add_subparsers(dest="performance_cmd", required=True)
    ps = performance.add_parser("show"); ps.add_argument("report"); ps.set_defaults(func=cmd_performance_show)
    pc = performance.add_parser("compare"); pc.add_argument("before"); pc.add_argument("after"); pc.add_argument("--output"); pc.add_argument("--before-db"); pc.add_argument("--after-db"); pc.add_argument("--scientific-output"); pc.set_defaults(func=cmd_performance_compare)
    po = performance.add_parser("overhead"); po.add_argument("--iterations", type=int, default=100000); po.add_argument("--output"); po.set_defaults(func=cmd_performance_overhead)
    validate = sub.add_parser("validate", help="Run representative scientific validation").add_subparsers(dest="validate_cmd", required=True)
    vo = validate.add_parser("observation"); vo.add_argument("--data-root", required=True); vo.add_argument("--workspace", required=True); vo.add_argument("--output-dir", required=True); vo.add_argument("--reference-workspace"); _add_progress(vo); vo.set_defaults(func=cmd_validate_observation)
    return p


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
