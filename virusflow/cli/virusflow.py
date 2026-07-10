from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional, List

import yaml

from ..registry import database as db
from ..storage.filesystem import FileSystemStorage
from ..tasks import available_tasks, get_task_class
from ..tasks.base import TaskContext
from ..executors.local import LocalExecutor
from ..core.identity import ZipCode
from ..core.targets import BiasTarget


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


def _parse_zipcode_key(key: str) -> ZipCode:
    parts = key.split("+")
    if len(parts) != 5:
        raise SystemExit(f"Invalid zipcode key '{key}'. Expected 5 parts joined by '+'.")
    return ZipCode(ifuslot=parts[0], ifuid=parts[1], specid=parts[2], amp=parts[3], controller=parts[4])


def _format_artifacts(rows: List[dict], csv: bool = False, include_summary: bool = False) -> str:
    if csv:
        import csv as _csv
        import io as _io
        if not rows:
            return ""
        cols = [
            "id",
            "kind",
            "name",
            "path",
            "amp_key",
            "validity_start",
            "validity_end",
            "created_at",
            "qa_status",
        ]
        if include_summary:
            cols.append("summary")
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])
        return buf.getvalue()
    # text table formatting
    cols = [
        ("id", "ID"),
        ("kind", "KIND"),
        ("name", "NAME"),
        ("path", "PATH"),
        ("amp_key", "ZIPCODE"),
        ("validity_start", "VSTART"),
        ("validity_end", "VEND"),
        ("created_at", "CREATED"),
        ("qa_status", "QA"),
    ]
    if include_summary:
        cols.append(("summary", "SUMMARY"))
    widths: List[int] = []
    for key, title in cols:
        w = len(title)
        for r in rows:
            v = r.get(key)
            s = "" if v is None else str(v)
            if len(s) > w:
                w = len(s)
        widths.append(w)
    header = " ".join([title.ljust(w) for (_k, title), w in zip(cols, widths)])
    sep = " ".join(["-" * w for w in widths])
    lines = [header, sep]
    for r in rows:
        fields: List[str] = []
        for (key, _title), w in zip(cols, widths):
            s = "" if r.get(key) is None else str(r.get(key))
            if len(s) > w:
                s = s[: w - 1] + "…" if w > 1 else s[:w]
            fields.append(s.ljust(w))
        lines.append(" ".join(fields))
    return "\n".join(lines)


def _format_table(rows: List[dict], csv: bool = False) -> str:
    if csv:
        import csv as _csv
        import io as _io
        if not rows:
            return ""
        cols = [
            "exposure_id",
            "when_utc",
            "frame_type",
            "expnum",
            "qobject",
            "qprog",
            "pexptime",
            "date",
            "qra",
            "qdec",
            "tar_path",
        ]
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])
        return buf.getvalue()
    # text table
    cols = [
        ("exposure_id", "EXPOSURE"),
        ("when_utc", "DATE"),
        ("frame_type", "TYPE"),
        ("expnum", "EXP#"),
        ("qobject", "QOBJECT"),
        ("qprog", "QPROG"),
        ("pexptime", "PEXPTIME"),
        ("date", "DATEHDR"),
        ("qra", "QRA"),
        ("qdec", "QDEC"),
        ("tar_path", "TAR")
    ]
    # Compute widths
    widths = []
    for key, title in cols:
        w = len(title)
        for r in rows:
            v = r.get(key)
            s = "" if v is None else str(v)
            if len(s) > w:
                w = len(s)
        widths.append(w)
    # Build header
    parts = []
    for (key, title), w in zip(cols, widths):
        parts.append(title.ljust(w))
    out = [" ".join(parts)]
    out.append(" ".join(["-" * w for w in widths]))
    # Rows
    for r in rows:
        fields = []
        for (key, _title), w in zip(cols, widths):
            v = r.get(key)
            s = "" if v is None else str(v)
            if len(s) > w:
                s = s[: w - 1] + "…" if w > 1 else s[:w]
            fields.append(s.ljust(w))
        out.append(" ".join(fields))
    return "\n".join(out)


def cmd_exposures(args: argparse.Namespace) -> None:
    rows = db.list_exposure_table(db_path=args.db, start_date=args.start_date, end_date=args.end_date, limit=args.limit)
    if not rows:
        msg = "No exposures found"
        if args.start_date and args.end_date:
            msg += f" in date window {args.start_date}..{args.end_date}"
        print(msg)
        return
    table = _format_table(rows, csv=bool(args.csv))
    if table:
        print(table)


def cmd_artifacts(args: argparse.Namespace) -> None:
    # Optional zipcode filter
    zc = None
    if args.zipcode:
        zc = _parse_zipcode_key(args.zipcode)
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
        from ..core.artifact_service import ArtifactService, Scope
        svc = ArtifactService(args.db)
        row = svc.find_best(kind=args.kind, scope=Scope(zc), at_time=at_time, policy=(args.policy or "latest_valid"))
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
        from ..core.artifact_service import ArtifactService
        svc = ArtifactService(args.db)
        import json as _json
        for r in rows:
            s = svc.load_summary(r)
            r["summary"] = _json.dumps(s, sort_keys=True) if s is not None else None
    # Optional status filter (client-side) for convenience
    if args.status:
        rows = [r for r in rows if (str(r.get("qa_status") or "").lower() == str(args.status).lower())]
        if not rows:
            print("No artifacts found")
            return
    print(_format_artifacts(rows, csv=bool(args.csv), include_summary=bool(args.summary)))


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
    _db.save_qa_results(int(args.artifact_id), status=args.status, metrics=metrics, db_path=args.db)
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
        zc = _parse_zipcode_key(args.zipcode)
    rows = db.list_artifacts(kind=args.kind, zipcode=zc, db_path=args.db, limit=args.limit)
    if args.status:
        rows = [r for r in rows if (str(r.get("qa_status") or "").lower() == str(args.status).lower())]
    if not rows:
        print("No artifacts found")
        return
    print(_format_artifacts(rows, csv=bool(args.csv)))

