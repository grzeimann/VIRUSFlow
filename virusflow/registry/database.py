from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from ..core.identity import RawFileId, ZipCode
from ..core.artifacts import Artifact, ProvenanceInfo


DEFAULT_DB_PATH = os.environ.get("VIRUSFLOW_DB", str(Path.cwd() / "virusflow.sqlite3"))


def _connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


SCHEMA = r"""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS exposures (
    id TEXT PRIMARY KEY,
    when_utc TEXT,
    frame_type TEXT
);

CREATE TABLE IF NOT EXISTS amplifiers (
    key TEXT PRIMARY KEY,
    ifuslot TEXT,
    ifuid TEXT,
    specid TEXT,
    amp TEXT,
    controller TEXT
);

CREATE TABLE IF NOT EXISTS raw_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exposure_id TEXT,
    frame_type TEXT,
    path TEXT,
    tar_member TEXT,
    storage_backend TEXT,
    amp_key TEXT,
    FOREIGN KEY(exposure_id) REFERENCES exposures(id),
    FOREIGN KEY(amp_key) REFERENCES amplifiers(key)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    name TEXT,
    path TEXT,
    amp_key TEXT,
    validity_start TEXT,
    validity_end TEXT
);

CREATE TABLE IF NOT EXISTS provenance (
    artifact_id INTEGER PRIMARY KEY,
    software_version TEXT,
    git_commit TEXT,
    algorithm TEXT,
    parameters_hash TEXT,
    created_at TEXT,
    parents TEXT,
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS dependencies (
    parent_id INTEGER,
    child_id INTEGER,
    PRIMARY KEY(parent_id, child_id)
);

CREATE TABLE IF NOT EXISTS qa_results (
    artifact_id INTEGER PRIMARY KEY,
    status TEXT,
    metrics_json TEXT,
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);
"""


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def _parse_filename_meta(path: str) -> Tuple[str, str, Optional[str]]:
    """Best-effort parse of VIRUS filename into (exposure_id, frame_type, amp_token).

    Expected like: 20260511T035810.4_074LL_cmp.fits ->
      exposure_id: 20260511T035810.4
      frame_type: cmp
      amp_token: 074LL
    Returns (exposure_id, frame_type, amp_token or None).
    """
    name = os.path.basename(path)
    if not name.endswith(".fits"):
        return ("unknown", "unk", None)
    base = name[:-5]
    parts = base.split("_")
    exposure_id = parts[0] if parts else "unknown"
    frame_type = parts[-1] if parts else "unk"
    amp_token = parts[1] if len(parts) >= 3 else None
    return (exposure_id, frame_type, amp_token)


