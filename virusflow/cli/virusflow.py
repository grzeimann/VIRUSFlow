from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Optional, List

import yaml

# Prefer a non-interactive Matplotlib backend by default to avoid GUI warnings in workers
import os as _os_mplcfg
_os_mplcfg.environ.setdefault("MPLBACKEND", "Agg")

from ..registry import database as db
from ..storage.filesystem import FileSystemStorage
from ..tasks import available_tasks, get_task_class
from ..tasks.base import TaskContext
from ..core.identity import ZipCode, parse_zipcode_key
from .formatting import format_artifacts_table, format_exposures_table


def cmd_init(args: argparse.Namespace) -> None:
    db.init_db(args.db)
    print(f"Initialized registry at {args.db}")


def cmd_scan(args: argparse.Namespace) -> None:
    storage = FileSystemStorage(args.root)
    db.init_db(args.db)
    count = 0
    zipcode_keys = set()
    indexed_tars = set()
    # Unified iteration over both filesystem FITS and FITS inside tar archives
    with db.connect(args.db) as conn:
        for src in storage.iter_raw_sources():
            # For tar-backed members, ensure we have a DB tar index built once per tar
            if src.backend == "tar":
                p = os.path.abspath(str(src.path))
                if p not in indexed_tars:
                    try:
                        db.ensure_tar_index(p, conn=conn)
                    except Exception:
                        pass
                    indexed_tars.add(p)
            rid = db.register_raw_file(str(src.path), db_path=args.db, tar_member=src.tar_member, conn=conn)
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


def cmd_tasks(args: argparse.Namespace) -> None:
    av = available_tasks()
    print(yaml.safe_dump(av, sort_keys=False))


def cmd_exposures(args: argparse.Namespace) -> None:
    rows = db.list_exposure_table(db_path=args.db, start_date=args.start_date, end_date=args.end_date, limit=args.limit)
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


def cmd_storage_migrate(args: argparse.Namespace) -> None:
    from ..artifacts.migration import migrate_stages_8_10_storage

    result = migrate_stages_8_10_storage(
        args.db, delete_payloads=bool(args.delete_payloads)
    )
    print(yaml.safe_dump(result.__dict__, sort_keys=False))


def cmd_scratch_cleanup(args: argparse.Namespace) -> None:
    from ..storage.scratch import cleanup_abandoned_scratch

    removed = cleanup_abandoned_scratch(args.workdir)
    print(f"Removed {removed} abandoned scratch files from {args.workdir}")


# ------------------ QA subcommands ------------------

def cmd_qa_set(args: argparse.Namespace) -> None:
    from ..registry import database as _db
    import json as _json
    metrics = None
    if args.metrics:
        try:
            metrics = _json.loads(args.metrics)
        except Exception as e:
            raise SystemExit(f"Invalid JSON for --metrics: {e}")
    from ..artifacts import ArtifactService
    svc = ArtifactService(args.db)
    svc.set_diagnostics(int(args.artifact_id), status=args.status, metrics=metrics)
    print(f"Saved QA for artifact {args.artifact_id}: status={args.status}")


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


essential_kinds = {"master_bias", "master_dark", "master_flat", "master_cmp", "trace", "wave"}


