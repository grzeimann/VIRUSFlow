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


# ------------------ Planner subcommands ------------------

def _parse_zipcode_key(key: str) -> ZipCode:
    parts = key.split("+")
    if len(parts) != 5:
        raise SystemExit(f"Invalid zipcode key '{key}'. Expected 5 parts joined by '+'.")
    return ZipCode(ifuslot=parts[0], ifuid=parts[1], specid=parts[2], amp=parts[3], controller=parts[4])


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

    os.makedirs(args.workdir, exist_ok=True)
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

    # exposures table
    sp = sub.add_parser("exposures", help="Show a quick readable table from exposures in the registry", parents=[global_opts])
    sp.add_argument("--start-date", help="Filter start date YYYYMMDD")
    sp.add_argument("--end-date", help="Filter end date YYYYMMDD")
    sp.add_argument("--limit", type=int, help="Limit number of rows")
    sp.add_argument("--csv", action="store_true", help="Output as CSV instead of a fixed-width table")
    sp.set_defaults(func=cmd_exposures)

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
