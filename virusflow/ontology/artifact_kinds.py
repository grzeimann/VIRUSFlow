from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from .coordinates import CoordinateConvention
from .lifecycle import ArtifactLifecycle
from .scopes import PhysicalScope
from .units import Unit


@dataclass(frozen=True)
class ArtifactKindSpec:
    name: str
    scope: PhysicalScope
    payload_type: str
    units: Optional[str]
    coordinates: CoordinateConvention
    required_components: Tuple[str, ...] = ()
    optional_components: Tuple[str, ...] = ()
    allowed_roles: Tuple[str, ...] = ("calibration", "reduction", "diagnostic", "analytic")
    lifecycle: ArtifactLifecycle = ArtifactLifecycle.CANONICAL


def _spec(
    name: str,
    scope: PhysicalScope,
    units: Optional[str],
    coordinates: CoordinateConvention,
    required: Iterable[str],
    optional: Iterable[str] = (),
    lifecycle: ArtifactLifecycle = ArtifactLifecycle.CANONICAL,
) -> ArtifactKindSpec:
    return ArtifactKindSpec(
        name=name,
        scope=scope,
        payload_type="array",
        units=units,
        coordinates=coordinates,
        required_components=tuple(required),
        optional_components=tuple(optional),
        lifecycle=lifecycle,
    )


