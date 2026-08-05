"""Canonical full-width baseline reduction for one atomic VIRUS Exposure."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Iterable

import numpy as np

from .science import PhysicalCCDTask, ReducedScienceAmplifierTask, _SciencePublisher, _instant
from ..algorithms.astrometry import (
    ASTROMETRY_VERSION,
    detect_fiber_sources,
    fit_catalog_astrometry,
    parse_header_pointing,
    tan_fiber_coordinates,
)
from ..algorithms.completion import build_completion_coverage
from ..algorithms.exposure import CalibratedFiberState, apply_relative_response
from ..algorithms.exposure_state import classify_mode_and_effective_time
from ..algorithms.extraction import (
    EXTRACTION_VERSION,
    extract_fractional_aperture,
    validate_wavelength_rows,
)
from ..algorithms.physical_ccd import ALGORITHM_VERSION as CONTRIBUTION_CORRECTION_VERSION
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
from ..config.defaults import BASELINE_RESPONSE_CONFIGURATION, EFFECTIVE_EXPOSURE_POLICY
from ..core.scientific_metadata import scientific_metadata_from_header
from ..io import PanSTARRSCSVProvider, RawFrameLoader
from ..ontology.scopes import PhysicalScope
from ..planning.targets import PhysicalCCDTarget


AMP_CODE = {"LL": 0, "LU": 1, "RU": 2, "RL": 3}
CALIBRATION_KINDS = (
    "master_bias", "master_dark", "master_ldls", "master_arc",
    "trace_map", "wavelength_map",
)


def _component(name, value, units, coordinates, **metadata):
    array = np.asarray(value)
    if array.ndim > 2:
        raise ValueError(f"Component {name} must be flattened to at most two dimensions")
    return LogicalComponent(
        name, "array1d" if array.ndim == 1 else "array2d", array,
        units, coordinates, metadata,
    )


def _mask_component(name, value, units="1", coordinates="none", **metadata):
    return LogicalComponent(name, "mask", np.asarray(value), units, coordinates, metadata)


@dataclass(frozen=True)
class GlobalFiberFrame:
    """Exposure-wide fiber frame assembled by concatenating per-amplifier arrays."""

    spectrum: np.ndarray
    variance: np.ndarray
    valid_fraction: np.ndarray
    wavelength: np.ndarray
    fiber_identity: np.ndarray
    focal: np.ndarray
    within_response: np.ndarray
    parent_ids: list[int]


class ExposureTask(_SciencePublisher):
    name = "full_exposure"
    version = "v2"

    @staticmethod
    def _no_extractable_message(exposure_id: str, failures: dict, *, reason: str | None = None) -> str:
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
            details.append(f"{omitted} additional failure reason{'s' if omitted != 1 else ''}")
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
            kind=kind, role="reduction", components=components,
            summaries=dict(summaries or {}), metadata=dict(metadata or {}), scope=scope,
            scientific_metadata=(
                dict(getattr(self, "_exposure_scientific_metadata", {}) or {})
                if scope.exposure_id == getattr(self.target, "exposure_id", None)
                else {}
            ),
            parents=[int(value) for value in parents], validity=Validity(at, at, "exposure_identity"),
            configuration_refs=list(refs), assumptions=list(assumptions),
        )
        artifact = self._publish(request, algorithm_name=algorithm, algorithm_version=version)
        self._qa(artifact, request.summaries, status=status, usability=usability)
        return artifact

    def _ensure_calibrations(self, zipcodes, at: datetime):
        from ..performance import phase

        service = ArtifactService(self.ctx.db_path)
        available = {}
        failures = {}
        requested_keys = {zipcode.key() for zipcode in zipcodes}
        candidate_sets = [
            service.adapter.find(
                kind="amp_to_amp_normalization", zipcode=None, at_time=at, limit=None
            ),
            service.adapter.find(
                kind="amp_to_amp_normalization", zipcode=None, at_time=None, limit=None
            ),
        ]
        amp_normalization = None
        response_by_key = {}
        seen_builds = set()
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
                candidate_responses = {}
                for parent_id in service.describe(candidate)["provenance"]["parents"]:
                    row = service.adapter.get_row(int(parent_id))
                    if (
                        row is not None
                        and row.get("kind") == "within_amp_fiber_normalization"
                        and row.get("amp_key")
                    ):
                        candidate_responses[str(row["amp_key"])] = row
                if requested_keys <= set(candidate_responses):
                    amp_normalization = candidate
                    response_by_key = candidate_responses
                    break
            if amp_normalization is not None:
                break
        for zipcode in zipcodes:
            kinds = {}
            for kind in CALIBRATION_KINDS:
                with phase("calibration_selection"):
                    existing = service.select_best(
                        kind=kind, scope=Scope(zipcode=zipcode), at_time=at,
                        policy="latest_valid",
                    )
                    if existing is None:
                        existing = service.select_best(
                            kind=kind, scope=Scope(zipcode=zipcode), at_time=at,
                            policy="nearest",
                        )
                if existing is None:
                    failures.setdefault(zipcode.key(), []).append(
                        f"{kind}: missing published calibration; run 'virusflow run calibrations' first"
                    )
                    continue
                kinds[kind] = existing
            response = response_by_key.get(zipcode.key())
            if response is None:
                failures.setdefault(zipcode.key(), []).append(
                    "within_amp_fiber_normalization: absent from selected coherent "
                    "amp_to_amp_normalization calibration build; run 'virusflow run calibrations' first"
                )
            else:
                kinds["within_amp_fiber_normalization"] = response
            if amp_normalization is None:
                failures.setdefault(zipcode.key(), []).append(
                    "amp_to_amp_normalization: missing published calibration build; "
                    "run 'virusflow run calibrations' first"
                )
            else:
                kinds["amp_to_amp_normalization"] = amp_normalization
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
            if (
                required <= names
                and applicability.get("algorithm_versions")
                == cls._baseline_application_versions()
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
                int(candidate["id"]) for candidate in self._complete_baseline_candidates(service, at)
            }:
                raise RuntimeError(
                    "Configured baseline response is incomplete, inactive, inapplicable in time, "
                    "or incompatible with the current extraction/correction methods"
                )
            return service.get(int(row["id"]))

        configured_path = self.ctx.config.get("baseline_response_path")
        if configured_path is None:
            candidates = self._complete_baseline_candidates(service, at)
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
            payload["wavelength"], payload["response"], payload["uncertainty"], payload["mask"],
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
            "separate_exposure_measurements": config_value["separate_exposure_measurements"],
            "atmospheric_correction_applied": False,
            "isolated_instrumental_throughput": False,
            "absolute_flux_calibration": False,
            "uncertainty_state": "not supplied by legacy curves; NaN with mask bit 2",
            "mask_bits": {"1": "response invalid", "2": "response uncertainty unavailable"},
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
                    "wavelength", baseline.get_array("wavelength"), "Angstrom", "wavelength_angstrom"
                ),
                "response": _component(
                    "response", baseline.get_array("response"), "1", "wavelength_angstrom"
                ),
                "uncertainty": _component(
                    "uncertainty", baseline.get_array("uncertainty"), "1", "wavelength_angstrom",
                    convention="standard_deviation",
                ),
                "mask": _mask_component(
                    "mask", baseline.get_array("mask"), "1", "wavelength_angstrom",
                    bit_1="response_invalid", bit_2="uncertainty_unavailable",
                ),
            },
            summaries=dict(baseline.scalars),
            metadata=metadata,
            scope=Scope(zipcode=None, physical_scope=PhysicalScope.INSTRUMENT_EPOCH),
            validity=Validity(None, None, "legacy_remedy_instrument_epoch_unspecified"),
            configuration_refs=[reference],
            assumptions=[
                "Atmospheric extinction was not separately removed from the legacy response.",
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
    def _amp_from_physical(array, amp: str):
        data = np.asarray(array)
        half = data.shape[0] // 2
        return data[:half] if amp in {"LL", "RU"} else data[half:][::-1]

    def _assemble_global_fiber_frame(
        self, *, ordered_keys, amp_results, amp_factors, amp_artifact_id,
        fplane, fiber_offsets_by_ifuid, failures, exposure_id,
    ) -> GlobalFiberFrame:
        global_spectrum = []
        global_variance = []
        global_valid = []
        global_wavelength = []
        global_identity = []
        global_focal = []
        global_within = []
        parent_ids = [amp_artifact_id]
        amp_index_by_key = {key: index for index, key in enumerate(ordered_keys)}
        for key, amp_factor in zip(ordered_keys, amp_factors):
            item = amp_results[key]
            amp_spectrum_normalization = normalize_amplifier_spectrum(
                item["spectrum"], item["variance"], item["within"], amp_factor,
            )
            normalized = amp_spectrum_normalization.get_array("normalized_spectrum")
            normalized_variance = amp_spectrum_normalization.get_array("normalized_variance")
            zipcode = item["zipcode"]
            parent_ids.extend(item["parent_ids"])
            if zipcode.ifuslot not in fplane:
                failures.setdefault(key, []).append("IFUSLOT absent from fplane configuration")
                continue
            valid_rows = item["valid_wavelength_rows"]
            original_fiber_indices = np.flatnonzero(valid_rows)
            n_fiber = original_fiber_indices.size
            local = fiber_offsets_by_ifuid[zipcode.ifuid][zipcode.amp]
            fp_x, fp_y = fplane[zipcode.ifuslot]
            focal = local[valid_rows] + np.asarray([fp_x, fp_y])
            identities = np.column_stack((
                np.full(n_fiber, amp_index_by_key[key]),
                original_fiber_indices,
                np.full(n_fiber, int(zipcode.ifuslot)),
                np.full(n_fiber, int(zipcode.specid)),
                np.full(n_fiber, AMP_CODE[zipcode.amp]),
            )).astype(np.int32)
            global_spectrum.append(normalized[valid_rows].astype(np.float32))
            global_variance.append(normalized_variance[valid_rows].astype(np.float32))
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

        if not global_spectrum:
            raise RuntimeError(self._no_extractable_message(exposure_id, failures))
        return GlobalFiberFrame(
            spectrum=np.concatenate(global_spectrum),
            variance=np.concatenate(global_variance),
            valid_fraction=np.concatenate(global_valid),
            wavelength=np.concatenate(global_wavelength),
            fiber_identity=np.concatenate(global_identity),
            focal=np.concatenate(global_focal),
            within_response=np.concatenate(global_within),
            parent_ids=parent_ids,
        )

    def run(self, inputs):
        from ..registry import database as db

        exposure_id = self.target.exposure_id
        at = self.target.at_time or _instant(exposure_id)
        raw_rows = [
            raw for raw in db.list_raw_files(exposure_id=exposure_id, db_path=self.ctx.resolved_raw_db_path())
            if raw.frame_type == "sci" and raw.zipcode is not None
        ]
        if not raw_rows:
            raise RuntimeError(f"No science inputs for Exposure {exposure_id}")
        raw_rows.sort(key=lambda row: row.zipcode.key())
        zipcodes = [row.zipcode for row in raw_rows]
        if len({zipcode.key() for zipcode in zipcodes}) != len(zipcodes):
            raise RuntimeError(f"Repeated amplifier identity in Exposure {exposure_id}")

        loader = self.ctx.config.get("raw_frame_loader") if isinstance(self.ctx.config, dict) else None
        representative = (loader or RawFrameLoader()).load_ref(raw_rows[0])
        header = representative.header
        self._exposure_scientific_metadata = scientific_metadata_from_header(header)
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
        complete_calibration_keys = {
            key for key, kinds in calibration.items()
            if all(kind in kinds for kind in (
                *CALIBRATION_KINDS,
                "within_amp_fiber_normalization", "amp_to_amp_normalization",
            ))
        }
        if not complete_calibration_keys:
            raise RuntimeError(self._no_extractable_message(
                exposure_id, failures, reason="no amplifier has complete calibration coverage",
            ))
        reduced = {}
        for zipcode in zipcodes:
            try:
                state = ReducedScienceAmplifierTask(
                    self.ctx, target=SimpleNamespace(zipcode=zipcode, exposure_id=exposure_id),
                    params={"apply_calibrations": True},
                ).run(calibration.get(zipcode.key(), {}))["reduced_science_state"]
                reduced[zipcode.key()] = state
            except Exception as exc:
                failures.setdefault(zipcode.key(), []).append(f"reduced_science_state: {type(exc).__name__}: {exc}")

        groups = {}
        for zipcode in zipcodes:
            key = (zipcode.ifuslot, zipcode.ifuid, zipcode.specid, zipcode.controller)
            groups.setdefault(key, {})[zipcode.amp] = zipcode

        physical = {}
        for identity, amps in sorted(groups.items()):
            for side, pair in (("left", ("LL", "LU")), ("right", ("RU", "RL"))):
                if not all(amp in amps for amp in pair):
                    for amp in pair:
                        if amp in amps:
                            failures.setdefault(amps[amp].key(), []).append(f"missing physical-CCD partner for {side}")
                    continue
                lower, upper = amps[pair[0]], amps[pair[1]]
                if any(
                    "trace_map" not in calibration.get(zipcode.key(), {})
                    for zipcode in (lower, upper)
                ) or lower.key() not in reduced or upper.key() not in reduced:
                    failures.setdefault(lower.key(), []).append(f"{side} physical CCD unavailable from calibration coverage")
                    failures.setdefault(upper.key(), []).append(f"{side} physical CCD unavailable from calibration coverage")
                    continue
                try:
                    target = PhysicalCCDTarget(exposure_id, lower.specid, side, lower, upper, at)
                    result = PhysicalCCDTask(self.ctx, target=target).run({
                        "lower_state": reduced[lower.key()],
                        "upper_state": reduced[upper.key()],
                        "lower_trace": calibration[lower.key()]["trace_map"],
                        "upper_trace": calibration[upper.key()]["trace_map"],
                    })
                    physical[(identity, side)] = {
                        "model": result["ccd_scattered_light_model"],
                        "state": result["physical_ccd_state"],
                    }
                except Exception as exc:
                    failures.setdefault(lower.key(), []).append(f"{side} CCD: {type(exc).__name__}: {exc}")
                    failures.setdefault(upper.key(), []).append(f"{side} CCD: {type(exc).__name__}: {exc}")

        amp_results = {}
        wavelength_fiber_exclusions = {}
        reduction_parent_ids = []
        for identity, amps in sorted(groups.items()):
            for side, pair in (("left", ("LL", "LU")), ("right", ("RU", "RL"))):
                product = physical.get((identity, side))
                if product is None:
                    continue
                physical_state = product["state"]
                physical_image = np.asarray(physical_state.scatter.get_array("scatter_subtracted_image"), dtype=np.float32)
                physical_variance = np.asarray(physical_state.assembly.get_array("variance"), dtype=np.float32)
                physical_mask = np.asarray(physical_state.assembly.get_array("pixel_mask"), dtype=np.uint8)
                scatter_model_id = int(product["model"].id)
                reduction_parent_ids.append(scatter_model_id)
                lower, upper = amps[pair[0]], amps[pair[1]]
                lower_trace_row = calibration[lower.key()]["trace_map"]
                upper_trace_row = calibration[upper.key()]["trace_map"]
                lower_trace = service.load_component(lower_trace_row, "fiber_trace_map")["data"]
                upper_trace = service.load_component(upper_trace_row, "fiber_trace_map")["data"]

                for zipcode, trace, trace_row in (
                    (lower, lower_trace, lower_trace_row),
                    (upper, upper_trace, upper_trace_row),
                ):
                    if "wavelength_map" not in calibration.get(zipcode.key(), {}):
                        failures.setdefault(zipcode.key(), []).append(
                            "extraction unavailable from wavelength calibration coverage"
                        )
                        continue
                    image = self._amp_from_physical(physical_image, zipcode.amp)
                    variance = self._amp_from_physical(physical_variance, zipcode.amp)
                    mask = self._amp_from_physical(physical_mask, zipcode.amp)
                    extraction = extract_fractional_aperture(image, variance, trace, pixel_mask=mask, width=5.0)
                    normalization_row = calibration[zipcode.key()]["within_amp_fiber_normalization"]
                    normalization_id = int(normalization_row["id"])
                    within = np.asarray(
                        service.load_component(normalization_row, "normalization")["data"],
                        dtype=np.float32,
                    )
                    normalization_valid = np.asarray(
                        service.load_component(normalization_row, "valid_mask")["data"],
                        dtype=np.uint8,
                    )
                    spectrum_shape = extraction.get_array("spectrum").shape
                    if within.shape != spectrum_shape or normalization_valid.shape != spectrum_shape:
                        failures.setdefault(zipcode.key(), []).append(
                            "fiber normalization or validity shape does not match extracted spectrum"
                        )
                        continue
                    wave_row = calibration[zipcode.key()]["wavelength_map"]
                    wavelength = np.asarray(service.load_component(wave_row, "wavelength_map")["data"], dtype=np.float32)
                    validation = validate_wavelength_rows(wavelength, extraction.get_array("spectrum").shape)
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
                            "non_finite_fiber_indices": validation.get_array("non_finite_fiber_indices").tolist(),
                            "non_increasing_fiber_indices": validation.get_array("non_increasing_fiber_indices").tolist(),
                            "wavelength_map_artifact_id": int(wave_row["id"]),
                        }
                    if not validation.scalars["any_valid"]:
                        failures.setdefault(zipcode.key(), []).append(
                            "wavelength calibration has no finite, strictly increasing fiber rows"
                        )
                        continue
                    within_amp_parents = [
                        scatter_model_id, int(trace_row["id"]), int(wave_row["id"]),
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

        ordered_keys = sorted(amp_results)
        if not ordered_keys:
            raise RuntimeError(self._no_extractable_message(exposure_id, failures))
        amp_rows = {
            int(calibration[key]["amp_to_amp_normalization"]["id"]):
            calibration[key]["amp_to_amp_normalization"]
            for key in ordered_keys
        }
        if len(amp_rows) != 1:
            raise RuntimeError(
                "selected within-amplifier responses do not share one coherent "
                "amp_to_amp_normalization calibration build"
            )
        amp_row = next(iter(amp_rows.values()))
        amp_artifact_id = int(amp_row["id"])
        amp_description = service.describe(amp_row)
        amplifier_keys = list(
            (amp_description["summary"].get("algorithm_metadata") or {}).get(
                "amplifier_keys"
            ) or []
        )
        stored_factors = np.asarray(
            service.load_component(amp_row, "amplifier_factors")["data"], dtype=np.float32
        )
        if len(amplifier_keys) != stored_factors.size:
            raise RuntimeError(
                "selected amp_to_amp_normalization coverage metadata does not match its factors"
            )
        factor_by_key = dict(zip(amplifier_keys, stored_factors))
        missing_factors = [key for key in ordered_keys if key not in factor_by_key]
        if missing_factors:
            raise RuntimeError(
                "selected amp_to_amp_normalization does not cover extracted amplifiers: "
                + ", ".join(missing_factors)
            )
        amp_factors = np.asarray([factor_by_key[key] for key in ordered_keys], dtype=np.float32)
        if not np.all(np.isfinite(amp_factors) & (amp_factors > 0.0)):
            raise RuntimeError("selected calibration amplifier factors must be finite and positive")
        exposure_scope = Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.EXPOSURE)
        amp_artifact = service.get(amp_artifact_id)

        frame = self._assemble_global_fiber_frame(
            ordered_keys=ordered_keys, amp_results=amp_results, amp_factors=amp_factors,
            amp_artifact_id=amp_artifact_id, fplane=fplane,
            fiber_offsets_by_ifuid=fiber_offsets_by_ifuid, failures=failures,
            exposure_id=exposure_id,
        )
        spectrum = frame.spectrum
        spectrum_variance = frame.variance
        valid_fraction = frame.valid_fraction
        wavelength = frame.wavelength
        fiber_identity = frame.fiber_identity
        focal = frame.focal
        within_response = frame.within_response
        normalization_parent_ids = frame.parent_ids

        pointing = parse_header_pointing(header)
        ra0, dec0, pa = pointing.scalars["ra0"], pointing.scalars["dec0"], pointing.scalars["pa"]
        header_evidence = pointing.meta["evidence"]
        initial_tan = tan_fiber_coordinates(ra0, dec0, pa, focal[:, 0], focal[:, 1])
        initial_ra, initial_dec = initial_tan.get_array("ra"), initial_tan.get_array("dec")
        initial_rotation = initial_tan.scalars["rotation"]
        initial_artifact = self._request(
            kind="initial_astrometry", scope=exposure_scope,
            components={
                "parameters": _component("parameters", np.asarray([ra0, dec0, pa, initial_rotation]), "deg", "icrs"),
                "header_evidence": _component("header_evidence", np.asarray([ra0, dec0, pa]), "deg", "icrs"),
            },
            parents=sorted(set(reduction_parent_ids)),
            summaries={"fiber_count": int(spectrum.shape[0]), "initial_ra": ra0, "initial_dec": dec0, "initial_pa": pa},
            metadata={"header_evidence": header_evidence}, refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.tan_fiber_coordinates", version=ASTROMETRY_VERSION,
        )
        broadband = np.nanmedian(spectrum, axis=1)
        detections = detect_fiber_sources(
            broadband, fiber_identity[:, 2], focal[:, 0], focal[:, 1],
            threshold_sigma=float(self.params.get("detection_sigma", 5.0)),
        )
        if detections.size:
            indices = detections[:, 0].astype(int)
            detections = np.column_stack((detections, initial_ra[indices], initial_dec[indices]))
        else:
            detections = np.empty((0, 8), dtype=float)
        detection_artifact = self._request(
            kind="source_detection_catalog", scope=exposure_scope,
            components={"detections": _component("detections", detections, "electron", "icrs")},
            parents=[int(initial_artifact.id), *sorted(set(reduction_parent_ids))],
            summaries={"detection_count": int(detections.shape[0])}, refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.detect_fiber_sources", version=ASTROMETRY_VERSION,
        )

        provider = self.ctx.config.get("catalog_provider") if isinstance(self.ctx.config, dict) else None
        provider = provider or PanSTARRSCSVProvider()
        catalog_error = None
        try:
            table = provider.cone_search(ra0, dec0, 9.0 / 60.0)
            catalog = np.column_stack((
                np.asarray(table["raMean"], dtype=float), np.asarray(table["decMean"], dtype=float),
                np.asarray(table["gMeanPSFMag"], dtype=float),
            )) if len(table) else np.empty((0, 3), dtype=float)
        except Exception as exc:
            catalog = np.empty((0, 3), dtype=float)
            catalog_error = f"{type(exc).__name__}: {exc}"
        astrometry_fit = fit_catalog_astrometry(detections, ra0, dec0, catalog)
        match_table = astrometry_fit.get_array("matches")
        fit_parameters = astrometry_fit.get_array("parameters")
        astrometry_success = astrometry_fit.scalars["success"]
        match_status = "pass" if astrometry_success else "warn"
        match_usability = "usable" if astrometry_success else "degraded"
        match_artifact = self._request(
            kind="catalog_match_table", scope=exposure_scope,
            components={
                "matches": _component("matches", match_table, "arcsec", "icrs"),
                "catalog_rows": _component("catalog_rows", catalog, "1", "icrs"),
            },
            parents=[int(initial_artifact.id), int(detection_artifact.id)],
            summaries={
                "catalog_row_count": int(catalog.shape[0]), "candidate_match_count": int(np.sum(match_table[:, 5])) if match_table.size else 0,
                "accepted_match_count": int(np.sum(match_table[:, 6])) if match_table.size else 0,
                "astrometry_refined": int(astrometry_success),
            },
            metadata={
                "provider": getattr(provider, "name", type(provider).__name__),
                "provider_version": getattr(provider, "version", "unknown"),
                "environmental_error": catalog_error,
                "columns": ["detection_index", "catalog_index", "separation_arcsec", "dra_arcsec", "ddec_arcsec", "candidate", "accepted", "residual_arcsec", "g_mag"],
            }, refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.fit_catalog_astrometry", version=ASTROMETRY_VERSION,
            status=match_status, usability=match_usability,
        )
        if astrometry_success:
            final_ra0 = ra0 + fit_parameters[0] / (np.cos(np.deg2rad(dec0)) * 3600.0)
            final_dec0 = dec0 + fit_parameters[1] / 3600.0
            final_pa = pa + np.rad2deg(fit_parameters[2])
        else:
            final_ra0, final_dec0, final_pa = ra0, dec0, pa
        final_tan = tan_fiber_coordinates(final_ra0, final_dec0, final_pa, focal[:, 0], focal[:, 1])
        final_ra, final_dec, final_rotation = (
            final_tan.get_array("ra"), final_tan.get_array("dec"), final_tan.scalars["rotation"],
        )
        accepted_residual = match_table[:, 7][match_table[:, 6].astype(bool)] if match_table.size else np.array([])
        final_artifact = self._request(
            kind="final_astrometry", scope=exposure_scope,
            components={
                "parameters": _component("parameters", np.asarray([final_ra0, final_dec0, final_pa, final_rotation]), "deg", "icrs"),
                "fit_evidence": _component("fit_evidence", fit_parameters, "arcsec", "icrs"),
            },
            parents=[int(initial_artifact.id), int(match_artifact.id)],
            summaries={
                "accepted_match_count": int(accepted_residual.size),
                "residual_rms_arcsec": float(np.sqrt(np.nanmean(np.square(accepted_residual)))) if accepted_residual.size else float("nan"),
                "refined": int(astrometry_success),
            },
            metadata={"fallback": None if astrometry_success else "initial_header_tan", "catalog_error": catalog_error},
            refs=exposure_refs, algorithm="virusflow.algorithms.astrometry.fit_catalog_astrometry", version=ASTROMETRY_VERSION,
            status=match_status, usability=match_usability,
        )
        coordinates_artifact = self._request(
            kind="fiber_sky_coordinates", scope=Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.FIBER),
            components={
                "coordinates": _component("coordinates", np.column_stack((final_ra, final_dec)), "deg", "icrs"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
                "focal_plane_coordinates": _component("focal_plane_coordinates", focal, "arcsec", "none"),
            },
            parents=[int(final_artifact.id)], summaries={"fiber_count": int(fiber_identity.shape[0])}, refs=exposure_refs,
            algorithm="virusflow.algorithms.astrometry.tan_fiber_coordinates", version=ASTROMETRY_VERSION,
            status=match_status, usability=match_usability,
        )

        source_mask = np.zeros(spectrum.shape[0], dtype=bool)
        if detections.size:
            source_mask[detections[:, 0].astype(int)] = True
        sky_selection = select_sky_fibers(spectrum, valid_fraction, source_mask=source_mask)
        sky_mask = sky_selection.get_array("mask")
        broadband_flux = sky_selection.get_array("broadband_flux")
        sky_center, sky_sigma = sky_selection.scalars["center"], sky_selection.scalars["sigma"]
        sky_mask_artifact = self._request(
            kind="sky_fiber_mask", scope=Scope(zipcode=None, exposure_id=exposure_id, physical_scope=PhysicalScope.FIBER),
            components={
                "mask": _mask_component("mask", sky_mask),
                "broadband_flux": _component("broadband_flux", broadband_flux, "electron", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(coordinates_artifact.id), int(detection_artifact.id), *sorted(set(normalization_parent_ids))],
            summaries={
                "sky_fiber_count": int(sky_mask.sum()), "sky_ifuslot_count": int(np.unique(fiber_identity[sky_mask.astype(bool), 2]).size),
                "sky_broadband_center": sky_center, "sky_broadband_robust_sigma": sky_sigma,
            }, refs=exposure_refs, algorithm="virusflow.algorithms.sky.select_sky_fibers", version=SKY_VERSION,
        )
        representative_width = float(np.nanmedian(np.diff(wavelength_bin_edges(wavelength), axis=1)))
        minimum_lsf_fwhm = float(self.params.get("minimum_lsf_fwhm", 2.0 * representative_width))
        sampling_target = float(self.params.get("sky_samples_per_fwhm", 6.0))
        configured_oversample = self.params.get("sky_oversample")
        if configured_oversample is None:
            oversampling_factor, native_samples_per_fwhm = derive_sky_oversampling_factor(
                minimum_lsf_fwhm, representative_width,
                target_samples_per_fwhm=sampling_target,
            )
        else:
            oversampling_factor = max(1, int(configured_oversample))
            native_samples_per_fwhm = minimum_lsf_fwhm / representative_width
        incident_sky_result = oversampled_incident_sky(
            wavelength, spectrum, sky_mask, oversample=oversampling_factor,
            minimum_lsf_fwhm=minimum_lsf_fwhm,
            target_samples_per_fwhm=sampling_target,
        )
        sky_wave = incident_sky_result.get_array("wavelength")
        incident_sky = incident_sky_result.get_array("flux_density")
        sky_variance = incident_sky_result.get_array("variance_density")
        sky_counts = incident_sky_result.get_array("sample_count")

        amp_indices = fiber_identity[:, 0].astype(int)
        illumination_result = measure_exposure_illumination(broadband_flux, sky_mask, amp_indices, len(ordered_keys))
        amp_illumination = illumination_result.get_array("amplifier_factor")
        fiber_illumination = illumination_result.get_array("fiber_factor")
        illumination_artifact = self._request(
            kind="exposure_illumination_correction", scope=exposure_scope,
            components={
                "fiber_factor": _component("fiber_factor", fiber_illumination.astype(np.float32), "1", "none"),
                "amplifier_factor": _component("amplifier_factor", amp_illumination.astype(np.float32), "1", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(sky_mask_artifact.id)],
            summaries={
                "factor_median": float(np.nanmedian(amp_illumination)),
                "factor_robust_sigma": float(1.4826 * np.nanmedian(np.abs(amp_illumination - np.nanmedian(amp_illumination)))),
            },
            refs=exposure_refs, algorithm="virusflow.algorithms.response.measure_exposure_illumination", version=RESPONSE_VERSION,
        )
        sky_model = LatentSkyModel(
            sky_wave, incident_sky, sky_variance,
            sampling_target=sampling_target,
            oversampling_factor=oversampling_factor,
            reference_resolution="intrinsic_without_accepted_lsf",
        )
        sky_model_artifact = self._request(
            kind="sky_model", scope=exposure_scope,
            components={
                "latent_wavelength": _component("latent_wavelength", sky_wave, "Angstrom", "wavelength_angstrom"),
                "latent_flux_density": _component("latent_flux_density", incident_sky, "electron / Angstrom", "wavelength_angstrom"),
                "latent_variance_density": _component("latent_variance_density", sky_variance, "electron2 / Angstrom2", "wavelength_angstrom"),
                "sample_count": _component("sample_count", sky_counts, "1", "wavelength_angstrom"),
                "fiber_coefficients": _component("fiber_coefficients", fiber_illumination, "1", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(sky_mask_artifact.id), int(illumination_artifact.id), *sorted(set(normalization_parent_ids))],
            summaries={
                "grid_samples": int(sky_wave.size),
                "minimum_sample_count": int(np.min(sky_counts)),
                "sampling_target_per_fwhm": sampling_target,
                "native_samples_per_fwhm": float(native_samples_per_fwhm),
                "oversampling_factor": int(oversampling_factor),
            },
            metadata={
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
        sky_subtraction = predict_and_subtract_sky(sky_model, wavelength, spectrum, sky_mask, fiber_illumination)
        sky_subtracted = sky_subtraction.get_array("sky_subtracted")
        residual_sigma = sky_subtraction.scalars["residual_robust_sigma"]

        baseline_artifact = self._select_or_import_baseline(service, config, at)
        baseline_artifact_id = int(baseline_artifact.id)
        baseline_wavelength = np.asarray(
            service.load_component(baseline_artifact_id, "wavelength")["data"], dtype=np.float32
        )
        baseline_response = np.asarray(
            service.load_component(baseline_artifact_id, "response")["data"], dtype=np.float32
        )
        baseline_uncertainty = np.asarray(
            service.load_component(baseline_artifact_id, "uncertainty")["data"], dtype=np.float32
        )
        baseline_mask = np.asarray(
            service.load_component(baseline_artifact_id, "mask")["data"], dtype=np.uint16
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
        response_model = compact_fiber_response(
            wavelength, within_response, amp_factors, fiber_illumination,
            fiber_identity, knot_stride=int(self.params.get("response_knot_stride", 16)),
        )
        response_artifact = self._request(
            kind="fiber_response_model", scope=exposure_scope,
            components={
                "wavelength_knots": _component("wavelength_knots", response_model.wavelength_knots, "Angstrom", "wavelength_angstrom"),
                "within_amp_knots": _component("within_amp_knots", response_model.within_amp_knots, "1", "wavelength_angstrom"),
                "amplifier_factors": _component("amplifier_factors", amp_factors, "1", "none"),
                "illumination_factors": _component("illumination_factors", fiber_illumination, "1", "none"),
                "fiber_identity": _component("fiber_identity", fiber_identity, "1", "none"),
            },
            parents=[int(baseline_artifact.id), int(illumination_artifact.id), int(amp_artifact.id), *sorted(set(normalization_parent_ids))],
            summaries={
                "response_median": float(np.nanmedian(fiber_illumination)),
                "response_outlier_fraction": float(np.mean(np.abs(fiber_illumination - np.nanmedian(fiber_illumination)) > 5 * np.nanstd(fiber_illumination))),
                "knot_count": int(response_model.wavelength_knots.shape[1]),
            },
            metadata={
                "composition": ["within_amplifier_fiber_normalization", "calibration_build_amp_to_amp_normalization", "exposure_illumination_correction"],
                "baseline_relative_response_artifact_id": int(baseline_artifact.id),
                "baseline_application": "selected independently and applied once after sky subtraction",
                "calibration_build_amp_to_amp_artifact_id": amp_artifact_id,
                "interpolation": "linear_in_wavelength",
            },
            refs=exposure_refs, algorithm="virusflow.algorithms.response.compact_fiber_response", version=RESPONSE_VERSION,
            status="warn", usability="degraded",
        )
        header_transparency = header.get("TRANSPAR")
        try:
            header_transparency = float(header_transparency)
        except (TypeError, ValueError):
            header_transparency = None
        if header_transparency is not None and (
            not np.isfinite(header_transparency) or header_transparency <= 0.0
        ):
            header_transparency = None
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
                int(sky_model_artifact.id), int(response_artifact.id),
                int(final_artifact.id), *tuple(sorted(set(reduction_parent_ids))),
            ),
            metadata={
                "sky_residual_robust_sigma": residual_sigma,
                "sky_evaluator_version": SKY_VERSION,
                "pixel_integration": sky_model.integration_method,
                "response_evidence_state": baseline_metadata.get("evidence_state", "unknown"),
                "baseline_relative_response_artifact_id": int(baseline_artifact.id),
                "baseline_applied_count": response_application.scalars["baseline_applied_count"],
                "exposure_illumination_applied_count": response_application.scalars["illumination_applied_count"],
                "exposure_transparency_measurement": header_transparency,
                "exposure_transparency_application": (
                    "applied once as a separate gray factor"
                    if header_transparency is not None
                    else "not available; no transparency factor applied"
                ),
                "absolute_flux_calibration": False,
                "atmospheric_correction_applied": False,
                "isolated_instrumental_throughput": False,
                "variance_terms": (
                    "extracted statistical variance divided by response squared; measured baseline "
                    "uncertainty added where available; imported Remedy uncertainty is unknown; "
                    "transparency is treated as fixed because its uncertainty is unavailable; "
                    "sky-model covariance not yet added"
                ),
                "wavelength_fiber_exclusions": wavelength_fiber_exclusions,
            },
        )

        mode_classification = classify_mode_and_effective_time(
            header, parallel_offset_seconds=EFFECTIVE_EXPOSURE_POLICY.parallel_offset_seconds
        )
        mode = mode_classification.scalars["mode"]
        effective_seconds = mode_classification.scalars["effective_seconds"]
        time_evidence = mode_classification.meta["time_evidence"]
        mode_artifact = self._request(
            kind="exposure_mode_classification", scope=exposure_scope,
            components={
                "classification": _component("classification", np.asarray([1 if mode == "parallel" else 0]), "1", "none"),
                "source_fields": _component("source_fields", np.asarray([time_evidence["EXPTIME"] or np.nan, time_evidence["PEXPTIME"] or np.nan]), "s", "none"),
            },
            parents=[int(initial_artifact.id)],
            summaries={"parallel": int(mode == "parallel")}, metadata={"mode": mode, **time_evidence}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure_state.classify_mode_and_effective_time", version=EFFECTIVE_EXPOSURE_POLICY.version,
        )
        effective_artifact = self._request(
            kind="effective_exposure_time", scope=exposure_scope,
            components={
                "effective_seconds": _component("effective_seconds", np.asarray([effective_seconds]), "s", "none"),
                "source_fields": _component("source_fields", np.asarray([time_evidence["EXPTIME"] or np.nan, time_evidence["PEXPTIME"] or np.nan, EFFECTIVE_EXPOSURE_POLICY.parallel_offset_seconds]), "s", "none"),
            },
            parents=[int(mode_artifact.id)], summaries={"effective_seconds": float(effective_seconds)},
            metadata={"mode": mode, "policy_version": EFFECTIVE_EXPOSURE_POLICY.version, **time_evidence}, refs=exposure_refs,
            algorithm="virusflow.algorithms.exposure_state.classify_mode_and_effective_time", version=EFFECTIVE_EXPOSURE_POLICY.version,
        )

        coverage_result = build_completion_coverage(
            zipcodes, calibration, reduced, amp_results, failures, wavelength_fiber_exclusions, AMP_CODE,
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
            exposure_product_status[status_name if status_name in exposure_product_status else "unknown"] += 1
        completion_artifact = self._request(
            kind="exposure_completion_manifest", scope=exposure_scope,
            components={
                "coverage": _component("coverage", coverage_array, "1", "none"),
                "amplifier_identity": _component("amplifier_identity", identity_array, "1", "none"),
            },
            parents=[int(sky_model_artifact.id), int(response_artifact.id), int(effective_artifact.id), int(final_artifact.id)],
            summaries={
                "raw_amplifier_count": coverage_result.scalars["raw_amplifier_count"],
                "reduced_amplifier_count": coverage_result.scalars["reduced_amplifier_count"],
                "extracted_amplifier_count": coverage_result.scalars["extracted_amplifier_count"],
                "ifuslot_count": coverage_result.scalars["ifuslot_count"],
                "extracted_ifuslot_count": coverage_result.scalars["extracted_ifuslot_count"],
                "failed_or_missing_amplifier_count": coverage_result.scalars["failed_or_missing_amplifier_count"],
                "excluded_wavelength_fiber_count": sum(
                    item["excluded_count"] for item in wavelength_fiber_exclusions.values()
                ),
                "amplifier_count_with_wavelength_fiber_exclusions": len(wavelength_fiber_exclusions),
                "usable_product_count": exposure_product_status["pass"],
                "suspect_product_count": exposure_product_status["warn"] + exposure_product_status["unknown"],
                "failed_product_count": exposure_product_status["fail"],
            },
            metadata={
                "failures": failures,
                "wavelength_fiber_exclusions": wavelength_fiber_exclusions,
                "zipcode_order": [zipcode.key() for zipcode in zipcodes],
                "coverage_columns": ["reduced", "trace", "wavelength", "extracted", "no_recorded_failure"],
                "persistent_science_intermediates": [],
                "scratch_cleanup": "in_memory_released_after_observation_assembly",
            },
            refs=exposure_refs, algorithm="virusflow.tasks.exposure.ExposureTask", version=self.version,
            status=completion_status, usability=completion_usability,
        )
        return {
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
            "amp_to_amp_normalization": amp_artifact,
            "calibrated_fiber_state": calibrated_state,
        }
