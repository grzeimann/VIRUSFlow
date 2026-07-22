from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
from typing import Iterable, List, Optional, Tuple

import numpy as np

from ..artifacts.models import ConfigurationReference
from ..core.identity import ZipCode
from .defaults import (
    CCD_TRANSFORM_CONFIGURATION,
    GAIN_FALLBACK_CONFIGURATION,
    ORIENTATION_CONFIGURATION,
    READ_NOISE_FALLBACK_CONFIGURATION,
    ASTROMETRY_CONFIGURATION,
    BASELINE_RESPONSE_CONFIGURATION,
    FIBER_GEOMETRY_CONFIGURATION,
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

    def resolve_fplane(self, path: str | Path | None = None) -> Tuple[dict[str, tuple[float, float]], ConfigurationReference]:
        """Load versioned IFUSLOT focal-plane offsets at the configuration boundary."""

        selected = Path(path) if path is not None else self.root / "fplaneall.txt"
        if not selected.is_file():
            raise FileNotFoundError(f"F-plane configuration not found: {selected}")
        digest = hashlib.sha256(selected.read_bytes()).hexdigest()
        offsets: dict[str, tuple[float, float]] = {}
        for line in selected.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) < 3:
                continue
            offsets[str(fields[0]).zfill(3)] = (float(fields[1]), float(fields[2]))
        if "000" not in offsets or len(offsets) < 70:
            raise ValueError(f"F-plane configuration is incomplete: {selected}")
        return offsets, ConfigurationReference(
            kind="fplane_geometry", version=f"sha256:{digest[:16]}",
            identity=str(selected.name), evidence_state="verified",
        )

    def fiber_offsets(self) -> Tuple[dict[str, np.ndarray], ConfigurationReference]:
        """Return the explicit versioned baseline 448-fiber local IFU geometry."""

        config = FIBER_GEOMETRY_CONFIGURATION
        columns = int(config.value["columns"])
        rows = int(config.value["rows"])
        separation = float(config.value["fiber_separation_arcsec"])
        points = []
        for column in range(columns):
            x = (column - (columns - 1) / 2.0) * separation
            for row in range(rows):
                y = (row - (rows - 1) / 2.0) * separation * np.sqrt(3.0) / 2.0
                y += (column % 2) * separation * np.sqrt(3.0) / 4.0
                points.append((x, y))
        geometry = np.asarray(points, dtype=float)
        remove = int(config.value["remove_outermost"])
        keep = np.argsort(np.sum(np.square(geometry), axis=1))[: geometry.shape[0] - remove]
        geometry = geometry[np.sort(keep)]
        if geometry.shape != (448, 2):
            raise RuntimeError(f"configured fiber geometry has unexpected shape {geometry.shape}")
        by_amp = {}
        for index, amp in enumerate(config.value["amplifier_order"]):
            by_amp[str(amp)] = geometry[index * 112 : (index + 1) * 112].copy()
        return by_amp, ConfigurationReference(
            config.kind, config.version, identity="VIRUS-448", evidence_state=config.evidence_state
        )

    def exposure_references(self) -> List[ConfigurationReference]:
        return [
            ConfigurationReference(
                config.kind, config.version, identity="exposure-baseline", evidence_state=config.evidence_state
            )
            for config in (ASTROMETRY_CONFIGURATION, FIBER_GEOMETRY_CONFIGURATION, BASELINE_RESPONSE_CONFIGURATION)
        ]
