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
    # Regular FITS on filesystem
    for p in storage.list_fits():
        rid = db.register_raw_file(str(p), db_path=args.db)
        if rid is not None:
            count += 1
    # FITS inside tar archives
    for tar_path, member in storage.list_tar_fits():
        rid = db.register_raw_file(str(tar_path), db_path=args.db, tar_member=member)
        if rid is not None:
            count += 1
    print(f"Registered {count} raw FITS files from {args.root}")


def cmd_tasks(args: argparse.Namespace) -> None:
    av = available_tasks()
    print(yaml.safe_dump(av, sort_keys=False))


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
    plan_path = Path(args.plan)
    text = plan_path.read_text()
    try:
        plan = yaml.safe_load(text)
    except Exception:
        # Fallback to JSON for backward compatibility
        plan = json.loads(text)
    ctx = TaskContext(db_path=args.db, workdir=args.workdir, config={})

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
    exec = LocalExecutor()
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
    sp.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    # Delegate to the chosen function
    args.func(args)


if __name__ == "__main__":
    main()
