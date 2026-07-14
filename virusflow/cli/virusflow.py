from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional, List

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
    # Optional single base_reduction probe
    if args.probe:
        try:
            import virusflow.algorithms.ccd as _ccd
        except Exception as e:  # pragma: no cover
            print(f"Probe unavailable (import error): {e}")
            return
        (rid0, rf0) = rows[0]
        try:
            img, _hdr = _ccd.base_reduction(rf0.path, rf0.tar_member, return_header=False)
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
    cfg = {"workers": args.workers, "debug_timing": bool(args.debug_timing), "debug_inputs": bool(getattr(args, "debug_inputs", False))}
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
    # Submit to PlanningExecutor (planning-native; no TaskGraph dependency)
    from ..executors.planning_executor import PlanningExecutor as _PlanningExecutor
    execp = _PlanningExecutor(max_workers=(args.workers if args.workers and args.workers > 0 else 1), debug=bool(args.debug_timing))
    # Add tasks preserving dependencies
    for st in scheduled:
        execp.add_task(st.id, st.task, kind=st.kind, depends_on=st.depends_on)
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


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="virusflow", description="VIRUSFlow CLI")

    # Define a global/parent parser so --db can appear before or after subcommands
    global_opts = argparse.ArgumentParser(add_help=False)
    global_opts.add_argument("--db", default=str(Path.cwd() / "virusflow.sqlite3"), help="Path to registry DB")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="Initialize registry database", parents=[global_opts])
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("scan", help="Scan a root directory for raw FITS files", parents=[global_opts])
    sp.add_argument("root", help="Root path to scan")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("tasks", help="List available tasks and versions")
    sp.set_defaults(func=cmd_tasks)

    # artifacts listing
    sp = sub.add_parser("artifacts", help="List or select artifacts in the registry", parents=[global_opts])
    sp.add_argument("--kind", help="Artifact kind to filter (e.g., master_bias, master_dark)")
    sp.add_argument("--zipcode", help="ZipCode key to filter (IFUSLOT+IFUID+SPECID+AMP+CONTROLLER)")
    sp.add_argument("--at", help="Only artifacts valid at this time (YYYYMMDD or ISO datetime)")
    sp.add_argument("--limit", type=int, help="Limit number of rows")
    sp.add_argument("--csv", action="store_true", help="Output as CSV instead of a fixed-width table")
    sp.add_argument("--summary", action="store_true", help="Include sidecar JSON summary if available (no FITS I/O)")
    sp.add_argument("--best", action="store_true", help="Select best artifact per policy instead of listing (requires --kind and --zipcode)")
    sp.add_argument("--policy", choices=["latest_valid", "latest"], help="Selection policy for --best (default: latest_valid)")
    sp.add_argument("--status", help="Filter by QA status (e.g., pass, fail)")
    sp.set_defaults(func=cmd_artifacts)

    # exposures table
    sp = sub.add_parser("exposures", help="Show a quick readable table from exposures in the registry", parents=[global_opts])
    sp.add_argument("--start-date", help="Filter start date YYYYMMDD")
    sp.add_argument("--end-date", help="Filter end date YYYYMMDD")
    sp.add_argument("--limit", type=int, help="Limit number of rows")
    sp.add_argument("--csv", action="store_true", help="Output as CSV instead of a fixed-width table")
    sp.set_defaults(func=cmd_exposures)

    # QA group with subcommands
    qa_p = sub.add_parser("qa", help="Manage/view artifact QA", parents=[global_opts])
    qa_sub = qa_p.add_subparsers(dest="qa_cmd", required=True)
    qas = qa_sub.add_parser("set", help="Set QA status/metrics", parents=[global_opts])
    qas.add_argument("--artifact-id", required=True, help="Artifact id")
    qas.add_argument("--status", required=True, help="QA status (e.g., pass, fail)")
    qas.add_argument("--metrics", help="JSON dict of QA metrics")
    qas.set_defaults(func=cmd_qa_set)
    qash = qa_sub.add_parser("show", help="Show QA for an artifact", parents=[global_opts])
    qash.add_argument("--artifact-id", required=True, help="Artifact id")
    qash.set_defaults(func=cmd_qa_show)
    qal = qa_sub.add_parser("list", help="List artifacts with optional QA filter", parents=[global_opts])
    qal.add_argument("--kind", help="Filter by artifact kind")
    qal.add_argument("--zipcode", help="Filter by zipcode key")
    qal.add_argument("--status", help="Filter by QA status (e.g., pass, fail)")
    qal.add_argument("--limit", type=int, help="Limit number of rows")
    qal.add_argument("--csv", action="store_true", help="Output as CSV")
    qal.set_defaults(func=cmd_qa_list)
    qae = qa_sub.add_parser("eval", help="Evaluate QA for a specific artifact", parents=[global_opts])
    qae.add_argument("--artifact-id", required=True, help="Artifact id")
    qae.add_argument("--kind", required=True, help="Artifact kind (e.g., trace, wave)")
    qae.add_argument("--meta", help="JSON dict of algo meta or summary to feed QA engine")
    qae.add_argument("--from-summary", action="store_true", help="Use ArtifactService.describe(summary) as meta if --meta not provided")
    qae.set_defaults(func=cmd_qa_eval)
    qab = qa_sub.add_parser("backfill", help="Re-evaluate QA for many artifacts", parents=[global_opts])
    qab.add_argument("--kind", help="Only re-evaluate this kind (optional)")
    qab.add_argument("--since", help="Only artifacts created since YYYYMMDD (optional)")
    qab.add_argument("--limit", type=int, help="Limit number of artifacts (optional)")
    qab.add_argument("--from-summary", action="store_true", help="Use ArtifactService.describe(summary) as meta for each artifact")
    qab.add_argument("--dry-run", action="store_true", help="Do not write results, just show planned actions")
    qab.set_defaults(func=cmd_qa_backfill)

    # debug-raw helper
    sp_dbg = sub.add_parser("debug-raw", help="Probe raw inputs for a zipcode and frame type", parents=[global_opts])
    sp_dbg.add_argument("--zipcode", required=True, help="ZipCode key (IFUSLOT+IFUID+SPECID+AMP+CONTROLLER)")
    sp_dbg.add_argument("--frame-type", required=True, help="Frame type (e.g., zro, drk, flt)")
    sp_dbg.add_argument("--start-date", help="Filter start date YYYYMMDD")
    sp_dbg.add_argument("--end-date", help="Filter end date YYYYMMDD")
    sp_dbg.add_argument("--limit", type=int, help="Show up to N sample rows (default 10)")
    sp_dbg.add_argument("--verify", action="store_true", help="For tar members, verify FITS header readability via tarfile+astropy")
    sp_dbg.add_argument("--probe", action="store_true", help="Attempt a single algorithms.ccd.base_reduction on the first candidate and report")
    sp_dbg.set_defaults(func=cmd_debug_raw)

    # plan group with subcommands
    plan_p = sub.add_parser("plan", help="Create a YAML plan from scientific intent")
    plan_sub = plan_p.add_subparsers(dest="plan_cmd", required=True)

    # plan calibrations
    spc = plan_sub.add_parser("calibrations", help="Plan calibration tasks for a date window", parents=[global_opts])
    spc.add_argument("--start-date", required=True, help="Start date YYYYMMDD")
    spc.add_argument("--end-date", required=True, help="End date YYYYMMDD")
    spc.add_argument("--bias-version", default=None, help="Bias task version, default=latest")
    spc.add_argument("--dark-version", default=None, help="Dark task version, default=latest")
    spc.add_argument("--flat-version", default=None, help="Flat task version, default=latest")
    spc.add_argument("--cmp-version", default=None, help="Cmp task version, default=latest")
    spc.add_argument("--twi-version", default=None, help="Twi task version, default=latest")
    spc.add_argument("--trace-version", default=None, help="Trace task version, default=latest")
    spc.add_argument("--wave-version", default=None, help="Wave task version, default=latest")
    spc.add_argument("--only-zipcode", help="Developer filter: comma-separated ZipCode keys (IFUSLOT+IFUID+SPECID+AMP+CONTROLLER)")
    spc.add_argument("--limit", type=int, help="Developer filter: limit number of zipcodes")
    spc.set_defaults(func=cmd_plan_calibrations)

    # plan night (stub)
    spn = plan_sub.add_parser("night", help="Plan a nightly reduction run", parents=[global_opts])
    spn.add_argument("--date", required=True, help="Night date YYYYMMDD")
    spn.set_defaults(func=cmd_plan_night)

    # plan exposure (stub)
    spe = plan_sub.add_parser("exposure", help="Plan reduction of a single exposure", parents=[global_opts])
    spe.add_argument("--exposure-id", required=True, help="Exposure identifier (e.g., 20260511T035810.4)")
    spe.set_defaults(func=cmd_plan_exposure)

    # plan observation-set (stub)
    spo = plan_sub.add_parser("observation-set", help="Plan higher-level products from multiple exposures", parents=[global_opts])
    spo.add_argument("--name", required=True, help="Observation set name")
    spo.set_defaults(func=cmd_plan_observation_set)

    # run
    sp = sub.add_parser("run", help="Plan and execute calibrations (planning-first)", parents=[global_opts])
    sp.add_argument("--workdir", default=str(Path.cwd() / "work"), help="Working directory")
    sp.add_argument("--workers", type=int, default=4, help="Worker threads for algorithms and task batches (set 0 for serial)")
    sp.add_argument("--qa-out-dir", help="Directory to write QA outputs (plots and JSON packets)")
    sp.add_argument("--qa-yaml", help="Path to QA rules YAML (overrides VF_QA_YAML and default docs/qa_default.yml)")
    sp.add_argument("--debug-timing", action="store_true", help="Print timing diagnostics during run")
    sp.add_argument("--debug-inputs", action="store_true", help="Print a few resolved raw inputs per task (paths/tar_members)")
    # Planning options
    sp.add_argument("--planning-yaml", help="Path to planning rules YAML to override defaults (see docs/planning_config.md)")
    sp.add_argument("--plan-only", action="store_true", help="Only perform planning and write planning_report.yml to --workdir, do not execute tasks")
    sp.add_argument("--plan-start-date", help="Planning date window start (YYYYMMDD)")
    sp.add_argument("--plan-end-date", help="Planning date window end (YYYYMMDD)")
    sp.add_argument("--only-zipcodes", help="Comma-separated ZipCode keys to limit planning (IFUSLOT+IFUID+SPECID+AMP+CONTROLLER)")
    # Mapping helper controls (science→calib selection centralization)
    sp.add_argument("--use-mapping-helper", action="store_true", help="Use planning.mapping.select_for_edge for artifact selection inside tasks (experimental)")
    sp.add_argument("--mapping-tolerance-days", type=int, help="Optional tolerance window (days) for mapping helper when selecting parent calibrations")
    # Force planning controls
    sp.add_argument("--force-replan", action="store_true", help="Force planning even if an existing artifact is registered (rebuild calibrations)")
    sp.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    # Delegate to the chosen function
    args.func(args)


if __name__ == "__main__":
    main()
