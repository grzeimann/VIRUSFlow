from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from virusflow.config import ConfigurationService


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IFUIDS = ("001", "003", "004", "005", "007", "008", "025", "030", "038", "041")
AMPLIFIER_SLICES = {
    "LU": (0, 112),
    "LL": (112, 224),
    "RL": (224, 336),
    "RU": (336, 448),
}


def _archival_fiber_positions(ifuid: str, amplifier: str) -> np.ndarray:
    """Independent transcription of the archived get_ifucenfile behavior."""

    filename = "IFUcen_HETDEX_reverse_R.txt" if ifuid == "004" else "IFUcen_HETDEX.txt"
    table = np.loadtxt(
        REPOSITORY_ROOT / "IFUcen_files" / filename,
        usecols=[0, 1, 2, 4],
        skiprows=30,
    )
    if ifuid in {"003", "004", "005", "008"}:
        table[224:] = table[-1:223:-1]
    for exceptional_ifuid, first, second in (
        ("007", 37, 38),
        ("025", 208, 213),
        ("030", 445, 446),
        ("038", 302, 303),
        ("041", 251, 252),
    ):
        if ifuid == exceptional_ifuid:
            table[[first, second], 1:3] = table[[second, first], 1:3]
    start, stop = AMPLIFIER_SLICES[amplifier]
    return table[start:stop, 1:3][::-1]


@pytest.mark.parametrize("ifuid", IFUIDS)
@pytest.mark.parametrize("amplifier", tuple(AMPLIFIER_SLICES))
def test_calibrated_fiber_positions_match_archival_behavior(ifuid: str, amplifier: str):
    service = ConfigurationService(REPOSITORY_ROOT)

    actual, reference = service.fiber_positions(ifuid, amplifier)

    np.testing.assert_array_equal(actual, _archival_fiber_positions(ifuid, amplifier))
    assert actual.shape == (112, 2)
    assert reference.kind == "ifu_fiber_geometry"
    assert reference.version == "archival-ifucen-r3-1"
    assert reference.identity == f"IFUID:{ifuid}"
    assert reference.evidence_state == "verified"


def test_fiber_offsets_preserve_amplifier_order_and_do_not_expose_cached_data():
    service = ConfigurationService(REPOSITORY_ROOT)
    expected_standard, _ = service.fiber_offsets("001")

    corrected, _ = service.fiber_offsets("003")
    corrected["RL"][:] = np.nan
    repeated_standard, _ = service.fiber_offsets("001")

    assert tuple(repeated_standard) == ("LU", "LL", "RL", "RU")
    for amplifier in AMPLIFIER_SLICES:
        np.testing.assert_array_equal(repeated_standard[amplifier], expected_standard[amplifier])
        np.testing.assert_array_equal(
            repeated_standard[amplifier],
            _archival_fiber_positions("001", amplifier),
        )


def test_fiber_position_configuration_fails_clearly_for_invalid_input(tmp_path: Path):
    service = ConfigurationService(REPOSITORY_ROOT)
    with pytest.raises(ValueError, match="Unknown VIRUS amplifier 'XX'"):
        service.fiber_positions("001", "XX")
    with pytest.raises(ValueError, match="Invalid VIRUS IFUID"):
        service.fiber_offsets("")

    with pytest.raises(FileNotFoundError, match="VIRUS fiber-position configuration not found"):
        ConfigurationService(tmp_path).fiber_offsets("001")

    source_directory = tmp_path / "IFUcen_files"
    source_directory.mkdir()
    (source_directory / "IFUcen_HETDEX.txt").write_text("0 0 0 X 0\n" * 31)
    with pytest.raises(ValueError, match=r"has shape .* expected \(448, 4\)"):
        ConfigurationService(tmp_path).fiber_offsets("001")
