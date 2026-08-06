from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class QAFact:
    name: str
    value: Any
    units: Optional[str] = None
    component: Optional[str] = None


@dataclass(frozen=True)
class QARuleResult:
    rule_id: str
    passed: bool
    severity: str
    message: Optional[str] = None


@dataclass(frozen=True)
class QAStatus:
    value: str
    policy_version: str = "1"


@dataclass(frozen=True)
class Usability:
    state: str = "usable"
    contexts: List[str] = field(default_factory=list)
    reason: Optional[str] = None


@dataclass(frozen=True)
class QABundle:
    facts: Dict[str, QAFact]
    rules: List[QARuleResult]
    status: QAStatus
    usability: Usability
