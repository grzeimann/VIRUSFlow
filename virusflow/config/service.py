from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np

from ..artifacts.models import ConfigurationReference
from ..core.identity import ZipCode
from .defaults import (
    CCD_TRANSFORM_CONFIGURATION,
    GAIN_FALLBACK_CONFIGURATION,
    ORIENTATION_CONFIGURATION,
    READ_NOISE_FALLBACK_CONFIGURATION,
)


class ConfigurationService:
    """Resolve versioned configuration without embedding file access in algorithms."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path.cwd()

    def amplifier_references(self, zipcode: ZipCode) -> List[ConfigurationReference]:
        identity = zipcode.key()
        configs = (
            ORIENTATION_CONFIGURATION,
            CCD_TRANSFORM_CONFIGURATION,
            GAIN_FALLBACK_CONFIGURATION,
            READ_NOISE_FALLBACK_CONFIGURATION,
        )
        return [
            ConfigurationReference(c.kind, c.version, identity=identity, evidence_state=c.evidence_state)
            for c in configs
        ]

    def resolve_trace_reference(self, *, zipcode: ZipCode, at: str | datetime) -> Tuple[np.ndarray, ConfigurationReference]:
        date_text = at.strftime("%Y%m%d") if isinstance(at, datetime) else str(at)[:8]
        target_date = datetime.strptime(date_text, "%Y%m%d")
        pattern = (
            self.root
            / "Fiber_Locations"
            / "*"
            / f"fiber_loc_{zipcode.specid.zfill(3)}_{zipcode.ifuslot.zfill(3)}_{zipcode.ifuid.zfill(3)}_{zipcode.amp}.txt"
        )
        candidates = sorted(pattern.parent.parent.glob(f"*/{pattern.name}"))
        if not candidates:
            raise FileNotFoundError(f"No trace reference for {zipcode.key()} under {self.root / 'Fiber_Locations'}")

        def distance(path: Path) -> float:
            try:
                return abs((target_date - datetime.strptime(path.parent.name, "%Y%m%d")).days)
            except ValueError:
                return float("inf")

        selected = min(candidates, key=distance)
        data = np.asarray(np.loadtxt(selected), dtype=float)
        ref = ConfigurationReference(
            kind="trace_reference",
            version=selected.parent.name,
            identity=zipcode.key(),
            evidence_state="verified",
        )
        return data, ref

