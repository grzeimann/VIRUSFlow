#!/usr/bin/env python3
"""
Diagnostic helper for pytest installation and PATH/launcher issues.

Usage:
  python scripts/check_pytest_env.py

This will print:
- Python interpreter path
- pip module path and version bound to this interpreter
- pytest import location and version (via python -m)
- Location of the pytest console script resolved by PATH (shutil.which)
- Basic checks for common mismatches and guidance to fix them
"""
from __future__ import annotations

import os
import sys
import shutil
import subprocess
from typing import Optional


def _run(cmd: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate(timeout=20)
        return p.returncode or 0, out.strip(), err.strip()
    except Exception as e:
        return 1, "", str(e)


def _site_packages_of_python(exe: str) -> Optional[str]:
    code = (
        "import sys, site;"
        "paths = [p for p in (site.getsitepackages()+[site.getusersitepackages()]) if p and isinstance(p,str)];"
        "print(paths[0] if paths else '')"
    )
    rc, out, _ = _run([exe, "-c", code])
    return out if rc == 0 and out else None


def main() -> int:
    py = sys.executable
    print(f"Python executable: {py}")

    # pip bound to this interpreter
    rc, out, err = _run([py, "-m", "pip", "--version"])
    print(f"pip --version: {(out or err)}")

    # pytest via module (guaranteed to use this interpreter if installed)
    rc, out, err = _run([py, "-m", "pytest", "--version"])
    if rc == 0:
        print(f"python -m pytest --version: {out}")
    else:
        print("python -m pytest failed (pytest likely not installed for this interpreter).")
        print(err)

    # Where does pytest import from?
    code = (
        "import importlib, sys;"
        "m = importlib.import_module('pytest');"
        "print(getattr(m,'__file__', None) or 'UNKNOWN')"
    )
    rc, out, err = _run([py, "-c", code])
    print(f"pytest module path (via import): {out or err or 'UNKNOWN'}")

    # Which pytest console script is on PATH?
    which_pytest = shutil.which("pytest")
    print(f"pytest console script (shutil.which): {which_pytest}")

    # Compare environments
    site_for_py = _site_packages_of_python(py) or ""
    print(f"Primary site-packages for Python: {site_for_py}")

    suggestions: list[str] = []

    # Detect venv vs system python mismatch
    if which_pytest:
        # On Unix, venv puts scripts in <venv>/bin; on Windows in <venv>\\Scripts
        bin_dir = os.path.dirname(which_pytest)
        if os.name == 'nt':
            likely_env_dir = os.path.dirname(bin_dir)
        else:
            likely_env_dir = os.path.dirname(bin_dir)
        # Heuristic: is our sys.executable under the same env dir?
        same_env = os.path.commonpath([py, likely_env_dir]) == likely_env_dir
        if not same_env:
            suggestions.append(
                "Your PATH resolves 'pytest' from a different environment than sys.executable. "
                "Use 'python -m pytest' or activate the environment that owns that pytest script."
            )

    # If pytest module path is empty but which pytest is set, likely PATH points to global pytest
    if not out and which_pytest:
        suggestions.append(
            "'pytest' command is on PATH but cannot be imported in the current interpreter. "
            "Install dev extras for this interpreter: python -m pip install -e '.[dev]'"
        )

    # Additional targeted diagnostics: user-site vs env mismatch
    rc_user_site, user_site, _ = _run([py, "-c", "import site; print(site.getusersitepackages())"])
    if rc_user_site == 0 and user_site:
        imported_from_user_site = (out.startswith(user_site) if out else False)
        if imported_from_user_site and not which_pytest:
            # pytest imported from user site, but console script not found on PATH
            suggestions.append(
                "pytest is installed in your user site-packages, but the console script directory is not on PATH. "
                "Either add ~/.local/bin (macOS/Linux) or %APPDATA%/Python/PythonXY/Scripts (Windows) to PATH, "
                "or reinstall pytest into the active environment without --user."
            )
            # Concrete commands for conda and pip
            if "conda" in (os.environ.get("CONDA_PREFIX") or ""):
                env_bin = os.path.join(os.environ.get("CONDA_PREFIX"), "bin" if os.name != "nt" else "Scripts")
                suggestions.append(
                    f"Conda env detected. To fix: activate the env and run: python -m pip install -e '.[dev]'. "
                    f"Console scripts will be placed in: {env_bin}."
                )
            else:
                suggestions.append(
                    "Run: python -m pip install -e '.[dev]'  (ensure this is the same 'python' shown above)."
                )
            suggestions.append(
                "Optionally, uninstall the user-level pytest to avoid confusion: python -m pip uninstall -y pytest"
            )

    # PATH hints for user installs
    user_base = _run([py, "-c", "import site,sys; print(site.USER_BASE)"])[1]
    if user_base:
        user_bin = os.path.join(user_base, "Scripts" if os.name == "nt" else "bin")
        if user_bin not in (os.environ.get("PATH") or ""):
            suggestions.append(
                f"User scripts directory not on PATH: {user_bin}. Add it to PATH or activate your venv before running 'pytest'."
            )
            if os.name != "nt":
                suggestions.append(
                    f"Example (bash/zsh): export PATH=\"{user_bin}:$PATH\""
                )

    # If running inside conda, hint at 'conda run'
    if os.environ.get("CONDA_PREFIX"):
        suggestions.append(
            "Conda detected: you can also run tests with 'conda run -n $(basename \"$CONDA_PREFIX\") python -m pytest -q' to ensure the right interpreter."
        )

    # Shell hash cache hint (bash/zsh caches command locations)
    suggestions.append(
        "If you recently installed pytest and the shell still says 'command not found' or runs the wrong one, try: hash -r"
    )

    if suggestions:
        print("\nSuggestions:")
        for s in suggestions:
            print(f" - {s}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
