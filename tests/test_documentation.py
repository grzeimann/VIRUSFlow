from __future__ import annotations

import re
from pathlib import Path

from virusflow.cli.virusflow import build_parser, resolve_nworkers


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [
    ROOT / "README.md", ROOT / "docs/getting-started.md", ROOT / "docs/cli-reference.md",
    ROOT / "docs/examples.md", ROOT / "docs/troubleshooting.md", ROOT / "docs/performance.md",
    ROOT / "docs/migration/stage-11.md", ROOT / "docs/architecture/current-system.md",
]


def test_documentation_links_resolve():
    link_pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    failures = []
    for document in DOCUMENTS:
        for target in link_pattern.findall(document.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (document.parent / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                failures.append(f"{document.relative_to(ROOT)} -> {target}")
    assert failures == []


def test_documented_command_families_exist_and_default_is_four_workers():
    parser = build_parser()
    invocations = [
        ["init"], ["scan", "/raw"], ["exposures"],
        ["run", "calibrations"], ["run", "exposure", "--exposure-id", "20260609T031649.6"],
        ["run", "observation", "--observation-id", "20260609-OBSID6"],
        ["artifact", "list"], ["artifact", "show", "1"],
        ["model", "list"], ["model", "show", "1"],
        ["qa", "list"], ["qa", "show", "--artifact-id", "1"],
        ["study", "list"], ["study", "show", "study"],
        ["storage", "report"], ["cleanup", "scratch", "--workdir", "/work"],
        ["cleanup", "cache"], ["cleanup", "legacy"], ["config", "show"],
        ["performance", "show", "performance.json"],
        ["performance", "compare", "before.json", "after.json"],
        ["performance", "overhead"],
        ["validate", "observation", "--data-root", "/raw", "--workspace", "/work", "--output-dir", "/report"],
    ]
    for invocation in invocations:
        parser.parse_args(invocation)
    assert resolve_nworkers() == 4


def test_docs_describe_removed_dense_products_as_nonpersistent():
    text = "\n".join(document.read_text().lower() for document in DOCUMENTS)
    assert "four workers by default" in text or "defaults to four workers" in text
    assert "normal production does not persist" in text
    assert "plan (stubs)" not in text
    assert "serial-default" not in text
