from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ZipCode:
    """Unique identity for a VIRUS amplifier/channel.

    Components:
    - IFUSLOT
    - IFUID
    - SPECID
    - AMP
    - CONTROLLER
    """

    ifuslot: str
    ifuid: str
    specid: str
    amp: str
    controller: str

    def as_tuple(self) -> Tuple[str, str, str, str, str]:
        return (self.ifuslot, self.ifuid, self.specid, self.amp, self.controller)

    def key(self) -> str:
        return "+".join(self.as_tuple())


@dataclass(frozen=True)
class RawFileId:
    exposure_id: str
    frame_type: str
    path: str
    tar_member: Optional[str] = None
    storage_backend: str = "filesystem"
    zipcode: Optional[ZipCode] = None
