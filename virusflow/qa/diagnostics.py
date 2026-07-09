from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Optional

from ..registry import database as db


@dataclass
class QAResult:
    status: str  # e.g., PASS/WARN/FAIL
    metrics: Dict[str, float] = field(default_factory=dict)
    notes: Optional[str] = None


def save_qa(artifact_id: int, qa: QAResult, db_path: str) -> None:
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO qa_results(artifact_id, status, metrics_json) VALUES(?, ?, ?)",
            (artifact_id, qa.status, json.dumps(qa.metrics)),
        )