def _extract_observation_number(path: str) -> Optional[int]:
    """Extract observation number from a path component like 'virusXXXXXXX' or 'virusXXXXXXX.tar'.

    The rule: a 7-digit number < 999 is a non-test frame; values >= 999 are test and should be ignored.
    Returns the integer if found, otherwise None.
    """
    import re

    p = Path(path)
    for part in p.parts:
        m = re.match(r"^virus(\d{7})(?:\.tar)?$", part, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def upsert_amplifier(conn: sqlite3.Connection, z: ZipCode) -> None:
    conn.execute(
        """
        INSERT INTO amplifiers(key, ifuslot, ifuid, specid, amp, controller)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            ifuslot=excluded.ifuslot,
            ifuid=excluded.ifuid,
            specid=excluded.specid,
            amp=excluded.amp,
            controller=excluded.controller
        """,
        (z.key(), z.ifuslot, z.ifuid, z.specid, z.amp, z.controller),
    )


def register_raw_file(path: str, frame_type: Optional[str] = None, db_path: str = DEFAULT_DB_PATH, tar_member: Optional[str] = None) -> Optional[RawFileId]:
    """Register a raw FITS file in the DB unless it belongs to a test observation.

    Supports files inside tar archives by passing tar_member (member path inside the tar).

    Test observations are identified by enclosing directory or tarball named like
    'virusXXXXXXX' or 'virusXXXXXXX.tar' where the 7-digit number >= 999. Such
    files are ignored (not inserted into the DB), and the function returns None.
    """
    # Check observation number in the path context (use tar filename if provided)
    obs_num = _extract_observation_number(path)
    if obs_num is not None and obs_num >= 999:
        # Ignore test frames
        return None

    # For files inside tar, parse metadata from the member name if available; else fallback to outer path
    parse_target = tar_member if tar_member else path
    exposure_id, ft, amp_token = _parse_filename_meta(parse_target)
    frame_type = frame_type or ft
    with connect(db_path) as conn:
        # exposure
        when_utc = None
        try:
            # naive parse of first token as timestamp
            ts = exposure_id.split("T")[0]
            when_utc = datetime.strptime(ts, "%Y%m%d").isoformat()
        except Exception:
            when_utc = None
        conn.execute(
            "INSERT OR IGNORE INTO exposures(id, when_utc, frame_type) VALUES(?, ?, ?)",
            (exposure_id, when_utc, frame_type),
        )
        # amplifier unknown, unless later mapped via external metadata
        amp_key = None
        storage_backend = "tar" if tar_member else "filesystem"
        conn.execute(
            """
            INSERT INTO raw_files(exposure_id, frame_type, path, tar_member, storage_backend, amp_key)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (exposure_id, frame_type, os.path.abspath(path), tar_member, storage_backend, amp_key),
        )
    return RawFileId(
        exposure_id=exposure_id,
        frame_type=frame_type,
        path=os.path.abspath(path),
        tar_member=tar_member,
        storage_backend=("tar" if tar_member else "filesystem"),
    )


def list_exposures(db_path: str = DEFAULT_DB_PATH) -> List[str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM exposures ORDER BY id").fetchall()
        return [r[0] for r in rows]


def list_raw_files(exposure_id: Optional[str] = None, db_path: str = DEFAULT_DB_PATH) -> List[RawFileId]:
    with connect(db_path) as conn:
        if exposure_id:
            rows = conn.execute(
                "SELECT exposure_id, frame_type, path, tar_member, storage_backend, amp_key FROM raw_files WHERE exposure_id=?",
                (exposure_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT exposure_id, frame_type, path, tar_member, storage_backend, amp_key FROM raw_files",
            ).fetchall()
        out: List[RawFileId] = []
        for r in rows:
            zipcode = None
            out.append(
                RawFileId(
                    exposure_id=r[0], frame_type=r[1], path=r[2], tar_member=r[3], storage_backend=r[4], zipcode=zipcode
                )
            )
        return out


def list_raw_files_scoped(
    frame_type: str,
    start_date: str,
    end_date: str,
    zipcode: Optional[ZipCode] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Tuple[int, RawFileId]]:
    """List raw files filtered by frame_type and exposure date window, optionally by zipcode.

    Returns list of (raw_db_id, RawFileId) tuples. ZipCode filtering is a placeholder
    until raw_files.amp_key is populated; currently it is ignored.
    Dates are compared against exposures.when_utc which is stored as YYYYMMDD ISO date.
    """
    # Normalize dates to YYYYMMDD
    def _d(s: str) -> str:
        return s.split("T", 1)[0]

    sd, ed = _d(start_date), _d(end_date)
    with connect(db_path) as conn:
        rows = conn.execute(
            (
                "SELECT rf.id, rf.exposure_id, rf.frame_type, rf.path, rf.tar_member, rf.storage_backend, rf.amp_key "
                "FROM raw_files rf JOIN exposures e ON rf.exposure_id = e.id "
                "WHERE LOWER(rf.frame_type)=LOWER(?) AND e.when_utc IS NOT NULL AND substr(e.when_utc,1,8) BETWEEN ? AND ?"
            ),
            (frame_type, sd, ed),
        ).fetchall()
        out: List[Tuple[int, RawFileId]] = []
        for r in rows:
            zipcode_val = None  # TODO: map from r[6] amp_key once populated
            rf = RawFileId(
                exposure_id=r[1], frame_type=r[2], path=r[3], tar_member=r[4], storage_backend=r[5], zipcode=zipcode_val
            )
            out.append((int(r[0]), rf))
        # TODO: apply zipcode filtering when amp_key is available. For now, return all.
        return out


def save_artifact(artifact: Artifact, prov: ProvenanceInfo, db_path: str = DEFAULT_DB_PATH) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO artifacts(kind, name, path, amp_key, validity_start, validity_end)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.kind,
                artifact.name,
                artifact.path,
                artifact.zipcode.key() if artifact.zipcode else None,
                artifact.validity_start.isoformat() if artifact.validity_start else None,
                artifact.validity_end.isoformat() if artifact.validity_end else None,
            ),
        )
        artifact_id = cur.lastrowid
        parents = ",".join(prov.parents)
        conn.execute(
            """
            INSERT INTO provenance(artifact_id, software_version, git_commit, algorithm, parameters_hash, created_at, parents)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                prov.software_version,
                prov.git_commit,
                prov.algorithm,
                prov.parameters_hash,
                prov.created_at.isoformat(),
                parents,
            ),
        )
        return artifact_id


def get_artifact(artifact_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT a.*, p.software_version, p.git_commit, p.algorithm, p.parameters_hash, p.created_at, p.parents
            FROM artifacts a LEFT JOIN provenance p ON a.id = p.artifact_id WHERE a.id=?
            """,
            (artifact_id,),
        ).fetchone()
        return dict(row) if row else None


def list_zipcodes(
    db_path: str = DEFAULT_DB_PATH,
    frame_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
) -> List[ZipCode]:
    """Discover unique ZipCodes that have raw files in an optional date window.

    If frame_type/start_date/end_date are provided, restrict discovery to
    raw_files matching those filters. The mapping uses raw_files.amp_key
    joined to amplifiers.key. If amp_key is NULL for all rows (current
    placeholder), this will return an empty list, in which case planners
    may fall back to developer-provided --only-zipcode filters.
    """
    # Helper to normalize YYYYMMDD from potential ISO-like strings
    def _d(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        return str(s).split("T", 1)[0]

    sd = _d(start_date)
    ed = _d(end_date)
    with connect(db_path) as conn:
        base = (
            "SELECT DISTINCT a.ifuslot, a.ifuid, a.specid, a.amp, a.controller "
            "FROM raw_files rf JOIN exposures e ON rf.exposure_id = e.id "
            "JOIN amplifiers a ON rf.amp_key = a.key "
            "WHERE 1=1 "
        )
        params: List[str] = []
        if frame_type:
            base += "AND LOWER(rf.frame_type)=LOWER(?) "
            params.append(frame_type)
        if sd and ed:
            base += "AND e.when_utc IS NOT NULL AND substr(e.when_utc,1,8) BETWEEN ? AND ? "
            params.extend([sd, ed])
        sql = base + "ORDER BY a.ifuslot, a.ifuid, a.specid, a.amp, a.controller"
        if limit and limit > 0:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, tuple(params)).fetchall()
        out: List[ZipCode] = []
        for r in rows:
            out.append(ZipCode(ifuslot=r[0], ifuid=r[1], specid=r[2], amp=r[3], controller=r[4]))
        return out
