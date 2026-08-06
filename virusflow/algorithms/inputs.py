from __future__ import annotations

from typing import Iterable, List

import numpy as np


def array_frames(inputs: Iterable) -> List[np.ndarray]:
    """Normalize already-loaded/preprocessed algorithm inputs without file I/O."""
    frames: List[np.ndarray] = []
    for index, item in enumerate(inputs or []):
        value = item.get("data") if isinstance(item, dict) else item
        if value is None:
            raise TypeError(f"Algorithm input {index} has no array data; Tasks must load raw files through virusflow.io")
        array = np.asarray(value, dtype=float)
        if array.ndim != 2:
            raise ValueError(f"Algorithm input {index} must be a 2D array, got shape={array.shape}")
        frames.append(array)
    if not frames:
        raise ValueError("At least one array input is required")
    shapes = {frame.shape for frame in frames}
    if len(shapes) != 1:
        raise ValueError(f"Input frames have differing shapes: {sorted(shapes)}")
    return frames
