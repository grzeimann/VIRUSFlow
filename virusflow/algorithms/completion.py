from __future__ import annotations

"""Exposure-wide amplifier coverage and completion status."""

import numpy as np

from ..core.algo_result import AlgoResult


COMPLETION_VERSION = "amplifier-coverage-1.0"


def build_completion_coverage(zipcodes, calibration, reduced, amp_results, failures, wavelength_fiber_exclusions, amp_code) -> AlgoResult:
    """Tabulate per-amplifier processing coverage and derive completion status."""

    coverage = []
    identities = []
    for zipcode in zipcodes:
        kinds = calibration.get(zipcode.key(), {})
        coverage.append([
            int(zipcode.key() in reduced), int("trace_map" in kinds), int("wavelength_map" in kinds),
            int(zipcode.key() in amp_results), int(zipcode.key() not in failures),
        ])
        identities.append([int(zipcode.ifuslot), int(zipcode.specid), amp_code[zipcode.amp]])
    coverage_array = np.asarray(coverage, dtype=np.uint8)
    identity_array = np.asarray(identities, dtype=np.int32)
    complete = not failures and not wavelength_fiber_exclusions
    ordered_keys = sorted(amp_results)
    return AlgoResult(
        kind="exposure_completion_coverage",
        version=COMPLETION_VERSION,
        arrays={"coverage": coverage_array, "amplifier_identity": identity_array},
        scalars={
            "status": "pass" if complete else "warn",
            "usability": "usable" if complete else "degraded",
            "raw_amplifier_count": len(zipcodes),
            "reduced_amplifier_count": int(coverage_array[:, 0].sum()),
            "extracted_amplifier_count": int(coverage_array[:, 3].sum()),
            "ifuslot_count": len({zipcode.ifuslot for zipcode in zipcodes}),
            "extracted_ifuslot_count": len({amp_results[key]["zipcode"].ifuslot for key in ordered_keys}),
            "failed_or_missing_amplifier_count": len(failures),
        },
    )
