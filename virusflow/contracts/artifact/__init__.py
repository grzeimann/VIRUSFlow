from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol


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
            components=[LogicalComponentSpec(name="master", model_type="array2d", required=True)],
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
            components=[LogicalComponentSpec(name="master_dark", model_type="array2d", required=True)],
            optional_components=[LogicalComponentSpec(name="dark_pixel_mask", model_type="array2d", required=False)],
            summaries=["bad_fraction"],
            required_metadata=[],
            applicability="Dark calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )


class MasterFlatContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_flat",
            components=[LogicalComponentSpec(name="master_flat", model_type="array2d", required=True)],
            optional_components=[LogicalComponentSpec(name="flat_response_mask", model_type="array2d", required=False)],
            summaries=["bad_fraction"],
            required_metadata=[],
            applicability="Flat calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )


class TraceContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="trace",
            components=[LogicalComponentSpec(name="fiber_trace_map", model_type="array2d", required=True)],
            optional_components=[],
            summaries=["per_fiber_trace_residual_rms_ds", "trace_len"],
            required_metadata=[],
            applicability="Trace solution for fiber extraction",
            validity_semantics="Valid with master_flat parent",
            provenance_expectations=["master_flat"],
        )


class WaveContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="wave",
            components=[LogicalComponentSpec(name="wavelength_map", model_type="array2d", required=True)],
            optional_components=[],
            summaries=["per_fiber_wavelength_residual_rms_ds", "best_nmatch", "best_rms"],
            required_metadata=[],
            applicability="Wavelength calibration",
            validity_semantics="Valid with master_cmp and trace parents",
            provenance_expectations=["master_cmp", "trace"],
        )


class MasterCmpContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_cmp",
            components=[LogicalComponentSpec(name="master_comparison_lamp", model_type="array2d", required=True)],
            optional_components=[],
            summaries=[],
            required_metadata=[],
            applicability="Comparison (arc) calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )


class MasterTwiContract:
    def spec(self) -> ArtifactContractSpec:
        return ArtifactContractSpec(
            kind="master_twi",
            components=[LogicalComponentSpec(name="master_twilight", model_type="array2d", required=True)],
            optional_components=[],
            summaries=[],
            required_metadata=[],
            applicability="Twilight calibration products",
            validity_semantics="Valid for instrument zipcode and date window",
            provenance_expectations=[],
        )
