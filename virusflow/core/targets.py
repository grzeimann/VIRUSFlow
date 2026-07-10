from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Type, Iterable

from .identity import ZipCode


_DATE_FMT = "%Y%m%d"


def _norm_date(s: str) -> str:
    # Accept YYYYMMDD or ISO-like YYYYMMDDTHHMMSS, normalize to YYYYMMDD
    s = str(s)
    if "T" in s:
        s = s.split("T", 1)[0]
    # Basic validation
    try:
        datetime.strptime(s, _DATE_FMT)
    except Exception as e:
        raise ValueError(f"Invalid date '{s}', expected YYYYMMDD") from e
    return s


@dataclass(frozen=True)
class BiasTarget:
    zipcode: ZipCode
    start_date: str  # YYYYMMDD (UTC day)
    end_date: str    # YYYYMMDD (UTC day)

    def __post_init__(self):  # type: ignore[override]
        # validate/normalize date strings
        object.__setattr__(self, "start_date", _norm_date(self.start_date))
        object.__setattr__(self, "end_date", _norm_date(self.end_date))

    def node_id(self) -> str:
        return f"bias:{self.zipcode.key()}:{self.start_date}:{self.end_date}"

    def to_dict(self) -> Dict:
        return {
            "type": "bias",
            "zipcode": {
                "ifuslot": self.zipcode.ifuslot,
                "ifuid": self.zipcode.ifuid,
                "specid": self.zipcode.specid,
                "amp": self.zipcode.amp,
                "controller": self.zipcode.controller,
            },
            "start_date": self.start_date,
            "end_date": self.end_date,
        }


@dataclass(frozen=True)
class DarkTarget:
    zipcode: ZipCode
    start_date: str  # YYYYMMDD (UTC day)
    end_date: str    # YYYYMMDD (UTC day)

    def __post_init__(self):  # type: ignore[override]
        object.__setattr__(self, "start_date", _norm_date(self.start_date))
        object.__setattr__(self, "end_date", _norm_date(self.end_date))

    def node_id(self) -> str:
        return f"dark:{self.zipcode.key()}:{self.start_date}:{self.end_date}"

    def to_dict(self) -> Dict:
        return {
            "type": "dark",
            "zipcode": {
                "ifuslot": self.zipcode.ifuslot,
                "ifuid": self.zipcode.ifuid,
                "specid": self.zipcode.specid,
                "amp": self.zipcode.amp,
                "controller": self.zipcode.controller,
            },
            "start_date": self.start_date,
            "end_date": self.end_date,
        }


@dataclass(frozen=True)
class CalibrationNeed:
    name: str            # task name, e.g., 'bias', 'dark'
    frame_type: str      # raw frame_type required, e.g., 'zro', 'drk'
    target_cls: Type[BiasTarget]  # constructor for target


def default_calibration_needs(include_bias: bool = True, include_dark: bool = True) -> List[CalibrationNeed]:
    needs: List[CalibrationNeed] = []
    if include_bias:
        needs.append(CalibrationNeed(name="bias", frame_type="zro", target_cls=BiasTarget))
    if include_dark:
        needs.append(CalibrationNeed(name="dark", frame_type="drk", target_cls=DarkTarget))
    return needs


def build_tasks_for_zipcode(
    zipcode: ZipCode,
    start_date: str,
    end_date: str,
    needs: Iterable[CalibrationNeed],
    versions: Optional[Dict[str, Optional[str]]] = None,
) -> List[Dict]:
    """Build plan task dicts for a single zipcode and a set of needs."""
    versions = versions or {}
    tasks: List[Dict] = []
    for need in needs:
        tgt = need.target_cls(zipcode, start_date, end_date)
        node_id = tgt.node_id()
        tasks.append({
            "id": node_id,
            "name": need.name,
            "version": versions.get(need.name),
            "target": tgt.to_dict(),
        })
    return tasks


def build_calibration_tasks(
    zipcodes: Iterable[ZipCode],
    start_date: str,
    end_date: str,
    needs: Iterable[CalibrationNeed],
    versions: Optional[Dict[str, Optional[str]]] = None,
) -> List[Dict]:
    """Build plan task dicts for a set of zipcodes and needs in zipcode-major order."""
    tasks: List[Dict] = []
    for zc in zipcodes:
        tasks.extend(build_tasks_for_zipcode(zc, start_date, end_date, needs, versions=versions))
    return tasks
