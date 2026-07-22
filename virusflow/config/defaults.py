from __future__ import annotations

from .models import DitherPolicy, EffectiveExposurePolicy, VersionedConfiguration
from ..ontology.coordinates import UPPER_AMPLIFIER_REFLECTION_INDEX


ORIENTATION_CONFIGURATION = VersionedConfiguration(
    kind="amplifier_orientation",
    version="legacy-characterized-1",
    value={"double_flip": ["LU", "RL"], "legacy_ampname_x_flip": ["LR", "UL"]},
    evidence_state="verified",
    source="virusflow.algorithms.ccd.orient_amplifier_image",
)

CCD_TRANSFORM_CONFIGURATION = VersionedConfiguration(
    kind="ccd_transform",
    version="indexed-1",
    value={"upper_reflection_index": UPPER_AMPLIFIER_REFLECTION_INDEX},
    evidence_state="verified",
    source="approved migration decision",
)

GAIN_FALLBACK_CONFIGURATION = VersionedConfiguration(
    kind="gain_fallback",
    version="legacy-unknown-1",
    value=0.85,
    evidence_state="unknown",
    source="virusflow.algorithms.ccd.reduce_raw_amplifier_frame",
)

READ_NOISE_FALLBACK_CONFIGURATION = VersionedConfiguration(
    kind="read_noise_fallback",
    version="legacy-unknown-1",
    value=3.0,
    evidence_state="unknown",
    source="virusflow.algorithms.ccd.reduce_raw_amplifier_frame",
)

EFFECTIVE_EXPOSURE_POLICY = EffectiveExposurePolicy()
DITHER_POLICY = DitherPolicy()

