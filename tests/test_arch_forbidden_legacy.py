from __future__ import annotations

import pathlib

# Guardrails for Section 8 — ensure no obsolete execution paths remain in algorithms
# - Algorithms must not call write_array_fits directly
# - Algorithms must not accept or refer to an 'output_path' parameter

def test_algorithms_do_not_reference_write_array_fits_or_output_path():
    alg_dir = pathlib.Path(__file__).resolve().parents[1] / "virusflow" / "algorithms"
    assert alg_dir.is_dir(), f"Algorithms directory not found: {alg_dir}"
    forbidden = ["write_array_fits", "output_path"]
    offenders: list[str] = []
    for py in alg_dir.glob("*.py"):
        txt = py.read_text(encoding="utf-8")
        # Allow docstrings to mention storage-neutral results but not legacy params
        for pat in forbidden:
            if pat in txt:
                offenders.append(f"{py.name}: contains '{pat}'")
    # Whitelist known modules that may define guidance but are not algorithms (none here)
    if offenders:
        raise AssertionError("Forbidden legacy patterns found in algorithms files:\n" + "\n".join(offenders))
