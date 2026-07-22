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

FIBER_GEOMETRY_CONFIGURATION = VersionedConfiguration(
    kind="ifu_fiber_geometry",
    version="documented-20x23-448-baseline-1",
    value={
        "columns": 20,
        "rows": 23,
        "fiber_separation_arcsec": 2.2,
        "remove_outermost": 12,
        "amplifier_order": ["LL", "LU", "RU", "RL"],
    },
    evidence_state="provisional",
    source="VIRUS 448-fiber IFU geometry documented by pyhetdex IFUCenter contract",
)

ASTROMETRY_CONFIGURATION = VersionedConfiguration(
    kind="astrometry_projection",
    version="header-tan-reference-1",
    value={"scale_arcsec": 1.0, "x_scale": -1.0, "y_scale": 1.0, "system_rotation_deg": 1.55, "axis_swap": True},
    evidence_state="verified",
    source="astrometry.py reference and astrometry knowledge note",
)

BASELINE_RESPONSE_CONFIGURATION = VersionedConfiguration(
    kind="baseline_relative_response",
    version="unity-reference-1",
    value={"model": "unity", "normalization": "relative"},
    evidence_state="provisional",
    source="No historical response curve was supplied; explicit identity baseline",
)

WAVELENGTH_INPUT_MASK_CONFIGURATION = VersionedConfiguration(
    kind="wavelength_input_mask_policy",
    version="bounded-flat-mask-1",
    value={"maximum_flat_mask_fraction": 0.25, "always_apply_dark_mask": True},
    evidence_state="provisional",
    source="20260609 characterization: reject pathological near-global flat masks while retaining mask evidence",
)
