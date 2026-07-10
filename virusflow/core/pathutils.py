from __future__ import annotations

from pathlib import Path
from typing import Union, Optional
import os

PathLike = Union[str, os.PathLike]


def ensure_dir(path: Optional[PathLike]) -> None:
    """Create a directory path if it doesn't exist (parents included).

    - Accepts None or empty string and treats it as current directory (".").
    - Safe to call concurrently; exist_ok=True avoids races.
    """
    p = Path(path or ".")
    p.mkdir(parents=True, exist_ok=True)
