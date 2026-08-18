from __future__ import annotations

from .models import DitherPolicy, EffectiveExposurePolicy, VersionedConfiguration
from ..ontology.coordinates import UPPER_AMPLIFIER_Y_OFFSET


ORIENTATION_CONFIGURATION = VersionedConfiguration(
    kind="amplifier_orientation",
    version="legacy-characterized-1",
    value={"double_flip": ["LU", "RL"], "legacy_ampname_x_flip": ["LR", "UL"]},
    evidence_state="verified",
    source="virusflow.algorithms.ccd.orient_amplifier_image",
)

CCD_TRANSFORM_CONFIGURATION = VersionedConfiguration(
    kind="ccd_transform",
    version="indexed-2",
    value={"upper_y_offset": UPPER_AMPLIFIER_Y_OFFSET},
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
    version="archival-ifucen-r3-1",
    value={
        "directory": "IFUcen_files",
        "default_source": "IFUcen_HETDEX.txt",
        "alternate_sources": {"004": "IFUcen_HETDEX_reverse_R.txt"},
        "load_usecols": (0, 1, 2, 4),
        "skiprows": 30,
        "table_shape": (448, 4),
        "coordinate_columns": (1, 3),
        "right_side_reversals": {
            "003": (224, 448),
            "004": (224, 448),
            "005": (224, 448),
            "008": (224, 448),
        },
        "coordinate_swaps": {
            "007": ((37, 38),),
            "025": ((208, 213),),
            "030": ((445, 446),),
            "038": ((302, 303),),
            "041": ((251, 252),),
        },
        "amplifier_slices": {
            "LU": (0, 112),
            "LL": (112, 224),
            "RL": (224, 336),
            "RU": (336, 448),
        },
        "reverse_for_extracted_spectrum_order": True,
        "fiber_radius_arcsec": 0.75,
    },
    evidence_state="verified",
    source=(
        "IFUcen_HETDEX.txt, IFUcen_HETDEX_reverse_R.txt, and "
        "VIRUSFlow_Knowledge_Note_Fiber_Positions"
    ),
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
    version="remedy-effective-response-atmosphere-separated-1.0",
    identity="legacy-remedy-effective-response",
    value={
        "file": "data/baseline_relative_response_remedy_1.txt",
        "response_definition": "throughput / normalization",
        "instrument_epoch": "legacy-remedy-reference-epoch-unspecified",
        "derivation_method": {
            "extraction": (
                "Remedy get_spectra npix=5 fractional aperture average "
                "(weighted sum divided by five)"
            ),
            "psf_treatment": (
                "no fitted detector PSF in fiber extraction; "
                "trace-centered fractional aperture"
            ),
            "contribution_correction": (
                "Remedy get_powerlaw gap-sampled smooth scattered-light "
                "subtraction before extraction"
            ),
            "calibration_convention": (
                "Remedy LDLS/twilight/master-science fiber normalization followed by "
                "throughput / normalization effective response"
            ),
            "legacy_code": (
                "grzeimann/Remedy quick_reduction.py blob fe58dc801dee1ebe2f92adfc05aec88cdae70e2a; "
                "fiber_utils.py blob e64b0e0a0d8f5f631b8279b83ed6d2f3db89232b"
            ),
        },
        "application_configuration": {
            "extraction": "fractional-aperture-5px-1",
            "psf_treatment": "fixed top-hat aperture; no fitted PSF/LSF",
            "contribution_correction": (
                "physical-CCD gap-scatter subtraction plus calibration-build fiber normalization"
            ),
            "calibration_convention": (
                "within-amplifier and amp-to-amp factors applied before one baseline division"
            ),
            "algorithm_versions": {
                "extraction": "fractional-sum-aperture-1.0",
                "psf_treatment": "none-fixed-aperture-1",
                "contribution_correction": "physical-ccd-gap-polynomial-1.0",
                "calibration_convention": "relative-response-factorized-3.0",
            },
        },
        "atmospheric_content": "removed_with_model",
        "construction_extinction_model": "mcdonald_extinction.dat",
        "construction_airmass": 1.22,
        "construction_airmass_basis": "HET fixed altitude",
        "source_baseline": "legacy Remedy throughput / normalization",
        "atmospheric_separation": {
            "extinction_model_identity": "mcdonald-observatory-mean-extinction",
            "calibration_exposure_airmasses": [1.22],
            "construction_extinction_model": "mcdonald_extinction.dat",
            "construction_airmass_basis": "HET fixed altitude",
            "source_baseline": "legacy Remedy throughput / normalization",
        },
        "separate_exposure_measurements": (
            "Remedy guider mirror illumination and transparency were exposure-specific "
            "and are not baseline components"
        ),
    },
    evidence_state="provisional",
    source=(
        "Legacy Remedy throughput / normalization with McDonald mean extinction "
        "removed at HET fixed-altitude airmass 1.22"
    ),
)

