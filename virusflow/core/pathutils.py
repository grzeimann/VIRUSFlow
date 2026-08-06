from __future__ import annotations

from pathlib import Path
from typing import Union, Optional
import os
import re

PathLike = Union[str, os.PathLike]


def ensure_dir(path: Optional[PathLike]) -> None:
    """Create a directory path if it doesn't exist (parents included).

    - Accepts None or empty string and treats it as current directory (".").
    - Safe to call concurrently; exist_ok=True avoids races.
    """
    p = Path(path or ".")
    p.mkdir(parents=True, exist_ok=True)


def sanitize_for_filename(text: Optional[str]) -> str:
    """Return a filesystem-safe filename fragment.

    - Replaces path separators (os.sep/os.altsep) with '_'.
    - Collapses spaces and any non [A-Za-z0-9._-] characters to '_'.
    - Strips leading/trailing underscores.
    - Returns empty string safely if input is None/empty.
    """
    if not text:
        return ""
    s = str(text)
    # Replace OS-specific separators
    seps = [os.sep]
    if os.altsep:
        seps.append(os.altsep)
    for sep in seps:
        if sep:
            s = s.replace(sep, "_")
    # Replace spaces with underscore
    s = s.replace(" ", "_")
    # Replace any remaining unsafe chars with underscore
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    # Collapse repeated underscores
    s = re.sub(r"_+", "_", s)
    return s.strip("_")