def cmd_qa_backfill(args: argparse.Namespace) -> None:
    """Re-evaluate QA for many artifacts, optionally filtered by kind and date.

    By default uses summary-derived meta when --from-summary is set; otherwise passes an empty meta.
    """
    import datetime as _dt
    from ..artifacts import ArtifactService
    svc = ArtifactService(args.db)
    rows = db.list_artifacts(kind=args.kind, zipcode=None, db_path=args.db, limit=args.limit)
    # Optional since filter based on created_at when present
    if args.since:
        try:
            since_dt = _dt.datetime.strptime(str(args.since), "%Y%m%d")
        except Exception:
            raise SystemExit("--since must be YYYYMMDD")
        def _ok(r):
            ca = r.get("created_at")
            if not ca:
                return True
            try:
                return _dt.datetime.fromisoformat(str(ca)) >= since_dt
            except Exception:
                return True
        rows = [r for r in rows if _ok(r)]
    n = 0
    n_fail = 0
    for r in rows:
        try:
            art_id = int(r.get("id"))
            kind = args.kind or str(r.get("kind"))
            meta = None
            if args.from_summary:
                try:
                    desc = svc.describe(r)
                    meta = desc.get("summary") if isinstance(desc, dict) else None
                except Exception:
                    meta = None
            if args.dry_run:
                print(f"[dry-run] would evaluate artifact_id={art_id} kind={kind}")
                continue
            status = svc.diagnostics.evaluate_and_save(artifact_id=art_id, kind=kind, meta=meta or {})
            print(f"artifact_id={art_id} kind={kind} status={status}")
            n += 1
            if str(status).lower() == "fail":
                n_fail += 1
        except Exception as e:
            print(f"error evaluating artifact id={r.get('id')}: {e}")
    if not args.dry_run:
        print(f"Backfill complete: evaluated={n}, failures={n_fail}")

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

# ------------------ Debugging helpers ------------------

def cmd_debug_raw(args: argparse.Namespace) -> None:
    """Probe raw inputs for a zipcode and frame type without running algorithms.

    Prints a concise table and optionally attempts a single CCD base reduction on the first item.
    """
    from ..registry import database as _db
    import os as _os
    import tarfile as _tarfile
    from astropy.io import fits as _fits
    # Ensure algorithms I/O knows which registry DB to use for tar member reads
    try:
        from ..algorithms.io import set_registry_db_path as _set_registry_db_path
        _set_registry_db_path(args.db)
    except Exception:
        pass

    zc = parse_zipcode_key(args.zipcode) if getattr(args, "zipcode", None) else None
    if zc is None:
        print("--zipcode is required (IFUSLOT+IFUID+SPECID+AMP+CONTROLLER)")
        return
    sd = args.start_date or "19000101"
    ed = args.end_date or "21000101"
    rows = _db.list_raw_files_scoped(frame_type=args.frame_type, start_date=sd, end_date=ed, zipcode=zc, db_path=args.db)
    if not rows:
        print("No raw files found for the given filters.")
        return
    print(f"Found {len(rows)} raw files for frame_type={args.frame_type} zipcode={zc.key()} {sd}..{ed}")
    # Show up to N samples
    N = int(args.limit or 10)
    for (rid, rf) in rows[:N]:
        exists = _os.path.exists(rf.path)
        info = f"id={rid} path={rf.path} member={rf.tar_member} backend={rf.storage_backend} exists={exists}"
        print(" - " + info)
        # For tar members, optionally verify readability
        if args.verify and rf.storage_backend == "tar" and rf.tar_member and exists:
            try:
                with _tarfile.open(rf.path, mode="r:") as tf:
                    m = tf.getmember(rf.tar_member)
                    ef = tf.extractfile(m) if m is not None else None
                    if ef is None:
                        print("   [verify] cannot extract member (None)")
                    else:
                        with _fits.open(ef, memmap=False):
                            print("   [verify] FITS header read OK")
            except Exception as e:
                print(f"   [verify] error: {e}")
    # Optional single reduce_raw_amplifier_frame probe
    if args.probe:
        try:
            import virusflow.algorithms.ccd as _ccd
        except Exception as e:  # pragma: no cover
            print(f"Probe unavailable (import error): {e}")
            return
        (rid0, rf0) = rows[0]
        try:
            img, _hdr = _ccd.reduce_raw_amplifier_frame(rf0.path, rf0.tar_member, return_header=False)
            print(f"Probe success: shape={getattr(img, 'shape', None)} from path={rf0.path} member={rf0.tar_member}")
        except Exception as e:
            print(f"Probe failed: {e}")

# ------------------ Planner subcommands ------------------


