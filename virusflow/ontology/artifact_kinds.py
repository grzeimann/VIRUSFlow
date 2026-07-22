from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from .coordinates import CoordinateConvention
from .scopes import PhysicalScope
from .units import Unit


@dataclass(frozen=True)
class ArtifactKindSpec:
    name: str
    scope: PhysicalScope
    payload_type: str
    units: Optional[str]
    coordinates: CoordinateConvention
    required_components: Tuple[str, ...] = ()
    optional_components: Tuple[str, ...] = ()
    allowed_roles: Tuple[str, ...] = ("calibration", "reduction", "diagnostic", "analytic")


def _spec(
    name: str,
    scope: PhysicalScope,
    units: Optional[str],
    coordinates: CoordinateConvention,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> ArtifactKindSpec:
    return ArtifactKindSpec(
        name=name,
        scope=scope,
        payload_type="array",
        units=units,
        coordinates=coordinates,
        required_components=tuple(required),
        optional_components=tuple(optional),
    )


ARTIFACT_KINDS: Dict[str, ArtifactKindSpec] = {
    "master_bias": _spec("master_bias", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master", "per_pixel_bias_scatter")),
    "master_dark": _spec("master_dark", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_dark", "dark_pixel_mask")),
    "master_ldls": _spec("master_ldls", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_ldls", "flat_response_mask")),
    "master_arc": _spec("master_arc", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_arc",)),
    "master_twilight": _spec("master_twilight", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_twilight",)),
    "trace_map": _spec("trace_map", PhysicalScope.AMPLIFIER, Unit.PIXEL.value, CoordinateConvention.FIBER_BY_DISPERSION_PIXEL, ("fiber_trace_map", "trace_sample_columns", "sampled_trace_positions", "per_fiber_trace_residual_rms")),
    "wavelength_map": _spec("wavelength_map", PhysicalScope.AMPLIFIER, Unit.ANGSTROM.value, CoordinateConvention.FIBER_BY_DISPERSION_PIXEL, ("wavelength_map", "per_fiber_wavelength_residual_rms"), ("arc_identification",)),
    "read_noise": _spec("read_noise", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.NONE, ("read_noise",)),
    "gain": _spec("gain", PhysicalScope.AMPLIFIER, "electron / adu", CoordinateConvention.NONE, ("gain",)),
    "pixel_mask": _spec("pixel_mask", PhysicalScope.PIXEL, Unit.DIMENSIONLESS.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("mask",)),
    "detector_variance": _spec("detector_variance", PhysicalScope.PIXEL, Unit.ELECTRON_VARIANCE.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("variance",)),
    "oriented_detector_image": _spec("oriented_detector_image", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("image",)),
    "overscan_model": _spec("overscan_model", PhysicalScope.AMPLIFIER, Unit.ADU.value, CoordinateConvention.RAW_AMPLIFIER, ("row_model",)),
    "overscan_corrected_image": _spec("overscan_corrected_image", PhysicalScope.AMPLIFIER, Unit.ADU.value, CoordinateConvention.RAW_AMPLIFIER, ("image",)),
}


LEGACY_KIND_ALIASES: Dict[str, str] = {
    "master_flat": "master_ldls",
    "masterflt": "master_ldls",
    "master_cmp": "master_arc",
    "mastercmp": "master_arc",
    "master_twi": "master_twilight",
    "trace": "trace_map",
    "wave": "wavelength_map",
}


def canonical_kind(name: str) -> str:
    key = str(name or "").strip().lower()
    return LEGACY_KIND_ALIASES.get(key, key)


def kind_spec(name: str) -> ArtifactKindSpec:
    canonical = canonical_kind(name)
    try:
        return ARTIFACT_KINDS[canonical]
    except KeyError as exc:
        raise KeyError(f"Unregistered Artifact kind: {name!r}") from exc


def kind_candidates(name: str) -> Tuple[str, ...]:
    canonical = canonical_kind(name)
    aliases = [legacy for legacy, target in LEGACY_KIND_ALIASES.items() if target == canonical]
    return tuple(dict.fromkeys([canonical, str(name).strip().lower(), *aliases]))

