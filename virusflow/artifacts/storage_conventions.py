from __future__ import annotations

from dataclasses import replace

import numpy as np

from .requests import LogicalComponent


FLUX_SCALE = 1.0e-17
VARIANCE_SCALE = FLUX_SCALE ** 2
FLUX_BUNIT = "1e-17 erg s-1 cm-2 Angstrom-1"
VARIANCE_BUNIT = "(1e-17 erg s-1 cm-2 Angstrom-1)^2"

ASTROMETRIC_KINDS = frozenset(
    {
        "initial_astrometry",
        "catalog_match_table",
        "final_astrometry",
        "fiber_sky_coordinates",
    }
)


def is_astrometric(kind: str, component: LogicalComponent) -> bool:
    coordinate = str(component.coordinates or "").lower()
    return (
        str(kind).lower() in ASTROMETRIC_KINDS
        or "icrs" in coordinate
        or "world" in coordinate
        or "astrom" in coordinate
        or component.name == "sky_coordinates"
    )


def normalize_component(kind: str, component: LogicalComponent) -> LogicalComponent:
    """Apply persistence dtypes without constraining algorithm precision."""

    if str(component.model_type).lower() == "mask":
        value = np.asarray(component.value)
        if value.dtype.kind == "b":
            value = value.astype(np.uint8)
        elif value.dtype.itemsize > 2 or value.dtype.kind not in "ui":
            maximum = int(np.nanmax(value)) if value.size else 0
            value = value.astype(np.uint8 if maximum <= 255 else np.uint16)
        return replace(component, value=value)
    value = np.asarray(component.value)
    if value.dtype.kind == "f" and not is_astrometric(kind, component):
        value = value.astype(np.float32, copy=False)
    return replace(component, value=value)


def scaled_flux_component(name: str, physical_value, coordinates: str) -> LogicalComponent:
    return LogicalComponent(
        name,
        "array1d" if np.asarray(physical_value).ndim == 1 else "array2d",
        np.asarray(physical_value, dtype=np.float32),
        FLUX_BUNIT,
        coordinates,
        {"physical_scale": FLUX_SCALE, "bunit": FLUX_BUNIT, "scale_convention": "physical_per_stored_unit"},
    )


def scaled_variance_component(name: str, physical_value, coordinates: str) -> LogicalComponent:
    return LogicalComponent(
        name,
        "array1d" if np.asarray(physical_value).ndim == 1 else "array2d",
        np.asarray(physical_value, dtype=np.float32),
        VARIANCE_BUNIT,
        coordinates,
        {"physical_scale": VARIANCE_SCALE, "bunit": VARIANCE_BUNIT, "scale_convention": "physical_per_stored_unit"},
    )
