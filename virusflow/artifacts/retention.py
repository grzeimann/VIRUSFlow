"""Narrow retention policy for rebuildable calibration payloads."""

from __future__ import annotations

from dataclasses import dataclass

from ..ontology.artifact_kinds import canonical_kind


@dataclass(frozen=True)
class PayloadRetentionRule:
    """Payload components that may be evicted after evidence is validated."""

    evictable_components: tuple[str, ...]
    required_descendant_kinds: tuple[str, ...]
    eviction_trigger_kind: str


# Kinds not listed here are permanently retained.  Requirements describe the
# smallest validated Product chain currently published by the calibration DAG.
CACHEABLE_CALIBRATION_PAYLOADS: dict[str, PayloadRetentionRule] = {
    "master_hg": PayloadRetentionRule(
        ("master_hg",), ("master_arc", "wavelength_map"), "wavelength_map"
    ),
    "master_cd": PayloadRetentionRule(
        ("master_cd",), ("master_arc", "wavelength_map"), "wavelength_map"
    ),
    "master_arc": PayloadRetentionRule(
        ("master_arc",), ("wavelength_map",), "wavelength_map"
    ),
    "master_twilight": PayloadRetentionRule(
        ("master_twilight",),
        (
            "ccd_scattered_light_model",
            "extracted_master_twilight_spectrum",
            "exposure_fiber_response",
        ),
        "exposure_fiber_response",
    ),
    "master_ldls": PayloadRetentionRule(
        ("master_ldls",),
        (
            "trace_map",
            "ccd_scattered_light_model",
            "extracted_master_ldls_spectrum",
            "exposure_fiber_response",
        ),
        "exposure_fiber_response",
    ),
    "master_sci": PayloadRetentionRule(
        ("master_sci",),
        (
            "ccd_scattered_light_model",
            "extracted_master_sci_spectrum",
            "fiber_wavelength_spectral_mask",
        ),
        "fiber_wavelength_spectral_mask",
    ),
}


def retention_rule(kind: str) -> PayloadRetentionRule | None:
    """Return the cacheable-payload rule; absence means always retain."""

    return CACHEABLE_CALIBRATION_PAYLOADS.get(canonical_kind(kind))


def is_eviction_trigger(kind: str) -> bool:
    """Return whether publishing this kind can release an ancestor payload."""

    trigger = canonical_kind(kind)
    return any(
        canonical_kind(rule.eviction_trigger_kind) == trigger
        for rule in CACHEABLE_CALIBRATION_PAYLOADS.values()
    )


def eviction_candidate_kinds(trigger_kind: str) -> tuple[str, ...]:
    """Return only payload kinds releasable by this terminal Product kind."""

    trigger = canonical_kind(trigger_kind)
    return tuple(
        kind for kind, rule in CACHEABLE_CALIBRATION_PAYLOADS.items()
        if canonical_kind(rule.eviction_trigger_kind) == trigger
    )


__all__ = [
    "CACHEABLE_CALIBRATION_PAYLOADS",
    "PayloadRetentionRule",
    "eviction_candidate_kinds",
    "is_eviction_trigger",
    "retention_rule",
]
