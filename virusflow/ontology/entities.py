from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

from ..core.identity import ZipCode
from ..core.identity import measurement_group_identity


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy a mapping so identity-bearing group values cannot be mutated."""

    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class MeasurementGroupSlot:
    member_scope_key: str
    member_computation_id: str


@dataclass(frozen=True)
class MeasurementGroup:
    """Immutable cross-scope declaration; realized Artifacts are not its identity."""

    member_kind: str
    coherence_rule: str
    coherence_rule_version: str
    coherence_key: Mapping[str, Any]
    declared_slots: tuple[MeasurementGroupSlot, ...]
    anchor_measurement_group_ids: tuple[str, ...] = ()
    grouping_parameters: Mapping[str, Any] = field(default_factory=dict)
    configuration_references: tuple[Mapping[str, Any], ...] = ()
    measurement_group_id: str = ""

    def __post_init__(self) -> None:
        slots = tuple(sorted(self.declared_slots, key=lambda slot: slot.member_scope_key))
        if not slots:
            raise ValueError("MeasurementGroup requires at least one declared slot")
        if len({slot.member_scope_key for slot in slots}) != len(slots):
            raise ValueError("MeasurementGroup declared scope keys must be unique")
        object.__setattr__(self, "declared_slots", slots)
        object.__setattr__(self, "coherence_key", _frozen_mapping(self.coherence_key))
        object.__setattr__(self, "grouping_parameters", _frozen_mapping(self.grouping_parameters))
        object.__setattr__(
            self, "configuration_references",
            tuple(_frozen_mapping(item) for item in self.configuration_references),
        )
        computed = measurement_group_identity(
            member_kind=self.member_kind, coherence_rule=self.coherence_rule,
            coherence_rule_version=self.coherence_rule_version,
            coherence_key=self.coherence_key, declared_slots=slots,
            anchor_measurement_group_ids=self.anchor_measurement_group_ids,
            grouping_parameters=self.grouping_parameters,
            configuration_references=self.configuration_references,
        )
        if self.measurement_group_id and self.measurement_group_id != computed:
            raise ValueError("MeasurementGroup ID does not match its immutable definition")
        object.__setattr__(self, "measurement_group_id", computed)


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
