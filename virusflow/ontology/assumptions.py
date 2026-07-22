from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceState(str, Enum):
    VERIFIED = "verified"
    PROVISIONAL = "provisional"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AssumptionSpec:
    id: str
    statement: str
    state: EvidenceState


ASSUMPTIONS = {
    "uniform_center_track_twilight": AssumptionSpec(
        "uniform_center_track_twilight",
        "Center-track twilight is sufficiently uniform for exposure-wide relative normalization.",
        EvidenceState.PROVISIONAL,
    ),
    "stable_five_pixel_aperture_capture": AssumptionSpec(
        "stable_five_pixel_aperture_capture",
        "The fractional five-pixel aperture captures a stable fraction of the fiber profile.",
        EvidenceState.PROVISIONAL,
    ),
}

