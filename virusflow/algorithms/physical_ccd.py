from __future__ import annotations

"""Pure physical-CCD assembly and baseline gap-constrained scatter algorithms."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.algo_result import AlgoResult
from ..ontology.coordinates import UPPER_AMPLIFIER_Y_OFFSET


ALGORITHM_VERSION = "physical-ccd-gap-polynomial-1.1"
TRANSFORM_VERSION = "indexed-2"
PAIRING = {"left": ("LL", "LU"), "right": ("RU", "RL")}


@dataclass(frozen=True)
class ScatteredLightModel:
    """Compact total-degree-two physical-CCD surface."""

    coefficients: np.ndarray
    detector_shape: tuple[int, int]
    representation: str = "robust_total_degree_2_polynomial"

    def __post_init__(self) -> None:
        coefficients = np.asarray(self.coefficients, dtype=np.float32)
        if coefficients.shape != (6,):
            raise ValueError("total-degree-two scattered-light model requires six coefficients")
        if len(self.detector_shape) != 2 or min(self.detector_shape) < 1:
            raise ValueError("detector_shape must contain two positive dimensions")
        object.__setattr__(self, "coefficients", coefficients)
        object.__setattr__(self, "detector_shape", tuple(int(x) for x in self.detector_shape))

    def evaluate(self, x=None, y=None) -> np.ndarray:
        ny, nx = self.detector_shape
        if x is None and y is None:
            yy, xx = np.indices(self.detector_shape, dtype=np.float32)
        elif x is None or y is None:
            raise ValueError("x and y must either both be supplied or both be omitted")
        else:
            xx, yy = np.broadcast_arrays(np.asarray(x, dtype=np.float32), np.asarray(y, dtype=np.float32))
        xn = 2.0 * xx / max(1, nx - 1) - 1.0
        yn = 2.0 * yy / max(1, ny - 1) - 1.0
        design = _design(xn.ravel(), yn.ravel())
        return (design @ self.coefficients).reshape(xx.shape).astype(np.float32)


def compact_scattered_light_payload(result: AlgoResult) -> dict[str, np.ndarray]:
    """Extract the reconstructable model and bounded fit evidence from a fit result."""

    def bounded_indices(mask: np.ndarray, limit: int = 2048) -> np.ndarray:
        indices = np.flatnonzero(mask)
        if indices.size <= limit:
            return indices.astype(np.int32)
        # Keep diagnostic evidence deterministic and distributed across the
        # detector without allowing it to grow into another dense payload.
        selection = np.linspace(0, indices.size - 1, limit, dtype=np.int64)
        return indices[selection].astype(np.int32)

    model = np.asarray(result.get_array("model"))
    gap = np.asarray(result.get_array("gap_sample_mask"), dtype=bool)
    fit = np.asarray(result.get_array("retained_fit_sample_mask"), dtype=bool)
    holdout = np.asarray(result.get_array("holdout_sample_mask"), dtype=bool)
    residual = np.asarray(result.get_array("fit_residual"), dtype=np.float32)
    finite_residual = np.isfinite(residual)
    residual_indices = bounded_indices(finite_residual)
    return {
        "model_parameters": np.asarray(result.get_array("model_parameters"), dtype=np.float32),
        "detector_shape": np.asarray(model.shape, dtype=np.int32),
        "gap_sample_indices": bounded_indices(gap),
        "fit_sample_indices": bounded_indices(fit),
        "holdout_sample_indices": bounded_indices(holdout),
        "residual_sample_indices": residual_indices,
        "residual_sample_values": residual.ravel()[residual_indices].astype(np.float32),
    }


def scattered_light_model_from_payload(payload: dict[str, np.ndarray]) -> ScatteredLightModel:
    return ScatteredLightModel(
        payload["model_parameters"], tuple(np.asarray(payload["detector_shape"], dtype=int))
    )


def upper_y(y):
    """Translate a zero-indexed upper-amplifier row into physical-CCD coordinates."""

    return UPPER_AMPLIFIER_Y_OFFSET + np.asarray(y)


def inverse_upper_y(y):
    """Map a physical-CCD upper row back to its zero-indexed amplifier row."""

    return np.asarray(y) - UPPER_AMPLIFIER_Y_OFFSET


def amplifier_from_physical(physical_array, amp: str) -> np.ndarray:
    """Project one amplifier from a physical CCD without changing row order."""

    data = np.asarray(physical_array)
    if data.ndim < 1 or data.shape[0] != 2 * UPPER_AMPLIFIER_Y_OFFSET:
        raise ValueError(
            "physical CCD array must have "
            f"{2 * UPPER_AMPLIFIER_Y_OFFSET} rows, got {data.shape}"
        )
    amp = str(amp).upper()
    if amp in {"LL", "RU"}:
        return data[:UPPER_AMPLIFIER_Y_OFFSET]
    if amp in {"LU", "RL"}:
        return data[UPPER_AMPLIFIER_Y_OFFSET:]
    raise ValueError(f"unknown amplifier {amp!r}")


def _validate_pair(side: str, lower_amp: str, upper_amp: str, lower, upper) -> tuple[np.ndarray, np.ndarray]:
    side = str(side).lower()
    if side not in PAIRING:
        raise ValueError("side must be 'left' or 'right'")
    if (str(lower_amp), str(upper_amp)) != PAIRING[side]:
        raise ValueError(f"{side} CCD requires {PAIRING[side]}, got {(lower_amp, upper_amp)}")
    lo = np.asarray(lower)
    up = np.asarray(upper)
    if lo.ndim != 2 or up.ndim != 2 or lo.shape != up.shape:
        raise ValueError("paired amplifier arrays must be shape-matched 2D arrays")
    if lo.shape[0] != UPPER_AMPLIFIER_Y_OFFSET:
        raise ValueError(
            f"paired amplifier height must be {UPPER_AMPLIFIER_Y_OFFSET}, got {lo.shape[0]}"
        )
    return lo, up


def assemble_physical_ccd(
    lower_image,
    upper_image,
    *,
    side: str,
    lower_amp: str,
    upper_amp: str,
    lower_variance=None,
    upper_variance=None,
    lower_mask=None,
    upper_mask=None,
) -> AlgoResult:
    """Assemble two canonical amplifier arrays using explicit indexed transforms."""

    lo, up = _validate_pair(side, lower_amp, upper_amp, lower_image, upper_image)
    height, width = lo.shape
    image = np.empty((2 * height, width), dtype=np.result_type(lo, up))
    image[:height] = lo
    image[height:] = up

    def paired(a, b, *, dtype=None, fill=0):
        if a is None:
            a = np.full(lo.shape, fill, dtype=dtype or float)
        if b is None:
            b = np.full(up.shape, fill, dtype=dtype or float)
        aa, bb = np.asarray(a), np.asarray(b)
        if aa.shape != lo.shape or bb.shape != up.shape:
            raise ValueError("variance and mask arrays must match their amplifier image")
        out = np.empty(image.shape, dtype=dtype or np.result_type(aa, bb))
        out[:height] = aa
        out[height:] = bb
        return out

    variance = paired(lower_variance, upper_variance, dtype=np.float32, fill=np.nan)
    pixel_mask = paired(lower_mask, upper_mask, dtype=np.uint8, fill=0)
    pixel_mask |= (~np.isfinite(image) | ~np.isfinite(variance)).astype(np.uint8)

    source_amplifier = np.empty(image.shape, dtype=np.uint8)
    source_amplifier[:height] = 0
    source_amplifier[height:] = 1
    source_y = np.broadcast_to(np.arange(height, dtype=np.int16)[:, None], lo.shape)
    source_y_coordinate = np.empty(image.shape, dtype=np.int16)
    source_y_coordinate[:height] = source_y
    source_y_coordinate[height:] = source_y

    seam_mask = np.zeros(image.shape, dtype=np.uint8)
    seam_mask[height - 1 : height + 1] = 1
    # There are no unmeasured detector rows in the materialized 2064-row array.
    # A dedicated all-false component records this fact instead of hiding the gap policy.
    inter_amplifier_gap_mask = np.zeros(image.shape, dtype=np.uint8)

    physical_rows = np.concatenate((np.arange(height), upper_y(np.arange(height))))
    if np.unique(physical_rows).size != image.shape[0] or set(physical_rows.tolist()) != set(range(image.shape[0])):
        raise RuntimeError("physical CCD transform duplicated or omitted detector rows")

    return AlgoResult(
        kind="physical_ccd_assembly",
        version=ALGORITHM_VERSION,
        meta={
            "side": str(side).lower(),
            "lower_amp": lower_amp,
            "upper_amp": upper_amp,
            "upper_transform": "upper_y = 1032 + y",
            "transform_version": TRANSFORM_VERSION,
            "seam_between_rows": [height - 1, height],
            "inter_amplifier_gap_rows": 0,
        },
        scalars={
            "detector_row_count": int(image.shape[0]),
            "unique_source_row_count": int(np.unique(physical_rows).size),
            "transform_invertible": 1,
        },
        arrays={
            "image": image,
            "variance": variance,
            "pixel_mask": pixel_mask,
            "seam_mask": seam_mask,
            "inter_amplifier_gap_mask": inter_amplifier_gap_mask,
            "source_amplifier_map": source_amplifier,
            "source_y_coordinate": source_y_coordinate,
        },
    )


def physical_trace_map(lower_trace, upper_trace) -> np.ndarray:
    lower = np.asarray(lower_trace, dtype=float)
    upper = np.asarray(upper_trace, dtype=float)
    if lower.ndim != 2 or upper.ndim != 2 or lower.shape[1] != upper.shape[1]:
        raise ValueError("paired trace maps must be 2D with the same dispersion length")
    return np.vstack((lower, upper_y(upper)))


def gap_sample_mask(
    shape: tuple[int, int],
    traces,
    *,
    core_exclusion_pixels: float = 4.5,
    minimum_group_gap_pixels: float = 12.0,
) -> np.ndarray:
    """Select detector pixels between widely separated trace groups."""

    ny, nx = shape
    tr = np.asarray(traces, dtype=float)
    if tr.ndim != 2 or tr.shape[1] != nx:
        raise ValueError("trace map is incompatible with physical CCD image")
    mask = np.zeros((ny, nx), dtype=bool)
    for x in range(nx):
        positions = np.sort(tr[:, x][np.isfinite(tr[:, x])])
        if positions.size < 2:
            continue
        gaps = np.where(np.diff(positions) >= float(minimum_group_gap_pixels))[0]
        for index in gaps:
            start = max(0, int(np.ceil(positions[index] + core_exclusion_pixels)))
            stop = min(ny, int(np.floor(positions[index + 1] - core_exclusion_pixels)) + 1)
            if stop > start:
                mask[start:stop, x] = True
    return mask


def _design(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.column_stack((np.ones_like(x), x, y, x * x, x * y, y * y))


def fit_gap_scattered_light(
    assembly: AlgoResult,
    lower_trace,
    upper_trace,
    *,
    core_exclusion_pixels: float = 4.5,
    minimum_group_gap_pixels: float = 12.0,
    holdout_chunk_period: int = 5,
    sigma_clip: float = 4.0,
    iterations: int = 4,
    explicit_mask: Optional[np.ndarray] = None,
) -> AlgoResult:
    """Fit the approved simplest robust smooth 2D gap-constrained baseline."""

    image = np.asarray(assembly.get_array("image"), dtype=float)
    pixel_mask = np.asarray(assembly.get_array("pixel_mask"), dtype=bool)
    if explicit_mask is not None:
        if np.asarray(explicit_mask).shape != image.shape:
            raise ValueError("explicit scatter fit mask must match the CCD image")
        pixel_mask |= np.asarray(explicit_mask, dtype=bool)
    traces = physical_trace_map(lower_trace, upper_trace)
    gap = gap_sample_mask(
        image.shape, traces,
        core_exclusion_pixels=core_exclusion_pixels,
        minimum_group_gap_pixels=minimum_group_gap_pixels,
    )
    gap &= np.isfinite(image) & ~pixel_mask
    chunk_width = max(8, image.shape[1] // 32)
    xgrid = np.broadcast_to(np.arange(image.shape[1])[None, :], image.shape)
    holdout = gap & (((xgrid // chunk_width) % max(2, int(holdout_chunk_period))) == 0)
    fit_mask = gap & ~holdout
    yy, xx = np.where(fit_mask)
    if yy.size < 30:
        raise ValueError(f"insufficient clean gap samples for scattered-light fit: {yy.size}")

    xn = 2.0 * xx.astype(float) / max(1, image.shape[1] - 1) - 1.0
    yn = 2.0 * yy.astype(float) / max(1, image.shape[0] - 1) - 1.0
    values = image[yy, xx]
    keep = np.ones(values.size, dtype=bool)
    coefficients = np.zeros(6, dtype=float)
    for _ in range(max(1, int(iterations))):
        coefficients, *_ = np.linalg.lstsq(_design(xn[keep], yn[keep]), values[keep], rcond=None)
        residual = values - _design(xn, yn) @ coefficients
        center = float(np.nanmedian(residual[keep]))
        scale = float(1.4826 * np.nanmedian(np.abs(residual[keep] - center)))
        if not np.isfinite(scale) or scale <= 0:
            break
        next_keep = np.abs(residual - center) <= float(sigma_clip) * scale
        if np.array_equal(next_keep, keep):
            break
        keep = next_keep

    full_y, full_x = np.indices(image.shape, dtype=float)
    full_x = 2.0 * full_x / max(1, image.shape[1] - 1) - 1.0
    full_y = 2.0 * full_y / max(1, image.shape[0] - 1) - 1.0
    model = (_design(full_x.ravel(), full_y.ravel()) @ coefficients).reshape(image.shape)
    residual_image = np.full(image.shape, np.nan, dtype=np.float32)
    residual_image[gap] = (image - model)[gap]

    fit_residual = residual_image[fit_mask & np.isfinite(residual_image)]
    holdout_residual = residual_image[holdout & np.isfinite(residual_image)]
    robust = lambda a: float(1.4826 * np.nanmedian(np.abs(a - np.nanmedian(a)))) if a.size else float("nan")
    seam_row = image.shape[0] // 2
    source_scale = float(np.nanpercentile(np.abs(image[np.isfinite(image)]), 95))
    model_scale = float(np.nanpercentile(np.abs(model[np.isfinite(model)]), 95))
    continuity = float(np.nanmedian(np.abs(model[seam_row] - model[seam_row - 1])))
    boundary_band = np.r_[residual_image[max(0, seam_row - 4):seam_row].ravel(), residual_image[seam_row:seam_row + 4].ravel()]
    boundary_band = boundary_band[np.isfinite(boundary_band)]
    retained_fit_mask = np.zeros(image.shape, dtype=bool)
    retained_fit_mask[yy[keep], xx[keep]] = True

    return AlgoResult(
        kind="ccd_scattered_light_model",
        version=ALGORITHM_VERSION,
        meta={
            **dict(assembly.meta or {}),
            "surface_model": "robust_total_degree_2_polynomial",
            "core_exclusion_pixels": float(core_exclusion_pixels),
            "minimum_group_gap_pixels": float(minimum_group_gap_pixels),
            "holdout_chunk_period": int(holdout_chunk_period),
            "sigma_clip": float(sigma_clip),
        },
        scalars={
            "gap_sample_count": int(gap.sum()),
            "fit_sample_count": int(keep.sum()),
            "rejected_fit_sample_count": int((~keep).sum()),
            "holdout_sample_count": int(holdout.sum()),
            "fit_residual_robust_sigma": robust(fit_residual),
            "holdout_residual_robust_sigma": robust(holdout_residual),
            "boundary_residual_robust_sigma": robust(boundary_band),
            "cross_amplifier_model_discontinuity": continuity,
            "model_to_source_p95_ratio": model_scale / source_scale if source_scale > 0 else float("nan"),
        },
        arrays={
            "model": model.astype(np.float32),
            "gap_sample_mask": gap.astype(np.uint8),
            "fit_sample_mask": fit_mask.astype(np.uint8),
            "retained_fit_sample_mask": retained_fit_mask.astype(np.uint8),
            "holdout_sample_mask": holdout.astype(np.uint8),
            "fit_residual": residual_image,
            "model_parameters": coefficients,
            "scatter_subtracted_image": (image - model).astype(np.float32),
        },
    )