# ------------------ Planner subcommands ------------------


def cmd_plan_calibrations(args: argparse.Namespace) -> None:
    # Discover zipcodes for the calibration window, optionally filter/limit for debugging
    if not args.start_date or not args.end_date:
        raise SystemExit("plan calibrations requires --start-date and --end-date (YYYYMMDD)")

    # Developer-only filter: --only-zipcode allows comma-separated ZipCode keys
    zipcodes: List[ZipCode] = []
    if args.only_zipcode:
        zkeys = [z.strip() for z in args.only_zipcode.split(",") if z.strip()]
        zipcodes = [_parse_zipcode_key(z) for z in zkeys]
    else:
        # Discover from registry
        zipcodes = db.list_zipcodes(
            db_path=args.db,
            frame_type="zro",
            start_date=args.start_date,
            end_date=args.end_date,
            limit=args.limit,
        )
        if not zipcodes:
            raise SystemExit(f"No zipcodes found for date window {args.start_date}..{args.end_date}")
    tasks = []
    for zc in zipcodes:
        tgt = BiasTarget(zc, args.start_date, args.end_date)
        node_id = tgt.node_id()
        tasks.append({
            "id": node_id,
            "name": "bias",
            "version": args.bias_version,
            "target": tgt.to_dict(),
        })

    plan = {"tasks": tasks}
    print(yaml.safe_dump(plan, sort_keys=False))


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

def cmd_run(args: argparse.Namespace) -> None:
    # Enforce DB mode: require an existing registry DB path
    if not os.path.exists(args.db):
        raise SystemExit(f"Registry DB not found at {args.db}. Initialize and scan first: 'virusflow init --db {args.db}' then 'virusflow scan --db {args.db} <root>'.")

    from ..core.pathutils import ensure_dir
    plan_path = Path(args.plan)
    text = plan_path.read_text()
    try:
        plan = yaml.safe_load(text)
    except Exception:
        # Fallback to JSON for backward compatibility
        plan = json.loads(text)

    # Configure I/O layer with registry DB for DB-backed tar indexing (mandatory for tar-backed FITS)
    from ..algorithms import io as aio
    aio.set_registry_db_path(args.db)

    ctx = TaskContext(db_path=args.db, workdir=args.workdir, config={"workers": args.workers, "debug_timing": bool(args.debug_timing)})

    # Instantiate tasks (target-scoped when provided)
    instances = {}
    for t in plan.get("tasks", []):
        cls = get_task_class(t["name"], t.get("version"))
        target_obj = None
        tgt = t.get("target")
        if tgt and t["name"] == "bias":
            z = tgt.get("zipcode", {})
            zc = ZipCode(
                ifuslot=z.get("ifuslot", "000"),
                ifuid=z.get("ifuid", "000"),
                specid=z.get("specid", "000"),
                amp=z.get("amp", "XX"),
                controller=z.get("controller", "X"),
            )
            target_obj = BiasTarget(zc, tgt.get("start_date"), tgt.get("end_date"))
        instances[t["id"]] = cls(ctx, target=target_obj)

    # Build simple graph and execute in-order
    exec = LocalExecutor(max_workers=(args.workers if args.workers and args.workers > 0 else 1), debug=bool(args.debug_timing))
    for t in plan.get("tasks", []):
        node_id = t["id"]
        deps = t.get("deps", [])
        exec.add_task(node_id, instances[node_id], depends_on=deps)

    ensure_dir(args.workdir)
    exec.run()
    print("Run complete")


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

    # plan group with subcommands
    plan_p = sub.add_parser("plan", help="Create a YAML plan from scientific intent")
    plan_sub = plan_p.add_subparsers(dest="plan_cmd", required=True)

    # plan calibrations
    spc = plan_sub.add_parser("calibrations", help="Plan calibration tasks for a date window", parents=[global_opts])
    spc.add_argument("--start-date", required=True, help="Start date YYYYMMDD")
    spc.add_argument("--end-date", required=True, help="End date YYYYMMDD")
    spc.add_argument("--bias-version", default=None, help="Bias task version, default=latest")
    spc.add_argument("--dark-version", default=None, help="Dark task version, default=latest")
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
    sp = sub.add_parser("run", help="Run a previously created YAML plan file", parents=[global_opts])
    sp.add_argument("plan", help="Path to plan YAML file (JSON still accepted)")
    sp.add_argument("--workdir", default=str(Path.cwd() / "work"), help="Working directory")
    sp.add_argument("--workers", type=int, default=4, help="Worker threads for algorithms and task batches (set 0 for serial)")
    sp.add_argument("--debug-timing", action="store_true", help="Print timing diagnostics during run")
    sp.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    # Delegate to the chosen function
    args.func(args)


if __name__ == "__main__":
    main()
