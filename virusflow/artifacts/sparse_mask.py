from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class EncodedMask:
    shape: tuple[int, ...]
    dtype: str
    encoding: str
    payload: np.ndarray

    def dense(self) -> np.ndarray:
        return decode_mask(self)


def _runs(flat: np.ndarray) -> np.ndarray:
    nonzero = np.flatnonzero(flat)
    if nonzero.size == 0:
        return np.empty((0, 3), dtype=np.int64)
    starts = nonzero[np.r_[True, np.diff(nonzero) != 1]]
    stops = nonzero[np.r_[np.diff(nonzero) != 1, True]] + 1
    rows: list[tuple[int, int, int]] = []
    for start, stop in zip(starts, stops):
        segment = flat[start:stop]
        value_starts = np.r_[0, np.flatnonzero(np.diff(segment) != 0) + 1]
        value_stops = np.r_[value_starts[1:], segment.size]
        rows.extend(
            (int(start + a), int(b - a), int(segment[a]))
            for a, b in zip(value_starts, value_stops)
        )
    return np.asarray(rows, dtype=np.int64).reshape((-1, 3))


def encode_mask(mask, *, allowed: Iterable[str] = ("sparse", "rle", "packed", "dense")) -> EncodedMask:
    """Choose the smallest lossless representation from the allowed encodings."""

    array = np.asarray(mask)
    if array.dtype.kind not in "bui" or array.dtype.itemsize > 2:
        raise TypeError("masks must be bool, uint8, or uint16 bit fields")
    dtype = np.dtype(np.uint8 if array.dtype.kind == "b" else array.dtype)
    if dtype not in (np.dtype(np.uint8), np.dtype(np.uint16)):
        dtype = np.dtype(np.uint16)
    dense = np.asarray(array, dtype=dtype)
    flat = dense.ravel()
    choices: dict[str, np.ndarray] = {}
    permitted = {str(value).lower() for value in allowed}
    if "dense" in permitted:
        choices["dense"] = dense
    if "sparse" in permitted:
        index = np.flatnonzero(flat)
        choices["sparse"] = np.column_stack((index, flat[index])).astype(np.int64, copy=False)
    if "rle" in permitted:
        choices["rle"] = _runs(flat)
    if "packed" in permitted and np.all((flat == 0) | (flat == 1)):
        choices["packed"] = np.packbits(flat.astype(np.uint8), bitorder="little")
    if not choices:
        raise ValueError("no valid mask encoding was allowed")
    encoding, payload = min(choices.items(), key=lambda item: (item[1].nbytes, item[0]))
    return EncodedMask(tuple(int(x) for x in dense.shape), dtype.str, encoding, payload)


def decode_mask(encoded: EncodedMask) -> np.ndarray:
    dtype = np.dtype(encoded.dtype)
    size = int(np.prod(encoded.shape, dtype=np.int64))
    if encoded.encoding == "dense":
        return np.asarray(encoded.payload, dtype=dtype).reshape(encoded.shape)
    flat = np.zeros(size, dtype=dtype)
    if encoded.encoding == "sparse":
        rows = np.asarray(encoded.payload, dtype=np.int64).reshape((-1, 2))
        if rows.size:
            flat[rows[:, 0]] = rows[:, 1].astype(dtype, copy=False)
    elif encoded.encoding == "rle":
        for start, length, value in np.asarray(encoded.payload, dtype=np.int64).reshape((-1, 3)):
            flat[int(start): int(start + length)] = value
    elif encoded.encoding == "packed":
        flat[:] = np.unpackbits(
            np.asarray(encoded.payload, dtype=np.uint8), bitorder="little", count=size
        ).astype(dtype, copy=False)
    else:
        raise ValueError(f"unknown mask encoding: {encoded.encoding}")
    return flat.reshape(encoded.shape)
