from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Optional, Tuple


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used by stable identities."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def measurement_group_identity(
    *, member_kind: str, coherence_rule: str, coherence_rule_version: str,
    coherence_key: Mapping[str, Any], declared_slots: Tuple[Any, ...],
    anchor_measurement_group_ids: Tuple[str, ...] = (),
    grouping_parameters: Mapping[str, Any] | None = None,
    configuration_references: Tuple[Mapping[str, Any], ...] = (),
) -> str:
    """Compute the immutable identity of a declared measurement cohort."""

    members = sorted(
        ({"member_scope_key": slot.member_scope_key,
          "member_computation_id": slot.member_computation_id} for slot in declared_slots),
        key=lambda slot: slot["member_scope_key"],
    )
    payload = {
        "identity_schema": 1, "member_kind": str(member_kind),
        "coherence_rule": str(coherence_rule),
        "coherence_rule_version": str(coherence_rule_version),
        "coherence_key": dict(coherence_key),
        "anchor_measurement_group_ids": sorted(map(str, anchor_measurement_group_ids)),
        "material_grouping_parameters": dict(grouping_parameters or {}),
        "material_configuration_references": list(configuration_references),
        "declared_members": members,
    }
    return "mg:v1:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


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


def parse_zipcode_key(key: str) -> ZipCode:
    """Parse a zipcode key string 'IFUSLOT+IFUID+SPECID+AMP+CONTROLLER' into a ZipCode.

    Raises SystemExit on invalid input to match CLI error behavior.
    """
    parts = str(key).split("+")
    if len(parts) != 5:
        raise SystemExit(f"Invalid zipcode key '{key}'. Expected 5 parts joined by '+'.")
    return ZipCode(ifuslot=parts[0], ifuid=parts[1], specid=parts[2], amp=parts[3], controller=parts[4])


@dataclass(frozen=True)
class RawFileId:
    exposure_id: str
    frame_type: str
    path: str
    tar_member: Optional[str] = None
    storage_backend: str = "filesystem"
    zipcode: Optional[ZipCode] = None
    archive_offset: Optional[int] = None
    archive_size: Optional[int] = None
    outer_tar_member: Optional[str] = None
