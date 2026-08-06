from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.identity import ZipCode


@dataclass(frozen=True)
class ExposureIdentity:
    exposure_id: str
    when_utc: Optional[str] = None
    mode: str = "unknown"


@dataclass(frozen=True)
class AmplifierIdentity:
    zipcode: ZipCode


@dataclass(frozen=True)
class PhysicalCCDIdentity:
    exposure_id: str
    specid: str
    side: str


@dataclass(frozen=True)
class ObservationIdentity:
    observation_id: str


@dataclass(frozen=True)
class DitherSetIdentity:
    dither_set_id: str
    observation_id: str
