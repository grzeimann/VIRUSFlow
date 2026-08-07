#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Architecture gate: enforce key Architecture_Goals.md rules in CI.

This is a lightweight static checker intended to fail fast when common
architectural constraints are violated. It scans source files for known-bad
imports/couplings and verifies the presence of essential registrations and
atomic write semantics.

Docs: see Architecture_Goals.md at the repo root.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "virusflow"
GOALS_DOC = REPO_ROOT / "Architecture_Goals.md"


DISALLOWED_IMPORT_PATTERNS = [
    # Any usage of legacy core.artifacts anywhere
    re.compile(r"(^|\s)core\.artifacts(\W|$)"),
]

# Algorithms must not import registry.* (DB) directly
ALGO_DISALLOWED_IMPORTS = [
    re.compile(r"from\s+\.+registry\s+import\s+"),
    re.compile(r"from\s+virusflow\.registry\s+import\s+"),
    re.compile(r"import\s+virusflow\.registry(\.|\s|$)"),
]

# Planning layer must not import algorithms or storage directly
PLANNING_DISALLOWED_IMPORTS = [
    re.compile(r"from\s+virusflow\.algorithms\s+import\s+"),
    re.compile(r"import\s+virusflow\.algorithms(\.|\s|$)"),
    re.compile(r"from\s+virusflow\.storage\s+import\s+"),
    re.compile(r"import\s+virusflow\.storage(\.|\s|$)"),
]

# Discourage new imports of legacy core.targets outside whitelisted files
CORE_TARGETS_IMPORT_PATTERN = re.compile(r"(^|\s)(from\s+virusflow\.core\s+import\s+targets|from\s+virusflow\.core\.targets\s+import\s+|import\s+virusflow\.core\.targets(\.|\s|$))")
# Files allowed to import legacy core.targets during deprecation window
CORE_TARGETS_ALLOWED = {
    (SRC_ROOT / "core" / "targets.py").as_posix(),
    (SRC_ROOT / "cli" / "virusflow.py").as_posix(),
}


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def scan_for_disallowed_imports() -> list[str]:
    errors: list[str] = []
    for p in SRC_ROOT.rglob("*.py"):
        txt = _read_text(p)
        # universal disallowed patterns
        for pat in DISALLOWED_IMPORT_PATTERNS:
            if pat.search(txt):
                errors.append(f"{p}: uses legacy core.artifacts (forbidden). See Architecture_Goals.md: Product-agnostic persistence.")
        # algorithm-specific constraints
        if (SRC_ROOT / "algorithms") in p.parents:
            for pat in ALGO_DISALLOWED_IMPORTS:
                if pat.search(txt):
                    errors.append(f"{p}: algorithms must not import registry.* directly. See Architecture_Goals.md: Separation of concerns.")
        # planning-layer constraints: must not import algorithms or storage
        if (SRC_ROOT / "planning") in p.parents:
            for pat in PLANNING_DISALLOWED_IMPORTS:
                if pat.search(txt):
                    errors.append(f"{p}: planning layer must not import algorithms/storage. See Architecture_Goals.md: Modularity boundaries.")
        # Prevent new references to TaskGraph outside core/graph.py (allow legacy executor shim)
        legacy_ok = (p == (SRC_ROOT / "executors" / "local.py"))
        if p != (SRC_ROOT / "core" / "graph.py") and not legacy_ok:
            import re as _re
            if _re.search(r"\bTaskGraph\b", txt):
                errors.append(f"{p}: references TaskGraph. New code should use the planning scheduler; TaskGraph is deprecated and confined to core.graph only (legacy executor exempt).")
        # Forbid new imports of legacy core.targets outside allowed files
        if CORE_TARGETS_IMPORT_PATTERN.search(txt):
            # Normalize path string
            p_str = p.as_posix()
            if p_str not in CORE_TARGETS_ALLOWED:
                errors.append(f"{p}: imports legacy core.targets. New code should use virusflow.planning.targets. Allowed only in core/targets.py and CLI legacy path during deprecation window.")
    return errors


def verify_serializer_registration() -> list[str]:
    """Ensure array/fits serializer is registered in artifacts.service."""
    p = SRC_ROOT / "artifacts" / "service.py"
    txt = _read_text(p)
    errs: list[str] = []
    import re as _re
    text_min = _re.sub(r"\s+", "", txt)
    if not _re.search(r"register\(\"array\",\"fits\"", text_min):
        errs.append(f"{p}: expected SerializerRegistry to register ('array','fits'). See Architecture_Goals.md: Artifacts subsystem.")
    return errs


def verify_atomic_fits_write() -> list[str]:
    """Ensure io_fits.write_array_fits writes temp-then-replace and emits sidecar."""
    p = SRC_ROOT / "artifacts" / "io_fits.py"
    txt = _read_text(p)
    errs: list[str] = []
    # very lightweight heuristics
    if ".tmp" not in txt or ".replace(" not in txt:
        errs.append(f"{p}: write_array_fits should write to a temp file and replace atomically. See Architecture_Goals.md: Storage/Idempotency.")
    if "sidecar" not in txt or "json" not in txt:
        errs.append(f"{p}: write_array_fits should write a compact JSON sidecar. See Architecture_Goals.md: Storage.")
    return errs


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Run architecture goals gate checks")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors (default: on)")
    args = ap.parse_args(argv)

    findings: list[str] = []
    findings += scan_for_disallowed_imports()
    findings += verify_serializer_registration()
    findings += verify_atomic_fits_write()

    if findings:
        print("Architecture gate failed. Please address the following findings:\n", file=sys.stderr)
        for f in findings:
            print(" - " + f, file=sys.stderr)
        hint = f"See {GOALS_DOC} for architectural goals and acceptance criteria."
        print("\n" + hint, file=sys.stderr)
        return 2

    # Optional: friendly success message in CI logs
    print("Architecture gate passed: repository aligns with documented goals.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
