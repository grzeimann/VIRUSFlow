from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class LogicalComponentSpec:
    """Format-agnostic declaration of a logical component of an artifact.

    model_type: array2d | array1d | image | table | model | scalar | collection
    name: semantic name within the artifact (e.g., "master", "mask").
    """

    name: str
    model_type: str
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class ArtifactContractSpec:
    kind: str
    components: List[LogicalComponentSpec] = field(default_factory=list)
    optional_components: List[LogicalComponentSpec] = field(default_factory=list)
    summaries: List[str] = field(default_factory=list)  # logical summary field names
    required_metadata: List[str] = field(default_factory=list)
    applicability: Optional[str] = None  # free-form description of when applicable
    validity_semantics: Optional[str] = None  # e.g., time/science window rules (descriptive)
    provenance_expectations: List[str] = field(default_factory=list)  # names of expected parent kinds


class ArtifactContract(Protocol):
    """Protocol for defining logical scientific products.

    No storage layout or QA bindings are defined here.
    """

    def spec(self) -> ArtifactContractSpec:  # pragma: no cover - Protocol signature
        ...


# Per-kind stubs (to be elaborated in later sections without storage details)

class MasterBiasContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_bias",
            components=[
                LogicalComponentSpec(name="master", model_type="array2d", required=True),
                LogicalComponentSpec(name="per_pixel_bias_scatter", model_type="array2d", required=True),
            ],
            optional_components=[],
            summaries=["read_noise"],
            required_metadata=[],
            applicability="Bias calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )


class MasterDarkContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_dark",
            components=[
                LogicalComponentSpec(name="master_dark", model_type="array2d", required=True),
                LogicalComponentSpec(name="dark_pixel_mask", model_type="array2d", required=True),
            ],
            optional_components=[],
            summaries=["bad_fraction"],
            required_metadata=[],
            applicability="Dark calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )


class MasterLDLSContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_ldls",
            components=[
                LogicalComponentSpec(name="master_ldls", model_type="array2d", required=True),
                LogicalComponentSpec(name="flat_response_mask", model_type="array2d", required=True),
            ],
            optional_components=[],
            summaries=["bad_fraction"],
            required_metadata=[],
            applicability="Flat calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )


class TraceMapContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="trace_map",
            components=[
                LogicalComponentSpec(name="fiber_trace_map", model_type="array2d", required=True),
                LogicalComponentSpec(name="trace_sample_columns", model_type="array1d", required=True),
                LogicalComponentSpec(name="sampled_trace_positions", model_type="array2d", required=True),
                LogicalComponentSpec(name="per_fiber_trace_residual_rms", model_type="array1d", required=True),
                LogicalComponentSpec(name="trace_sample_valid_mask", model_type="array2d", required=True),
                LogicalComponentSpec(name="trace_fit_residuals", model_type="array2d", required=True),
                LogicalComponentSpec(name="per_fiber_valid_sample_count", model_type="array1d", required=True),
                LogicalComponentSpec(name="trace_interpolated_fiber_mask", model_type="array1d", required=True),
            ],
            optional_components=[],
            summaries=["per_fiber_trace_residual_rms_ds", "trace_len"],
            required_metadata=[],
            applicability="Trace solution for fiber extraction",
            validity_semantics="Valid with master_ldls parent",
            provenance_expectations=["master_ldls"],
        )


class WavelengthMapContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="wavelength_map",
            components=[
                LogicalComponentSpec(name="wavelength_map", model_type="array2d", required=True),
                LogicalComponentSpec(name="per_fiber_wavelength_residual_rms", model_type="array1d", required=True),
                LogicalComponentSpec(name="arc_identification", model_type="array2d", required=True),
                LogicalComponentSpec(name="arc_candidate_evidence", model_type="array2d", required=True),
                LogicalComponentSpec(name="arc_line_evidence", model_type="array2d", required=True),
                LogicalComponentSpec(name="seed_region_attempted_mask", model_type="array1d", required=True),
                LogicalComponentSpec(name="seed_region_success_mask", model_type="array1d", required=True),
                LogicalComponentSpec(name="seed_region_failure_code", model_type="array1d", required=True),
                LogicalComponentSpec(name="seed_fit_coefficients", model_type="array2d", required=True),
                LogicalComponentSpec(name="interpolated_fiber_mask", model_type="array1d", required=True),
                LogicalComponentSpec(name="extrapolated_fiber_mask", model_type="array1d", required=True),
                LogicalComponentSpec(name="input_mask_indices", model_type="array1d", required=True),
                LogicalComponentSpec(name="input_mask_shape", model_type="array1d", required=True),
            ],
            optional_components=[],
            summaries=["per_fiber_wavelength_residual_rms_ds", "best_nmatch", "best_rms"],
            required_metadata=[],
            applicability="Wavelength calibration",
            validity_semantics="Valid with master_arc and trace_map parents",
            provenance_expectations=["master_arc", "trace_map"],
        )


class MasterArcContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_arc",
            components=[LogicalComponentSpec(name="master_arc", model_type="array2d", required=True)],
            optional_components=[],
            summaries=[],
            required_metadata=[],
            applicability="Comparison (arc) calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )


class MasterHgContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_hg",
            components=[LogicalComponentSpec(name="master_hg", model_type="array2d", required=True)],
            applicability="Isolated mercury lamp calibration group",
            validity_semantics="Paired by nearest temporal center within three hours",
        )


class MasterCdContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_cd",
            components=[LogicalComponentSpec(name="master_cd", model_type="array2d", required=True)],
            applicability="Isolated cadmium lamp calibration group",
            validity_semantics="Paired by nearest temporal center within three hours",
        )


class MasterSciContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_sci",
            components=[LogicalComponentSpec(name="master_sci", model_type="array2d", required=True)],
            summaries=["n_inputs", "robust_illumination", "finite_fraction"],
            required_metadata=["calibration_group"],
            applicability="Sufficient long-exposure science calibration group",
            validity_semantics="Monthly, dark-time, or named observing block applicability",
        )


class ExtractedMasterSciSpectrumContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="extracted_master_sci_spectrum",
            components=[
                LogicalComponentSpec("spectrum", "array2d", True),
                LogicalComponentSpec("valid_pixel_fraction", "array2d", True),
                LogicalComponentSpec("effective_aperture_width", "array2d", True),
                LogicalComponentSpec("extraction_valid", "array2d", True),
                LogicalComponentSpec("aperture_start_row", "array2d", True),
                LogicalComponentSpec("aperture_first_weight", "array2d", True),
                LogicalComponentSpec("aperture_last_weight", "array2d", True),
                LogicalComponentSpec("aperture_sample_mask_bits", "array2d", True),
            ],
            summaries=["fiber_count", "spectral_sample_count", "valid_sample_fraction"],
            required_metadata=["calibration_group", "algorithm_metadata"],
            applicability="Extracted Master Science spectra in detector-column space",
            validity_semantics="Inherited from the Master Science image",
            provenance_expectations=["master_sci", "trace_map"],
        )


class FiberWavelengthSpectralMaskContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="fiber_wavelength_spectral_mask",
            components=[
                LogicalComponentSpec("mask", "array2d", True),
                LogicalComponentSpec("spectral_model", "array2d", True),
                LogicalComponentSpec("normalization", "array2d", True),
                LogicalComponentSpec("good_wavelength_solution", "array1d", True),
            ],
            summaries=["masked_fraction", "good_wavelength_solution_count", "fiber_count"],
            required_metadata=["calibration_group", "algorithm_metadata"],
            applicability="Fiber-by-spectral-sample Master Science reliability mask",
            validity_semantics="Inherited from extracted spectra and wavelength solution",
            provenance_expectations=["extracted_master_sci_spectrum", "wavelength_map"],
        )


class MasterTwilightContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_twilight",
            components=[LogicalComponentSpec(name="master_twilight", model_type="array2d", required=True)],
            optional_components=[],
            summaries=[],
            required_metadata=[],
            applicability="Twilight calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )
