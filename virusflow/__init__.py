"""
VIRUSFlow package
Minimal initial implementation to support phases 1–6 of the architecture vision.
This package provides:
- Core data model classes
- Registry (SQLite)
- Storage backends (filesystem, tar) stubs
- Task framework and simple dependency graph
- Basic provenance and QA recording
- Executable CLI for initializing a registry, scanning a directory,
  planning a simple reduction, and running it locally.
"""
from importlib.metadata import version, PackageNotFoundError

__all__ = [
    "get_version",
]


def get_version() -> str:
    """Return the installed package version or '0.0.0-dev' if unavailable."""
    try:
        return version("virusflow")
    except PackageNotFoundError:
        return "0.0.0-dev"
