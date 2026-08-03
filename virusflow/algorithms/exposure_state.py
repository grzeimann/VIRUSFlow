from __future__ import annotations

"""Exposure-level observing mode classification and effective exposure time."""

from typing import Mapping

from ..core.algo_result import AlgoResult
from ..core.exposure_metadata import interpret_virus_exposure_header


def classify_mode_and_effective_time(header: Mapping, *, parallel_offset_seconds: float = 8.0) -> AlgoResult:
    context = interpret_virus_exposure_header(header, frame_type="sci")
    mode = context.observing_mode
    exptime = float(header["EXPTIME"]) if header.get("EXPTIME") is not None else None
    pexptime = float(header["PEXPTIME"]) if header.get("PEXPTIME") is not None else None
    if mode == "primary":
        effective = exptime
        source = "EXPTIME"
    else:
        effective = None if pexptime is None else max(0.0, pexptime - float(parallel_offset_seconds))
        source = "PEXPTIME_minus_offset"
    time_evidence = {
        "EXPTIME": exptime,
        "PEXPTIME": pexptime,
        "OBJECT": context.virus_object,
        "QOBJECT": context.qobject,
        "QRA": context.qra,
        "QDEC": context.qdec,
        "QPROG": context.qprog,
        "requested_target": context.requested_target,
        "requested_target_source": context.requested_target_source,
        "requested_ifuslot": context.requested_ifuslot,
        "het_track": context.het_track,
        "virus_primary": context.virus_primary,
        "q_metadata_expected": context.q_metadata_expected,
        "q_metadata_complete": context.q_metadata_complete,
        "object_qobject_consistent": context.object_qobject_consistent,
        "source": source,
    }
    return AlgoResult(
        kind="exposure_mode_classification",
        meta={"time_evidence": time_evidence},
        scalars={"mode": mode, "effective_seconds": effective},
    )
