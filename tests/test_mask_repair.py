from __future__ import annotations

import numpy as np

from virusflow.algorithms.utils.masks import repair_masked_columns


def test_repair_pixels_column_median_replaces_masked_with_column_median():
    # Build a simple 4x5 image with increasing values per column
    yy, xx = np.indices((4, 5))
    img = (xx * 10 + yy).astype(float)
    # Mask a few pixels across different columns
    m = np.zeros_like(img, dtype=np.uint8)
    m[0, 0] = 1  # column 0: unmasked values are [10,20,30]? Wait - base is xx*10+yy, so col0 values [0,1,2,3]
    m[3, 2] = 1  # column 2 values [20,21,22,23]
    m[1, 4] = 1  # column 4 values [40,41,42,43]

    repaired = repair_masked_columns(img, m)

    # Column medians for unmasked entries
    # col0 unmasked values [1,2,3] -> median = 2
    assert repaired[0, 0] == 2
    # col2 unmasked [20,21,23] -> median = 21
    assert repaired[3, 2] == 21
    # col4 unmasked [40,42,43] -> median = 42
    assert repaired[1, 4] == 42

    # Unmasked pixels remain unchanged
    assert np.all(repaired[m == 0] == img[m == 0])


def test_repair_masked_columns_handles_none_or_shape_mismatch():
    img = np.arange(9, dtype=float).reshape(3, 3)
    # None mask -> unchanged
    out = repair_masked_columns(img, None)
    assert np.array_equal(out, img)
    # Shape mismatch -> unchanged
    m = np.zeros((2, 2), dtype=np.uint8)
    out2 = repair_masked_columns(img, m)
    assert np.array_equal(out2, img)
