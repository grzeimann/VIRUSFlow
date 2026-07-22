from __future__ import annotations

from enum import Enum


class RelationKind(str, Enum):
    CONTAINS = "contains"
    MEMBER_OF = "member_of"
    DERIVED_FROM = "derived_from"
    CALIBRATED_BY = "calibrated_by"
    SUPERSEDES = "supersedes"
    VALID_FOR = "valid_for"
    MEASURES = "measures"
    PREDICTS = "predicts"
    REFINES = "refines"
    COMPARES_TO = "compares_to"
    USES_CONFIGURATION = "uses_configuration"