def cmd_plan_calibrations(args: argparse.Namespace) -> None:
    # Legacy plan-file generation has been removed. Use the planning-first run path instead.
    raise SystemExit("'virusflow plan calibrations' is removed. Use 'virusflow run --plan-start-date YYYYMMDD --plan-end-date YYYYMMDD' for planning-first execution.")


def cmd_plan_night(args: argparse.Namespace) -> None:
    # Stub: produce an empty plan with a note for now
    plan = {"tasks": [], "note": f"night planning for date {args.date} not yet implemented"}
    print(yaml.safe_dump(plan, sort_keys=False))


def cmd_plan_exposure(args: argparse.Namespace) -> None:
    plan = {"tasks": [], "note": f"exposure planning for {args.exposure_id} not yet implemented"}
    print(yaml.safe_dump(plan, sort_keys=False))


def cmd_plan_observation_set(args: argparse.Namespace) -> None:
    plan = {"tasks": [], "note": f"observation-set planning for {args.name} not yet implemented"}
    print(yaml.safe_dump(plan, sort_keys=False))


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
    zcs = _db.list_zipcodes(db_path=args.db, frame_type=None, start_date=getattr(args, "plan_start_date", None), end_date=getattr(args, "plan_end_date", None))
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

    planned, report = G.plan(db_path=args.db, scopes=scopes, when=when_win, force_replan=bool(getattr(args, "force_replan", False)))
    # Persist a simple planning report YAML alongside workdir
    ensure_dir(args.workdir)
    rep_path = Path(args.workdir) / "planning_report.yml"
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
            }
        except Exception:
            return {"repr": repr(t)}

    rep = {
        "planned": [_tgt(t) for t in report.planned],
        "existing": [_tgt(t) for t in report.existing],
        "skipped": [_tgt(t) for t in report.skipped],
        "reasons": report.reasons,
        "summary": {
            "n_planned": len(report.planned),
            "n_existing": len(report.existing),
            "n_skipped": len(report.skipped),
            "n_scopes": len(scopes),
        },
    }
    try:
        import yaml as _yaml
        rep_text = _yaml.safe_dump(rep, sort_keys=False)
    except Exception:
        rep_text = _json.dumps(rep, indent=2, sort_keys=True)
    rep_path.write_text(rep_text)
    # Print concise summary to stdout
    try:
        s = rep.get("summary", {})
        print(f"Planning summary: planned={s.get('n_planned', 0)} existing={s.get('n_existing', 0)} skipped={s.get('n_skipped', 0)} scopes={s.get('n_scopes', 0)}")
    except Exception:
        pass
    print(f"Planning complete: wrote {rep_path}")
    # Automatic execution of planned calibrations unless --plan-only is passed
    if getattr(args, "plan_only", False):
        return
    # Ensure algorithms I/O knows which registry DB to use for tar member reads during execution
    try:
        from ..algorithms.io import set_registry_db_path as _set_registry_db_path  # type: ignore
        _set_registry_db_path(args.db)
    except Exception:
        pass
    # Execute scheduled tasks
    from ..planning import schedule as _schedule, adapt_target as _adapt_target
    from ..tasks.mapping import default_kind_to_task as _default_kind_to_task
    from ..tasks.base import TaskContext as _TaskContext
    if _schedule is None:
        raise RuntimeError("planning.schedule is unavailable")
    # Build context and mapping
    cfg = {"nworkers": nworkers, "workers": nworkers, "inside_task_worker": False, "debug_timing": bool(args.debug_timing), "debug_inputs": bool(getattr(args, "debug_inputs", False))}
    if getattr(args, "qa_out_dir", None):
        cfg["qa_out_dir"] = args.qa_out_dir
    # Experimental mapping helper controls
    if getattr(args, "use_mapping_helper", False):
        cfg["use_mapping_helper"] = True
    if getattr(args, "mapping_tolerance_days", None) is not None:
        cfg["mapping_tolerance_days"] = int(args.mapping_tolerance_days)
    ctx = _TaskContext(db_path=args.db, workdir=args.workdir, config=cfg)

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
    # Submit to the planning-native executor without the deprecated graph shim.
    from ..executors.planning_executor import PlanningExecutor as _PlanningExecutor
    progress_cfg = resolve_progress_config(args, cfg_obj)
    execp = _PlanningExecutor(
        max_workers=nworkers, debug=bool(args.debug_timing), **progress_cfg,
        performance_path=getattr(args, "performance_report", None),
    )
    # Add tasks preserving dependencies
    for st in scheduled:
        execp.add_task(st.id, st.task, kind=st.kind, depends_on=st.depends_on)
    for index, target in enumerate(report.existing):
        execp.add_observed(
            f"cached:{index}:{getattr(target, 'kind', 'target')}",
            kind=getattr(target, "kind", "target"), state="cached", target=repr(target),
        )
    for index, target in enumerate(report.skipped):
        execp.add_observed(
            f"skipped:{index}:{getattr(target, 'kind', 'target')}",
            kind=getattr(target, "kind", "target"), state="skipped", target=repr(target),
        )
    ensure_dir(args.workdir)
    execp.run()
    # Print executor summary if available
    try:
        stats = getattr(execp, "execution_stats", {}) or {}
        total = int(stats.get("total", 0))
        ok_n = int(stats.get("succeeded", 0))
        fail_n = int(stats.get("failed", 0))
        print(f"Run complete (planning-first default path): executed={total}, ok={ok_n}, failed={fail_n}")
        if fail_n > 0:
            # Show a few sample failures to aid debugging
            fails = list(stats.get("failures", []))[:5]
            for f in fails:
                try:
                    print(f" - Failed {f.get('kind','?')} node {f.get('id','?')}: {f.get('reason','')}")
                except Exception:
                    pass
            print("Hint: re-run with --debug-timing for detailed progress and errors.")
    except Exception:
        print("Run complete (planning-first default path)")




