from datetime import datetime

import numpy as np

from virusflow.artifacts import ConfigurationReference, Scope, Validity
from virusflow.artifacts.requests import ArtifactRequest, LogicalComponent
from virusflow.core.identity import ZipCode
from virusflow.ontology import (
    ARTIFACT_KINDS,
    CoordinateConvention,
    PhysicalScope,
    RelationKind,
    UPPER_AMPLIFIER_Y_OFFSET,
    canonical_kind,
    kind_candidates,
    kind_spec,
)
from virusflow.qa import QABundle, QAFact, QAStatus, Usability


def test_canonical_calibration_kinds_and_legacy_aliases():
    expected = {
        "master_bias",
        "master_dark",
        "master_ldls",
        "master_hg",
        "master_cd",
        "master_arc",
        "master_twilight",
        "master_sci",
        "extracted_master_sci_spectrum",
        "fiber_wavelength_spectral_mask",
        "trace_map",
        "wavelength_map",
    }
    assert expected.issubset(ARTIFACT_KINDS)
    assert canonical_kind("master_flat") == "master_ldls"
    assert canonical_kind("master_cmp") == "master_arc"
    assert canonical_kind("trace") == "trace_map"
    assert "master_cmp" in kind_candidates("master_arc")
    assert kind_spec("wave").name == "wavelength_map"


def test_approved_upper_amplifier_transform_is_unambiguous():
    assert UPPER_AMPLIFIER_Y_OFFSET == 1032
    y = np.array([0, 1, 1031])
    assert (UPPER_AMPLIFIER_Y_OFFSET + y).tolist() == [1032, 1033, 2063]


def test_artifact_request_expresses_scope_validity_units_and_configuration():
    zc = ZipCode("013", "043", "412", "LL", "S_N_0021")
    validity = Validity(datetime(2026, 6, 9), datetime(2026, 6, 10))
    component = LogicalComponent(
        "master",
        "array2d",
        np.ones((2, 2)),
        units="electron",
        coordinates=CoordinateConvention.ORIENTED_AMPLIFIER.value,
    )
    req = ArtifactRequest(
        kind="master_bias",
        components={"master": component},
        scope=Scope(zc, physical_scope=PhysicalScope.AMPLIFIER),
        validity=validity,
        configuration_refs=[ConfigurationReference("gain", "unknown", zc.key())],
    )
    assert req.validity.start == datetime(2026, 6, 9)
    assert req.components["master"].units == "electron"
    assert req.configuration_refs[0].evidence_state == "unknown"


def test_qa_fact_status_and_usability_are_separate_objects():
    bundle = QABundle(
        facts={"read_noise": QAFact("read_noise", 3.0, "electron")},
        rules=[],
        status=QAStatus("pass"),
        usability=Usability("usable", ["science", "diagnostic"]),
    )
    assert bundle.facts["read_noise"].value == 3.0
    assert bundle.status.value == "pass"
    assert bundle.usability.state == "usable"
    assert RelationKind.DERIVED_FROM.value == "derived_from"
