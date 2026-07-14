from __future__ import annotations

"""
Planning-native executor that runs ScheduledTask instances without relying on the
legacy core.graph.TaskGraph. Intended for use with virusflow.planning.scheduler.schedule().

Features:
- Executes tasks in batches when their depends_on are satisfied.
- Optional threading with a max_workers parameter (similar to LocalExecutor).
- Captures exceptions per task and continues advancing the graph where possible.
- Prints a compact per-kind progress line when debug is enabled.

This module intentionally avoids importing legacy core.graph to help retire TaskGraph.
"""
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple, Optional


@dataclass
class _Node:
    id: str
    kind: str
    task: object
    deps: List[str]


class PlanningExecutor:
    def __init__(self, max_workers: int = 1, debug: bool = False) -> None:
        self.max_workers = max(1, int(max_workers))
        self.debug = bool(debug)
        self._nodes: Dict[str, _Node] = {}
        # Execution statistics populated after run()
        # Shape: {
        #   'total': int, 'succeeded': int, 'failed': int,
        #   'per_kind': {kind: {'total': int, 'ok': int, 'fail': int}},
        #   'failures': [{'id': node_id, 'kind': kind, 'reason': str}]
        # }
        self.execution_stats: Dict[str, object] = {}
        # Live table control (enabled by default when stdout is a TTY and not in debug line mode)
        try:
            import sys as _sys
            self._live_enabled_default = bool(getattr(_sys.stdout, "isatty", lambda: False)())
        except Exception:
            self._live_enabled_default = False

    def add_task(self, node_id: str, task: object, kind: Optional[str] = None, depends_on: List[str] | None = None) -> None:
        k = kind or getattr(task, "kind", "task")
        self._nodes[node_id] = _Node(id=node_id, kind=str(k), task=task, deps=list(depends_on or []))

    def _indeg_and_dependents(self) -> Tuple[Dict[str, int], Dict[str, List[str]]]:
        indeg: Dict[str, int] = {k: 0 for k in self._nodes}
        dependents: Dict[str, List[str]] = {k: [] for k in self._nodes}
        for nid, n in self._nodes.items():
            for d in n.deps:
                indeg[nid] = indeg.get(nid, 0) + 1
                dependents.setdefault(d, []).append(nid)
        return indeg, dependents

    def run(self) -> None:
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

        indeg, dependents = self._indeg_and_dependents()
        ready = [k for k, v in indeg.items() if v == 0]
        done: Dict[str, bool] = {}
        failed: Dict[str, str] = {}

        def _has_failed_dep(nid: str) -> bool:
            return any((d in failed) for d in self._nodes[nid].deps)

        def _run_one(nid: str):
            try:
                return nid, self._nodes[nid].task.run({}), None
            except Exception as e:  # pragma: no cover - behavior mirrored from LocalExecutor
                return nid, None, str(e)

        # Simple progress counters by kind
        totals: Dict[str, int] = {}
        for n in self._nodes.values():
            totals[n.kind] = totals.get(n.kind, 0) + 1
        ok: Dict[str, int] = {k: 0 for k in totals}
        fl: Dict[str, int] = {k: 0 for k in totals}
        running: Dict[str, int] = {k: 0 for k in totals}

        # Live table renderer (enabled in default mode when stdout is a TTY)
        use_live = (not self.debug) and getattr(self, "_live_enabled_default", False)
        _printed_lines = 0

        def _render_live():
            nonlocal _printed_lines
            if not use_live:
                return
            import sys as _sys
            # Move cursor up and clear previous lines
            if _printed_lines > 0:
                _sys.stdout.write("\x1b[" + str(_printed_lines) + "A")  # move up
                for _ in range(_printed_lines):
                    _sys.stdout.write("\x1b[2K\n")  # clear line and move down
                _sys.stdout.write("\x1b[" + str(_printed_lines) + "A")  # move back up
            # Compose table
            rows = []
            header = "Task Kind                    | total | running | ok | fail"
            sep = "-" * len(header)
            rows.append(header)
            rows.append(sep)
            for k in sorted(totals.keys()):
                rows.append(f"{k:<28} | {totals.get(k,0):>5} | {running.get(k,0):>7} | {ok.get(k,0):>2} | {fl.get(k,0):>4}")
            # Overall summary
            rows.append(sep)
            all_total = sum(totals.values())
            all_running = sum(running.values())
            all_ok = sum(ok.values())
            all_fail = sum(fl.values())
            rows.append(f"Overall                      | {all_total:>5} | {all_running:>7} | {all_ok:>2} | {all_fail:>4}")
            # Optional QA footer with warn/fail tallies
            try:
                from ..artifacts.diagnostics import qa_tallies_snapshot as _qa_snap  # type: ignore
                q = _qa_snap() or {}
                qt = q.get("__all__", {}) or {}
                q_pass = int(qt.get("pass", 0))
                q_warn = int(qt.get("warn", 0))
                q_fail = int(qt.get("fail", 0))
                rows.append(f"QA totals                    |  pass={q_pass}  warn={q_warn}  fail={q_fail}")
            except Exception:
                pass
            text = "\n".join(rows) + "\n"
            _sys.stdout.write(text)
            _sys.stdout.flush()
            _printed_lines = len(rows)

        def _bump(kind: str, success: bool):
            if success:
                ok[kind] = ok.get(kind, 0) + 1
            else:
                fl[kind] = fl.get(kind, 0) + 1
            _render_live()
            if self.debug:
                parts = [f"{k}: {ok.get(k,0)}/{totals.get(k,0)} ok, {fl.get(k,0)} fail" for k in sorted(totals.keys())]
                print("\r[Progress] " + " | ".join(parts), end="", flush=True)

        # Initial render
        _render_live()
        while ready:
            batch = ready[:]
            ready.clear()
            # Filter out nodes whose deps have failed
            runnable = [nid for nid in batch if not _has_failed_dep(nid)]
            skipped = [nid for nid in batch if _has_failed_dep(nid)]
            for nid in skipped:
                failed[nid] = "blocked-dependency"
                done[nid] = True
                _bump(self._nodes[nid].kind, False)
                for dep in dependents.get(nid, []):
                    indeg[dep] -= 1
                    if indeg[dep] == 0:
                        ready.append(dep)

            if not runnable:
                continue

            if self.max_workers <= 1 or len(runnable) == 1:
                for nid in runnable:
                    # mark running
                    k = self._nodes[nid].kind
                    running[k] = running.get(k, 0) + 1
                    _render_live()
                    _, _res, err = _run_one(nid)
                    # done running
                    running[k] = max(0, running.get(k, 0) - 1)
                    if err is not None:
                        failed[nid] = err
                        _bump(k, False)
                    else:
                        _bump(k, True)
                    done[nid] = True
                    for dep in dependents.get(nid, []):
                        indeg[dep] -= 1
                        if indeg[dep] == 0:
                            ready.append(dep)
            else:
                with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                    # submit initial batch up to max_workers
                    in_flight = {}
                    q = runnable[:]
                    # Fill initial slots
                    while q and len(in_flight) < self.max_workers:
                        nid0 = q.pop(0)
                        k0 = self._nodes[nid0].kind
                        running[k0] = running.get(k0, 0) + 1
                        fut0 = ex.submit(_run_one, nid0)
                        in_flight[fut0] = nid0
                    _render_live()
                    while in_flight:
                        done_futs, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
                        for fut in done_futs:
                            nid = in_flight.pop(fut)
                            k = self._nodes[nid].kind
                            _nid, _res, err = fut.result()
                            # decrement running for this kind
                            running[k] = max(0, running.get(k, 0) - 1)
                            if err is not None:
                                failed[nid] = err
                                _bump(k, False)
                            else:
                                _bump(k, True)
                            done[nid] = True
                            for dep in dependents.get(nid, []):
                                indeg[dep] -= 1
                                if indeg[dep] == 0:
                                    ready.insert(0, dep)  # prioritize newly-unlocked
                        # Top up
                        while q and len(in_flight) < self.max_workers:
                            nid2 = q.pop(0)
                            k2 = self._nodes[nid2].kind
                            running[k2] = running.get(k2, 0) + 1
                            in_flight[ex.submit(_run_one, nid2)] = nid2
                        _render_live()

        # Final snapshot/newline for progress print
        if self.debug:
            print()
        elif use_live:
            _render_live()
            try:
                import sys as _sys
                _sys.stdout.write("\n")
                _sys.stdout.flush()
            except Exception:
                pass

        # If any nodes remain not done, raise to indicate a likely cycle
        if len(done) != len(self._nodes):
            raise ValueError("Not all tasks executed; possible cycle or unresolved dependencies")

        # Prepare execution statistics (always available after run)
        total_nodes = len(self._nodes)
        succeeded = sum(ok.values())
        failed_total = sum(fl.values())
        per_kind: Dict[str, Dict[str, int]] = {}
        for k, tot in totals.items():
            per_kind[k] = {"total": int(tot), "ok": int(ok.get(k, 0)), "fail": int(fl.get(k, 0))}
        failures_list: List[Dict[str, str]] = []
        for nid, reason in failed.items():
            try:
                k = self._nodes[nid].kind
            except Exception:
                k = "unknown"
            failures_list.append({"id": nid, "kind": k, "reason": str(reason)})
        self.execution_stats = {
            "total": int(total_nodes),
            "succeeded": int(succeeded),
            "failed": int(failed_total),
            "per_kind": per_kind,
            "failures": failures_list,
        }

        # If there were failures, surface a concise error summary without stopping the whole run (only in debug show details)
        if failed and self.debug:
            kinds: Dict[str, int] = {}
            for nid, reason in failed.items():
                k = self._nodes[nid].kind
                kinds[k] = kinds.get(k, 0) + 1
                print(f"[Error] {k} {nid}: {reason}")

        # Always print a one-line execution summary for user feedback
        try:
            print(f"Execution summary: total={total_nodes}, ok={succeeded}, failed={failed_total}")
        except Exception:
            pass