ATMOSPHERIC_EXTINCTION_CONFIGURATION = VersionedConfiguration(
    kind="atmospheric_extinction_model",
    version="mcdonald-mean-extinction-1.0",
    identity="mcdonald-observatory-mean-extinction",
    value={
        "file": "data/mcdonald_extinction.dat",
        "site": "McDonald Observatory",
        "coefficient_definition": "extinction magnitude per airmass",
        "coefficient_units": "mag / airmass",
        "wavelength_units": "Angstrom",
        "valid_wavelength_min_angstrom": 3400.0,
        "valid_wavelength_max_angstrom": 7000.0,
        "interpolation": "linear within valid range; no extrapolation",
        "uncertainty_state": "not supplied; NaN with mask bit 2",
    },
    evidence_state="provisional",
    source=(
        "Imported mcdonald_extinction.dat SHA256 "
        "d6e41b8bab5185d375371cf70a4288e240527c5890e150e3d93f25e8803c5810; "
        "bibliographic source not supplied"
    ),
)

DAR_SEED_CONFIGURATION = VersionedConfiguration(
    kind="dar_seed_model",
    version="remedy-empirical-dar-seed-1.0",
    identity="remedy-five-point-cubic-dar-seed",
    value={
        "source_wavelength_angstrom": [3500.0, 4000.0, 4500.0, 5000.0, 5500.0],
        "source_displacement_arcsec": [-0.74, -0.40, -0.08, 0.08, 0.20],
        "fit_degree": 3,
        "instrument_angle_convention": (
            "angle measured counterclockwise from instrument +x; "
            "delta_x = cos(angle) * dar_scalar, delta_y = sin(angle) * dar_scalar"
        ),
        "zero_point_convention": (
            "absolute: the cubic curve is evaluated directly at each requested "
            "wavelength with no reference-wavelength subtraction"
        ),
        "angle_deg": 0.0,
        "angle_convention": (
            "fixed instrument-frame convention matching Remedy's own application of "
            "the DAR curve directly along instrument +x with no separate rotation; "
            "this is a documented seed simplification, not a per-exposure parallactic "
            "angle computation. Real per-exposure deviation from this seed is intended "
            "to be absorbed by the fitted chromatic_psf_model centroid residual."
        ),
    },
    evidence_state="provisional",
    source="Remedy extract.py DAR curve + Extract.get_ADR_RAdec",
)

SOURCE_EXTRACTION_CONFIGURATION = VersionedConfiguration(
    kind="point_source_extraction",
    version="wavelength-local-moffat-coupling-1.0",
    identity="production-source-extraction-defaults",
    value={
        "max_fiber_distance_arcsec": 6.0,
        "omitted_coupling_tolerance": 0.05,
        "psf_interval_count": 10,
        "fwhm_bounds_arcsec": [1.0, 4.0],
        "search_radius_arcsec": 3.0,
        "beta": 3.5,
        "fit_background": True,
        "grid_half_points": 12,
        "wavelength_grid_tolerance_angstrom": 1.0,
    },
    evidence_state="provisional",
    source=(
        "virusflow.algorithms.spatial_psf and virusflow.algorithms.source_extraction "
        "production defaults; see docs/architecture/spatial-psf-dar-coupling-resource-pycharm.md"
    ),
)

WAVELENGTH_INPUT_MASK_CONFIGURATION = VersionedConfiguration(
    kind="wavelength_input_mask_policy",
    version="bounded-flat-mask-1",
    value={"maximum_flat_mask_fraction": 0.25, "always_apply_dark_mask": True},
    evidence_state="provisional",
    source="20260609 characterization: reject pathological near-global flat masks while retaining mask evidence",
)

MASTER_SCI_EXTRACTION_CONFIGURATION = VersionedConfiguration(
    kind="master_sci_extraction",
    version="fractional-aperture-5px-1",
    value={"aperture_width": 5.0},
    evidence_state="verified",
    source="VIRUSFlow spectral-extraction knowledge note and canonical exposure extractor",
)
