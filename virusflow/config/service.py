from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import hashlib
from pathlib import Path
from typing import List, Tuple

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


@lru_cache(maxsize=None)
def _load_fiber_position_table(
    path: Path,
    usecols: tuple[int, ...],
    skiprows: int,
    expected_shape: tuple[int, int],
) -> np.ndarray:
    """Load and retain an immutable authoritative fiber-position table."""

    if not path.is_file():
        raise FileNotFoundError(f"VIRUS fiber-position configuration not found: {path}")
    table = np.asarray(
        np.loadtxt(path, usecols=usecols, skiprows=skiprows),
        dtype=float,
    )
    if table.shape != expected_shape:
        raise ValueError(
            f"VIRUS fiber-position configuration {path} has shape {table.shape}; "
            f"expected {expected_shape}"
        )
    table.setflags(write=False)
    return table


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

    def _corrected_fiber_position_table(self, ifuid: str) -> tuple[np.ndarray, str]:
        config = FIBER_GEOMETRY_CONFIGURATION
        values = config.value
        ifuid_text = str(ifuid).strip()
        if not ifuid_text.isdigit() or len(ifuid_text) > 3:
            raise ValueError(f"Invalid VIRUS IFUID {ifuid!r}; expected one to three digits")
        normalized_ifuid = ifuid_text.zfill(3)
        source = values["alternate_sources"].get(normalized_ifuid, values["default_source"])
        path = self.root / values["directory"] / source
        base = _load_fiber_position_table(
            path,
            tuple(values["load_usecols"]),
            int(values["skiprows"]),
            tuple(values["table_shape"]),
        )

        # Every IFU-specific operation is made on a working copy. The cached
        # source table remains immutable and can safely serve later IFUs.
        table = base.copy()
        reversal = values["right_side_reversals"].get(normalized_ifuid)
        if reversal is not None:
            start, stop = map(int, reversal)
            if not 0 <= start < stop <= table.shape[0]:
                raise ValueError(
                    f"Invalid fiber-position reversal {reversal} for IFU {normalized_ifuid}"
                )
            table[start:stop] = table[start:stop][::-1].copy()

        coordinate_start, coordinate_stop = map(int, values["coordinate_columns"])
        for first, second in values["coordinate_swaps"].get(normalized_ifuid, ()):
            if not 0 <= int(first) < table.shape[0] or not 0 <= int(second) < table.shape[0]:
                raise ValueError(
                    f"Invalid fiber-position coordinate swap {(first, second)} "
                    f"for IFU {normalized_ifuid}"
                )
            coordinates = table[[int(first), int(second)], coordinate_start:coordinate_stop].copy()
            table[int(first), coordinate_start:coordinate_stop] = coordinates[1]
            table[int(second), coordinate_start:coordinate_stop] = coordinates[0]
        return table, normalized_ifuid

    @staticmethod
    def _fiber_geometry_reference(ifuid: str) -> ConfigurationReference:
        config = FIBER_GEOMETRY_CONFIGURATION
        return ConfigurationReference(
            config.kind,
            config.version,
            identity=f"IFUID:{ifuid}",
            evidence_state=config.evidence_state,
        )

    def fiber_positions(
        self, ifuid: str, amplifier: str
    ) -> Tuple[np.ndarray, ConfigurationReference]:
        """Return calibrated IFU coordinates in extracted-spectrum order."""

        values = FIBER_GEOMETRY_CONFIGURATION.value
        amp = str(amplifier).upper()
        if amp not in values["amplifier_slices"]:
            recognized = ", ".join(values["amplifier_slices"])
            raise ValueError(f"Unknown VIRUS amplifier {amplifier!r}; expected one of {recognized}")
        table, normalized_ifuid = self._corrected_fiber_position_table(ifuid)
        start, stop = map(int, values["amplifier_slices"][amp])
        if not 0 <= start < stop <= table.shape[0]:
            raise ValueError(f"Invalid fiber-position slice {(start, stop)} for amplifier {amp}")
        coordinate_start, coordinate_stop = map(int, values["coordinate_columns"])
        positions = table[start:stop, coordinate_start:coordinate_stop]
        if values["reverse_for_extracted_spectrum_order"]:
            positions = positions[::-1]
        positions = positions.copy()
        if positions.shape != (112, 2):
            raise ValueError(
                f"Fiber positions for IFU {normalized_ifuid} amplifier {amp} "
                f"have shape {positions.shape}; expected (112, 2)"
            )
        return positions, self._fiber_geometry_reference(normalized_ifuid)

    def fiber_offsets(
        self, ifuid: str
    ) -> Tuple[dict[str, np.ndarray], ConfigurationReference]:
        """Return calibrated positions for every amplifier of one VIRUS IFU."""

        table, normalized_ifuid = self._corrected_fiber_position_table(ifuid)
        values = FIBER_GEOMETRY_CONFIGURATION.value
        coordinate_start, coordinate_stop = map(int, values["coordinate_columns"])
        by_amp: dict[str, np.ndarray] = {}
        for amp, bounds in values["amplifier_slices"].items():
            start, stop = map(int, bounds)
            if not 0 <= start < stop <= table.shape[0]:
                raise ValueError(f"Invalid fiber-position slice {bounds} for amplifier {amp}")
            positions = table[start:stop, coordinate_start:coordinate_stop]
            if values["reverse_for_extracted_spectrum_order"]:
                positions = positions[::-1]
            by_amp[str(amp)] = positions.copy()
        unexpected = {amp: value.shape for amp, value in by_amp.items() if value.shape != (112, 2)}
        if unexpected:
            raise ValueError(f"Unexpected VIRUS amplifier fiber-position shapes: {unexpected}")
        return by_amp, self._fiber_geometry_reference(normalized_ifuid)

    def exposure_references(self) -> List[ConfigurationReference]:
        return [
            ConfigurationReference(
                config.kind, config.version, identity="exposure-baseline", evidence_state=config.evidence_state
            )
            for config in (ASTROMETRY_CONFIGURATION, BASELINE_RESPONSE_CONFIGURATION)
        ]
