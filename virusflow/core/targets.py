from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict

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
