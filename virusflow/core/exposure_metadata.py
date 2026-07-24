from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional


def _header_value(header: Mapping[str, Any], name: str) -> Any:
    value = header.get(name)
    return None if value in (None, "") else value


@dataclass(frozen=True)
class VIRUSExposureMetadata:
    """Raw and interpreted VIRUS exposure-header semantics."""

    frame_class: str
    virus_object: Optional[Any]
    qobject: Optional[Any]
    qra: Optional[Any]
    qdec: Optional[Any]
    qprog: Optional[Any]
    requested_target: Optional[str]
    requested_target_source: Optional[str]
    requested_ifuslot: Optional[str]
    het_track: Optional[str]
    observing_mode: str
    virus_primary: Optional[bool]
    q_metadata_expected: bool
    q_metadata_complete: Optional[bool]
    object_qobject_consistent: Optional[bool]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def interpret_virus_exposure_header(
    header: Mapping[str, Any], *, frame_type: str
) -> VIRUSExposureMetadata:
    """Separate requested-target metadata from VIRUS operational context.

    ``QOBJECT/QRA/QDEC/QPROG`` describe the science-database request. ``OBJECT``
    describes the VIRUS exposure itself and determines primary/parallel mode.
    A primary science ``OBJECT`` is parsed from the right so underscores in the
    requested target name remain unambiguous.
    """

    frame_class = "science" if str(frame_type).strip().lower() == "sci" else "calibration"
    virus_object = _header_value(header, "OBJECT")
    qobject = _header_value(header, "QOBJECT")
    qra = _header_value(header, "QRA")
    qdec = _header_value(header, "QDEC")
    qprog = _header_value(header, "QPROG")

    object_text = "" if virus_object is None else str(virus_object).strip()
    qobject_text = None if qobject is None else str(qobject).strip()
    requested_ifuslot = None
    het_track = None
    object_target = None
    if frame_class == "science" and object_text.casefold() != "parallel":
        parts = object_text.rsplit("_", 2)
        if (
            len(parts) == 3
            and parts[0]
            and len(parts[1]) == 3
            and parts[1].isdigit()
            and parts[2].upper() in {"E", "W"}
        ):
            object_target, requested_ifuslot, het_track = parts[0], parts[1], parts[2].upper()

    if qobject_text:
        requested_target = qobject_text
        requested_target_source = "QOBJECT"
    elif object_target:
        requested_target = object_target
        requested_target_source = "OBJECT_prefix"
    else:
        requested_target = None
        requested_target_source = None

    if frame_class == "calibration":
        observing_mode = "calibration"
        virus_primary = None
    elif object_text.casefold() == "parallel":
        observing_mode = "parallel"
        virus_primary = False
    else:
        observing_mode = "primary"
        virus_primary = True

    q_metadata_expected = frame_class == "science"
    q_metadata_complete = (
        all(value is not None for value in (qobject, qra, qdec, qprog))
        if q_metadata_expected
        else None
    )
    object_qobject_consistent = (
        object_target == qobject_text
        if object_target is not None and qobject_text is not None
        else None
    )
    return VIRUSExposureMetadata(
        frame_class=frame_class,
        virus_object=virus_object,
        qobject=qobject,
        qra=qra,
        qdec=qdec,
        qprog=qprog,
        requested_target=requested_target,
        requested_target_source=requested_target_source,
        requested_ifuslot=requested_ifuslot,
        het_track=het_track,
        observing_mode=observing_mode,
        virus_primary=virus_primary,
        q_metadata_expected=q_metadata_expected,
        q_metadata_complete=q_metadata_complete,
        object_qobject_consistent=object_qobject_consistent,
    )
