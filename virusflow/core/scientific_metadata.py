from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional


SCIENTIFIC_METADATA_FIELDS = (
    "observation_time",
    "airmass",
    "ambient_temperature",
    "humidity",
    "pressure",
    "program_id",
    "object",
    "rho_start",
    "theta_start",
    "phi_start",
    "x_start",
    "y_start",
)

TRACKER_FIELDS = (
    "rho_start",
    "theta_start",
    "phi_start",
    "x_start",
    "y_start",
)

_HEADER_KEYWORDS = {
    "observation_time": ("DATE",),
    "airmass": ("AIRMASS",),
    "ambient_temperature": ("AMBTEMP", "AMBIENT", "TAMBIENT", "TEMPAMB", "OUTTEMP"),
    "humidity": ("HUMIDITY",),
    "pressure": ("PRESSURE",),
    "program_id": ("QPROG",),
    "object": ("OBJECT",),
    "rho_start": ("RHO_STRT",),
    "theta_start": ("THE_STRT",),
    "phi_start": ("PHI_STRT",),
    "x_start": ("X_STRT",),
    "y_start": ("Y_STRT",),
}

_FLOAT_FIELDS = {
    "airmass",
    "ambient_temperature",
    "humidity",
    "pressure",
    *TRACKER_FIELDS,
}
_TEXT_FIELDS = {"program_id", "object"}


def _first_header_value(header: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = header.get(key)
        if value is not None:
            return value
    return None


def _finite_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    result = str(value).strip()
    if not result or result.lower() in {"nan", "none", "null"}:
        return None
    return result


def _datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def normalize_scientific_metadata(values: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Return only canonical scientific-state fields with stable Python values."""

    source = values or {}
    normalized: dict[str, Any] = {}
    for field in SCIENTIFIC_METADATA_FIELDS:
        value = source.get(field)
        if field == "observation_time":
            normalized[field] = _datetime(value)
        elif field in _FLOAT_FIELDS:
            normalized[field] = _finite_float(value)
        elif field in _TEXT_FIELDS:
            normalized[field] = _text(value)
    return normalized


def scientific_metadata_from_header(header: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the compact scientific state from demonstrated raw FITS fields."""

    return normalize_scientific_metadata({
        field: _first_header_value(header, keywords)
        for field, keywords in _HEADER_KEYWORDS.items()
    })


def aggregate_scientific_metadata(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize selected raw/evidence records for a calibration composite."""

    normalized = [normalize_scientific_metadata(record) for record in records]
    result = normalize_scientific_metadata({})

    instants = [
        record["observation_time"]
        for record in normalized
        if record["observation_time"] is not None
    ]
    if instants:
        epoch = datetime(1970, 1, 1)
        mean_seconds = sum((value - epoch).total_seconds() for value in instants) / len(instants)
        result["observation_time"] = datetime.fromtimestamp(
            mean_seconds, tz=timezone.utc
        ).replace(
            tzinfo=None
        )

    for field in ("ambient_temperature", "humidity", "pressure"):
        finite = [record[field] for record in normalized if record[field] is not None]
        result[field] = sum(finite) / len(finite) if finite else None

    for field in ("program_id", "object"):
        values = {record[field] for record in normalized if record[field] is not None}
        result[field] = next(iter(values)) if len(values) == 1 else None

    # A representative or averaged exposure airmass or tracker position is not
    # meaningful for multi-frame base calibration composites.
    result["airmass"] = None
    for field in TRACKER_FIELDS:
        result[field] = None
    return result


def scientific_metadata_for_database(values: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = normalize_scientific_metadata(values)
    observation_time = normalized["observation_time"]
    normalized["observation_time"] = (
        observation_time.isoformat(timespec="microseconds")
        if observation_time is not None
        else None
    )
    return normalized
