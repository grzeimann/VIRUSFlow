from __future__ import annotations

"""
Algorithm implementations for VIRUSFlow tasks.

This package hosts self‑contained, testable algorithm functions that pipeline
Task wrappers call. Start with bias (step_bias), and expand to dark, flats,
trace, wavelength, extraction, etc.

Design goals:
- Keep algorithms free of CLI/DB concerns; pass in only what is needed.
- Prefer pure functions where possible; side effects (file I/O) are localized.
- Versioning is primarily handled at the Task level; algorithm modules can host
  multiple routines or helper variants if needed.
"""