def cmd_run(args: argparse.Namespace) -> None:
    # Enforce DB mode: require an existing registry DB path
    if not os.path.exists(args.db):
        raise SystemExit(f"Registry DB not found at {args.db}. Initialize and scan first: 'virusflow init --db {args.db}' then 'virusflow scan --db {args.db} <root>'.")
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
    return TaskContext(str(args.db), str(Path(args.workdir).resolve()), cfg)


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

    if not Path(args.db).exists():
        raise SystemExit(f"Registry DB not found: {args.db}; run 'virusflow init' and 'virusflow scan' first")
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
    exposure_ids = explicit or tuple(observation_exposure_ids(args.observation_id, db_path=args.db))
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
        "workdir": str(Path(args.workdir).resolve()),
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
        help="Registry SQLite path (default: VIRUSFLOW_DB or ./virusflow.sqlite3)",
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
    _add_progress(parser)
    parser.add_argument("--workdir", default=os.environ.get("VIRUSFLOW_WORKDIR", str(Path.cwd() / "work")))
    parser.add_argument("--configuration-root", default=os.environ.get("VIRUSFLOW_CONFIG_ROOT", str(Path.cwd())))
    parser.add_argument("--preserve-failed-scratch", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="virusflow", description="Artifact-driven VIRUS reduction and validation")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="Initialize a registry")
    _add_db(sp); sp.set_defaults(func=cmd_init)
    sp = sub.add_parser("scan", help="Register raw FITS inputs")
    _add_db(sp); sp.add_argument("root"); sp.set_defaults(func=cmd_scan)
    sp = sub.add_parser("exposures", help="List scanned exposures")
    _add_db(sp); sp.add_argument("--start-date"); sp.add_argument("--end-date"); sp.add_argument("--limit", type=int); sp.add_argument("--csv", action="store_true"); sp.set_defaults(func=cmd_exposures)
    sp = sub.add_parser("tasks", help="List registered task implementations"); sp.set_defaults(func=cmd_tasks)

    run = sub.add_parser("run", help="Execute the task graph").add_subparsers(dest="run_cmd", required=True)
    cal = run.add_parser("calibrations", help="Plan and execute calibration products")
    _add_science_run(cal)
    cal.add_argument("--planning-yaml"); cal.add_argument("--plan-only", action="store_true")
    cal.add_argument("--start-date", dest="plan_start_date"); cal.add_argument("--end-date", dest="plan_end_date")
    cal.add_argument("--only-zipcodes"); cal.add_argument("--force-replan", action="store_true")
    cal.add_argument("--qa-out-dir"); cal.add_argument("--qa-yaml"); cal.add_argument("--debug-timing", action="store_true"); cal.add_argument("--debug-inputs", action="store_true")
    cal.add_argument("--use-mapping-helper", action="store_true"); cal.add_argument("--mapping-tolerance-days", type=int)
    cal.set_defaults(func=cmd_run)
    exp = run.add_parser("exposure", help="Reduce one atomic science exposure")
    _add_science_run(exp); exp.add_argument("--exposure-id", required=True); exp.set_defaults(func=cmd_run_exposure)
    obs = run.add_parser("observation", help="Reduce registry-derived observation membership")
    _add_science_run(obs); obs.add_argument("--observation-id", required=True); obs.add_argument("--dither-set-id"); obs.add_argument("--exposure-id", action="append", help="Explicit member override (repeatable)"); obs.set_defaults(func=cmd_run_observation)

    artifact = sub.add_parser("artifact", help="Inspect artifacts and provenance").add_subparsers(dest="artifact_cmd", required=True)
    al = artifact.add_parser("list"); _add_db(al)
    al.add_argument("--kind"); al.add_argument("--zipcode"); al.add_argument("--at"); al.add_argument("--limit", type=int); al.add_argument("--csv", action="store_true"); al.add_argument("--summary", action="store_true"); al.add_argument("--best", action="store_true"); al.add_argument("--policy", choices=["latest_valid", "latest"]); al.add_argument("--status"); al.set_defaults(func=cmd_artifacts)
    ash = artifact.add_parser("show"); _add_db(ash); ash.add_argument("artifact_id", type=int); ash.set_defaults(func=cmd_artifact_show)

    model = sub.add_parser("model", help="Inspect accepted and candidate models").add_subparsers(dest="model_cmd", required=True)
    ml = model.add_parser("list"); _add_db(ml); ml.add_argument("--kind"); ml.add_argument("--state"); ml.add_argument("--limit", type=int, default=100); ml.set_defaults(func=cmd_models)
    ms = model.add_parser("show"); _add_db(ms); ms.add_argument("artifact_id", type=int); ms.set_defaults(func=cmd_artifact_show)

    storage = sub.add_parser("storage", help="Report persistent storage").add_subparsers(dest="storage_cmd", required=True)
    sr = storage.add_parser("report"); _add_db(sr); sr.add_argument("--largest", type=int, default=10); sr.set_defaults(func=cmd_storage_report)
    cleanup = sub.add_parser("cleanup", help="Inventory by default; mutate only with explicit flags").add_subparsers(dest="cleanup_cmd", required=True)
    cs = cleanup.add_parser("scratch"); cs.add_argument("--workdir", required=True); cs.add_argument("--execute", action="store_true"); cs.set_defaults(func=cmd_cleanup)
    cc = cleanup.add_parser("cache"); _add_db(cc); cc.add_argument("--execute", action="store_true"); cc.set_defaults(func=cmd_cleanup)
    cl = cleanup.add_parser("legacy"); _add_db(cl); cl.add_argument("--deactivate", action="store_true"); cl.add_argument("--delete-payloads", action="store_true"); cl.add_argument("--validation-succeeded", action="store_true"); cl.set_defaults(func=cmd_cleanup)

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
