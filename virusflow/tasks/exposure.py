"""Canonical full-width baseline reduction for one atomic VIRUS Exposure."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from types import SimpleNamespace
from typing import Iterable

import numpy as np

from .science import (
    PhysicalCCDTask,
    ReducedScienceAmplifierTask,
    _SciencePublisher,
    _instant,
)
from ..algorithms import dar, source_extraction, spatial_psf
from ..algorithms.astrometry import (
    ASTROMETRY_VERSION,
    detect_fiber_sources,
    fit_catalog_astrometry,
    parse_header_pointing,
    sky_to_focal_plane,
    tan_fiber_coordinates,
)
from ..algorithms.atmosphere import (
    EXTINCTION_VERSION,
    atmospheric_extinction_model,
    evaluate_atmospheric_extinction,
)
from ..algorithms.completion import build_completion_coverage
from ..algorithms.exposure import CalibratedFiberState, apply_relative_response
from ..algorithms.exposure_state import classify_mode_and_effective_time
from ..algorithms.extraction import (
    EXTRACTION_VERSION,
    extract_fractional_aperture,
    validate_wavelength_rows,
)
from ..algorithms.physical_ccd import (
    ALGORITHM_VERSION as CONTRIBUTION_CORRECTION_VERSION,
)
from ..algorithms.response import (
    RESPONSE_VERSION,
    baseline_relative_response,
    compact_fiber_response,
    measure_exposure_illumination,
    normalize_amplifier_spectrum,
)
from ..algorithms.sky import (
    SKY_VERSION,
    LatentSkyModel,
    derive_sky_oversampling_factor,
    oversampled_incident_sky,
    predict_and_subtract_sky,
    select_sky_fibers,
    wavelength_bin_edges,
)
from ..artifacts import ArtifactService, Scope, Validity
from ..artifacts.requests import ArtifactRequest, LogicalComponent
from ..artifacts.storage_conventions import FLUX_SCALE, VARIANCE_SCALE
from ..config import ConfigurationService
from ..config.defaults import (
    ATMOSPHERIC_EXTINCTION_CONFIGURATION,
    BASELINE_RESPONSE_CONFIGURATION,
    DAR_SEED_CONFIGURATION,
    EFFECTIVE_EXPOSURE_POLICY,
    FIBER_GEOMETRY_CONFIGURATION,
    SOURCE_EXTRACTION_CONFIGURATION,
)
from ..core.algo_result import AlgoResult
from ..core.scientific_metadata import (
    normalize_scientific_metadata,
    scientific_metadata_from_header,
)
from ..io import PanSTARRSCSVProvider, RawFrameLoader
from ..ontology.scopes import PhysicalScope
from ..planning.targets import PhysicalCCDTarget


SPATIAL_PSF_STATUS_CODE = {"measured": 0, "degraded": 1}


AMP_CODE = {"LL": 0, "LU": 1, "RU": 2, "RL": 3}
CALIBRATION_KINDS = (
    "master_bias",
    "master_dark",
    "master_ldls",
    "master_arc",
    "trace_map",
    "wavelength_map",
)


def _component(name, value, units, coordinates, **metadata):
    array = np.asarray(value)
    if array.ndim > 2:
        raise ValueError(
            f"Component {name} must be flattened to at most two dimensions"
        )
    return LogicalComponent(
        name,
        "array1d" if array.ndim == 1 else "array2d",
        array,
        units,
        coordinates,
        metadata,
    )


def _mask_component(name, value, units="1", coordinates="none", **metadata):
    return LogicalComponent(
        name, "mask", np.asarray(value), units, coordinates, metadata
    )


@dataclass(frozen=True)
class GlobalFiberFrame:
    """Exposure-wide fiber frame assembled by concatenating per-amplifier arrays."""

    spectrum: np.ndarray
    variance: np.ndarray
    measured_spectrum: np.ndarray
    valid_fraction: np.ndarray
    wavelength: np.ndarray
    fiber_identity: np.ndarray
    focal: np.ndarray
    within_response: np.ndarray
    amplifier_response: np.ndarray
    parent_ids: list[int]


@dataclass(frozen=True)
class ExposureInferenceState:
    """One internally consistent exposure-wide source/sky inference solution."""

    ra0: float
    dec0: float
    pa: float
    ra: np.ndarray
    dec: np.ndarray
    detections: np.ndarray
    match_table: np.ndarray
    fit_parameters: np.ndarray
    astrometry_success: bool
    object_mask: np.ndarray
    sky_mask: np.ndarray
    broadband_flux: np.ndarray
    sky_center: float
    sky_sigma: float
    fiber_illumination: np.ndarray
    amplifier_illumination: np.ndarray
    sky_wave: np.ndarray
    incident_sky: np.ndarray
    sky_variance: np.ndarray
    sky_counts: np.ndarray
    sky_model: LatentSkyModel
    sky_subtracted: np.ndarray
    residual_sigma: float
    iteration: int


class ExposureTask(_SciencePublisher):
    name = "full_exposure"
    version = "v3"

    @staticmethod
    def _no_extractable_message(
        exposure_id: str, failures: dict, *, reason: str | None = None
    ) -> str:
        calibration_hint = "; run 'virusflow run calibrations' first"
        include_calibration_hint = False
        by_failure = defaultdict(set)
        for zipcode_key, messages in failures.items():
            for message in messages:
                message = str(message)
                if message.endswith(calibration_hint):
                    message = message.removesuffix(calibration_hint)
                    include_calibration_hint = True
                by_failure[message].add(str(zipcode_key))
        details = []
        for message, zipcode_keys in sorted(
            by_failure.items(), key=lambda item: (-len(item[1]), item[0])
        )[:8]:
            count = len(zipcode_keys)
            details.append(f"{message} ({count} amplifier{'s' if count != 1 else ''})")
        omitted = max(0, len(by_failure) - len(details))
        if omitted:
            details.append(
                f"{omitted} additional failure reason{'s' if omitted != 1 else ''}"
            )
        context = f": {reason}" if reason else ""
        summary = f"; failures: {'; '.join(details)}" if details else ""
        next_step = calibration_hint if include_calibration_hint else ""
        return f"Exposure {exposure_id} produced no extractable amplifier{context}{summary}{next_step}"

    def _request(
        self,
        *,
        kind: str,
        components: dict,
        scope: Scope,
        parents: Iterable[int] = (),
        summaries: dict | None = None,
        metadata: dict | None = None,
        refs=(),
        assumptions=(),
        algorithm: str,
        version: str,
        status: str = "pass",
        usability: str = "usable",
    ):
        at = self.target.at_time or _instant(self.target.exposure_id)
        request = ArtifactRequest(
            kind=kind,
            role="reduction",
            components=components,
            summaries=dict(summaries or {}),
            metadata=dict(metadata or {}),
            scope=scope,
            scientific_metadata=(
                dict(getattr(self, "_exposure_scientific_metadata", {}) or {})
                if scope.exposure_id == getattr(self.target, "exposure_id", None)
                else {}
            ),
            parents=[int(value) for value in parents],
            validity=Validity(at, at, "exposure_identity"),
            configuration_refs=list(refs),
            assumptions=list(assumptions),
        )
        artifact = self._publish(
            request, algorithm_name=algorithm, algorithm_version=version
        )
        self._qa(artifact, request.summaries, status=status, usability=usability)
        return artifact

    def _ensure_calibrations(self, zipcodes, at: datetime):
        from ..performance import phase

        service = ArtifactService(self.ctx.db_path)
        available = {}
        failures = {}
        requested_keys = {zipcode.key() for zipcode in zipcodes}
        candidate_sets = [
            service.adapter.find(kind="exposure_fiber_response", zipcode=None, at_time=at, limit=None),
            service.adapter.find(kind="exposure_fiber_response", zipcode=None, at_time=None, limit=None),
        ]
        response = None
        seen_builds = set()
        best_overlap = -1
        for candidates in candidate_sets:
            for candidate in candidates:
                candidate_id = int(candidate["id"])
                if candidate_id in seen_builds:
                    continue
                seen_builds.add(candidate_id)
                if (
                    candidate.get("amp_key") is not None
                    or candidate.get("exposure_id") is not None
                    or str(candidate.get("state") or "active") != "active"
                ):
                    continue
                candidate_keys = set(
                    (service.describe(candidate)["summary"].get("algorithm_metadata") or {}).get("amplifier_keys") or []
                )
                # A degraded build (missing a routine minority of amplifiers) still
                # covers this exposure's healthy majority; pick the build with the
                # best overlap rather than requiring exact full coverage.
                overlap = len(requested_keys & candidate_keys)
                if overlap > best_overlap:
                    best_overlap = overlap
                    response = candidate
            if best_overlap == len(requested_keys):
                break
        for zipcode in zipcodes:
            kinds = {}
            for kind in CALIBRATION_KINDS:
                with phase("calibration_selection"):
                    existing = service.select_best(
                        kind=kind,
                        scope=Scope(zipcode=zipcode),
                        at_time=at,
                        policy="latest_valid",
                    )
                    if existing is None:
                        existing = service.select_best(
                            kind=kind,
                            scope=Scope(zipcode=zipcode),
                            at_time=at,
                            policy="nearest",
                        )
                if existing is None:
                    failures.setdefault(zipcode.key(), []).append(
                        f"{kind}: missing published calibration; run 'virusflow run calibrations' first"
                    )
                    continue
                kinds[kind] = existing
            if response is None or zipcode.key() not in (
                (service.describe(response)["summary"].get("algorithm_metadata") or {}).get("amplifier_keys") or []
            ):
                failures.setdefault(zipcode.key(), []).append(
                    "exposure_fiber_response: absent from selected exposure response; "
                    "run 'virusflow run calibrations' first"
                )
            else:
                kinds["exposure_fiber_response"] = response
            available[zipcode.key()] = kinds
        return available, failures

    @staticmethod
    def _baseline_application_versions() -> dict[str, str]:
        return {
            "extraction": EXTRACTION_VERSION,
            "psf_treatment": "none-fixed-aperture-1",
            "contribution_correction": CONTRIBUTION_CORRECTION_VERSION,
            "calibration_convention": RESPONSE_VERSION,
        }

    @classmethod
    def _complete_baseline_candidates(cls, service, at: datetime) -> list[dict]:
        required = {"wavelength", "response", "uncertainty", "mask"}
        candidates = service.adapter.find(
            kind="baseline_relative_response", zipcode=None, at_time=at, limit=None
        )
        complete = []
        for row in candidates:
            if str(row.get("state") or "active") != "active":
                continue
            description = service.describe(row)
            names = {
                component["name"]
                for component in description["components"]
                if component.get("payload_state", "present") == "present"
            }
            applicability = description["summary"].get("applicability") or {}
            atmospheric_content = description["summary"].get("atmospheric_content")
            separation = description["summary"].get("atmospheric_separation") or {}
            calibration_airmasses = (
                separation.get("calibration_exposure_airmasses") or []
            )
            try:
                valid_calibration_airmasses = bool(calibration_airmasses) and all(
                    np.isfinite(float(value)) and float(value) > 0.0
                    for value in calibration_airmasses
                )
            except (TypeError, ValueError, OverflowError):
                valid_calibration_airmasses = False
            convention_complete = atmospheric_content == "absorbed_unknown" or (
                atmospheric_content == "removed_with_model"
                and bool(separation.get("extinction_model_identity"))
                and valid_calibration_airmasses
            )
            if (
                required <= names
                and applicability.get("algorithm_versions")
                == cls._baseline_application_versions()
                and convention_complete
            ):
                complete.append(row)
        return complete

    def _select_or_import_baseline(self, service, config, at: datetime):
        """Select one baseline Product, importing the bundled Remedy seed once."""

        configured_id = self.ctx.config.get("baseline_response_artifact_id")
        if configured_id is not None:
            row = service.adapter.get_row(int(configured_id))
            if row is None or row.get("kind") != "baseline_relative_response":
                raise RuntimeError(
                    f"Configured baseline_response_artifact_id={configured_id} is not a baseline response"
                )
            if int(row["id"]) not in {
                int(candidate["id"])
                for candidate in self._complete_baseline_candidates(service, at)
            }:
                raise RuntimeError(
                    "Configured baseline response is incomplete, inactive, inapplicable in time, "
                    "or incompatible with the current extraction/correction methods"
                )
            return service.get(int(row["id"]))

        configured_path = self.ctx.config.get("baseline_response_path")
        if configured_path is None:
            candidates = self._complete_baseline_candidates(service, at)
            current_candidates = []
            for candidate in candidates:
                summary = service.describe(candidate)["summary"]
                method = summary.get("derivation_method_identity") or {}
                obsolete_bundled_default = (
                    method.get("name") == BASELINE_RESPONSE_CONFIGURATION.identity
                    and summary.get("payload_version")
                    != BASELINE_RESPONSE_CONFIGURATION.version
                )
                if not obsolete_bundled_default:
                    current_candidates.append(candidate)
            candidates = current_candidates
            if candidates:
                # Registry ordering is newest first.  A later measured baseline
                # therefore supersedes the imported seed instead of multiplying
                # another response layer onto it.
                return service.get(int(candidates[0]["id"]))

        payload, reference = config.resolve_baseline_response(configured_path)
        configured_versions = BASELINE_RESPONSE_CONFIGURATION.value[
            "application_configuration"
        ]["algorithm_versions"]
        current_versions = self._baseline_application_versions()
        if configured_versions != current_versions:
            raise RuntimeError(
                "The bundled baseline is incompatible with the current extraction/correction "
                "methods; regenerate and version a method-matched baseline"
            )
        baseline = baseline_relative_response(
            payload["wavelength"],
            payload["response"],
            payload["uncertainty"],
            payload["mask"],
            version=reference.version,
        )
        config_value = BASELINE_RESPONSE_CONFIGURATION.value
        metadata = {
            "evidence_state": BASELINE_RESPONSE_CONFIGURATION.evidence_state,
            "payload_version": reference.version,
            "response_object": "empirical_effective_relative_response",
            "response_definition": config_value["response_definition"],
            "instrument_epoch": config_value["instrument_epoch"],
            "derivation_method_identity": {
                "name": BASELINE_RESPONSE_CONFIGURATION.identity,
                **config_value["derivation_method"],
            },
            "applicability": {
                **config_value["application_configuration"],
                "wavelength_min_angstrom": float(baseline.get_array("wavelength")[0]),
                "wavelength_max_angstrom": float(baseline.get_array("wavelength")[-1]),
                "selection": "independent baseline Product; apply exactly once after sky subtraction",
            },
            "atmospheric_content": config_value["atmospheric_content"],
            "construction_extinction_model": config_value[
                "construction_extinction_model"
            ],
            "construction_airmass": config_value["construction_airmass"],
            "construction_airmass_basis": config_value["construction_airmass_basis"],
            "source_baseline": config_value["source_baseline"],
            "atmospheric_separation": config_value["atmospheric_separation"],
            "separate_exposure_measurements": config_value[
                "separate_exposure_measurements"
            ],
            "atmospheric_correction_applied": False,
            "isolated_instrumental_throughput": False,
            "absolute_flux_calibration": False,
            "uncertainty_state": "not supplied by legacy curves; NaN with mask bit 2",
            "mask_bits": {
                "1": "response invalid",
                "2": "response uncertainty unavailable",
            },
            "source_payload_name": payload["source_name"],
            "source_payload_sha256": payload["source_sha256"],
            "legacy_input_provenance": {
                "throughput_sha256": "6a6715e048dbc7d8ef709f371b2bdf2b0f7bf0fc2c063134b184d9494c9f141a",
                "normalization_sha256": "c8c3eee4b89e1a688e9c438e451b5e63d8c961e860cb00c0e40ab4cdd0770c23",
                "retention": "inputs used only to create this payload; not response components",
            },
        }
        request = ArtifactRequest(
            kind="baseline_relative_response",
            role="calibration",
            components={
                "wavelength": _component(
                    "wavelength",
                    baseline.get_array("wavelength"),
                    "Angstrom",
                    "wavelength_angstrom",
                ),
                "response": _component(
                    "response",
                    baseline.get_array("response"),
                    "1",
                    "wavelength_angstrom",
                ),
                "uncertainty": _component(
                    "uncertainty",
                    baseline.get_array("uncertainty"),
                    "1",
                    "wavelength_angstrom",
                    convention="standard_deviation",
                ),
                "mask": _mask_component(
                    "mask",
                    baseline.get_array("mask"),
                    "1",
                    "wavelength_angstrom",
                    bit_1="response_invalid",
                    bit_2="uncertainty_unavailable",
                ),
            },
            summaries=dict(baseline.scalars),
            metadata=metadata,
            scope=Scope(zipcode=None, physical_scope=PhysicalScope.INSTRUMENT_EPOCH),
            validity=Validity(None, None, "legacy_remedy_instrument_epoch_unspecified"),
            configuration_refs=[reference],
            assumptions=[
                "McDonald mean extinction was removed from the legacy effective response at airmass 1.22.",
                "The exact observing epoch and release that produced the supplied legacy curves were not recovered.",
                "No absolute flux scale or isolated instrumental throughput is asserted.",
            ],
        )
        artifact = self._publish(
            request,
            algorithm_name="virusflow.algorithms.response.baseline_relative_response",
            algorithm_version=reference.version,
        )
        self._qa(artifact, request.summaries, status="warn", usability="degraded")
        return artifact

    @staticmethod
    def _complete_extinction_candidates(
        service, at: datetime, identity: str
    ) -> list[dict]:
        required = {"wavelength", "extinction_coefficient", "uncertainty", "mask"}
        rows = service.adapter.find(
            kind="atmospheric_extinction_model", zipcode=None, at_time=at, limit=None
        )
        complete = []
        for row in rows:
            if str(row.get("state") or "active") != "active":
                continue
            description = service.describe(row)
            components = {
                component["name"]
                for component in description["components"]
                if component.get("payload_state", "present") == "present"
            }
            if (
                required <= components
                and description["summary"].get("model_identity") == identity
            ):
                complete.append(row)
        return complete

    def _select_or_import_extinction_model(
        self, service, config, at: datetime, *, required_identity: str
    ):
        """Select one site extinction model or import the bundled McDonald model."""

        configured_id = self.ctx.config.get("atmospheric_extinction_artifact_id")
        if configured_id is not None:
            row = service.adapter.get_row(int(configured_id))
            if row is None or row.get("kind") != "atmospheric_extinction_model":
                raise RuntimeError(
                    f"Configured atmospheric_extinction_artifact_id={configured_id} "
                    "is not an atmospheric-extinction model"
                )
            acceptable = {
                int(candidate["id"])
                for candidate in self._complete_extinction_candidates(
                    service, at, required_identity
                )
            }
            if int(row["id"]) not in acceptable:
                raise RuntimeError(
                    "Configured atmospheric-extinction model is incomplete, inactive, "
                    "inapplicable in time, or does not match the baseline model identity"
                )
            return service.get(int(row["id"]))

        configured_path = self.ctx.config.get("atmospheric_extinction_path")
        if configured_path is None:
            candidates = self._complete_extinction_candidates(
                service, at, required_identity
            )
            if candidates:
                return service.get(int(candidates[0]["id"]))

        payload, reference = config.resolve_atmospheric_extinction(configured_path)
        if reference.identity != required_identity:
            raise RuntimeError(
                f"Baseline requires extinction model {required_identity!r}, but the selectable "
                f"configuration provides {reference.identity!r}"
            )
        model = atmospheric_extinction_model(
            payload["wavelength"],
            payload["extinction_coefficient"],
            payload["uncertainty"],
            payload["mask"],
            version=reference.version,
        )
        values = ATMOSPHERIC_EXTINCTION_CONFIGURATION.value
        metadata = {
            "evidence_state": ATMOSPHERIC_EXTINCTION_CONFIGURATION.evidence_state,
            "payload_version": reference.version,
            "model_identity": reference.identity,
            "coefficient_definition": values["coefficient_definition"],
            "coefficient_units": values["coefficient_units"],
            "site": values["site"],
            "applicability": {
                "wavelength_min_angstrom": model.scalars["wavelength_min_angstrom"],
                "wavelength_max_angstrom": model.scalars["wavelength_max_angstrom"],
                "site": values["site"],
                "interpolation": values["interpolation"],
            },
            "uncertainty_state": values["uncertainty_state"],
            "mask_bits": {
                "1": "coefficient invalid or outside valid range",
                "2": "coefficient uncertainty unavailable",
            },
            "source_payload_name": payload["source_name"],
            "source_payload_sha256": payload["source_sha256"],
            "input_source_sha256": (
                "d6e41b8bab5185d375371cf70a4288e240527c5890e150e3d93f25e8803c5810"
            ),
        }
        request = ArtifactRequest(
            kind="atmospheric_extinction_model",
            role="calibration",
            components={
                "wavelength": _component(
                    "wavelength",
                    model.get_array("wavelength"),
                    "Angstrom",
                    "wavelength_angstrom",
                ),
                "extinction_coefficient": _component(
                    "extinction_coefficient",
                    model.get_array("extinction_coefficient"),
                    "mag / airmass",
                    "wavelength_angstrom",
                ),
                "uncertainty": _component(
                    "uncertainty",
                    model.get_array("uncertainty"),
                    "mag / airmass",
                    "wavelength_angstrom",
                    convention="standard_deviation",
                ),
                "mask": _mask_component(
                    "mask",
                    model.get_array("mask"),
                    "1",
                    "wavelength_angstrom",
                    bit_1="coefficient_invalid",
                    bit_2="uncertainty_unavailable",
                ),
            },
            summaries=dict(model.scalars),
            metadata=metadata,
            scope=Scope(zipcode=None, physical_scope=PhysicalScope.INSTRUMENT_EPOCH),
            validity=Validity(None, None, "mcdonald_observatory_site_model"),
            configuration_refs=[reference],
            assumptions=[
                "The tabulated coefficient is interpreted as magnitudes per airmass.",
                "No coefficient uncertainty was supplied with the imported model.",
                "The model is not extrapolated beyond its retained wavelength range.",
            ],
        )
        artifact = self._publish(
            request,
            algorithm_name="virusflow.algorithms.atmosphere.atmospheric_extinction_model",
            algorithm_version=EXTINCTION_VERSION,
        )
        self._qa(artifact, request.summaries, status="warn", usability="degraded")
        return artifact

    @staticmethod
    def _amp_from_physical(array, amp: str):
        data = np.asarray(array)
        half = data.shape[0] // 2
        return data[:half] if amp in {"LL", "RU"} else data[half:][::-1]

    def _assemble_global_fiber_frame(
        self,
        *,
        ordered_keys,
        amp_results,
        amp_factors,
        amp_artifact_id,
        fplane,
        fiber_offsets_by_ifuid,
        failures,
        exposure_id,
    ) -> GlobalFiberFrame:
        global_spectrum = []
        global_variance = []
        global_measured_spectrum = []
        global_valid = []
        global_wavelength = []
        global_identity = []
        global_focal = []
        global_within = []
        global_amplifier = []
        parent_ids = [amp_artifact_id]
        amp_index_by_key = {key: index for index, key in enumerate(ordered_keys)}
        for key, amp_factor in zip(ordered_keys, amp_factors):
            item = amp_results[key]
            amp_spectrum_normalization = normalize_amplifier_spectrum(
                item["spectrum"],
                item["variance"],
                item["within"],
                amp_factor,
            )
            normalized = amp_spectrum_normalization.get_array("normalized_spectrum")
            normalized_variance = amp_spectrum_normalization.get_array(
                "normalized_variance"
            )
            zipcode = item["zipcode"]
            parent_ids.extend(item["parent_ids"])
            if zipcode.ifuslot not in fplane:
                failures.setdefault(key, []).append(
                    "IFUSLOT absent from fplane configuration"
                )
                continue
            valid_rows = item["valid_wavelength_rows"]
            original_fiber_indices = np.flatnonzero(valid_rows)
            n_fiber = original_fiber_indices.size
            local = fiber_offsets_by_ifuid[zipcode.ifuid][zipcode.amp]
            fp_x, fp_y = fplane[zipcode.ifuslot]
            focal = local[valid_rows] + np.asarray([fp_x, fp_y])
            identities = np.column_stack(
                (
                    np.full(n_fiber, amp_index_by_key[key]),
                    original_fiber_indices,
                    np.full(n_fiber, int(zipcode.ifuslot)),
                    np.full(n_fiber, int(zipcode.specid)),
                    np.full(n_fiber, AMP_CODE[zipcode.amp]),
                )
            ).astype(np.int32)
            global_spectrum.append(normalized[valid_rows].astype(np.float32))
            global_variance.append(normalized_variance[valid_rows].astype(np.float32))
            global_measured_spectrum.append(
                item["spectrum"][valid_rows].astype(np.float32)
            )
            response_valid_fraction = np.where(
                item["normalization_valid"] > 0,
                item["valid_fraction"],
                0.0,
            )
            global_valid.append(response_valid_fraction[valid_rows].astype(np.float32))
            global_wavelength.append(item["wavelength"][valid_rows].astype(np.float32))
            global_identity.append(identities)
            global_focal.append(focal.astype(np.float32))
            global_within.append(item["within"][valid_rows].astype(np.float32))
            global_amplifier.append(np.asarray(amp_factor, dtype=np.float32)[valid_rows])

        if not global_spectrum:
            raise RuntimeError(self._no_extractable_message(exposure_id, failures))
        return GlobalFiberFrame(
            spectrum=np.concatenate(global_spectrum),
            variance=np.concatenate(global_variance),
            measured_spectrum=np.concatenate(global_measured_spectrum),
            valid_fraction=np.concatenate(global_valid),
            wavelength=np.concatenate(global_wavelength),
            fiber_identity=np.concatenate(global_identity),
            focal=np.concatenate(global_focal),
            within_response=np.concatenate(global_within),
            amplifier_response=np.concatenate(global_amplifier),
            parent_ids=parent_ids,
        )

    @staticmethod
    def _build_exposure_object_mask(
        detections,
        match_table,
        catalog,
        ra0,
        dec0,
        pa,
        focal,
        *,
        radius: float,
    ) -> np.ndarray:
        """Mask fibers close to detected objects and accepted catalog counterparts."""
        centers = []
        detected = np.asarray(detections, dtype=float)
        if detected.size:
            centers.extend(detected[:, 2:4])
        matches = np.asarray(match_table, dtype=float)
        accepted = (
            matches[:, 6].astype(bool) if matches.size else np.zeros(0, dtype=bool)
        )
        if accepted.any():
            catalog_indices = matches[accepted, 1].astype(int)
            catalog_rows = np.asarray(catalog, dtype=float)[catalog_indices]
            projected = sky_to_focal_plane(
                ra0, dec0, pa, catalog_rows[:, 0], catalog_rows[:, 1]
            )
            centers.extend(
                np.column_stack(
                    (projected.get_array("focal_x"), projected.get_array("focal_y"))
                )
            )
        if not centers:
            return np.zeros(len(focal), dtype=bool)
        centers = np.asarray(centers, dtype=float).reshape((-1, 2))
        distance2 = np.sum(
            (np.asarray(focal, dtype=float)[:, None, :] - centers[None, :, :]) ** 2,
            axis=2,
        )
        return np.any(distance2 <= float(radius) ** 2, axis=1)

    def _exposure_inference_converged(self, previous, current) -> bool:
        """Return true when the bounded exposure inference has stopped changing."""
        mask_change = np.mean(previous.sky_mask != current.sky_mask)
        factors = np.asarray(current.fiber_illumination, dtype=float)
        previous_factors = np.asarray(previous.fiber_illumination, dtype=float)
        valid = (
            (factors > 0)
            & (previous_factors > 0)
            & np.isfinite(factors)
            & np.isfinite(previous_factors)
        )
        illumination_change = (
            float(
                np.nanmedian(np.abs(np.log(factors[valid] / previous_factors[valid])))
            )
            if valid.any()
            else np.inf
        )
        dra = (
            (current.ra - previous.ra)
            * np.cos(np.deg2rad((current.dec + previous.dec) / 2.0))
            * 3600.0
        )
        ddec = (current.dec - previous.dec) * 3600.0
        astrometry_change = float(np.nanmedian(np.hypot(dra, ddec)))
        return (
            mask_change
            <= float(self.params.get("exposure_inference_sky_mask_tolerance", 0.01))
            and illumination_change
            <= float(
                self.params.get("exposure_inference_log_illumination_tolerance", 0.01)
            )
            and astrometry_change
            <= float(
                self.params.get("exposure_inference_astrometry_tolerance_arcsec", 0.05)
            )
        )

    def _solve_exposure_inference(
        self, *, frame, catalog, ra0, dec0, pa, ordered_keys
    ) -> tuple[ExposureInferenceState, list[dict]]:
        """Run the small, in-memory source/sky/astrometry coordination loop."""
        spectrum, measured_spectrum, valid_fraction, wavelength = (
            frame.spectrum,
            frame.measured_spectrum,
            frame.valid_fraction,
            frame.wavelength,
        )
        focal, fiber_identity = frame.focal, frame.fiber_identity
        max_iterations = int(self.params.get("exposure_inference_max_iterations", 3))
        radius = float(
            self.params.get(
                "exposure_object_mask_radius_arcsec",
                2.0 * float(FIBER_GEOMETRY_CONFIGURATION.value["fiber_radius_arcsec"]),
            )
        )
        representative_width = float(
            np.nanmedian(np.diff(wavelength_bin_edges(wavelength), axis=1))
        )
        minimum_lsf_fwhm = float(
            self.params.get("minimum_lsf_fwhm", 2.0 * representative_width)
        )
        sampling_target = float(self.params.get("sky_samples_per_fwhm", 6.0))
        configured_oversample = self.params.get("sky_oversample")
        if configured_oversample is None:
            oversampling_factor, _ = (
                derive_sky_oversampling_factor(
                    minimum_lsf_fwhm,
                    representative_width,
                    target_samples_per_fwhm=sampling_target,
                )
            )
        else:
            oversampling_factor = max(1, int(configured_oversample))
        current_ra0, current_dec0, current_pa = float(ra0), float(dec0), float(pa)
        previous = None
        history = []
        for iteration in range(1, max(1, max_iterations) + 1):
            tan = tan_fiber_coordinates(
                current_ra0, current_dec0, current_pa, focal[:, 0], focal[:, 1]
            )
            coordinates_ra, coordinates_dec = tan.get_array("ra"), tan.get_array("dec")
            detection_spectrum = (
                spectrum if previous is None else previous.sky_subtracted
            )
            raw_detections = detect_fiber_sources(
                np.nanmedian(detection_spectrum, axis=1),
                fiber_identity[:, 2],
                focal[:, 0],
                focal[:, 1],
                threshold_sigma=float(self.params.get("detection_sigma", 5.0)),
            )
            detections = (
                np.column_stack(
                    (
                        raw_detections,
                        coordinates_ra[raw_detections[:, 0].astype(int)],
                        coordinates_dec[raw_detections[:, 0].astype(int)],
                    )
                )
                if raw_detections.size
                else np.empty((0, 8), dtype=float)
            )
            fit = fit_catalog_astrometry(detections, current_ra0, current_dec0, catalog)
            match_table, fit_parameters = fit.get_array("matches"), fit.get_array(
                "parameters"
            )
            success = bool(fit.scalars["success"])
            if not success and previous is not None:
                # Do not mix a failed refinement with an otherwise accepted solution.
                history.append(
                    {
                        "iteration": iteration,
                        "detection_count": int(detections.shape[0]),
                        "astrometry_refined": 0,
                        "retained_previous": 1,
                    }
                )
                break
            if success:
                refined_ra0 = current_ra0 + fit_parameters[0] / (
                    np.cos(np.deg2rad(current_dec0)) * 3600.0
                )
                refined_dec0 = current_dec0 + fit_parameters[1] / 3600.0
                refined_pa = current_pa + np.rad2deg(fit_parameters[2])
                refined = tan_fiber_coordinates(
                    refined_ra0, refined_dec0, refined_pa, focal[:, 0], focal[:, 1]
                )
                final_ra, final_dec = refined.get_array("ra"), refined.get_array("dec")
            else:
                refined_ra0, refined_dec0, refined_pa = (
                    current_ra0,
                    current_dec0,
                    current_pa,
                )
                final_ra, final_dec = coordinates_ra, coordinates_dec
            object_mask = self._build_exposure_object_mask(
                detections,
                match_table,
                catalog,
                refined_ra0,
                refined_dec0,
                refined_pa,
                focal,
                radius=radius,
            )
            selection = select_sky_fibers(
                spectrum, valid_fraction, source_mask=object_mask
            )
            sky_mask, broadband_flux = selection.get_array("mask"), selection.get_array(
                "broadband_flux"
            )
            incident = oversampled_incident_sky(
                wavelength,
                spectrum,
                sky_mask,
                oversample=oversampling_factor,
                minimum_lsf_fwhm=minimum_lsf_fwhm,
                target_samples_per_fwhm=sampling_target,
            )
            illumination = measure_exposure_illumination(
                broadband_flux,
                sky_mask,
                fiber_identity[:, 0].astype(int),
                len(ordered_keys),
            )
            sky_model = LatentSkyModel(
                incident.get_array("wavelength"),
                incident.get_array("flux_density"),
                incident.get_array("variance_density"),
                sampling_target=sampling_target,
                oversampling_factor=oversampling_factor,
                reference_resolution="intrinsic_without_accepted_lsf",
                integration_method=str(
                    incident.scalars["integration_method"]
                ),
            )
            subtraction = predict_and_subtract_sky(
                sky_model,
                wavelength,
                spectrum,
                sky_mask,
                illumination.get_array("fiber_factor"),
                measured_spectrum=measured_spectrum,
                normalization=(
                    frame.within_response * frame.amplifier_response
                ),
            )
            state = ExposureInferenceState(
                refined_ra0,
                refined_dec0,
                refined_pa,
                final_ra,
                final_dec,
                detections,
                match_table,
                fit_parameters,
                success,
                object_mask,
                sky_mask,
                broadband_flux,
                selection.scalars["center"],
                selection.scalars["sigma"],
                illumination.get_array("fiber_factor"),
                illumination.get_array("amplifier_factor"),
                incident.get_array("wavelength"),
                incident.get_array("flux_density"),
                incident.get_array("variance_density"),
                incident.get_array("sample_count"),
                sky_model,
                subtraction.get_array("sky_subtracted"),
                subtraction.scalars["residual_robust_sigma"],
                iteration,
            )
            converged = previous is not None and self._exposure_inference_converged(
                previous, state
            )
            history.append(
                {
                    "iteration": iteration,
                    "detection_count": int(detections.shape[0]),
                    "sky_fiber_count": int(np.sum(sky_mask)),
                    "astrometry_refined": int(success),
                    "converged": int(converged),
                }
            )
            previous = state
            current_ra0, current_dec0, current_pa = (
                refined_ra0,
                refined_dec0,
                refined_pa,
            )
            if converged:
                break
        return previous, history

    def _run_point_source_extraction(
        self,
        *,
        calibrated_state,
        exposure_scope,
        exposure_refs,
        detections,
        final_ra0,
        final_pa,
        final_dec0,
        final_artifact,
        detection_artifact,
    ):
        config = SOURCE_EXTRACTION_CONFIGURATION.value
        dar_config = DAR_SEED_CONFIGURATION.value
        fiber_radius = float(FIBER_GEOMETRY_CONFIGURATION.value["fiber_radius_arcsec"])
        beta = float(config["beta"])
        grid_half_points = int(config["grid_half_points"])
        fit_background = bool(config["fit_background"])

        flux = calibrated_state.flux
        variance = calibrated_state.variance
        mask = calibrated_state.mask
        wavelength = calibrated_state.wavelength
        focal = calibrated_state.focal_plane_coordinates

        override = self.params.get("source_position")
        source_x = source_y = None
        source_position_origin = None
        if override is not None:
            if "focal_x" in override and "focal_y" in override:
                source_x = float(override["focal_x"])
                source_y = float(override["focal_y"])
                source_position_origin = "override_focal_plane"
            elif "ra_deg" in override and "dec_deg" in override:
                converted = sky_to_focal_plane(
                    final_ra0,
                    final_dec0,
                    final_pa,
                    override["ra_deg"],
                    override["dec_deg"],
                )
                source_x = float(converted.get_array("focal_x"))
                source_y = float(converted.get_array("focal_y"))
                source_position_origin = "override_sky"
        if source_x is None and detections.size:
            brightest = int(np.argmax(detections[:, 4]))
            source_x = float(detections[brightest, 2])
            source_y = float(detections[brightest, 3])
            source_position_origin = "brightest_detection"
        if source_x is None:
            return {"status": "skipped_no_source"}

        exclusion_mask = source_extraction.select_source_fibers(
            focal[:, 0],
            focal[:, 1],
            source_x,
            source_y,
            max_distance_arcsec=float(config["max_fiber_distance_arcsec"]),
        )
        selected = ~exclusion_mask
        if int(selected.sum()) == 0:
            return {"status": "skipped_no_fibers_in_range"}

        selected_x = focal[selected, 0]
        selected_y = focal[selected, 1]
        selected_flux = flux[selected].astype(float)
        selected_variance = variance[selected].astype(float)
        selected_mask = mask[selected]
        selected_wavelength = wavelength[selected].astype(float)
        representative_wavelength = np.nanmedian(selected_wavelength, axis=0)

        dar_result = dar.dar_seed_model(
            source_wavelength=np.asarray(dar_config["source_wavelength_angstrom"]),
            source_displacement=np.asarray(dar_config["source_displacement_arcsec"]),
        )
        transform, transform_identity = dar.tan_plane_dar_transform(
            final_ra0, final_dec0, final_pa
        )
        dar_evaluation = dar.evaluate_dar_seed(
            representative_wavelength,
            cubic_coefficients=dar_result.get_array("cubic_coefficients"),
            angle_deg=float(dar_config["angle_deg"]),
            astrometric_transform=transform,
            astrometric_transform_identity=transform_identity,
            reference_ra_deg=final_ra0,
            reference_dec_deg=final_dec0,
        )
        dar_artifact = self._request(
            kind="dar_seed_model",
            scope=exposure_scope,
            components={
                "source_wavelength": _component(
                    "source_wavelength",
                    dar_result.get_array("source_wavelength"),
                    "Angstrom",
                    "wavelength_angstrom",
                ),
                "source_displacement": _component(
                    "source_displacement",
                    dar_result.get_array("source_displacement"),
                    "arcsec",
                    "wavelength_angstrom",
                ),
                "cubic_coefficients": _component(
                    "cubic_coefficients",
                    dar_result.get_array("cubic_coefficients"),
                    "1",
                    "none",
                ),
                "wavelength": _component(
                    "wavelength",
                    dar_evaluation.get_array("wavelength"),
                    "Angstrom",
                    "wavelength_angstrom",
                ),
                "delta_x": _component(
                    "delta_x",
                    dar_evaluation.get_array("delta_x"),
                    "arcsec",
                    "wavelength_angstrom",
                ),
                "delta_y": _component(
                    "delta_y",
                    dar_evaluation.get_array("delta_y"),
                    "arcsec",
                    "wavelength_angstrom",
                ),
                "delta_ra": _component(
                    "delta_ra",
                    dar_evaluation.get_array("delta_ra"),
                    "arcsec",
                    "wavelength_angstrom",
                ),
                "delta_dec": _component(
                    "delta_dec",
                    dar_evaluation.get_array("delta_dec"),
                    "arcsec",
                    "wavelength_angstrom",
                ),
            },
            parents=[int(final_artifact.id)],
            summaries={"angle_deg": float(dar_config["angle_deg"])},
            metadata={
                "source_position_origin": source_position_origin,
                "source_focal_x": source_x,
                "source_focal_y": source_y,
                "astrometric_transform_identity": transform_identity,
            },
            refs=exposure_refs,
            algorithm="virusflow.algorithms.dar.evaluate_dar_seed",
            version=dar.DAR_VERSION,
        )

        interval_count = int(config["psf_interval_count"])
        intervals = spatial_psf.build_wavelength_intervals(
            float(np.nanmin(representative_wavelength)),
            float(np.nanmax(representative_wavelength)),
            interval_count,
        )
        interval_reference_wavelength = intervals.mean(axis=1)
        interval_dar = dar.evaluate_dar_seed(
            interval_reference_wavelength,
            cubic_coefficients=dar_result.get_array("cubic_coefficients"),
            angle_deg=float(dar_config["angle_deg"]),
            astrometric_transform=transform,
            astrometric_transform_identity=transform_identity,
            reference_ra_deg=final_ra0,
            reference_dec_deg=final_dec0,
        )
        interval_delta_x = interval_dar.get_array("delta_x")
        interval_delta_y = interval_dar.get_array("delta_y")

        interval_valid = np.zeros(interval_count, dtype=bool)
        interval_centroid_x = np.zeros(interval_count, dtype=float)
        interval_centroid_y = np.zeros(interval_count, dtype=float)
        interval_fwhm = np.zeros(interval_count, dtype=float)
        interval_weight = np.zeros(interval_count, dtype=float)
        psf_artifacts = []
        for i, interval in enumerate(intervals):
            binned_flux, binned_uncertainty = (
                spatial_psf.bin_flux_by_wavelength_interval(
                    selected_wavelength,
                    selected_flux,
                    selected_variance,
                    selected_mask,
                    interval,
                )
            )
            fit_result = spatial_psf.fit_wavelength_interval_psf(
                selected_x,
                selected_y,
                fiber_radius,
                binned_flux,
                binned_uncertainty,
                seed_centroid_x=source_x + float(interval_delta_x[i]),
                seed_centroid_y=source_y + float(interval_delta_y[i]),
                wavelength_interval=interval,
                reference_wavelength=float(interval_reference_wavelength[i]),
                fwhm_bounds=tuple(config["fwhm_bounds_arcsec"]),
                search_radius_arcsec=float(config["search_radius_arcsec"]),
                beta=beta,
                fit_background=fit_background,
                grid_half_points=grid_half_points,
            )
            valid = bool(fit_result.scalars["valid"])
            dof = int(fit_result.scalars["dof"])
            chi2 = float(fit_result.scalars["chi2"])
            interval_valid[i] = valid
            interval_centroid_x[i] = float(fit_result.get_array("centroid_x"))
            interval_centroid_y[i] = float(fit_result.get_array("centroid_y"))
            interval_fwhm[i] = float(fit_result.get_array("fwhm"))
            interval_weight[i] = (
                1.0 / max(chi2 / dof, np.finfo(float).eps)
                if valid and dof > 0 and np.isfinite(chi2)
                else 0.0
            )
            status = str(fit_result.scalars["status"])
            psf_artifact = self._request(
                kind="spatial_psf_measurement",
                scope=exposure_scope,
                components={
                    "wavelength_interval_min": _component(
                        "wavelength_interval_min",
                        np.asarray([fit_result.scalars["wavelength_interval_min"]]),
                        "Angstrom",
                        "none",
                    ),
                    "wavelength_interval_max": _component(
                        "wavelength_interval_max",
                        np.asarray([fit_result.scalars["wavelength_interval_max"]]),
                        "Angstrom",
                        "none",
                    ),
                    "reference_wavelength": _component(
                        "reference_wavelength",
                        np.asarray([fit_result.scalars["reference_wavelength"]]),
                        "Angstrom",
                        "none",
                    ),
                    "centroid_x": _component(
                        "centroid_x",
                        np.asarray([fit_result.get_array("centroid_x")]),
                        "arcsec",
                        "none",
                    ),
                    "centroid_y": _component(
                        "centroid_y",
                        np.asarray([fit_result.get_array("centroid_y")]),
                        "arcsec",
                        "none",
                    ),
                    "fwhm": _component(
                        "fwhm",
                        np.asarray([fit_result.get_array("fwhm")]),
                        "arcsec",
                        "none",
                    ),
                    "beta": _component(
                        "beta", np.asarray([fit_result.scalars["beta"]]), "1", "none"
                    ),
                    "amplitude": _component(
                        "amplitude",
                        np.asarray([fit_result.get_array("amplitude")]),
                        "1e-17 response-corrected electron",
                        "none",
                    ),
                    "background": _component(
                        "background",
                        np.asarray([fit_result.get_array("background")]),
                        "1e-17 response-corrected electron",
                        "none",
                    ),
                    "covariance": _component(
                        "covariance", fit_result.get_array("covariance"), "1", "none"
                    ),
                    "chi2": _component("chi2", np.asarray([chi2]), "1", "none"),
                    "dof": _component("dof", np.asarray([dof]), "1", "none"),
                    "coverage": _component(
                        "coverage",
                        np.asarray([fit_result.scalars["coverage"]]),
                        "1",
                        "none",
                    ),
                    "fibers_used": _mask_component(
                        "fibers_used", fit_result.get_array("fibers_used")
                    ),
                    "valid": _component(
                        "valid", np.asarray([int(valid)], dtype=np.uint8), "1", "none"
                    ),
                    "status": _component(
                        "status",
                        np.asarray([SPATIAL_PSF_STATUS_CODE[status]], dtype=np.int32),
                        "1",
                        "none",
                    ),
                },
                parents=[int(dar_artifact.id)],
                summaries={
                    "valid": valid,
                    "usable_fiber_count": int(fit_result.scalars["usable_fiber_count"]),
                    "status": status,
                },
                metadata={
                    "interval_index": i,
                    "status_code_convention": SPATIAL_PSF_STATUS_CODE,
                },
                refs=exposure_refs,
                algorithm="virusflow.algorithms.spatial_psf.fit_wavelength_interval_psf",
                version=spatial_psf.PSF_FIT_VERSION,
                status="pass" if valid else "warn",
                usability="usable" if valid else "degraded",
            )
            psf_artifacts.append(psf_artifact)

        try:
            chromatic_result = spatial_psf.fit_chromatic_psf_model(
                interval_reference_wavelength,
                interval_delta_x,
                interval_delta_y,
                interval_centroid_x,
                interval_centroid_y,
                interval_fwhm,
                interval_valid,
                interval_weight,
                beta=beta,
            )
            chromatic_status = "fitted"
        except ValueError:
            fallback_fwhm = float(np.mean(config["fwhm_bounds_arcsec"]))
            chromatic_model = spatial_psf.ChromaticPSFModel(
                residual_centroid_coefficients_x=np.zeros(3, dtype=float),
                residual_centroid_coefficients_y=np.zeros(3, dtype=float),
                fwhm_coefficients=np.asarray([fallback_fwhm], dtype=float),
                valid_wavelength_min=float("inf"),
                valid_wavelength_max=float("-inf"),
                beta=beta,
            )
            chromatic_result = AlgoResult(
                kind="chromatic_psf_model",
                version=spatial_psf.CHROMATIC_PSF_VERSION,
                arrays={
                    "residual_centroid_coefficients_x": chromatic_model.residual_centroid_coefficients_x,
                    "residual_centroid_coefficients_y": chromatic_model.residual_centroid_coefficients_y,
                    "fwhm_coefficients": chromatic_model.fwhm_coefficients,
                },
                scalars={
                    "valid_wavelength_min": chromatic_model.valid_wavelength_min,
                    "valid_wavelength_max": chromatic_model.valid_wavelength_max,
                    "beta": chromatic_model.beta,
                    "fitted_interval_count": 0,
                },
                meta={"model": chromatic_model},
            )
            chromatic_status = "prior_only"
        chromatic_model = chromatic_result.meta["model"]
        chromatic_artifact = self._request(
            kind="chromatic_psf_model",
            scope=exposure_scope,
            components={
                "residual_centroid_coefficients_x": _component(
                    "residual_centroid_coefficients_x",
                    chromatic_result.get_array("residual_centroid_coefficients_x"),
                    "arcsec",
                    "none",
                ),
                "residual_centroid_coefficients_y": _component(
                    "residual_centroid_coefficients_y",
                    chromatic_result.get_array("residual_centroid_coefficients_y"),
                    "arcsec",
                    "none",
                ),
                "fwhm_coefficients": _component(
                    "fwhm_coefficients",
                    chromatic_result.get_array("fwhm_coefficients"),
                    "arcsec",
                    "none",
                ),
                "valid_wavelength_min": _component(
                    "valid_wavelength_min",
                    np.asarray([chromatic_result.scalars["valid_wavelength_min"]]),
                    "Angstrom",
                    "none",
                ),
                "valid_wavelength_max": _component(
                    "valid_wavelength_max",
                    np.asarray([chromatic_result.scalars["valid_wavelength_max"]]),
                    "Angstrom",
                    "none",
                ),
                "beta": _component(
                    "beta", np.asarray([chromatic_result.scalars["beta"]]), "1", "none"
                ),
            },
            parents=[
                int(dar_artifact.id),
                *[int(artifact.id) for artifact in psf_artifacts],
            ],
            summaries={
                "fitted_interval_count": int(
                    chromatic_result.scalars["fitted_interval_count"]
                ),
                "status": chromatic_status,
            },
            metadata={"status": chromatic_status},
            refs=exposure_refs,
            algorithm="virusflow.algorithms.spatial_psf.fit_chromatic_psf_model",
            version=spatial_psf.CHROMATIC_PSF_VERSION,
            status="pass" if chromatic_status == "fitted" else "warn",
            usability="usable" if chromatic_status == "fitted" else "degraded",
        )

        model_centroid_x, model_centroid_y, model_fwhm, model_status = (
            chromatic_model.evaluate(
                representative_wavelength,
                dar_evaluation.get_array("delta_x"),
                dar_evaluation.get_array("delta_y"),
            )
        )
        n_selected = int(selected.sum())
        n_pixel = representative_wavelength.shape[0]
        coupling = np.zeros((n_selected, n_pixel), dtype=np.float64)
        for w in range(n_pixel):
            coupling[:, w] = spatial_psf.integrate_moffat_over_apertures(
                selected_x,
                selected_y,
                fiber_radius,
                float(model_centroid_x[w]),
                float(model_centroid_y[w]),
                float(model_fwhm[w]),
                beta=beta,
                grid_half_points=grid_half_points,
            )

        flux_for_solve = selected_flux.copy()
        variance_for_solve = selected_variance.copy()
        bad = selected_mask != 0
        flux_for_solve[bad] = np.nan
        variance_for_solve[bad] = np.nan
        spectrum_result = source_extraction.extract_source_spectrum(
            flux_for_solve,
            variance_for_solve,
            coupling,
            background=fit_background,
        )
        captured_fraction = spectrum_result.get_array("captured_fraction")
        median_omitted = float(np.nanmedian(1.0 - captured_fraction))
        tolerance = float(config["omitted_coupling_tolerance"])
        extraction_status = "pass" if median_omitted <= tolerance else "warn"
        extraction_usability = "usable" if median_omitted <= tolerance else "degraded"
        design_matrix_code = np.asarray([1 if fit_background else 0], dtype=np.int32)

        extraction_components = {
            "wavelength": _component(
                "wavelength",
                representative_wavelength.astype(np.float32),
                "Angstrom",
                "wavelength_angstrom",
            ),
            "amplitude": _component(
                "amplitude",
                spectrum_result.get_array("amplitude"),
                "1e-17 response-corrected electron",
                "wavelength_angstrom",
            ),
            "variance": _component(
                "variance",
                spectrum_result.get_array("variance"),
                "1e-17 response-corrected electron",
                "wavelength_angstrom",
            ),
            "mask": _mask_component("mask", spectrum_result.get_array("mask")),
            "captured_fraction": _component(
                "captured_fraction", captured_fraction, "1", "wavelength_angstrom"
            ),
            "usable_fiber_count": _component(
                "usable_fiber_count",
                spectrum_result.get_array("usable_fiber_count"),
                "1",
                "wavelength_angstrom",
            ),
            "design_matrix_identity": _component(
                "design_matrix_identity", design_matrix_code, "1", "none"
            ),
        }
        extraction_artifact = self._request(
            kind="point_source_extraction",
            scope=exposure_scope,
            components=extraction_components,
            parents=[
                int(chromatic_artifact.id),
                int(final_artifact.id),
                int(detection_artifact.id),
            ],
            summaries={
                "median_captured_fraction": float(np.nanmedian(captured_fraction)),
                "median_omitted_fraction": median_omitted,
                "selected_fiber_count": n_selected,
            },
            metadata={
                "source_focal_x": source_x,
                "source_focal_y": source_y,
                "source_position_origin": source_position_origin,
                "omitted_coupling_tolerance": tolerance,
                "fit_background": fit_background,
                "design_matrix_identity": spectrum_result.scalars[
                    "design_matrix_identity"
                ],
                "design_matrix_code_convention": {
                    0: "columns=[coupling]",
                    1: "columns=[coupling, background]",
                },
                "chromatic_model_prior_only_fraction": float(np.mean(model_status)),
                "chromatic_model_status": chromatic_status,
            },
            refs=exposure_refs,
            algorithm="virusflow.algorithms.source_extraction.extract_source_spectrum",
            version=source_extraction.EXTRACTION_VERSION,
            status=extraction_status,
            usability=extraction_usability,
        )

        return {
            "status": "extracted",
            "artifact": extraction_artifact,
            "dar_seed_model": dar_artifact,
            "spatial_psf_measurements": tuple(psf_artifacts),
            "chromatic_psf_model": chromatic_artifact,
            "spectrum": {
                "wavelength": representative_wavelength.astype(np.float32),
                "amplitude": spectrum_result.get_array("amplitude"),
                "variance": spectrum_result.get_array("variance"),
                "mask": spectrum_result.get_array("mask"),
                "captured_fraction": captured_fraction,
            },
        }

    def run(self, inputs):
        from ..registry import database as db

        exposure_id = self.target.exposure_id
        at = self.target.at_time or _instant(exposure_id)
        raw_rows = [raw for raw in db.list_raw_files(exposure_id=exposure_id, db_path=self.ctx.resolved_raw_db_path())
                    if raw.frame_type == "sci" and raw.zipcode is not None]
        if not raw_rows:
            raise RuntimeError(f"No science inputs for Exposure {exposure_id}")
        raw_rows.sort(key=lambda row: row.zipcode.key())
        zipcodes = [row.zipcode for row in raw_rows]
        if len({zipcode.key() for zipcode in zipcodes}) != len(zipcodes):
            raise RuntimeError(f"Repeated amplifier identity in Exposure {exposure_id}")

        loader = (self.ctx.config.get("raw_frame_loader") if isinstance(self.ctx.config, dict) else None)
        representative = (loader or RawFrameLoader()).load_ref(raw_rows[0])
        header = representative.header
        self._exposure_scientific_metadata = scientific_metadata_from_header(header)
        scanned_exposure_metadata = db.get_exposure_metadata(exposure_id, db_path=self.ctx.resolved_raw_db_path()) or {}
        self._exposure_scientific_metadata["airmass"] = normalize_scientific_metadata(
            {"airmass": scanned_exposure_metadata.get("airmass")}
        )["airmass"]
        service = ArtifactService(self.ctx.db_path)
        config_root = self.ctx.config.get("configuration_root") if isinstance(self.ctx.config, dict) else None
        config = ConfigurationService(root=config_root)
        fplane_path = self.ctx.config.get("fplane_path") if isinstance(self.ctx.config, dict) else None
        fplane, fplane_ref = config.resolve_fplane(fplane_path)
        fiber_offsets_by_ifuid = {}
        fiber_refs = []
        for ifuid in sorted({zipcode.ifuid for zipcode in zipcodes}):
            fiber_offsets_by_ifuid[ifuid], fiber_ref = config.fiber_offsets(ifuid)
            fiber_refs.append(fiber_ref)
        exposure_refs = config.exposure_references() + [fplane_ref, *fiber_refs]

        calibration, failures = self._ensure_calibrations(zipcodes, at)
        complete_calibration_keys = {key for key, kinds in calibration.items()
            if all(
                kind in kinds
                for kind in (
                    *CALIBRATION_KINDS,
                    "exposure_fiber_response",
                )
            )
        }
        if not complete_calibration_keys:
            raise RuntimeError(
                self._no_extractable_message(
                    exposure_id,
                    failures,
                    reason="no amplifier has complete calibration coverage",
                )
            )
        reduced = {}

        def reduced_state(zipcode):
            """Materialize one detector state only when its CCD pair needs it."""

            key = zipcode.key()
            if key in reduced:
                return reduced[key]
            try:
                state = ReducedScienceAmplifierTask(
                    self.ctx,
                    target=SimpleNamespace(zipcode=zipcode, exposure_id=exposure_id),
                    params={"apply_calibrations": True},
                ).run(calibration.get(key, {}))["reduced_science_state"]
                reduced[key] = state
                return state
            except Exception as exc:
                failures.setdefault(key, []).append(
                    f"reduced_science_state: {type(exc).__name__}: {exc}"
                )
                return None

        groups = {}
        for zipcode in zipcodes:
            key = (zipcode.ifuslot, zipcode.ifuid, zipcode.specid, zipcode.controller)
            groups.setdefault(key, {})[zipcode.amp] = zipcode

        amp_results = {}
        wavelength_fiber_exclusions = {}
        reduction_parent_ids = []
        # An exposure_fiber_response payload is exposure-wide: its first axis
        # contains every participating amplifier.  Do not reload it in the
        # per-amplifier loop below.  Apart from repeated I/O, slices of a
        # freshly loaded full payload are views, so retaining those slices in
        # ``amp_results`` would keep one complete all-amplifier payload alive
        # for every amplifier.
        response_components: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for identity, amps in sorted(groups.items()):
            for side, pair in (("left", ("LL", "LU")), ("right", ("RU", "RL"))):
                if not all(amp in amps for amp in pair):
                    for amp in pair:
                        if amp in amps:
                            failures.setdefault(amps[amp].key(), []).append(
                                f"missing physical-CCD partner for {side}"
                            )
                    continue
                lower, upper = amps[pair[0]], amps[pair[1]]
                lower_state = reduced_state(lower)
                upper_state = reduced_state(upper)
                if (
                    any(
                        "trace_map" not in calibration.get(zipcode.key(), {})
                        for zipcode in (lower, upper)
                    )
                    or lower_state is None
                    or upper_state is None
                ):
                    failures.setdefault(lower.key(), []).append(
                        f"{side} physical CCD unavailable from calibration coverage"
                    )
                    failures.setdefault(upper.key(), []).append(
                        f"{side} physical CCD unavailable from calibration coverage"
                    )
                    reduced.pop(lower.key(), None)
                    reduced.pop(upper.key(), None)
                    continue
                try:
                    target = PhysicalCCDTarget(
                        exposure_id, lower.specid, side, lower, upper, at
                    )
                    physical_result = PhysicalCCDTask(self.ctx, target=target).run(
                        {
                            "lower_state": lower_state,
                            "upper_state": upper_state,
                            "lower_trace": calibration[lower.key()]["trace_map"],
                            "upper_trace": calibration[upper.key()]["trace_map"],
                        }
                    )
                    scatter_model = physical_result["ccd_scattered_light_model"]
                    physical_state = physical_result["physical_ccd_state"]
                except Exception as exc:
                    failures.setdefault(lower.key(), []).append(
                        f"{side} CCD: {type(exc).__name__}: {exc}"
                    )
                    failures.setdefault(upper.key(), []).append(
                        f"{side} CCD: {type(exc).__name__}: {exc}"
                    )
                    reduced.pop(lower.key(), None)
                    reduced.pop(upper.key(), None)
                    continue
                physical_image = np.asarray(
                    physical_state.scatter.get_array("scatter_subtracted_image"),
                    dtype=np.float32,
                )
                physical_variance = np.asarray(
                    physical_state.assembly.get_array("variance"), dtype=np.float32
                )
                physical_mask = np.asarray(
                    physical_state.assembly.get_array("pixel_mask"), dtype=np.uint8
                )
                scatter_model_id = int(scatter_model.id)
                reduction_parent_ids.append(scatter_model_id)
                lower, upper = amps[pair[0]], amps[pair[1]]
                lower_trace_row = calibration[lower.key()]["trace_map"]
                upper_trace_row = calibration[upper.key()]["trace_map"]
                lower_trace = service.load_component(
                    lower_trace_row, "fiber_trace_map"
                )["data"]
                upper_trace = service.load_component(
                    upper_trace_row, "fiber_trace_map"
                )["data"]

                for zipcode, trace, trace_row in (
                    (lower, lower_trace, lower_trace_row),
                    (upper, upper_trace, upper_trace_row),
                ):
                    if "wavelength_map" not in calibration.get(zipcode.key(), {}):
                        failures.setdefault(zipcode.key(), []).append(
                            "extraction unavailable from wavelength calibration coverage"
                        )
                        continue
                    if "exposure_fiber_response" not in calibration.get(
                        zipcode.key(), {}
                    ):
                        failures.setdefault(zipcode.key(), []).append(
                            "extraction unavailable from exposure fiber response coverage"
                        )
                        continue
                    image = self._amp_from_physical(physical_image, zipcode.amp)
                    variance = self._amp_from_physical(physical_variance, zipcode.amp)
                    mask = self._amp_from_physical(physical_mask, zipcode.amp)
                    extraction = extract_fractional_aperture(
                        image, variance, trace, pixel_mask=mask, width=5.0
                    )
                    normalization_row = calibration[zipcode.key()]["exposure_fiber_response"]
                    normalization_id = int(normalization_row["id"])
                    response_summary = service.describe(normalization_row)["summary"]
                    response_metadata = response_summary.get("algorithm_metadata") or {}
                    response_keys = list(response_metadata.get("amplifier_keys") or [])
                    if zipcode.key() not in response_keys:
                        failures.setdefault(zipcode.key(), []).append("exposure fiber response does not cover amplifier")
                        continue
                    response_index = response_keys.index(zipcode.key())
                    fibers_per_amp = int(response_summary.get("fibers_per_amplifier") or 0)
                    if normalization_id not in response_components:
                        response_components[normalization_id] = (
                            np.asarray(
                                service.load_component(
                                    normalization_row, "normalization"
                                )["data"],
                                dtype=np.float32,
                            ),
                            np.asarray(
                                service.load_component(
                                    normalization_row, "valid_mask"
                                )["data"],
                                dtype=np.uint8,
                            ),
                        )
                    total, total_valid = response_components[normalization_id]
                    spectrum_shape = extraction.get_array("spectrum").shape
                    start = response_index * fibers_per_amp
                    stop = start + fibers_per_amp
                    # Copy the amplifier portion.  ``amp_results`` survives
                    # until global frame assembly, and views would otherwise
                    # pin the large exposure-wide parent arrays.
                    within = total[start:stop].copy()
                    normalization_valid = total_valid[start:stop].copy()
                    if (
                        within.shape != spectrum_shape
                        or normalization_valid.shape != spectrum_shape
                    ):
                        failures.setdefault(zipcode.key(), []).append(
                            "fiber normalization or validity shape does not match extracted spectrum"
                        )
                        continue
                    wave_row = calibration[zipcode.key()]["wavelength_map"]
                    wavelength = np.asarray(
                        service.load_component(wave_row, "wavelength_map")["data"],
                        dtype=np.float32,
                    )
                    validation = validate_wavelength_rows(
                        wavelength, extraction.get_array("spectrum").shape
                    )
                    if not validation.scalars["shape_matches"]:
                        failures.setdefault(zipcode.key(), []).append(
                            "wavelength map shape does not match extracted spectrum"
                        )
                        continue
                    valid_wavelength_rows = validation.get_array("valid_rows")
                    excluded_rows = np.flatnonzero(~valid_wavelength_rows)
                    if excluded_rows.size:
                        wavelength_fiber_exclusions[zipcode.key()] = {
                            "excluded_count": int(excluded_rows.size),
                            "fiber_indices": excluded_rows.tolist(),
                            "non_finite_fiber_indices": validation.get_array(
                                "non_finite_fiber_indices"
                            ).tolist(),
                            "non_increasing_fiber_indices": validation.get_array(
                                "non_increasing_fiber_indices"
                            ).tolist(),
                            "wavelength_map_artifact_id": int(wave_row["id"]),
                        }
                    if not validation.scalars["any_valid"]:
                        failures.setdefault(zipcode.key(), []).append(
                            "wavelength calibration has no finite, strictly increasing fiber rows"
                        )
                        continue
                    within_amp_parents = [
                        scatter_model_id,
                        int(trace_row["id"]),
                        int(wave_row["id"]),
                        normalization_id,
                    ]
                    amp_results[zipcode.key()] = {
                        "zipcode": zipcode,
                        "spectrum": extraction.get_array("spectrum"),
                        "variance": extraction.get_array("variance"),
                        "valid_fraction": extraction.get_array("valid_pixel_fraction"),
                        "within": within,
                        "wavelength": wavelength,
                        "valid_wavelength_rows": valid_wavelength_rows,
                        "parent_ids": within_amp_parents,
                        "normalization_valid": normalization_valid,
                    }

                # Each reduced input is consumed by exactly one physical CCD.
                # Likewise, the physical assembly and fit are only needed to
                # make these two extracted spectra.  Drop their dense
                # detector images before proceeding to the next CCD.
                reduced.pop(lower.key(), None)
                reduced.pop(upper.key(), None)
                del (
                    physical_result,
                    physical_state,
                    scatter_model,
                    physical_image,
                    physical_variance,
                    physical_mask,
                    lower_trace,
                    upper_trace,
                )

        ordered_keys = sorted(amp_results)
        if not ordered_keys:
            raise RuntimeError(self._no_extractable_message(exposure_id, failures))
        response_rows = {
            int(calibration[key]["exposure_fiber_response"]["id"]): calibration[key]["exposure_fiber_response"]
            for key in ordered_keys
        }
        if len(response_rows) != 1:
            raise RuntimeError("extracted amplifiers do not share one exposure fiber response")
        response_row = next(iter(response_rows.values()))
        response_artifact_id = int(response_row["id"])
        # Per-amplifier copies above are the only response samples needed
        # from here on; release the exposure-wide arrays before building the
        # remaining exposure products.
        response_components.clear()
        del total, total_valid
        # The per-amplifier values stored above are already F * A * g.  Pass a
        # unit companion term solely through the existing frame assembler;
        # no median, continuum, or amplifier re-fit occurs during evaluation.
        amp_factors = [np.ones_like(amp_results[key]["within"], dtype=np.float32) for key in ordered_keys]
        exposure_scope = Scope(
            zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.EXPOSURE
        )
        response_calibration_artifact = service.get(response_artifact_id)

        frame = self._assemble_global_fiber_frame(
            ordered_keys=ordered_keys,
            amp_results=amp_results,
            amp_factors=amp_factors,
            amp_artifact_id=response_artifact_id,
            fplane=fplane,
            fiber_offsets_by_ifuid=fiber_offsets_by_ifuid,
            failures=failures,
            exposure_id=exposure_id,
        )
        spectrum = frame.spectrum
        spectrum_variance = frame.variance
        valid_fraction = frame.valid_fraction
        wavelength = frame.wavelength
        fiber_identity = frame.fiber_identity
        focal = frame.focal
        within_response = frame.within_response
        amplifier_response = frame.amplifier_response
        normalization_parent_ids = frame.parent_ids

        pointing = parse_header_pointing(header)
        ra0, dec0, pa = (
            pointing.scalars["ra0"],
            pointing.scalars["dec0"],
            pointing.scalars["pa"],
        )
        header_evidence = pointing.meta["evidence"]
        initial_rotation = tan_fiber_coordinates(
            ra0, dec0, pa, focal[:, 0], focal[:, 1]
        ).scalars["rotation"]
        initial_artifact = self._request(
            kind="initial_astrometry",
            scope=exposure_scope,
            components={
                "parameters": _component(
                    "parameters",
                    np.asarray([ra0, dec0, pa, initial_rotation]),
                    "deg",
                    "icrs",
                ),
                "header_evidence": _component(
                    "header_evidence", np.asarray([ra0, dec0, pa]), "deg", "icrs"
                ),
            },
            parents=sorted(set(reduction_parent_ids)),
            summaries={
                "fiber_count": int(spectrum.shape[0]),
                "initial_ra": ra0,
                "initial_dec": dec0,
                "initial_pa": pa,
            },
            metadata={"header_evidence": header_evidence},
            refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.tan_fiber_coordinates",
            version=ASTROMETRY_VERSION,
        )
        provider = (
            self.ctx.config.get("catalog_provider")
            if isinstance(self.ctx.config, dict)
            else None
        )
        provider = provider or PanSTARRSCSVProvider()
        catalog_error = None
        try:
            table = provider.cone_search(ra0, dec0, 9.0 / 60.0)
            catalog = (
                np.column_stack(
                    (
                        np.asarray(table["raMean"], dtype=float),
                        np.asarray(table["decMean"], dtype=float),
                        np.asarray(table["gMeanPSFMag"], dtype=float),
                    )
                )
                if len(table)
                else np.empty((0, 3), dtype=float)
            )
        except Exception as exc:
            catalog = np.empty((0, 3), dtype=float)
            catalog_error = f"{type(exc).__name__}: {exc}"
        inference, inference_history = self._solve_exposure_inference(
            frame=frame,
            catalog=catalog,
            ra0=ra0,
            dec0=dec0,
            pa=pa,
            ordered_keys=ordered_keys,
        )
        detections, match_table, fit_parameters = (
            inference.detections,
            inference.match_table,
            inference.fit_parameters,
        )
        final_ra0, final_dec0, final_pa = inference.ra0, inference.dec0, inference.pa
        final_ra, final_dec = inference.ra, inference.dec
        sky_mask, broadband_flux = inference.sky_mask, inference.broadband_flux
        sky_center, sky_sigma = inference.sky_center, inference.sky_sigma
        amp_illumination, fiber_illumination = (
            inference.amplifier_illumination,
            inference.fiber_illumination,
        )
        sky_wave, incident_sky, sky_variance, sky_counts, sky_model = (
            inference.sky_wave,
            inference.incident_sky,
            inference.sky_variance,
            inference.sky_counts,
            inference.sky_model,
        )
        sky_subtracted, residual_sigma = (
            inference.sky_subtracted,
            inference.residual_sigma,
        )
        final_rotation = tan_fiber_coordinates(
            final_ra0, final_dec0, final_pa, focal[:, 0], focal[:, 1]
        ).scalars["rotation"]
        representative_width = float(
            np.nanmedian(np.diff(wavelength_bin_edges(wavelength), axis=1))
        )
        minimum_lsf_fwhm = float(
            self.params.get("minimum_lsf_fwhm", 2.0 * representative_width)
        )
        sampling_target = float(self.params.get("sky_samples_per_fwhm", 6.0))
        oversampling_factor = sky_model.oversampling_factor
        native_samples_per_fwhm = minimum_lsf_fwhm / representative_width
        astrometry_success = inference.astrometry_success
        match_status = "pass" if astrometry_success else "warn"
        match_usability = "usable" if astrometry_success else "degraded"
        inference_metadata = {
            "exposure_inference_iteration": inference.iteration,
            "exposure_inference_history": inference_history,
        }
        detection_artifact = self._request(
            kind="source_detection_catalog",
            scope=exposure_scope,
            components={
                "detections": _component("detections", detections, "electron", "icrs")
            },
            parents=[int(initial_artifact.id), *sorted(set(reduction_parent_ids))],
            summaries={"detection_count": int(detections.shape[0])},
            metadata=inference_metadata,
            refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.detect_fiber_sources",
            version=ASTROMETRY_VERSION,
        )
        match_artifact = self._request(
            kind="catalog_match_table",
            scope=exposure_scope,
            components={
                "matches": _component("matches", match_table, "arcsec", "icrs"),
                "catalog_rows": _component("catalog_rows", catalog, "1", "icrs"),
            },
            parents=[int(initial_artifact.id), int(detection_artifact.id)],
            summaries={
                "catalog_row_count": int(catalog.shape[0]),
                "candidate_match_count": (
                    int(np.sum(match_table[:, 5])) if match_table.size else 0
                ),
                "accepted_match_count": (
                    int(np.sum(match_table[:, 6])) if match_table.size else 0
                ),
                "astrometry_refined": int(astrometry_success),
            },
            metadata={
                **inference_metadata,
                "provider": getattr(provider, "name", type(provider).__name__),
                "provider_version": getattr(provider, "version", "unknown"),
                "environmental_error": catalog_error,
                "columns": [
                    "detection_index",
                    "catalog_index",
                    "separation_arcsec",
                    "dra_arcsec",
                    "ddec_arcsec",
                    "candidate",
                    "accepted",
                    "residual_arcsec",
                    "g_mag",
                ],
            },
            refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.fit_catalog_astrometry",
            version=ASTROMETRY_VERSION,
            status=match_status,
            usability=match_usability,
        )
        accepted_residual = (
            match_table[:, 7][match_table[:, 6].astype(bool)]
            if match_table.size
            else np.array([])
        )
        final_artifact = self._request(
            kind="final_astrometry",
            scope=exposure_scope,
            components={
                "parameters": _component(
                    "parameters",
                    np.asarray([final_ra0, final_dec0, final_pa, final_rotation]),
                    "deg",
                    "icrs",
                ),
                "fit_evidence": _component(
                    "fit_evidence", fit_parameters, "arcsec", "icrs"
                ),
            },
            parents=[int(initial_artifact.id), int(match_artifact.id)],
            summaries={
                "accepted_match_count": int(accepted_residual.size),
                "residual_rms_arcsec": (
                    float(np.sqrt(np.nanmean(np.square(accepted_residual))))
                    if accepted_residual.size
                    else float("nan")
                ),
                "refined": int(astrometry_success),
            },
            metadata={
                **inference_metadata,
                "fallback": None if astrometry_success else "initial_header_tan",
                "catalog_error": catalog_error,
            },
            refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.fit_catalog_astrometry",
            version=ASTROMETRY_VERSION,
            status=match_status,
            usability=match_usability,
        )
        coordinates_artifact = self._request(
            kind="fiber_sky_coordinates",
            scope=Scope(
                zipcode=None,
                exposure_id=exposure_id,
                physical_scope=PhysicalScope.FIBER,
            ),
            components={
                "coordinates": _component(
                    "coordinates", np.column_stack((final_ra, final_dec)), "deg", "icrs"
                ),
                "fiber_identity": _component(
                    "fiber_identity", fiber_identity, "1", "none"
                ),
                "focal_plane_coordinates": _component(
                    "focal_plane_coordinates", focal, "arcsec", "none"
                ),
            },
            parents=[int(final_artifact.id)],
            summaries={"fiber_count": int(fiber_identity.shape[0])},
            metadata=inference_metadata,
            refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.tan_fiber_coordinates",
            version=ASTROMETRY_VERSION,
            status=match_status,
            usability=match_usability,
        )

        sky_mask_artifact = self._request(
            kind="sky_fiber_mask",
            scope=Scope(
                zipcode=None,
                exposure_id=exposure_id,
                physical_scope=PhysicalScope.FIBER,
            ),
            components={
                "mask": _mask_component("mask", sky_mask),
                "broadband_flux": _component(
                    "broadband_flux", broadband_flux, "electron", "none"
                ),
                "fiber_identity": _component(
                    "fiber_identity", fiber_identity, "1", "none"
                ),
            },
            parents=[
                int(coordinates_artifact.id),
                int(detection_artifact.id),
                *sorted(set(normalization_parent_ids)),
            ],
            summaries={
                "sky_fiber_count": int(sky_mask.sum()),
                "sky_ifuslot_count": int(
                    np.unique(fiber_identity[sky_mask.astype(bool), 2]).size
                ),
                "sky_broadband_center": sky_center,
                "sky_broadband_robust_sigma": sky_sigma,
            },
            metadata=inference_metadata,
            refs=exposure_refs,
            algorithm="virusflow.algorithms.sky.select_sky_fibers",
            version=SKY_VERSION,
        )
        illumination_artifact = self._request(
            kind="exposure_illumination_correction",
            scope=exposure_scope,
            components={
                "fiber_factor": _component(
                    "fiber_factor", fiber_illumination.astype(np.float32), "1", "none"
                ),
                "amplifier_factor": _component(
                    "amplifier_factor", amp_illumination.astype(np.float32), "1", "none"
                ),
                "fiber_identity": _component(
                    "fiber_identity", fiber_identity, "1", "none"
                ),
            },
            parents=[int(sky_mask_artifact.id)],
            summaries={
                "factor_median": float(np.nanmedian(amp_illumination)),
                "factor_robust_sigma": float(
                    1.4826
                    * np.nanmedian(
                        np.abs(amp_illumination - np.nanmedian(amp_illumination))
                    )
                ),
            },
            metadata=inference_metadata,
            refs=exposure_refs,
            algorithm="virusflow.algorithms.response.measure_exposure_illumination",
            version=RESPONSE_VERSION,
        )
        sky_model_artifact = self._request(
            kind="sky_model",
            scope=exposure_scope,
            components={
                "latent_wavelength": _component(
                    "latent_wavelength", sky_wave, "Angstrom", "wavelength_angstrom"
                ),
                "latent_flux_density": _component(
                    "latent_flux_density",
                    incident_sky,
                    "electron / Angstrom",
                    "wavelength_angstrom",
                ),
                "latent_variance_density": _component(
                    "latent_variance_density",
                    sky_variance,
                    "electron2 / Angstrom2",
                    "wavelength_angstrom",
                ),
                "sample_count": _component(
                    "sample_count", sky_counts, "1", "wavelength_angstrom"
                ),
                "fiber_coefficients": _component(
                    "fiber_coefficients", fiber_illumination, "1", "none"
                ),
                "fiber_identity": _component(
                    "fiber_identity", fiber_identity, "1", "none"
                ),
            },
            parents=[
                int(sky_mask_artifact.id),
                int(illumination_artifact.id),
                *sorted(set(normalization_parent_ids)),
            ],
            summaries={
                "grid_samples": int(sky_wave.size),
                "minimum_sample_count": int(np.min(sky_counts)),
                "sky_residual_robust_sigma": float(residual_sigma),
                "sampling_target_per_fwhm": sampling_target,
                "native_samples_per_fwhm": float(native_samples_per_fwhm),
                "oversampling_factor": int(oversampling_factor),
            },
            metadata={
                **inference_metadata,
                "representation": "supersampled_regular_flux_density_grid",
                "reference_resolution": sky_model.reference_resolution,
                "lsf_model_reference": None,
                "pixel_integration": sky_model.integration_method,
                "sampling_rule": "ceil(target_samples_per_fwhm / native_samples_per_fwhm)",
                "minimum_lsf_fwhm_angstrom": minimum_lsf_fwhm,
                "representative_native_pixel_width_angstrom": representative_width,
            },
            refs=exposure_refs,
            algorithm="virusflow.algorithms.sky.oversampled_incident_sky",
            version=SKY_VERSION,
        )
        baseline_artifact = self._select_or_import_baseline(service, config, at)
        baseline_artifact_id = int(baseline_artifact.id)
        baseline_wavelength = np.asarray(
            service.load_component(baseline_artifact_id, "wavelength")["data"],
            dtype=np.float32,
        )
        baseline_response = np.asarray(
            service.load_component(baseline_artifact_id, "response")["data"],
            dtype=np.float32,
        )
        baseline_uncertainty = np.asarray(
            service.load_component(baseline_artifact_id, "uncertainty")["data"],
            dtype=np.float32,
        )
        baseline_mask = np.asarray(
            service.load_component(baseline_artifact_id, "mask")["data"],
            dtype=np.uint16,
        )
        baseline_description = service.describe(baseline_artifact_id)
        baseline_metadata = baseline_description["summary"]
        baseline_relative_response(
            baseline_wavelength,
            baseline_response,
            baseline_uncertainty,
            baseline_mask,
            version=str(
                baseline_metadata.get("payload_version")
                or baseline_metadata.get("_algorithm_version")
                or "selected-baseline"
            ),
        )
        baseline_convention = baseline_metadata.get("atmospheric_content")
        if baseline_convention not in {"absorbed_unknown", "removed_with_model"}:
            raise RuntimeError(
                "Selected baseline does not declare a supported atmospheric_content convention"
            )

        def positive_header_float(keyword):
            value = header.get(keyword)
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return value if np.isfinite(value) and value > 0.0 else None

        exposure_airmass = self._exposure_scientific_metadata["airmass"]
        header_transparency = positive_header_float("TRANSPAR")
        header_mirror_illumination = positive_header_float("MILLUM")
        header_seeing = positive_header_float("SEEING")

        extinction_artifact = None
        extinction_evaluation = None
        selected_extinction_identity = None
        explicitly_selected_extinction = any(
            self.ctx.config.get(key) is not None
            for key in (
                "atmospheric_extinction_path",
                "atmospheric_extinction_artifact_id",
            )
        )
        if baseline_convention == "absorbed_unknown":
            if explicitly_selected_extinction:
                raise RuntimeError(
                    "The selected baseline has atmospheric_content=absorbed_unknown; "
                    "a separate wavelength-dependent extinction correction is forbidden"
                )
        else:
            if exposure_airmass is None or exposure_airmass <= 0.0:
                raise RuntimeError(
                    "The selected atmosphere-separated baseline requires explicit positive "
                    "AIRMASS in canonical raw scientific metadata; no default airmass is substituted"
                )
            separation = baseline_metadata.get("atmospheric_separation") or {}
            required_extinction_identity = separation.get("extinction_model_identity")
            if not required_extinction_identity:
                raise RuntimeError(
                    "Atmosphere-separated baseline is missing its extinction-model identity"
                )
            extinction_artifact = self._select_or_import_extinction_model(
                service,
                config,
                at,
                required_identity=str(required_extinction_identity),
            )
            extinction_id = int(extinction_artifact.id)
            extinction_description = service.describe(extinction_id)
            extinction_metadata = extinction_description["summary"]
            selected_extinction_identity = extinction_metadata.get("model_identity")
            extinction_wavelength = np.asarray(
                service.load_component(extinction_id, "wavelength")["data"],
                dtype=np.float32,
            )
            extinction_coefficient = np.asarray(
                service.load_component(extinction_id, "extinction_coefficient")["data"],
                dtype=np.float32,
            )
            extinction_uncertainty = np.asarray(
                service.load_component(extinction_id, "uncertainty")["data"],
                dtype=np.float32,
            )
            extinction_mask = np.asarray(
                service.load_component(extinction_id, "mask")["data"], dtype=np.uint16
            )
            atmospheric_extinction_model(
                extinction_wavelength,
                extinction_coefficient,
                extinction_uncertainty,
                extinction_mask,
                version=str(
                    extinction_metadata.get("payload_version")
                    or extinction_metadata.get("_algorithm_version")
                    or "selected-extinction-model"
                ),
            )
            extinction_evaluation = evaluate_atmospheric_extinction(
                wavelength,
                extinction_wavelength,
                extinction_coefficient,
                extinction_uncertainty,
                extinction_mask,
                airmass=exposure_airmass,
                range_policy=str(self.params.get("extinction_range_policy", "mask")),
            )

        response_model = compact_fiber_response(
            wavelength,
            within_response,
            amplifier_response,
            fiber_illumination,
            fiber_identity,
            knot_stride=int(self.params.get("response_knot_stride", 16)),
        )
        response_parents = [
            int(baseline_artifact.id),
            int(illumination_artifact.id),
            int(response_calibration_artifact.id),
            *sorted(set(normalization_parent_ids)),
        ]
        if extinction_artifact is not None:
            response_parents.append(int(extinction_artifact.id))
        response_artifact = self._request(
            kind="fiber_response_model",
            scope=exposure_scope,
            components={
                "wavelength_knots": _component(
                    "wavelength_knots",
                    response_model.wavelength_knots,
                    "Angstrom",
                    "wavelength_angstrom",
                ),
                "within_amp_knots": _component(
                    "within_amp_knots",
                    response_model.within_amp_knots,
                    "1",
                    "wavelength_angstrom",
                ),
                "amplifier_factors": _component(
                    "amplifier_factors", amplifier_response, "1", "wavelength_angstrom"
                ),
                "illumination_factors": _component(
                    "illumination_factors", fiber_illumination, "1", "none"
                ),
                "fiber_identity": _component(
                    "fiber_identity", fiber_identity, "1", "none"
                ),
            },
            parents=response_parents,
            summaries={
                "response_median": float(np.nanmedian(fiber_illumination)),
                "response_outlier_fraction": float(
                    np.mean(
                        np.abs(fiber_illumination - np.nanmedian(fiber_illumination))
                        > 5 * np.nanstd(fiber_illumination)
                    )
                ),
                "knot_count": int(response_model.wavelength_knots.shape[1]),
            },
            metadata={
                "composition": [
                    "exposure_fiber_response:F*A*g",
                    "exposure_illumination_correction",
                    *(["atmospheric_extinction_model"] if extinction_artifact else []),
                ],
                "baseline_relative_response_artifact_id": int(baseline_artifact.id),
                "baseline_application": "selected independently and applied once after sky subtraction",
                "baseline_atmospheric_content": baseline_convention,
                "atmospheric_extinction_model_artifact_id": (
                    int(extinction_artifact.id)
                    if extinction_artifact is not None
                    else None
                ),
                "atmospheric_extinction_model_identity": selected_extinction_identity,
                "exposure_airmass": exposure_airmass,
                "exposure_airmass_units": "1",
                "exposure_airmass_source": "canonical raw scientific metadata AIRMASS",
                "applied_airmass": (
                    exposure_airmass if extinction_evaluation is not None else None
                ),
                "gray_factors": {
                    "fiber_illumination_artifact_id": int(illumination_artifact.id),
                    "transparency": header_transparency,
                    "mirror_illumination": header_mirror_illumination,
                },
                "gray_factor_uncertainty": {
                    "transparency": "unavailable",
                    "mirror_illumination": "unavailable",
                },
                "seeing_measurement": header_seeing,
                "seeing_application": "retained separately; not a response factor",
                "exposure_fiber_response_artifact_id": response_artifact_id,
                "interpolation": "linear_in_wavelength",
            },
            refs=exposure_refs,
            algorithm="virusflow.algorithms.response.compact_fiber_response",
            version=RESPONSE_VERSION,
            status="warn",
            usability="degraded",
        )
        response_application = apply_relative_response(
            sky_subtracted,
            spectrum_variance,
            wavelength,
            valid_fraction,
            baseline_wavelength=baseline_wavelength,
            baseline_response=baseline_response,
            baseline_uncertainty=baseline_uncertainty,
            baseline_mask=baseline_mask,
            fiber_illumination=fiber_illumination,
            exposure_transparency=header_transparency,
            mirror_illumination=header_mirror_illumination,
            baseline_atmospheric_content=baseline_convention,
            extinction_correction=(
                extinction_evaluation.get_array("correction_factor")
                if extinction_evaluation is not None
                else None
            ),
            extinction_uncertainty=(
                extinction_evaluation.get_array("correction_uncertainty")
                if extinction_evaluation is not None
                else None
            ),
            extinction_mask=(
                extinction_evaluation.get_array("mask")
                if extinction_evaluation is not None
                else None
            ),
        )
        calibrated_flux = response_application.get_array("calibrated_flux")
        calibrated_variance = response_application.get_array("calibrated_variance")
        final_mask = response_application.get_array("mask")
        calibrated_state = CalibratedFiberState(
            exposure_id=exposure_id,
            flux=(calibrated_flux * FLUX_SCALE).astype(np.float32),
            variance=(calibrated_variance * VARIANCE_SCALE).astype(np.float32),
            mask=final_mask,
            wavelength=wavelength.astype(np.float32),
            fiber_identity=fiber_identity.astype(np.int32),
            sky_coordinates=np.column_stack((final_ra, final_dec)).astype(np.float64),
            focal_plane_coordinates=focal.astype(np.float32),
            model_artifact_ids=(
                int(sky_model_artifact.id),
                int(response_artifact.id),
                int(final_artifact.id),
                *tuple(sorted(set(reduction_parent_ids))),
            ),
            metadata={
                "sky_residual_robust_sigma": residual_sigma,
                "sky_evaluator_version": SKY_VERSION,
                "pixel_integration": sky_model.integration_method,
                "response_evidence_state": baseline_metadata.get(
                    "evidence_state", "unknown"
                ),
                "baseline_relative_response_artifact_id": int(baseline_artifact.id),
                "baseline_atmospheric_content": baseline_convention,
                "baseline_applied_count": response_application.scalars[
                    "baseline_applied_count"
                ],
                "exposure_illumination_applied_count": response_application.scalars[
                    "illumination_applied_count"
                ],
                "exposure_transparency_measurement": header_transparency,
                "exposure_transparency_application": (
                    "applied once as a separate gray factor"
                    if header_transparency is not None
                    else "not available; no transparency factor applied"
                ),
                "mirror_illumination_measurement": header_mirror_illumination,
                "mirror_illumination_application": (
                    "applied once as a separate gray factor"
                    if header_mirror_illumination is not None
                    else "not available; no mirror-illumination factor applied"
                ),
                "seeing_measurement": header_seeing,
                "seeing_application": "retained separately; not applied as a response factor",
                "exposure_airmass_measurement": exposure_airmass,
                "exposure_airmass_units": "1",
                "exposure_airmass_source": "canonical raw scientific metadata AIRMASS",
                "applied_airmass": (
                    exposure_airmass if extinction_evaluation is not None else None
                ),
                "atmospheric_extinction_model_artifact_id": (
                    int(extinction_artifact.id)
                    if extinction_artifact is not None
                    else None
                ),
                "atmospheric_extinction_model_identity": selected_extinction_identity,
                "atmospheric_extinction_applied_count": response_application.scalars[
                    "extinction_applied_count"
                ],
                "atmospheric_extinction_range_masked_count": (
                    extinction_evaluation.scalars["outside_valid_range_count"]
                    if extinction_evaluation is not None
                    else 0
                ),
                "absolute_flux_calibration": False,
                "atmospheric_correction_applied": extinction_evaluation is not None,
                "isolated_instrumental_throughput": False,
                "variance_terms": (
                    "extracted statistical variance divided by response squared; measured baseline "
                    "uncertainty added where available; imported Remedy uncertainty is unknown; "
                    "extinction-model uncertainty added where available; gray transparency and "
                    "mirror illumination are fixed because their uncertainties are unavailable; "
                    "sky-model covariance not yet added"
                ),
                "wavelength_fiber_exclusions": wavelength_fiber_exclusions,
            },
        )

        source_extraction_result = self._run_point_source_extraction(
            calibrated_state=calibrated_state,
            exposure_scope=exposure_scope,
            exposure_refs=exposure_refs,
            detections=detections,
            final_ra0=final_ra0,
            final_pa=final_pa,
            final_dec0=final_dec0,
            final_artifact=final_artifact,
            detection_artifact=detection_artifact,
        )
        if source_extraction_result.get("status") == "extracted":
            calibrated_state = replace(
                calibrated_state,
                point_source_extraction_artifact_id=int(
                    source_extraction_result["artifact"].id
                ),
                point_source_spectrum=source_extraction_result["spectrum"],
            )

        mode_classification = classify_mode_and_effective_time(
            header,
            parallel_offset_seconds=EFFECTIVE_EXPOSURE_POLICY.parallel_offset_seconds,
        )
        mode = mode_classification.scalars["mode"]
        effective_seconds = mode_classification.scalars["effective_seconds"]
        time_evidence = mode_classification.meta["time_evidence"]
        mode_artifact = self._request(
            kind="exposure_mode_classification",
            scope=exposure_scope,
            components={
                "classification": _component(
                    "classification",
                    np.asarray([1 if mode == "parallel" else 0]),
                    "1",
                    "none",
                ),
                "source_fields": _component(
                    "source_fields",
                    np.asarray(
                        [
                            time_evidence["EXPTIME"] or np.nan,
                            time_evidence["PEXPTIME"] or np.nan,
                        ]
                    ),
                    "s",
                    "none",
                ),
            },
            parents=[int(initial_artifact.id)],
            summaries={"parallel": int(mode == "parallel")},
            metadata={"mode": mode, **time_evidence},
            refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure_state.classify_mode_and_effective_time",
            version=EFFECTIVE_EXPOSURE_POLICY.version,
        )
        effective_artifact = self._request(
            kind="effective_exposure_time",
            scope=exposure_scope,
            components={
                "effective_seconds": _component(
                    "effective_seconds", np.asarray([effective_seconds]), "s", "none"
                ),
                "source_fields": _component(
                    "source_fields",
                    np.asarray(
                        [
                            time_evidence["EXPTIME"] or np.nan,
                            time_evidence["PEXPTIME"] or np.nan,
                            EFFECTIVE_EXPOSURE_POLICY.parallel_offset_seconds,
                        ]
                    ),
                    "s",
                    "none",
                ),
            },
            parents=[int(mode_artifact.id)],
            summaries={"effective_seconds": float(effective_seconds)},
            metadata={
                "mode": mode,
                "policy_version": EFFECTIVE_EXPOSURE_POLICY.version,
                **time_evidence,
            },
            refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure_state.classify_mode_and_effective_time",
            version=EFFECTIVE_EXPOSURE_POLICY.version,
        )

        coverage_result = build_completion_coverage(
            zipcodes,
            calibration,
            reduced,
            amp_results,
            failures,
            wavelength_fiber_exclusions,
            AMP_CODE,
        )
        coverage_array = coverage_result.get_array("coverage")
        identity_array = coverage_result.get_array("amplifier_identity")
        completion_status = coverage_result.scalars["status"]
        completion_usability = coverage_result.scalars["usability"]
        exposure_product_status = {"pass": 0, "warn": 0, "fail": 0, "unknown": 0}
        for row in service.adapter.list_all():
            if row.get("exposure_id") != exposure_id:
                continue
            diagnostic = service.adapter.get_diagnostics(int(row["id"])) or {}
            status_name = str(diagnostic.get("status") or "unknown")
            exposure_product_status[
                status_name if status_name in exposure_product_status else "unknown"
            ] += 1
        completion_artifact = self._request(
            kind="exposure_completion_manifest",
            scope=exposure_scope,
            components={
                "coverage": _component("coverage", coverage_array, "1", "none"),
                "amplifier_identity": _component(
                    "amplifier_identity", identity_array, "1", "none"
                ),
            },
            parents=[
                int(sky_model_artifact.id),
                int(response_artifact.id),
                int(effective_artifact.id),
                int(final_artifact.id),
            ],
            summaries={
                "raw_amplifier_count": coverage_result.scalars["raw_amplifier_count"],
                "reduced_amplifier_count": coverage_result.scalars[
                    "reduced_amplifier_count"
                ],
                "extracted_amplifier_count": coverage_result.scalars[
                    "extracted_amplifier_count"
                ],
                "ifuslot_count": coverage_result.scalars["ifuslot_count"],
                "extracted_ifuslot_count": coverage_result.scalars[
                    "extracted_ifuslot_count"
                ],
                "failed_or_missing_amplifier_count": coverage_result.scalars[
                    "failed_or_missing_amplifier_count"
                ],
                "excluded_wavelength_fiber_count": sum(
                    item["excluded_count"]
                    for item in wavelength_fiber_exclusions.values()
                ),
                "amplifier_count_with_wavelength_fiber_exclusions": len(
                    wavelength_fiber_exclusions
                ),
                "usable_product_count": exposure_product_status["pass"],
                "suspect_product_count": exposure_product_status["warn"]
                + exposure_product_status["unknown"],
                "failed_product_count": exposure_product_status["fail"],
            },
            metadata={
                "failures": failures,
                "wavelength_fiber_exclusions": wavelength_fiber_exclusions,
                "zipcode_order": [zipcode.key() for zipcode in zipcodes],
                "coverage_columns": [
                    "reduced",
                    "trace",
                    "wavelength",
                    "extracted",
                    "no_recorded_failure",
                ],
                "persistent_science_intermediates": [],
                "scratch_cleanup": "in_memory_released_after_observation_assembly",
                "point_source_extraction_status": source_extraction_result.get(
                    "status"
                ),
            },
            refs=exposure_refs,
            algorithm="virusflow.tasks.exposure.ExposureTask",
            version=self.version,
            status=completion_status,
            usability=completion_usability,
        )
        result = {
            "exposure_completion_manifest": completion_artifact,
            "initial_astrometry": initial_artifact,
            "source_detection_catalog": detection_artifact,
            "catalog_match_table": match_artifact,
            "final_astrometry": final_artifact,
            "fiber_sky_coordinates": coordinates_artifact,
            "sky_fiber_mask": sky_mask_artifact,
            "sky_model": sky_model_artifact,
            "baseline_relative_response": baseline_artifact,
            "exposure_illumination_correction": illumination_artifact,
            "fiber_response_model": response_artifact,
            "exposure_mode_classification": mode_artifact,
            "effective_exposure_time": effective_artifact,
            "exposure_fiber_response": response_calibration_artifact,
            "calibrated_fiber_state": calibrated_state,
        }
        if extinction_artifact is not None:
            result["atmospheric_extinction_model"] = extinction_artifact
        if source_extraction_result.get("status") == "extracted":
            result["dar_seed_model"] = source_extraction_result["dar_seed_model"]
            result["spatial_psf_measurements"] = source_extraction_result[
                "spatial_psf_measurements"
            ]
            result["chromatic_psf_model"] = source_extraction_result[
                "chromatic_psf_model"
            ]
            result["point_source_extraction"] = source_extraction_result["artifact"]
        return result
