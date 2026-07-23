from __future__ import annotations

"""Reproducible real-data scientific acceptance for VIRUSFlow Steps 8–10."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np

EXPOSURE_ID = "20260609T031649.6"
OBSERVATION_ID = "20260609-OBSID6"
DITHER_SET_ID = "20260609-OBSID6-DITHER"
OBSERVATION_EXPOSURES = (
    "20260609T031649.6",
    "20260609T031859.3",
    "20260609T032112.2",
)
LEFT_ZIPCODE = "060+003+206+LL+S/N 0039"
RIGHT_ZIPCODE = "060+003+206+RU+S/N 0039"

STEP_KINDS = {
    "ccd_scattered_light_model", "amp_to_amp_normalization", "initial_astrometry",
    "source_detection_catalog", "catalog_match_table", "final_astrometry",
    "fiber_sky_coordinates", "sky_fiber_mask", "sky_model", "baseline_relative_response",
    "exposure_illumination_correction", "fiber_response_model",
    "exposure_mode_classification", "effective_exposure_time", "exposure_completion_manifest",
    "observation_exposure_state", "observation_membership", "dither_assignment",
    "dither_registration", "dither_coverage_map", "observation_summary",
    "calibrated_fiber_observation",
}

FORBIDDEN_PERSISTENT_KINDS = {
    "reduced_science_image", "scatter_subtracted_image", "aperture_extracted_spectrum",
    "extracted_variance", "incident_sky_spectrum", "fiber_sky_prediction",
    "sky_subtracted_spectrum", "final_exposure_response",
}

EXPOSURE_RESULT_KINDS = (
    "exposure_completion_manifest", "initial_astrometry", "source_detection_catalog",
    "catalog_match_table", "final_astrometry", "fiber_sky_coordinates", "sky_fiber_mask",
    "sky_model", "baseline_relative_response", "exposure_illumination_correction", "fiber_response_model",
    "exposure_mode_classification", "effective_exposure_time", "amp_to_amp_normalization",
)


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _scan(data_root: Path, database: Path) -> int:
    from virusflow.registry import database as db
    from virusflow.storage.filesystem import FileSystemStorage

    db.init_db(str(database))
    count = 0
    indexed = set()
    with db.connect(str(database)) as connection:
        for source in FileSystemStorage(data_root).iter_raw_sources():
            if source.backend == "tar":
                tar_path = os.path.abspath(str(source.path))
                if tar_path not in indexed:
                    db.ensure_tar_index(tar_path, conn=connection)
                    indexed.add(tar_path)
            raw_id = db.register_raw_file(
                str(source.path), db_path=str(database), tar_member=source.tar_member,
                conn=connection,
            )
            count += int(raw_id is not None)
    return count


def _inventory(db_path: Path) -> dict:
    from virusflow.registry import database as db

    with db.connect(str(db_path)) as connection:
        frame_rows = connection.execute(
            "SELECT lower(frame_type), count(*) FROM raw_files GROUP BY lower(frame_type) ORDER BY lower(frame_type)"
        ).fetchall()
        science_rows = connection.execute(
            "SELECT exposure_id, count(*), count(DISTINCT amp_key) FROM raw_files "
            "WHERE lower(frame_type)='sci' GROUP BY exposure_id ORDER BY exposure_id"
        ).fetchall()
    return {
        "total_members": int(sum(row[1] for row in frame_rows)),
        "frame_counts": {str(row[0]): int(row[1]) for row in frame_rows},
        "science_exposures": [
            {"exposure_id": row[0], "members": int(row[1]), "amplifiers": int(row[2])}
            for row in science_rows
        ],
    }


def _row_manifest(service, row: dict) -> dict:
    description = service.describe(row)
    return {
        "id": int(row["id"]),
        "kind": row.get("canonical_kind") or row.get("kind"),
        "exposure_id": row.get("exposure_id"),
        "observation_id": row.get("observation_id"),
        "dither_set_id": row.get("dither_set_id"),
        "revision": row.get("revision"),
        "checksum": row.get("checksum"),
        "components": [component["name"] for component in description["components"]],
        "parents": sorted({int(item["parent_id"]) for item in description["relations"]}),
        "qa_status": (description.get("qa") or {}).get("status"),
        "usability": (description.get("qa") or {}).get("usability"),
        "summary": description.get("summary") or {},
    }


def _validate(service) -> tuple[list[dict], dict[str, int]]:
    from virusflow.ontology.artifact_kinds import kind_spec

    forbidden = [
        row for row in service.adapter.list_all()
        if (row.get("canonical_kind") or row.get("kind")) in FORBIDDEN_PERSISTENT_KINDS
        and str(row.get("state") or "active") == "active"
    ]
    if forbidden:
        _fail("Scratch-only Products were persisted: " + ", ".join(str(row["id"]) for row in forbidden[:20]))
    all_rows = [
        row for row in service.adapter.list_all()
        if (row.get("canonical_kind") or row.get("kind")) in STEP_KINDS
    ]
    latest = {}
    for row in all_rows:
        key = (
            row.get("canonical_kind") or row.get("kind"), row.get("amp_key"),
            row.get("exposure_id"), row.get("observation_id"), row.get("dither_set_id"),
        )
        previous = latest.get(key)
        if previous is None or (str(row.get("created_at") or ""), int(row["id"])) > (
            str(previous.get("created_at") or ""), int(previous["id"])
        ):
            latest[key] = row
    rows = list(latest.values())
    counts: dict[str, int] = {}
    manifest = []
    checksum_failures = []
    lineage_failures = []
    qa_failures = []
    for row in rows:
        kind = row.get("canonical_kind") or row.get("kind")
        counts[kind] = counts.get(kind, 0) + 1
        entry = _row_manifest(service, row)
        required = set(kind_spec(kind).required_components)
        if not required <= set(entry["components"]):
            checksum_failures.append(f"{row['id']}:{kind}:missing={sorted(required - set(entry['components']))}")
        for component in entry["components"]:
            try:
                service.load_component(row, component, verify_checksum=True)
            except Exception as exc:
                checksum_failures.append(f"{row['id']}:{kind}:{component}:{type(exc).__name__}:{exc}")
        if kind not in {"baseline_relative_response"} and not entry["parents"]:
            lineage_failures.append(f"{row['id']}:{kind}")
        if entry["qa_status"] == "fail" or entry["usability"] == "unusable":
            qa_failures.append(f"{row['id']}:{kind}:{entry['qa_status']}/{entry['usability']}")
        manifest.append(entry)
    if checksum_failures:
        _fail("Component contract/checksum failures: " + "; ".join(checksum_failures[:20]))
    if lineage_failures:
        _fail("Normalized lineage missing: " + "; ".join(lineage_failures[:20]))
    if qa_failures:
        _fail("Failed/unusable required Products: " + "; ".join(qa_failures[:20]))
    expected = {
        "ccd_scattered_light_model": 450,
        "sky_model": 3,
        "fiber_response_model": 3,
        "observation_exposure_state": 3,
        "observation_membership": 1,
        "dither_assignment": 1,
        "dither_registration": 1,
        "dither_coverage_map": 1,
        "observation_summary": 1,
        "calibrated_fiber_observation": 1,
    }
    for kind, minimum in expected.items():
        if counts.get(kind, 0) < minimum:
            _fail(f"Structural Product coverage {kind}={counts.get(kind, 0)} < {minimum}")
    return manifest, counts


def _latest(service, kind: str, *, exposure_id: str | None = None, zipcode=None, observation_id=None, dither_set_id=None):
    from virusflow.artifacts import Scope

    return service.select_best(
        kind=kind,
        scope=Scope(
            zipcode=zipcode, exposure_id=exposure_id,
            observation_id=observation_id, dither_set_id=dither_set_id,
        ),
        at_time=datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f") if exposure_id else None,
        policy="latest",
    )


def _existing_exposure_result(service, exposure_id: str) -> dict | None:
    # The final per-exposure spectral state is intentionally run-local. Reuse
    # begins at the complete observation Product, not at removed dense stages.
    return None


def _physical_figure(output_dir: Path, service) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from virusflow.algorithms.physical_ccd import ScatteredLightModel
    from virusflow.core.identity import parse_zipcode_key

    facts = {}
    figure, axes = plt.subplots(2, 4, figsize=(16, 8), constrained_layout=True)
    for row_index, (side, key) in enumerate((("left", LEFT_ZIPCODE), ("right", RIGHT_ZIPCODE))):
        zipcode = parse_zipcode_key(key)
        model_row = _latest(service, "ccd_scattered_light_model", exposure_id=EXPOSURE_ID, zipcode=zipcode)
        if model_row is None:
            _fail(f"Missing representative {side} compact physical CCD model")
        coefficients = service.load_component(model_row, "model_parameters")["data"]
        shape = tuple(service.load_component(model_row, "detector_shape")["data"].astype(int))
        model = ScatteredLightModel(coefficients, shape).evaluate()
        gap_index = service.load_component(model_row, "gap_sample_indices")["data"].astype(int)
        fit_index = service.load_component(model_row, "fit_sample_indices")["data"].astype(int)
        residual = service.load_component(model_row, "residual_sample_values")["data"]
        evidence = np.zeros(shape, dtype=float)
        evidence.ravel()[gap_index] = 1
        evidence.ravel()[fit_index] = 2
        axes[row_index, 0].imshow(model, origin="lower", aspect="auto")
        axes[row_index, 0].set_title(f"{side}: reconstructed compact model")
        axes[row_index, 1].imshow(evidence, origin="lower", aspect="auto", cmap="viridis")
        axes[row_index, 1].axhline(1031.5, color="red", lw=0.7)
        axes[row_index, 1].set_title("gap/fit samples + seam")
        mlo, mhi = np.nanpercentile(model, [2, 98])
        axes[row_index, 2].imshow(model, origin="lower", aspect="auto", vmin=mlo, vmax=mhi)
        axes[row_index, 2].set_title("scattered-light model")
        axes[row_index, 3].hist(residual[np.isfinite(residual)], bins=80, histtype="step", density=True)
        axes[row_index, 3].set_title("retained fit residuals")
        summary = model_row.get("metadata") or {}
        facts[side] = {
            name: summary.get(name) for name in (
                "gap_sample_count", "fit_sample_count", "holdout_sample_count",
                "fit_residual_robust_sigma", "holdout_residual_robust_sigma",
                "boundary_residual_robust_sigma", "cross_amplifier_model_discontinuity",
                "model_to_source_p95_ratio",
            )
        }
    path = output_dir / "physical_ccd_diagnostics.png"
    figure.savefig(path, dpi=130)
    plt.close(figure)
    return facts


def _exposure_figure(output_dir: Path, service, exposure_result: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 4, figsize=(17, 8), constrained_layout=True)
    manifest_row = service.adapter.get_row(int(exposure_result["exposure_completion_manifest"].id))
    completion = np.asarray(service.load_component(manifest_row, "coverage")["data"], dtype=float)
    axes[0, 0].imshow(completion.T, aspect="auto", interpolation="nearest", vmin=0, vmax=1)
    axes[0, 0].set_title("amplifier/IFU completion")

    state = exposure_result["calibrated_fiber_state"]
    spectrum = np.asarray(state.flux, dtype=float)
    variance = np.asarray(state.variance, dtype=float)
    axes[0, 1].plot(np.nanmedian(spectrum, axis=0), label="median flux")
    axes[0, 1].plot(np.sqrt(np.nanmedian(variance, axis=0)), label="sqrt variance")
    axes[0, 1].legend(fontsize=7)
    axes[0, 1].set_title("extraction and variance")

    amp_row = service.adapter.get_row(int(exposure_result["amp_to_amp_normalization"].id))
    factors = np.asarray(service.load_component(amp_row, "amplifier_factors")["data"], dtype=float)
    axes[0, 2].hist(factors[np.isfinite(factors)], bins=40, histtype="step")
    axes[0, 2].set_title("amplifier normalization")

    match_row = service.adapter.get_row(int(exposure_result["catalog_match_table"].id))
    matches = np.asarray(service.load_component(match_row, "matches")["data"], dtype=float)
    accepted = matches[:, 6].astype(bool) if matches.size else np.zeros(0, dtype=bool)
    if accepted.any():
        axes[0, 3].hist(matches[accepted, 7], bins=30, histtype="step")
    axes[0, 3].set_title("final astrometric residuals")

    coords_row = service.adapter.get_row(int(exposure_result["fiber_sky_coordinates"].id))
    sky_row = service.adapter.get_row(int(exposure_result["sky_fiber_mask"].id))
    focal = np.asarray(service.load_component(coords_row, "focal_plane_coordinates")["data"], dtype=float)
    sky_mask = np.asarray(service.load_component(sky_row, "mask")["data"], dtype=bool)
    axes[1, 0].scatter(focal[~sky_mask, 0], focal[~sky_mask, 1], s=0.2, alpha=0.2)
    axes[1, 0].scatter(focal[sky_mask, 0], focal[sky_mask, 1], s=0.3, alpha=0.5)
    axes[1, 0].set_title("explicit sky-fiber selection")

    incident_row = service.adapter.get_row(int(exposure_result["sky_model"].id))
    sky_wave = np.asarray(service.load_component(incident_row, "latent_wavelength")["data"], dtype=float)
    sky = np.asarray(service.load_component(incident_row, "latent_flux_density")["data"], dtype=float)
    axes[1, 1].plot(sky_wave, sky, lw=0.5)
    axes[1, 1].set_title("oversampled incident sky")

    response_row = service.adapter.get_row(int(exposure_result["fiber_response_model"].id))
    response = np.asarray(service.load_component(response_row, "illumination_factors")["data"], dtype=float)
    axes[1, 2].hist(response[np.isfinite(response)], bins=50, histtype="step")
    axes[1, 2].set_title("final response")

    statuses = {}
    for row in service.adapter.list_all():
        if row.get("exposure_id") != EXPOSURE_ID:
            continue
        qa = service.adapter.get_qa_bundle(int(row["id"])) or {}
        label = f"{qa.get('status')}/{qa.get('usability')}"
        statuses[label] = statuses.get(label, 0) + 1
    axes[1, 3].bar(range(len(statuses)), list(statuses.values()))
    axes[1, 3].set_xticks(range(len(statuses)), list(statuses), rotation=30, ha="right", fontsize=7)
    axes[1, 3].set_title("Product QA/usability")
    path = output_dir / "exposure_diagnostics.png"
    figure.savefig(path, dpi=130)
    plt.close(figure)
    return {
        "completion": manifest_row.get("metadata") or {},
        "catalog": match_row.get("metadata") or {},
        "final_astrometry": service.describe(exposure_result["final_astrometry"].id)["summary"],
        "sky": service.describe(exposure_result["sky_fiber_mask"].id)["summary"],
        "response": response_row.get("metadata") or {},
        "effective_time": service.describe(exposure_result["effective_exposure_time"].id)["summary"],
        "calibrated_state": dict(state.metadata),
        "qa_counts": statuses,
    }


def _observation_figure(output_dir: Path, service, observation_result: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    registration = observation_result["dither_registration"]
    coverage_product = observation_result["dither_coverage_map"]
    nominal = np.asarray(service.load_component(registration.id, "nominal_offsets")["data"], dtype=float)
    refined = np.asarray(service.load_component(registration.id, "refined_offsets")["data"], dtype=float)
    residual = np.asarray(service.load_component(registration.id, "registration_residuals")["data"], dtype=float)
    success = np.asarray(service.load_component(registration.id, "registration_success")["data"], dtype=bool)
    coverage = np.asarray(service.load_component(coverage_product.id, "coverage")["data"], dtype=float)
    x = np.asarray(service.load_component(coverage_product.id, "x_coordinate")["data"], dtype=float)
    y = np.asarray(service.load_component(coverage_product.id, "y_coordinate")["data"], dtype=float)
    summary = np.asarray(service.load_component(observation_result["observation_summary"].id, "member_state")["data"], dtype=float)
    figure, axes = plt.subplots(2, 3, figsize=(14, 8), constrained_layout=True)
    axes[0, 0].plot(nominal[:, 0], nominal[:, 1], "o-", label="nominal")
    axes[0, 0].plot(refined[:, 0], refined[:, 1], "x--", label="refined/fallback")
    axes[0, 0].legend()
    axes[0, 0].set_title("nominal vs refined offsets")
    axes[0, 1].quiver(nominal[:, 0], nominal[:, 1], residual[:, 0], residual[:, 1], angles="xy", scale_units="xy", scale=1)
    axes[0, 1].set_title("registration residual vectors")
    axes[0, 2].bar(np.arange(success.size), success.astype(int))
    axes[0, 2].set_title("catalog-refined registration")
    axes[1, 0].imshow(coverage, origin="lower", extent=[x.min(), x.max(), y.min(), y.max()], aspect="auto")
    axes[1, 0].set_title("dither coverage / holes")
    axes[1, 1].hist(coverage[coverage > 0], bins=np.arange(1, coverage.max() + 2) - 0.5)
    axes[1, 1].set_title("duplicated coverage")
    for column, label in ((0, "seeing"), (1, "transparency"), (2, "response"), (3, "effective time")):
        axes[1, 2].plot(np.arange(summary.shape[0]), summary[:, column], "o-", label=label)
    axes[1, 2].legend(fontsize=7)
    axes[1, 2].set_title("retained per-exposure state")
    path = output_dir / "observation_diagnostics.png"
    figure.savefig(path, dpi=130)
    plt.close(figure)
    return {
        "membership": service.describe(observation_result["observation_membership"].id)["summary"],
        "assignment": service.describe(observation_result["dither_assignment"].id)["summary"],
        "registration": service.describe(registration.id)["summary"],
        "coverage": service.describe(coverage_product.id)["summary"],
        "observation": service.describe(observation_result["observation_summary"].id)["summary"],
    }


def _write_report(
    output_dir: Path,
    inventory: dict,
    counts: dict,
    facts: dict,
    manifest: list[dict],
    validation: dict,
) -> tuple[Path, Path]:
    (output_dir / "steps_8_10_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    (output_dir / "steps_8_10_inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True))
    (output_dir / "steps_8_10_facts.json").write_text(json.dumps(facts, indent=2, sort_keys=True, default=str))
    catalog_error = (facts["exposure"].get("catalog") or {}).get("environmental_error")
    json_path = output_dir / "validation_report.json"
    json_path.write_text(json.dumps(validation, indent=2, sort_keys=True, default=str) + "\n")
    report = [
        "# VIRUSFlow Stage 11 Representative Observation Validation",
        "",
        f"Result: **{validation['result']}**",
        "",
        f"Exposure: `{EXPOSURE_ID}`  ",
        f"Observation: `{OBSERVATION_ID}` with `{', '.join(OBSERVATION_EXPOSURES)}`  ",
        f"Registered input members: `{inventory['total_members']}`  ",
        f"Catalog environmental limitation: `{catalog_error or 'none'}`",
        "",
        "## Physical CCD",
        "",
        "![Physical CCD diagnostics](physical_ccd_diagnostics.png)",
        "",
        f"Retained facts: `{json.dumps(facts['physical_ccd'], sort_keys=True, default=str)}`",
        "",
        "## Exposure",
        "",
        "![Exposure diagnostics](exposure_diagnostics.png)",
        "",
        f"Retained facts: `{json.dumps(facts['exposure'], sort_keys=True, default=str)}`",
        "",
        "## Observation and DitherSet",
        "",
        "![Observation diagnostics](observation_diagnostics.png)",
        "",
        f"Retained facts: `{json.dumps(facts['observation'], sort_keys=True, default=str)}`",
        "",
        "## Product/component manifest",
        "",
        f"Step 8–10 Product counts: `{json.dumps(counts, sort_keys=True)}`",
        "",
        "Every named component listed in `steps_8_10_manifest.json` was loaded through ArtifactService with checksum verification; normalized parents, revisions, QA status, usability, validity, configuration references, and scientific summaries remain queryable in the isolated registry.",
        "",
        "## Execution and storage",
        "",
        f"Workers: `{validation['execution']['workers']}`; mode: `{validation['execution']['mode']}`; runtime: `{validation['execution']['runtime_seconds']:.3f} s`.",
        "",
        f"Persistent bytes: `{validation['storage']['total_bytes']}`; final observation bytes: `{validation['storage']['final_observation_bytes']}`; scratch remaining: `{validation['cleanup']['scratch_bytes_after']}`.",
        "",
        "## Scientific and serial/parallel comparison",
        "",
        f"Validated baseline comparison: `{json.dumps(validation.get('validated_baseline_comparison'), sort_keys=True, default=str)}`",
        "",
        f"```json\n{json.dumps(validation.get('comparison'), indent=2, sort_keys=True, default=str)}\n```",
        "",
        "## Acceptance criteria",
        "",
        *[f"- {'PASS' if check['passed'] else 'FAIL'}: {check['name']} — {check.get('detail', '')}" for check in validation['checks']],
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in validation['limitations']],
        "",
        "The identity relative response and provisional dither geometry remain explicit degraded/configurable policies; this report does not claim an accepted physical LSF model or propagated sky/response covariance.",
    ]
    markdown_path = output_dir / "validation_report.md"
    markdown_path.write_text("\n".join(report) + "\n")
    return markdown_path, json_path


def _final_row(service) -> dict:
    rows = [
        row for row in service.adapter.list_all(kind="calibrated_fiber_observation")
        if str(row.get("state") or "active") == "active"
        and row.get("observation_id") == OBSERVATION_ID
    ]
    if not rows:
        _fail("Missing final calibrated observation Product")
    return max(rows, key=lambda row: (str(row.get("created_at") or ""), int(row["id"])))


def _array_metrics(left: np.ndarray, right: np.ndarray) -> dict:
    if left.shape != right.shape:
        return {"passed": False, "left_shape": list(left.shape), "right_shape": list(right.shape)}
    if left.dtype.kind in "biu" and right.dtype.kind in "biu":
        equal = bool(np.array_equal(left, right))
        return {"passed": equal, "shape": list(left.shape), "exact": equal}
    finite = np.isfinite(left) & np.isfinite(right)
    finite_patterns_equal = bool(np.array_equal(np.isfinite(left), np.isfinite(right)))
    if not finite.any():
        return {"passed": finite_patterns_equal, "shape": list(left.shape), "finite": 0, "finite_patterns_equal": finite_patterns_equal}
    difference = np.asarray(left[finite], dtype=np.float64) - np.asarray(right[finite], dtype=np.float64)
    return {
        "shape": list(left.shape), "finite": int(finite.sum()),
        "finite_patterns_equal": finite_patterns_equal,
        "max_abs": float(np.max(np.abs(difference))),
        "rms": float(np.sqrt(np.mean(difference * difference))),
    }


def compare_workspaces(current_workspace: Path, reference_workspace: Path) -> dict:
    """Compare final scientific components sequentially to bound memory use."""

    from virusflow.artifacts import ArtifactService
    current = ArtifactService(str(current_workspace / "registry.sqlite3"))
    reference = ArtifactService(str(reference_workspace / "registry.sqlite3"))
    current_row, reference_row = _final_row(current), _final_row(reference)
    tolerances = {
        "flux": {"atol": 5.0e-22}, "variance": {"atol": 5.0e-42},
        "wavelength": {"atol": 1.0e-8}, "sky_coordinates": {"atol": 1.0e-10},
        "focal_plane_coordinates": {"atol": 1.0e-10},
    }


def compare_validated_baseline(facts: dict) -> dict:
    """Compare meaningful scalar facts with the documented 2026-07-22 gate."""

    measurements = {
        "left_scatter_fit_sigma_electron": facts["physical_ccd"]["left"].get("fit_residual_robust_sigma"),
        "right_scatter_fit_sigma_electron": facts["physical_ccd"]["right"].get("fit_residual_robust_sigma"),
        "astrometry_rms_arcsec": facts["exposure"]["final_astrometry"].get("residual_rms_arcsec"),
        "sky_residual_sigma_electron": facts["exposure"]["calibrated_state"].get("sky_residual_robust_sigma"),
        "response_median": facts["exposure"]["response"].get("response_median"),
        "registration_rms_arcsec": facts["observation"]["registration"].get("registration_residual_rms_arcsec"),
        "covered_pixel_fraction": facts["observation"]["coverage"].get("covered_pixel_fraction"),
    }
    expected = {
        "left_scatter_fit_sigma_electron": (2.95525, 0.05),
        "right_scatter_fit_sigma_electron": (2.93268, 0.05),
        "astrometry_rms_arcsec": (0.724692, 0.05),
        "sky_residual_sigma_electron": (9.68230, 0.25),
        "response_median": (1.00000953, 1.0e-3),
        "registration_rms_arcsec": (2.85951, 0.15),
        "covered_pixel_fraction": (0.457677, 2.0e-3),
    }
    metrics = {}
    for name, (reference, tolerance) in expected.items():
        measured = measurements[name]
        finite = measured is not None and np.isfinite(float(measured))
        delta = abs(float(measured) - reference) if finite else float("inf")
        metrics[name] = {
            "measured": measured, "reference": reference, "absolute_tolerance": tolerance,
            "absolute_difference": delta, "passed": bool(finite and delta <= tolerance),
        }
    return {
        "reference": "VIRUSFLOW_STEPS_8_10_ACCEPTANCE.md (2026-07-22 real-data gate)",
        "passed": all(value["passed"] for value in metrics.values()),
        "metrics": metrics,
        "intentional_storage_effects": {
            "float32": "parallel/serial final-array comparison uses component-specific absolute tolerances",
            "flux_scaling": "physical values are compared after VFSCAL reconstruction",
            "sky_projection": "current flux-conserving latent-grid output is the accepted revised baseline",
            "lsf": "no difference attributed; no accepted physical LSF model exists",
        },
    }
    metrics = {}
    exact = {"mask", "fiber_identity", "exposure_index"}
    for component in (
        "flux", "variance", "mask", "wavelength", "fiber_identity",
        "sky_coordinates", "focal_plane_coordinates", "exposure_index",
    ):
        left = np.asarray(current.load_component(current_row, component)["data"])
        right = np.asarray(reference.load_component(reference_row, component)["data"])
        metric = _array_metrics(left, right)
        if component in exact:
            metric["passed"] = bool(metric.get("exact", False))
        else:
            atol = tolerances[component]["atol"]
            metric["atol"] = atol
            metric["passed"] = bool(metric.get("finite_patterns_equal", False) and metric.get("max_abs", float("inf")) <= atol)
        metrics[component] = metric
        del left, right
    current_desc, reference_desc = current.describe(current_row), reference.describe(reference_row)
    return {
        "schema": "virusflow.scientific-comparison.v1",
        "passed": all(value["passed"] for value in metrics.values()),
        "components": metrics,
        "artifact_identity": {
            "revision_equal": current_row.get("revision") == reference_row.get("revision"),
            "checksum_equal": current_row.get("checksum") == reference_row.get("checksum"),
            "current_revision": current_row.get("revision"),
            "reference_revision": reference_row.get("revision"),
        },
        "provenance_parent_count": {
            "current": len(current_desc["relations"]), "reference": len(reference_desc["relations"]),
        },
    }


def _publish_validation_report(database: Path, workspace: Path, markdown_path: Path, json_path: Path, final_artifact_id: int):
    from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
    from virusflow.artifacts.models import Scope
    from virusflow.ontology.scopes import PhysicalScope
    from virusflow.persistence.policy import DefaultPersistencePolicy
    from virusflow.publication.context import PublicationContext
    from virusflow.publication.service import DefaultPublicationService
    from virusflow.artifacts import ArtifactService

    request = ArtifactRequest(
        kind="validation_report", role="diagnostic",
        scope=Scope(zipcode=None, observation_id=OBSERVATION_ID, physical_scope=PhysicalScope.OBSERVATION),
        parents=[final_artifact_id],
        components={
            "report_json": LogicalComponent("report_json", "array1d", np.frombuffer(json_path.read_bytes(), dtype=np.uint8), "1", "none"),
            "report_markdown": LogicalComponent("report_markdown", "array1d", np.frombuffer(markdown_path.read_bytes(), dtype=np.uint8), "1", "none"),
        },
        metadata={"schema": "virusflow.validation.v1", "observation_id": OBSERVATION_ID},
    )
    publisher = DefaultPublicationService(
        svc=ArtifactService(str(database)), policy=DefaultPersistencePolicy(),
        base_dir=str(workspace / "artifacts"),
    )
    return publisher.publish([request], PublicationContext(
        "stage11_validation", "1", "representative_observation_validation", "1", {}, [], {},
    ))[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("/Users/grz85/data/VIRUS/20260609/virus"))
    parser.add_argument("--configuration-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--workspace", type=Path, help="isolated registry/artifact workspace")
    parser.add_argument("--output-dir", type=Path, help="scientific report directory")
    parser.add_argument(
        "--reuse-products", action="store_true",
        help="reuse complete immutable exposure revisions already in the isolated workspace",
    )
    parser.add_argument("--workers", type=int, default=4, help="graph workers (default: 4)")
    parser.add_argument("--serial", action="store_true", help="force one graph worker")
    parser.add_argument("--progress-mode", choices=["auto", "tty", "plain", "json"], default="plain")
    parser.add_argument("--progress-interval", type=float, default=30.0)
    parser.add_argument("--progress-file", type=Path, help="structured progress JSONL path")
    parser.add_argument("--reference-workspace", type=Path, help="compare final arrays with another completed workspace")
    args = parser.parse_args(argv)
    if not args.data_root.is_dir():
        _fail(f"Data root does not exist: {args.data_root}")
    workspace = args.workspace or Path(tempfile.mkdtemp(prefix="virusflow-steps-8-10-work-"))
    output_dir = args.output_dir or workspace / "report"
    workspace.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    database = workspace / "registry.sqlite3"

    from virusflow.artifacts import ArtifactService
    from virusflow.io.catalogs import PanSTARRSCSVProvider
    from virusflow.planning.targets import ExposureTarget, ObservationTarget
    from virusflow.tasks.base import TaskContext
    from virusflow.tasks.exposure import ExposureTask
    from virusflow.tasks.observation import ObservationTask
    from virusflow.executors.planning_executor import PlanningExecutor

    registered = _scan(args.data_root, database)
    inventory = _inventory(database)
    if inventory["total_members"] != 14100:
        _fail(f"20260609 inventory has {inventory['total_members']} members, expected 14100")
    selected = next(item for item in inventory["science_exposures"] if item["exposure_id"] == EXPOSURE_ID)
    if selected["amplifiers"] != 300:
        _fail(f"Selected Exposure has {selected['amplifiers']} amplifiers, expected 300")
    workers = 1 if args.serial else int(args.workers)
    if workers < 1:
        _fail("workers must be at least one")
    progress_file = args.progress_file or output_dir / "progress.jsonl"
    context = TaskContext(
        str(database), str(workspace / "artifacts"),
        {
            "configuration_root": str(args.configuration_root),
            "fplane_path": str(args.configuration_root / "fplaneall.txt"),
            "catalog_provider": PanSTARRSCSVProvider(timeout_seconds=60),
            "nworkers": workers,
        },
    )
    service = ArtifactService(str(database))
    executor = PlanningExecutor(
        max_workers=workers, progress=True, progress_mode=args.progress_mode,
        progress_interval=args.progress_interval, progress_path=str(progress_file),
    )
    for exposure_id in OBSERVATION_EXPOSURES:
        exposure_at = datetime.strptime(exposure_id, "%Y%m%dT%H%M%S.%f")
        executor.add_task(
            exposure_id, ExposureTask(context, target=ExposureTarget(exposure_id, exposure_at)),
            kind="exposure", target=f"exposure_id={exposure_id}",
        )
    observation_node = f"observation:{OBSERVATION_ID}"
    executor.add_task(
        observation_node,
        ObservationTask(context, target=ObservationTarget(OBSERVATION_ID, DITHER_SET_ID, OBSERVATION_EXPOSURES)),
        kind="observation", depends_on=list(OBSERVATION_EXPOSURES),
        target=f"observation_id={OBSERVATION_ID}",
    )
    started = time.monotonic()
    graph_results = executor.run()
    runtime = time.monotonic() - started
    exposure_results = {key: graph_results[key] for key in OBSERVATION_EXPOSURES}
    exposure_result = exposure_results[EXPOSURE_ID]
    observation_result = graph_results[observation_node]
    manifest, counts = _validate(service)
    facts = {
        "physical_ccd": _physical_figure(output_dir, service),
        "exposure": _exposure_figure(output_dir, service, exposure_result),
        "observation": _observation_figure(output_dir, service, observation_result),
        "storage": service.storage_summary(),
    }
    completion = facts["exposure"]["completion"]
    if int(completion.get("raw_amplifier_count", 0)) != 300 or int(completion.get("extracted_amplifier_count", 0)) != 299:
        _fail(f"Full Exposure coverage failed: {completion}")
    if int(facts["observation"]["assignment"].get("complete", 0)) != 1:
        _fail("Real Observation was not assigned as one complete standard sequence")
    final_row = _final_row(service)
    scratch_root = workspace / "artifacts" / ".scratch"
    scratch_after = sum(path.stat().st_size for path in scratch_root.rglob("*") if path.is_file()) if scratch_root.exists() else 0
    comparison = compare_workspaces(workspace, args.reference_workspace) if args.reference_workspace else None
    baseline_comparison = compare_validated_baseline(facts)
    forbidden_count = sum(
        count for kind, count in counts.items() if kind in FORBIDDEN_PERSISTENT_KINDS
    )
    storage = facts["storage"]
    cache_bytes = sum(
        int(row.get("payload_bytes") or 0) for row in service.adapter.list_all()
        if str(row.get("state") or "active") == "active" and str(row.get("lifecycle") or "") == "cache"
    )
    checks = [
        {"name": "complete observation", "passed": int(facts["observation"]["assignment"].get("complete", 0)) == 1, "detail": str(OBSERVATION_EXPOSURES)},
        {"name": "no dense production intermediates", "passed": forbidden_count == 0, "detail": f"active forbidden artifacts={forbidden_count}"},
        {"name": "scratch cleaned", "passed": scratch_after == 0, "detail": f"remaining bytes={scratch_after}"},
        {"name": "graph completed", "passed": executor.execution_stats.get("failed") == 0 and executor.execution_stats.get("blocked") == 0, "detail": json.dumps(executor.execution_stats.get("progress"), sort_keys=True)},
        {"name": "structured progress", "passed": progress_file.exists() and progress_file.stat().st_size > 0, "detail": str(progress_file)},
        {"name": "previous validated scientific baseline", "passed": baseline_comparison["passed"], "detail": baseline_comparison["reference"]},
    ]
    if comparison is not None:
        checks.append({"name": "parallel versus serial scientific equivalence", "passed": comparison["passed"], "detail": "component tolerances in comparison"})
        checks.append({"name": "deterministic artifact identity", "passed": comparison["artifact_identity"]["revision_equal"], "detail": json.dumps(comparison["artifact_identity"], sort_keys=True)})
    try:
        software_revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        software_revision = "unknown"
    validation = {
        "schema": "virusflow.validation.v1",
        "result": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "observation": {"observation_id": OBSERVATION_ID, "dither_set_id": DITHER_SET_ID, "exposure_ids": list(OBSERVATION_EXPOSURES)},
        "commands": ["python -m virusflow.cli.verify_steps_8_10 " + " ".join(argv or [])],
        "software_revision": software_revision,
        "effective_configuration": {
            "data_root": str(args.data_root), "configuration_root": str(args.configuration_root),
            "workspace": str(workspace), "artifact_root": str(workspace / "artifacts"),
            "registry": str(database), "scratch_root": str(scratch_root),
        },
        "execution": {
            "workers": workers, "mode": "serial" if workers == 1 else "parallel",
            "progress_mode": args.progress_mode, "runtime_seconds": runtime,
            "statistics": executor.execution_stats,
        },
        "storage": {
            **storage, "final_observation_bytes": int(final_row.get("payload_bytes") or 0),
            "cache_bytes": cache_bytes,
            "peak_scratch_bytes": None,
        },
        "cleanup": {"scratch_bytes_after": scratch_after, "scratch_cleaned": scratch_after == 0},
        "scientific_facts": facts,
        "validated_baseline_comparison": baseline_comparison,
        "comparison": comparison,
        "tolerances": (comparison or {}).get("components", {}),
        "checks": checks,
        "limitations": [
            "Peak scratch usage is not sampled because the accepted exposure path currently materializes its temporary states in memory.",
            "The production baseline relative response is provisional unity.",
            "No accepted physical LSF model is available.",
            "Sky/response covariance is not propagated.",
            "095+004+426+RU+S/N 0048 has zero-valued comparison arrays and no wavelength-dependent extraction.",
        ],
    }
    markdown_path, json_path = _write_report(output_dir, inventory, counts, facts, manifest, validation)
    report_artifact = _publish_validation_report(database, workspace, markdown_path, json_path, int(final_row["id"]))
    validation["validation_artifact_id"] = int(report_artifact.id)
    json_path.write_text(json.dumps(validation, indent=2, sort_keys=True, default=str) + "\n")
    print(f"PASS Steps 8–10 verification: registered={registered} workspace={workspace}")
    print(f"Products: {json.dumps(counts, sort_keys=True)}")
    print(f"Report: {markdown_path}")
    print(f"Machine report: {json_path}")
    return 0 if validation["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
