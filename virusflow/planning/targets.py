from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from ..artifacts.models import Scope


@dataclass(frozen=True)
class TemporalWindow:
    """Half-open time window [start, end) in UTC.

    For point-in-time planning (e.g., science exposures), window may be None
    and the at_time on Target can be used instead.
    """
    start: Optional[datetime]
    end: Optional[datetime]


@dataclass(frozen=True)
class Target:
    """Declarative target describing an artifact to produce.

    - kind: artifact kind (e.g., "master_bias", "trace", "science_2d").
    - scope: Scope context (typically per-zipcode for calibrations).
    - window: optional validity/aggregation window for calibrations.
    - at_time: optional point-in-time for science reductions.
    """
    kind: str
    scope: Scope
    window: Optional[TemporalWindow] = None
    at_time: Optional[datetime] = None


class CadencePolicy:
    """Protocol for cadence policies used by calibration nodes.

    Implementors may override windows(). The planner will also detect known
    policy types (TimeCadence/ExposureCountCadence) and delegate to helper
    functions in planning.cadence if windows() raises NotImplementedError.
    """

    def windows(self, *, frame_type: str, scope: Scope, db_path: str) -> List[TemporalWindow]:
        raise NotImplementedError


class TimeCadence(CadencePolicy):
    """Periodic time cadence policy.

    every_days: length of each cadence period.
    min_n_inputs: optional guard requiring at least this many raw inputs in a
                  window to emit a target (enforced by helpers).
    """

    def __init__(self, every_days: int, min_n_inputs: int = 1) -> None:
        self.every_days = int(every_days)
        self.min_n_inputs = int(min_n_inputs)

    # Defer to helper by default
    def windows(self, *, frame_type: str, scope: Scope, db_path: str) -> List[TemporalWindow]:  # noqa: ARG002
        raise NotImplementedError


class ExposureCountCadence(CadencePolicy):
    """Rolling exposure-count cadence policy.

    min_n: minimum number of inputs to emit a window.
    max_span_days: cut the window if it exceeds this span even if min_n not met.
    """

    def __init__(self, *, min_n: int, max_span_days: int) -> None:
        self.min_n = int(min_n)
        self.max_span_days = int(max_span_days)

    # Defer to helper by default
    def windows(self, *, frame_type: str, scope: Scope, db_path: str) -> List[TemporalWindow]:  # noqa: ARG002
        raise NotImplementedError
