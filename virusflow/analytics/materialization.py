from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from ..artifacts.models import Scope
from ..artifacts.requests import ArtifactRequest, LogicalComponent
from ..artifacts.service import ArtifactService
from ..ontology.lifecycle import ArtifactLifecycle
from ..ontology.artifact_kinds import kind_spec
from ..ontology.scopes import PhysicalScope
from ..persistence.policy import DefaultPersistencePolicy
from ..publication.context import PublicationContext
from ..publication.service import DefaultPublicationService
from ..registry import database as db


class RetentionPolicy(str, Enum):
    NONE = "none"
    SELECTED = "selected"
    OUTLIERS = "outliers"
    ALL = "all"
    UNTIL_STUDY_COMPLETION = "until_study_completion"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class AnalysisStudyRecord:
    study_id: str
    scientific_question: str
    selection: Mapping[str, Any]
    selected_observations: tuple[str, ...]
    model_versions: Mapping[str, str]
    calibration_versions: Mapping[str, str]
    software_version: str
    algorithm_versions: Mapping[str, str]
    intermediate_kinds: tuple[str, ...]
    retention_policy: RetentionPolicy
    expected_bytes: int
    state: str = "active"
    summary: Mapping[str, Any] = field(default_factory=dict)


class AnalysisStudyService:
    """Bounded study records and deliberate materialization of production values."""

    def __init__(self, db_path: str, output_dir: str) -> None:
        self.svc = ArtifactService(db_path)
        self.db_path = db_path
        self.output_dir = str(output_dir)
        self.publisher = DefaultPublicationService(
            svc=self.svc, policy=DefaultPersistencePolicy(), base_dir=self.output_dir
        )

    def create(
        self,
        *,
        scientific_question: str,
        selection: Mapping[str, Any],
        selected_observations: Sequence[str],
        model_versions: Mapping[str, str],
        calibration_versions: Mapping[str, str],
        software_version: str,
        algorithm_versions: Mapping[str, str],
        intermediate_kinds: Sequence[str],
        retention_policy: RetentionPolicy | str = RetentionPolicy.SELECTED,
        expected_bytes: int,
        study_id: str | None = None,
    ) -> AnalysisStudyRecord:
        policy = RetentionPolicy(retention_policy)
        if not scientific_question.strip():
            raise ValueError("a study requires a scientific question")
        if expected_bytes < 0:
            raise ValueError("expected storage budget cannot be negative")
        record = AnalysisStudyRecord(
            study_id or uuid.uuid4().hex,
            scientific_question.strip(),
            dict(selection),
            tuple(str(value) for value in selected_observations),
            dict(model_versions),
            dict(calibration_versions),
            str(software_version),
            dict(algorithm_versions),
            tuple(str(value) for value in intermediate_kinds),
            policy,
            int(expected_bytes),
        )
        with db.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO analysis_studies(
                    study_id,scientific_question,selection_json,selected_observations_json,
                    model_versions_json,calibration_versions_json,software_version,
                    algorithm_versions_json,intermediate_kinds_json,retention_policy,
                    expected_bytes,state,summary_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.study_id, record.scientific_question,
                    json.dumps(record.selection, sort_keys=True),
                    json.dumps(record.selected_observations),
                    json.dumps(record.model_versions, sort_keys=True),
                    json.dumps(record.calibration_versions, sort_keys=True),
                    record.software_version,
                    json.dumps(record.algorithm_versions, sort_keys=True),
                    json.dumps(record.intermediate_kinds), record.retention_policy.value,
                    record.expected_bytes, record.state, "{}", datetime.utcnow().isoformat(),
                ),
            )
        return record

    def get(self, study_id: str) -> AnalysisStudyRecord:
        with db.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM analysis_studies WHERE study_id=?", (str(study_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"analysis study not found: {study_id}")
        values = dict(row)
        return AnalysisStudyRecord(
            values["study_id"], values["scientific_question"],
            json.loads(values["selection_json"]), tuple(json.loads(values["selected_observations_json"])),
            json.loads(values["model_versions_json"]), json.loads(values["calibration_versions_json"]),
            values["software_version"], json.loads(values["algorithm_versions_json"]),
            tuple(json.loads(values["intermediate_kinds_json"])),
            RetentionPolicy(values["retention_policy"]), int(values["expected_bytes"]),
            values["state"], json.loads(values["summary_json"] or "{}"),
        )

    @staticmethod
    def _retain(policy: RetentionPolicy, *, selected: bool, outlier: bool) -> bool:
        if policy == RetentionPolicy.NONE:
            return False
        if policy == RetentionPolicy.SELECTED:
            return selected
        if policy == RetentionPolicy.OUTLIERS:
            return outlier
        return True

    def _used_bytes(self, study_id: str) -> int:
        with db.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT materialized_bytes FROM analysis_studies WHERE study_id=?", (study_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"analysis study not found: {study_id}")
        return int(row[0])

    def _record_retained(self, study: AnalysisStudyRecord, artifact, kind: str) -> None:
        used = self._used_bytes(study.study_id)
        projected = used + int(artifact.payload_bytes)
        if projected > study.expected_bytes:
            self.svc._purge_payload(int(artifact.id))
            self.svc.adapter.set_state(int(artifact.id), "obsolete")
            raise RuntimeError(
                f"analysis storage budget exceeded: projected {projected}, budget {study.expected_bytes}"
            )
        with db.connect(self.db_path) as connection:
            exists = connection.execute(
                "SELECT 1 FROM analysis_materializations WHERE study_id=? AND artifact_id=?",
                (study.study_id, int(artifact.id)),
            ).fetchone()
            if exists is None:
                connection.execute(
                    "INSERT INTO analysis_materializations(study_id,artifact_id,kind,retained,payload_bytes) VALUES(?,?,?,?,?)",
                    (study.study_id, int(artifact.id), kind, 1, int(artifact.payload_bytes)),
                )
                connection.execute(
                    "UPDATE analysis_studies SET materialized_bytes=materialized_bytes+? WHERE study_id=?",
                    (int(artifact.payload_bytes), study.study_id),
                )

    def materialize(
        self,
        study_id: str,
        *,
        intermediate_kind: str,
        producer: Callable[[], Any],
        parent_ids: Sequence[int],
        scope: Scope | None = None,
        selected: bool = False,
        outlier: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[Any, Any | None]:
        """Invoke the supplied production calculation and retain only by study policy."""

        study = self.get(study_id)
        if study.state != "active":
            raise RuntimeError(f"analysis study is not active: {study_id}")
        if intermediate_kind not in study.intermediate_kinds:
            raise ValueError(f"study did not authorize intermediate kind: {intermediate_kind}")
        value = producer()
        if not self._retain(study.retention_policy, selected=selected, outlier=outlier):
            return value, None
        array = np.asarray(value)
        used = self._used_bytes(study_id)
        projected = used + int(array.nbytes)
        if projected > study.expected_bytes:
            raise RuntimeError(
                f"analysis storage budget exceeded: projected {projected}, budget {study.expected_bytes}"
            )
        request = ArtifactRequest(
            kind="analysis_materialization",
            role="analytic",
            lifecycle=ArtifactLifecycle.ANALYSIS,
            scope=scope or Scope(zipcode=None, physical_scope=PhysicalScope.OBSERVATION),
            parents=[int(value) for value in parent_ids],
            components={
                "data": LogicalComponent(
                    "data", "array1d" if array.ndim == 1 else "array2d", array,
                    metadata=(dict(metadata or {})),
                )
            },
            metadata={
                "study_id": study_id,
                "intermediate_kind": intermediate_kind,
                "selection_query": dict(study.selection),
                "retention_policy": study.retention_policy.value,
                **dict(metadata or {}),
            },
        )
        context = PublicationContext(
            "analysis_materialization", "1", "production_algorithm_reuse", "1",
            {"study_id": study_id, "intermediate_kind": intermediate_kind}, [], {},
        )
        artifact = self.publisher.publish([request], context)[0]
        self._record_retained(study, artifact, intermediate_kind)
        return value, artifact

    def publish_candidate(
        self,
        study_id: str,
        *,
        candidate_kind: str,
        components: Mapping[str, LogicalComponent],
        accepted_model_id: int,
        parent_ids: Sequence[int] = (),
        scope: Scope | None = None,
        validation_metrics: Mapping[str, Any] | None = None,
        comparison: Mapping[str, Any] | None = None,
        decision: str = "pending",
    ):
        """Publish a bounded candidate linked to its study and accepted model.

        This deliberately does not promote or mutate the accepted production
        model; promotion remains a separate reviewed decision.
        """

        study = self.get(study_id)
        if study.state != "active":
            raise RuntimeError(f"analysis study is not active: {study_id}")
        if candidate_kind not in {"candidate_sky_model", "candidate_scattered_light_model"}:
            raise ValueError(f"unsupported candidate model kind: {candidate_kind}")
        logical_components = dict(components)
        missing = set(kind_spec(candidate_kind).required_components) - set(logical_components)
        if missing:
            raise ValueError(
                f"{candidate_kind} is missing required components: {sorted(missing)}"
            )
        raw_bytes = sum(int(np.asarray(component.value).nbytes) for component in logical_components.values())
        if self._used_bytes(study_id) + raw_bytes > study.expected_bytes:
            raise RuntimeError(
                f"analysis storage budget exceeded: projected at least "
                f"{self._used_bytes(study_id) + raw_bytes}, budget {study.expected_bytes}"
            )
        parents = sorted({int(accepted_model_id), *(int(value) for value in parent_ids)})
        request = ArtifactRequest(
            kind=candidate_kind,
            role="analytic",
            lifecycle=ArtifactLifecycle.ANALYSIS,
            scope=scope or Scope(
                zipcode=None,
                physical_scope=(
                    PhysicalScope.PHYSICAL_CCD
                    if candidate_kind == "candidate_scattered_light_model"
                    else PhysicalScope.EXPOSURE
                ),
            ),
            parents=parents,
            components=logical_components,
            metadata={
                "study_id": study_id,
                "accepted_model_id": int(accepted_model_id),
                "validation_metrics": dict(validation_metrics or {}),
                "comparison_with_accepted": dict(comparison or {}),
                "promotion_decision": str(decision),
            },
        )
        context = PublicationContext(
            "analysis_candidate", "1", "production_algorithm_reuse", "1",
            {"study_id": study_id, "candidate_kind": candidate_kind}, [], {},
        )
        artifact = self.publisher.publish([request], context)[0]
        self._record_retained(study, artifact, candidate_kind)
        return artifact

    def record_validation(
        self,
        study_id: str,
        *,
        candidate_artifact_id: int,
        metrics: Mapping[str, Any],
        comparison: Mapping[str, Any],
        decision: str,
    ) -> None:
        """Record a candidate comparison without promoting it automatically."""

        study = self.get(study_id)
        summary = dict(study.summary)
        validations = list(summary.get("validations") or [])
        validations.append(
            {
                "candidate_artifact_id": int(candidate_artifact_id),
                "metrics": dict(metrics),
                "comparison_with_accepted": dict(comparison),
                "decision": str(decision),
            }
        )
        summary["validations"] = validations
        with db.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE analysis_studies SET summary_json=? WHERE study_id=?",
                (json.dumps(summary, sort_keys=True, default=str), study_id),
            )

    def complete(self, study_id: str, *, summary: Mapping[str, Any]) -> None:
        study = self.get(study_id)
        if study.retention_policy == RetentionPolicy.UNTIL_STUDY_COMPLETION:
            with db.connect(self.db_path) as connection:
                rows = connection.execute(
                    "SELECT artifact_id FROM analysis_materializations WHERE study_id=?", (study_id,)
                ).fetchall()
            for row in rows:
                self.svc._purge_payload(int(row[0]))
        with db.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE analysis_studies SET state='complete',summary_json=?,completed_at=? WHERE study_id=?",
                (json.dumps(dict(summary), sort_keys=True, default=str), datetime.utcnow().isoformat(), study_id),
            )
