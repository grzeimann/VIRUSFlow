from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import json


def load_wave_packets(root: Path) -> List[Dict]:
    """Recursively discover and load wave_qa.json packets under root."""
    root = Path(root)
    packets: List[Dict] = []
    for p in root.rglob("wave_qa.json"):
        try:
            d = json.loads(p.read_text())
            d["_path"] = str(p)
            packets.append(d)
        except Exception:
            continue
    return packets


essential_keys = ("amp_id", "run_id", "obs_time", "zip_code")


def filter_by_zip_and_run(packets: List[Dict], zip_code: str, run_id: Optional[str] = None) -> List[Dict]:
    out = [p for p in packets if (p.get("zip_code") == zip_code)]
    if run_id is not None:
        out = [p for p in out if (p.get("run_id") == run_id)]
    return out


def summarize_time_series(packets: List[Dict]) -> List[Dict]:
    rows: List[Dict] = []
    for p in packets:
        rows.append({
            "amp_id": p.get("amp_id"),
            "obs_time": p.get("obs_time"),
            "status": p.get("status"),
            "rms_median": ((p.get("metrics") or {}).get("rms_median")),
            "plot": ((p.get("plots") or {}).get("identify_arc_summary")),
            "path": p.get("_path"),
        })
    return rows