ARTIFACT_KINDS: Dict[str, ArtifactKindSpec] = {
    "master_bias": _spec("master_bias", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master", "per_pixel_bias_scatter")),
    "master_dark": _spec("master_dark", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_dark", "dark_pixel_mask")),
    "master_ldls": _spec("master_ldls", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_ldls", "flat_response_mask")),
    "master_arc": _spec("master_arc", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_arc",)),
    "master_twilight": _spec("master_twilight", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_twilight",)),
    "master_sci": _spec("master_sci", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("master_science",)),
    "trace_map": _spec("trace_map", PhysicalScope.AMPLIFIER, Unit.PIXEL.value, CoordinateConvention.FIBER_BY_DISPERSION_PIXEL, ("fiber_trace_map", "trace_sample_columns", "sampled_trace_positions", "per_fiber_trace_residual_rms")),
    "wavelength_map": _spec("wavelength_map", PhysicalScope.AMPLIFIER, Unit.ANGSTROM.value, CoordinateConvention.FIBER_BY_DISPERSION_PIXEL, ("wavelength_map", "per_fiber_wavelength_residual_rms"), ("arc_identification",)),
    "read_noise": _spec("read_noise", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.NONE, ("read_noise",)),
    "gain": _spec("gain", PhysicalScope.AMPLIFIER, "electron / adu", CoordinateConvention.NONE, ("gain",)),
    "pixel_mask": _spec("pixel_mask", PhysicalScope.PIXEL, Unit.DIMENSIONLESS.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("mask",)),
    "detector_variance": _spec("detector_variance", PhysicalScope.PIXEL, Unit.ELECTRON_VARIANCE.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("variance",)),
    "oriented_detector_image": _spec("oriented_detector_image", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("image",)),
    "overscan_model": _spec("overscan_model", PhysicalScope.AMPLIFIER, Unit.ADU.value, CoordinateConvention.RAW_AMPLIFIER, ("row_model",)),
    "overscan_corrected_image": _spec("overscan_corrected_image", PhysicalScope.AMPLIFIER, Unit.ADU.value, CoordinateConvention.RAW_AMPLIFIER, ("image",)),
    # These scientific values remain named so analysis can request them, but
    # ArtifactService rejects their publication during production.
    "reduced_science_image": _spec("reduced_science_image", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value, CoordinateConvention.ORIENTED_AMPLIFIER, ("image", "variance", "pixel_mask"), lifecycle=ArtifactLifecycle.SCRATCH),
    "ccd_scattered_light_model": _spec(
        "ccd_scattered_light_model", PhysicalScope.PHYSICAL_CCD, Unit.ELECTRON.value,
        CoordinateConvention.PHYSICAL_CCD_ZERO_INDEXED,
        ("model_parameters", "detector_shape", "gap_sample_indices", "fit_sample_indices", "holdout_sample_indices", "residual_sample_indices", "residual_sample_values"),
        lifecycle=ArtifactLifecycle.MODEL,
    ),
    "scatter_subtracted_image": _spec(
        "scatter_subtracted_image", PhysicalScope.PHYSICAL_CCD, Unit.ELECTRON.value,
        CoordinateConvention.PHYSICAL_CCD_ZERO_INDEXED,
        ("image", "variance", "pixel_mask", "seam_mask", "inter_amplifier_gap_mask", "source_amplifier_map", "source_y_coordinate"),
        lifecycle=ArtifactLifecycle.SCRATCH,
    ),
    "aperture_extracted_spectrum": _spec(
        "aperture_extracted_spectrum", PhysicalScope.FIBER, Unit.ELECTRON.value,
        CoordinateConvention.FIBER_BY_DISPERSION_PIXEL,
        ("spectrum", "valid_pixel_fraction", "effective_aperture_width", "aperture_start_row", "fractional_weights", "extraction_valid"),
        lifecycle=ArtifactLifecycle.SCRATCH,
    ),
    "extracted_variance": _spec("extracted_variance", PhysicalScope.FIBER, Unit.ELECTRON_VARIANCE.value, CoordinateConvention.FIBER_BY_DISPERSION_PIXEL, ("variance",), lifecycle=ArtifactLifecycle.SCRATCH),
    "within_amp_fiber_normalization": _spec(
        "within_amp_fiber_normalization", PhysicalScope.FIBER, Unit.DIMENSIONLESS.value,
        CoordinateConvention.FIBER_BY_DISPERSION_PIXEL,
        ("raw_ratio", "normalization", "valid_mask", "common_twilight"),
    ),
    "amp_to_amp_normalization": _spec(
        "amp_to_amp_normalization", PhysicalScope.EXPOSURE, Unit.DIMENSIONLESS.value,
        CoordinateConvention.NONE, ("amplifier_factors", "amplifier_twilight_levels", "reference_level", "amplifier_identity"),
    ),
    "fiber_normalization": _spec(
        "fiber_normalization", PhysicalScope.FIBER, Unit.DIMENSIONLESS.value,
        CoordinateConvention.FIBER_BY_DISPERSION_PIXEL,
        ("normalization", "within_amp_factor", "amp_to_amp_factor"),
    ),
    "initial_astrometry": _spec("initial_astrometry", PhysicalScope.EXPOSURE, "deg", CoordinateConvention.ICRS, ("parameters", "header_evidence")),
    "source_detection_catalog": _spec("source_detection_catalog", PhysicalScope.EXPOSURE, Unit.ELECTRON.value, CoordinateConvention.ICRS, ("detections",)),
    "catalog_match_table": _spec("catalog_match_table", PhysicalScope.EXPOSURE, "arcsec", CoordinateConvention.ICRS, ("matches", "catalog_rows")),
    "final_astrometry": _spec("final_astrometry", PhysicalScope.EXPOSURE, "deg", CoordinateConvention.ICRS, ("parameters", "fit_evidence")),
    "fiber_sky_coordinates": _spec("fiber_sky_coordinates", PhysicalScope.FIBER, "deg", CoordinateConvention.ICRS, ("coordinates", "fiber_identity", "focal_plane_coordinates")),
    "sky_fiber_mask": _spec("sky_fiber_mask", PhysicalScope.FIBER, Unit.DIMENSIONLESS.value, CoordinateConvention.NONE, ("mask", "broadband_flux", "fiber_identity")),
    "incident_sky_spectrum": _spec("incident_sky_spectrum", PhysicalScope.EXPOSURE, Unit.ELECTRON.value, CoordinateConvention.WAVELENGTH_ANGSTROM, ("wavelength", "spectrum", "variance", "sample_count"), lifecycle=ArtifactLifecycle.SCRATCH),
    "sky_model": _spec(
        "sky_model", PhysicalScope.EXPOSURE, Unit.ELECTRON.value,
        CoordinateConvention.WAVELENGTH_ANGSTROM,
        ("latent_wavelength", "latent_flux_density", "latent_variance_density", "sample_count", "fiber_coefficients", "fiber_identity"),
        lifecycle=ArtifactLifecycle.MODEL,
    ),
    "fiber_sky_prediction": _spec("fiber_sky_prediction", PhysicalScope.FIBER, Unit.ELECTRON.value, CoordinateConvention.FIBER_BY_DISPERSION_PIXEL, ("prediction", "fiber_identity"), lifecycle=ArtifactLifecycle.SCRATCH),
    "sky_subtracted_spectrum": _spec("sky_subtracted_spectrum", PhysicalScope.FIBER, Unit.ELECTRON.value, CoordinateConvention.FIBER_BY_DISPERSION_PIXEL, ("spectrum", "variance", "fiber_identity"), lifecycle=ArtifactLifecycle.SCRATCH),
    "baseline_relative_response": _spec("baseline_relative_response", PhysicalScope.INSTRUMENT_EPOCH, Unit.DIMENSIONLESS.value, CoordinateConvention.WAVELENGTH_ANGSTROM, ("wavelength", "response")),
    "exposure_illumination_correction": _spec("exposure_illumination_correction", PhysicalScope.EXPOSURE, Unit.DIMENSIONLESS.value, CoordinateConvention.NONE, ("fiber_factor", "amplifier_factor", "fiber_identity")),
    "final_exposure_response": _spec("final_exposure_response", PhysicalScope.EXPOSURE, Unit.DIMENSIONLESS.value, CoordinateConvention.FIBER_BY_DISPERSION_PIXEL, ("response", "baseline_response", "illumination_factor", "fiber_identity"), lifecycle=ArtifactLifecycle.SCRATCH),
    "fiber_response_model": _spec(
        "fiber_response_model", PhysicalScope.EXPOSURE, Unit.DIMENSIONLESS.value,
        CoordinateConvention.WAVELENGTH_ANGSTROM,
        ("wavelength_knots", "within_amp_knots", "amplifier_factors", "illumination_factors", "fiber_identity"),
        lifecycle=ArtifactLifecycle.MODEL,
    ),
    "calibrated_fiber_observation": _spec(
        "calibrated_fiber_observation", PhysicalScope.OBSERVATION,
        "1e-17 erg s-1 cm-2 Angstrom-1", CoordinateConvention.FIBER_BY_DISPERSION_PIXEL,
        ("flux", "variance", "mask", "wavelength", "fiber_identity", "sky_coordinates", "focal_plane_coordinates", "exposure_index"),
        lifecycle=ArtifactLifecycle.CANONICAL,
    ),
    "analysis_materialization": _spec(
        "analysis_materialization", PhysicalScope.OBSERVATION, None,
        CoordinateConvention.NONE, ("data",), lifecycle=ArtifactLifecycle.ANALYSIS,
    ),
    "bias_stability": _spec(
        "bias_stability", PhysicalScope.AMPLIFIER, Unit.ELECTRON.value,
        CoordinateConvention.NONE,
        ("source_artifact_id", "median_bias_level", "median_bias_scatter"),
        lifecycle=ArtifactLifecycle.ANALYSIS,
    ),
    "candidate_sky_model": _spec(
        "candidate_sky_model", PhysicalScope.EXPOSURE, Unit.ELECTRON.value,
        CoordinateConvention.WAVELENGTH_ANGSTROM, ("latent_wavelength", "latent_flux_density"),
        lifecycle=ArtifactLifecycle.ANALYSIS,
    ),
    "candidate_scattered_light_model": _spec(
        "candidate_scattered_light_model", PhysicalScope.PHYSICAL_CCD, Unit.ELECTRON.value,
        CoordinateConvention.PHYSICAL_CCD_ZERO_INDEXED, ("model_parameters", "detector_shape"),
        lifecycle=ArtifactLifecycle.ANALYSIS,
    ),
    "exposure_mode_classification": _spec("exposure_mode_classification", PhysicalScope.EXPOSURE, Unit.DIMENSIONLESS.value, CoordinateConvention.NONE, ("classification", "source_fields")),
    "effective_exposure_time": _spec("effective_exposure_time", PhysicalScope.EXPOSURE, Unit.SECOND.value, CoordinateConvention.NONE, ("effective_seconds", "source_fields")),
    "exposure_completion_manifest": _spec("exposure_completion_manifest", PhysicalScope.EXPOSURE, Unit.DIMENSIONLESS.value, CoordinateConvention.NONE, ("coverage", "amplifier_identity")),
    "observation_exposure_state": _spec(
        "observation_exposure_state", PhysicalScope.EXPOSURE, Unit.DIMENSIONLESS.value,
        CoordinateConvention.NONE, ("state", "astrometry_parameters", "coverage_summary"),
    ),
    "observation_membership": _spec(
        "observation_membership", PhysicalScope.OBSERVATION, Unit.DIMENSIONLESS.value,
        CoordinateConvention.NONE, ("membership", "exposure_state"),
    ),
    "dither_assignment": _spec(
        "dither_assignment", PhysicalScope.DITHER_SET, "arcsec", CoordinateConvention.NONE,
        ("assignments", "sequence_evidence"),
    ),
    "dither_registration": _spec(
        "dither_registration", PhysicalScope.DITHER_SET, "arcsec", CoordinateConvention.NONE,
        ("nominal_offsets", "refined_offsets", "registration_residuals", "registration_success", "astrometry_parameters"),
    ),
    "dither_coverage_map": _spec(
        "dither_coverage_map", PhysicalScope.DITHER_SET, Unit.DIMENSIONLESS.value,
        CoordinateConvention.NONE, ("coverage", "x_coordinate", "y_coordinate"),
    ),
    "observation_summary": _spec(
        "observation_summary", PhysicalScope.OBSERVATION, Unit.DIMENSIONLESS.value,
        CoordinateConvention.NONE, ("member_state", "qa_usability"),
    ),
}


LEGACY_KIND_ALIASES: Dict[str, str] = {
    "master_flat": "master_ldls",
    "masterflt": "master_ldls",
    "master_cmp": "master_arc",
    "mastercmp": "master_arc",
    "master_twi": "master_twilight",
    "trace": "trace_map",
    "wave": "wavelength_map",
}


def canonical_kind(name: str) -> str:
    key = str(name or "").strip().lower()
    return LEGACY_KIND_ALIASES.get(key, key)


def kind_spec(name: str) -> ArtifactKindSpec:
    canonical = canonical_kind(name)
    try:
        return ARTIFACT_KINDS[canonical]
    except KeyError as exc:
        raise KeyError(f"Unregistered Artifact kind: {name!r}") from exc


def kind_candidates(name: str) -> Tuple[str, ...]:
    canonical = canonical_kind(name)
    aliases = [legacy for legacy, target in LEGACY_KIND_ALIASES.items() if target == canonical]
    return tuple(dict.fromkeys([canonical, str(name).strip().lower(), *aliases]))
