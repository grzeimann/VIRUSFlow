from __future__ import annotations

import io
import os
import re
import sqlite3
import tarfile
import time
from threading import Lock
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Set, Any

from astropy.io import fits

from ..core.exposure_metadata import interpret_virus_exposure_header
from ..core.identity import RawFileId, ZipCode
from ..core.scientific_metadata import (
    SCIENTIFIC_METADATA_FIELDS,
    scientific_metadata_for_database,
    scientific_metadata_from_header,
)


DEFAULT_DB_PATH = os.environ.get("VIRUSFLOW_DB", str(Path.cwd() / "virusflow.sqlite3"))
DEFAULT_RAW_DB_PATH = os.environ.get("VIRUSFLOW_RAW_DB", str(Path.cwd() / "virusflow_raw.sqlite3"))
_INITIALIZED_DATABASES: Dict[str, Tuple[int, int]] = {}
_INITIALIZE_LOCK = Lock()


def _record_sql(sql: str, elapsed: float) -> None:
    from ..performance import current_task_timing

    timing = current_task_timing()
    if timing is None:
        return
    normalized = " ".join(str(sql).strip().split())
    verb = normalized.split(" ", 1)[0].upper() if normalized else ""
    lower = normalized.lower()
    schema_operation = any(
        marker in lower
        for marker in ("create table", "create index", "alter table", "pragma table_info")
    )
    raw_catalog = not schema_operation and any(
        name in normalized.lower()
        for name in ("raw_files", "tar_members", "tar_files", "exposures", "exposure_details", "amplifiers")
    )
    known_tables = (
        "exposures", "exposure_details", "amplifiers", "raw_files", "tar_files",
        "tar_members", "artifacts", "artifact_records", "artifact_components",
        "artifact_scientific_metadata", "artifact_relations", "provenance",
        "qa_facts", "qa_records",
        "analysis_studies", "analysis_materializations", "performance_runs",
        "performance_tasks",
    )
    tables = [
        name for name in known_tables
        if re.search(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])", lower)
    ]
    event = {
        "seconds": max(0.0, float(elapsed)), "operation": verb,
        "sql": normalized[:500], "raw_catalog": raw_catalog,
        "write": verb in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"},
        "tables": tables,
    }
    timing.database_queries.append(event)
    timing.increment("database_queries")
    if raw_catalog:
        timing.increment("raw_catalog_queries")


class _TimingConnection(sqlite3.Connection):
    def execute(self, sql, parameters=(), /):
        from ..performance import phase
        started = time.perf_counter()
        try:
            with phase("database_query"):
                return super().execute(sql, parameters)
        finally:
            _record_sql(str(sql), time.perf_counter() - started)

    def executemany(self, sql, seq_of_parameters, /):
        from ..performance import phase
        started = time.perf_counter()
        try:
            with phase("database_query"):
                return super().executemany(sql, seq_of_parameters)
        finally:
            _record_sql(str(sql), time.perf_counter() - started)

    def executescript(self, sql_script, /):
        from ..performance import phase
        started = time.perf_counter()
        try:
            with phase("database_query"):
                return super().executescript(sql_script)
        finally:
            _record_sql(str(sql_script), time.perf_counter() - started)

# ---- Small internal helpers to keep SQL paths concise and consistent ----
def _as_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else None


def _zc_key(zipcode: Optional[ZipCode]) -> Optional[str]:
    try:
        return zipcode.key() if zipcode is not None else None
    except Exception:
        return None


def _rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict]:
    return [dict(r) for r in rows]


def _date8(s: str) -> str:
    # Normalize various YYYYMMDD[THH...] forms to YYYYMMDD
    return str(s).split("T", 1)[0].replace("-", "")[:8]


def _connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    # Use autocommit and WAL-friendly settings to reduce 'database is locked' during
    # concurrent writer scenarios common in tests and planner runs.
    # - isolation_level=None enables autocommit (each statement is its own transaction),
    #   avoiding long-lived write transactions held open by context managers.
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None, factory=_TimingConnection)
    try:
        from ..performance import current_task_timing
        timing = current_task_timing()
        if timing is not None:
            timing.increment("database_connections")
            timing.identity("database_paths", str(Path(db_path).resolve()))
    except Exception:
        pass
    try:
        # Busy timeout applies per-connection; keep it generous.
        conn.execute("PRAGMA busy_timeout=5000")
        from ..performance import legacy_baseline_enabled
        if legacy_baseline_enabled():
            conn.execute("PRAGMA journal_mode=WAL")
        # In WAL mode, NORMAL synchronous is generally safe and reduces writer stalls.
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        from ..performance import phase
        with phase("database_transaction"):
            conn.commit()
        conn.close()


RAW_SCHEMA = r"""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS exposures (
    id TEXT PRIMARY KEY,
    when_utc TEXT,
    frame_type TEXT
);

-- Additional exposure-level metadata captured from a representative FITS header during scan
CREATE TABLE IF NOT EXISTS exposure_details (
    exposure_id TEXT PRIMARY KEY,
    tar_path TEXT,          -- absolute path to enclosing .tar if applicable, else NULL
    expnum INTEGER,         -- observation number parsed from path (if < 999), else NULL
    qobject TEXT,
    qprog TEXT,
    pexptime REAL,
    date TEXT,
    qra TEXT,
    qdec TEXT,
    exptime REAL,           -- commanded/raw EXPTIME, seconds
    ambient_temperature REAL,
    object_name TEXT,       -- legacy OBJECT-or-QOBJECT field; retained for compatibility
    virus_object TEXT,      -- raw VIRUS OBJECT operational label
    requested_target TEXT, -- interpreted requested target, normally QOBJECT
    requested_target_source TEXT,
    requested_ifuslot TEXT,
    het_track TEXT,
    observing_mode TEXT,
    virus_primary INTEGER,
    q_metadata_expected INTEGER,
    q_metadata_complete INTEGER,
    object_qobject_consistent INTEGER,
    lamp TEXT,
    observing_block TEXT,
    FOREIGN KEY(exposure_id) REFERENCES exposures(id)
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
    outer_tar_member TEXT,
    storage_backend TEXT,
    amp_key TEXT,
    observation_time TIMESTAMP,
    ambient_temperature REAL,
    humidity REAL,
    pressure REAL,
    program_id TEXT,
    object TEXT,
    rho_start REAL,
    theta_start REAL,
    phi_start REAL,
    x_start REAL,
    y_start REAL,
    FOREIGN KEY(exposure_id) REFERENCES exposures(id),
    FOREIGN KEY(amp_key) REFERENCES amplifiers(key)
);

CREATE UNIQUE INDEX IF NOT EXISTS raw_files_uniq
ON raw_files(exposure_id, frame_type, path, tar_member);

-- Optional tar index to accelerate member reads; populated during scanning for uncompressed .tar
CREATE TABLE IF NOT EXISTS tar_files (
    path TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER,
    n_members INTEGER
);

CREATE TABLE IF NOT EXISTS tar_members (
    tar_path TEXT,
    member TEXT,
    offset INTEGER,
    size INTEGER,
    PRIMARY KEY(tar_path, member)
);

-- Optional index for Corral date-tar layouts (a tar of nested VIRUS tars). Offsets are
-- absolute byte positions within date_tar_path, so the same offset-based header reader
-- used for the single-level tar backend applies unchanged.
CREATE TABLE IF NOT EXISTS date_tar_files (
    date_tar_path TEXT,
    outer_member TEXT,
    mtime REAL,
    size INTEGER,
    n_members INTEGER,
    PRIMARY KEY(date_tar_path, outer_member)
);

CREATE TABLE IF NOT EXISTS date_tar_members (
    date_tar_path TEXT,
    outer_member TEXT,
    member TEXT,
    offset INTEGER,
    size INTEGER,
    PRIMARY KEY(date_tar_path, outer_member, member)
);
"""

ARTIFACT_SCHEMA = r"""
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

CREATE TABLE IF NOT EXISTS artifact_records (
    artifact_id INTEGER PRIMARY KEY,
    canonical_kind TEXT NOT NULL,
    role TEXT,
    payload_type TEXT,
    storage_format TEXT,
    physical_scope TEXT,
    exposure_id TEXT,
    observation_id TEXT,
    dither_set_id TEXT,
    revision TEXT UNIQUE,
    checksum TEXT,
    units_json TEXT,
    coordinates_json TEXT,
    configuration_refs_json TEXT,
    metadata_json TEXT,
    validity_policy TEXT,
    lifecycle TEXT NOT NULL DEFAULT 'canonical',
    state TEXT NOT NULL DEFAULT 'active',
    payload_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS artifact_scientific_metadata (
    artifact_id INTEGER PRIMARY KEY,
    observation_time TIMESTAMP,
    ambient_temperature REAL,
    humidity REAL,
    pressure REAL,
    program_id TEXT,
    object TEXT,
    rho_start REAL,
    theta_start REAL,
    phi_start REAL,
    x_start REAL,
    y_start REAL,
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS artifact_components (
    artifact_id INTEGER,
    name TEXT,
    model_type TEXT,
    path TEXT,
    payload_type TEXT,
    storage_format TEXT,
    checksum TEXT,
    units TEXT,
    coordinates TEXT,
    metadata_json TEXT,
    payload_bytes INTEGER NOT NULL DEFAULT 0,
    dtype TEXT,
    shape_json TEXT,
    PRIMARY KEY(artifact_id, name),
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS artifact_relations (
    parent_id INTEGER,
    child_id INTEGER,
    relation TEXT,
    PRIMARY KEY(parent_id, child_id, relation)
);

CREATE TABLE IF NOT EXISTS qa_results (
    artifact_id INTEGER PRIMARY KEY,
    status TEXT,
    metrics_json TEXT,
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS qa_facts (
    artifact_id INTEGER,
    name TEXT,
    value_json TEXT,
    units TEXT,
    component TEXT,
    PRIMARY KEY(artifact_id, name),
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS qa_decisions (
    artifact_id INTEGER PRIMARY KEY,
    status TEXT,
    usability TEXT,
    policy_version TEXT,
    rules_json TEXT,
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS analysis_studies (
    study_id TEXT PRIMARY KEY,
    scientific_question TEXT NOT NULL,
    selection_json TEXT NOT NULL,
    selected_observations_json TEXT NOT NULL,
    model_versions_json TEXT NOT NULL,
    calibration_versions_json TEXT NOT NULL,
    software_version TEXT,
    algorithm_versions_json TEXT NOT NULL,
    intermediate_kinds_json TEXT NOT NULL,
    retention_policy TEXT NOT NULL,
    expected_bytes INTEGER NOT NULL,
    materialized_bytes INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'active',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS analysis_materializations (
    study_id TEXT NOT NULL,
    artifact_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    retained INTEGER NOT NULL,
    payload_bytes INTEGER NOT NULL,
    PRIMARY KEY(study_id, artifact_id),
    FOREIGN KEY(study_id) REFERENCES analysis_studies(study_id),
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);

CREATE TABLE IF NOT EXISTS performance_runs (
    run_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    workers INTEGER,
    wall_seconds REAL,
    summary_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_tasks (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    task_kind TEXT NOT NULL,
    target TEXT,
    worker_id TEXT,
    status TEXT,
    wall_seconds REAL,
    timing_json TEXT NOT NULL,
    PRIMARY KEY(run_id, task_id, attempt),
    FOREIGN KEY(run_id) REFERENCES performance_runs(run_id)
);
CREATE INDEX IF NOT EXISTS performance_tasks_kind_idx ON performance_tasks(task_kind, run_id);

-- Helpful indexes (safe no-ops if already present)
CREATE INDEX IF NOT EXISTS artifacts_kind_amp ON artifacts(kind, amp_key);
CREATE INDEX IF NOT EXISTS provenance_created_at ON provenance(created_at);
CREATE INDEX IF NOT EXISTS artifact_records_kind_scope ON artifact_records(canonical_kind, physical_scope);
CREATE INDEX IF NOT EXISTS artifact_scientific_metadata_observation_time
ON artifact_scientific_metadata(observation_time);
CREATE INDEX IF NOT EXISTS artifact_scientific_metadata_ambient_temperature
ON artifact_scientific_metadata(ambient_temperature);
CREATE INDEX IF NOT EXISTS artifact_relations_child ON artifact_relations(child_id);
"""

RAW_SCHEMA += r"""
-- Helpful indexes (safe no-ops if already present)
CREATE INDEX IF NOT EXISTS exposure_details_requested_target_idx
ON exposure_details(requested_target);
CREATE INDEX IF NOT EXISTS exposure_details_qprog_idx ON exposure_details(qprog);
CREATE INDEX IF NOT EXISTS exposure_details_observing_mode_idx
ON exposure_details(observing_mode);
"""

# Preserved for any code/tests that still initialize a single shared database file
# containing both the raw catalog and the Artifact/Product registry.
SCHEMA = RAW_SCHEMA + ARTIFACT_SCHEMA


def _init_schema(db_path: str, schema: str) -> None:
    from ..performance import legacy_baseline_enabled

    resolved = str(Path(db_path).resolve())
    cache_key = (resolved, id(schema))
    with _INITIALIZE_LOCK:
        path = Path(resolved)
        if path.exists() and not legacy_baseline_enabled():
            stat = path.stat()
            signature = (int(stat.st_dev), int(stat.st_ino))
            if _INITIALIZED_DATABASES.get(cache_key) == signature:
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect(resolved) as conn:
            conn.executescript(schema)
        stat = path.stat()
        if not legacy_baseline_enabled():
            _INITIALIZED_DATABASES[cache_key] = (int(stat.st_dev), int(stat.st_ino))


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize a single shared database file with both raw and Artifact schemas."""

    _init_schema(db_path, SCHEMA)


def init_raw_db(db_path: str = DEFAULT_RAW_DB_PATH) -> None:
    """Initialize the raw-frame catalog database (exposures/raw_files/tar index)."""

    _init_schema(db_path, RAW_SCHEMA)


def init_artifact_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize the Artifact/Product registry database."""

    _init_schema(db_path, ARTIFACT_SCHEMA)


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


# --------- Minimal parsing and header reads to assemble full ZipCode ---------

def _parse_virus_member_name(member_name: str) -> Optional[Dict[str, Optional[str]]]:
    """Parse VIRUS member/file names like '.../20260511T035810.4_074LL_cmp.fits'.

    Returns dict with keys: obs_time, exposure_id (best-effort), expnum, frame_type, ifuslot, amp, amp_token.
    """
    p = Path(member_name)
    name = p.name
    if not name.lower().endswith(".fits"):
        return None
    stem = name[:-5]
    parts = stem.split("_")
    if len(parts) < 3:
        return None
    obs_time = parts[0]
    amp_key = parts[1]
    frame_type = parts[2]
    if len(amp_key) < 5:
        return None
    ifuslot = amp_key[:3]
    amp = amp_key[3:]
    path_parts = p.parts
    exposure_id = obs_time  # best-effort; more context may refine this
    expnum = path_parts[1] if len(path_parts) > 1 else None
    return {
        "obs_time": obs_time,
        "exposure_id": exposure_id,
        "expnum": expnum,
        "frame_type": frame_type,
        "ifuslot": ifuslot,
        "amp": amp,
        "amp_token": amp_key,
    }


_IFUSLOT_META_CACHE: Dict[str, Dict[str, Optional[str]]] = {}
# Track representative exposure headers per registry, not globally by Exposure ID.
_POPULATED_EXPOSURE_DETAILS: Set[Tuple[str, str]] = set()


def _ifuslot_meta_from_header(hdr) -> Dict[str, Optional[str]]:
    """Extract IFUSLOT-constant metadata (ifuid, specid, controller) from a FITS header."""

    return {
        "ifuid": hdr.get("IFUID"),
        "specid": hdr.get("SPECID"),
        "controller": hdr.get("CONTID") or hdr.get("CONTROLLER"),
    }


def _read_ifuslot_metadata_from_tar(tar_path: str, member_name: str) -> Dict[str, Optional[str]]:
    """Open a single FITS member from tar and return IFUSLOT-constant metadata.

    Keys: ifuid, specid, controller. Fallback to None on errors.
    """
    try:
        with tarfile.open(tar_path, mode="r") as tf:
            m = tf.getmember(member_name)
            ef = tf.extractfile(m) if m is not None else None
            if ef is None:
                return {"ifuid": None, "specid": None, "controller": None}
            with fits.open(ef, memmap=False) as hdul:  # type: ignore[arg-type]
                return _ifuslot_meta_from_header(hdul[0].header)
    except Exception:
        return {"ifuid": None, "specid": None, "controller": None}


def _read_ifuslot_metadata_from_date_tar(
    date_tar_path: str, outer_tar_member: str, member_name: str
) -> Dict[str, Optional[str]]:
    """Open a FITS member nested two tar levels deep (Corral date-tar layout)."""

    from ..storage.filesystem import RawSource, read_member_bytes

    try:
        source = RawSource(
            path=Path(date_tar_path), tar_member=member_name,
            backend="date_tar", outer_tar_member=outer_tar_member,
        )
        blob = read_member_bytes(source)
        with fits.open(io.BytesIO(blob), memmap=False) as hdul:
            return _ifuslot_meta_from_header(hdul[0].header)
    except Exception:
        return {"ifuid": None, "specid": None, "controller": None}


def _read_ifuslot_metadata_from_file(path: str) -> Dict[str, Optional[str]]:
    try:
        with fits.open(path, memmap=False) as hdul:
            return _ifuslot_meta_from_header(hdul[0].header)
    except Exception:
        return {"ifuid": None, "specid": None, "controller": None}


def _lookup_tar_member_offset(
    tar_path: str, member_name: str, *, conn: Optional[sqlite3.Connection], db_path: str
) -> Optional[int]:
    """Look up a member's cached data offset within its tar from a prior ensure_tar_index().

    Returns None when the tar hasn't been indexed (compressed tar, index not yet built,
    or this member simply isn't present) so callers can fall back to the slower
    tarfile.getmember() scan.
    """

    def _query(c: sqlite3.Connection) -> Optional[int]:
        row = c.execute(
            "SELECT offset FROM tar_members WHERE tar_path=? AND member=?",
            (tar_path, member_name),
        ).fetchone()
        return int(row[0]) if row else None

    try:
        if conn is not None:
            return _query(conn)
        with connect(db_path) as c:
            return _query(c)
    except Exception:
        return None


def _lookup_date_tar_member_offset(
    date_tar_path: str, outer_member: str, member_name: str, *,
    conn: Optional[sqlite3.Connection], db_path: str,
) -> Optional[int]:
    """Look up a nested-tar member's cached absolute offset from a prior ensure_date_tar_index().

    Returns None when the nested tar hasn't been indexed so callers can fall back to the
    slower two-level tarfile.getmember() extraction.
    """

    def _query(c: sqlite3.Connection) -> Optional[int]:
        row = c.execute(
            "SELECT offset FROM date_tar_members WHERE date_tar_path=? AND outer_member=? AND member=?",
            (date_tar_path, outer_member, member_name),
        ).fetchone()
        return int(row[0]) if row else None

    try:
        if conn is not None:
            return _query(conn)
        with connect(db_path) as c:
            return _query(c)
    except Exception:
        return None


def _read_header_via_tar_offset(tar_path: str, offset: int):
    """Read only the primary FITS header at a known byte offset inside an uncompressed tar.

    Avoids both the O(n) tarfile.getmember() scan and reading the member's pixel data.
    Returns None on any failure so callers can fall back to the slow path.
    """
    try:
        with open(tar_path, "rb") as fh:
            fh.seek(offset)
            return fits.Header.fromfile(fh, sep="", endcard=True, padding=True)
    except Exception:
        return None


def _exposure_header_fields(hdr, *, frame_type: str) -> Dict[str, Any]:
    """Extract grouping metadata without interpreting cadence policy."""

    scientific = scientific_metadata_from_header(hdr)
    lamp = next((hdr.get(key) for key in (
        "LAMP", "LAMPNAME", "LAMPTYPE", "OBJECT", "QOBJECT"
    ) if hdr.get(key) not in (None, "")), None)
    block = next((hdr.get(key) for key in (
        "OBSBLOCK", "BLOCKID", "OBSID", "QPROG"
    ) if hdr.get(key) not in (None, "")), None)
    context = interpret_virus_exposure_header(hdr, frame_type=frame_type)
    return {
        "qobject": context.qobject,
        "qprog": context.qprog,
        "pexptime": (str(hdr.get("PEXPTIME")) if hdr.get("PEXPTIME") is not None else None),
        "exptime": (str(hdr.get("EXPTIME")) if hdr.get("EXPTIME") is not None else None),
        "ambient_temperature": scientific["ambient_temperature"],
        "object_name": context.virus_object,
        "virus_object": context.virus_object,
        "requested_target": context.requested_target,
        "requested_target_source": context.requested_target_source,
        "requested_ifuslot": context.requested_ifuslot,
        "het_track": context.het_track,
        "observing_mode": context.observing_mode,
        "virus_primary": context.virus_primary,
        "q_metadata_expected": context.q_metadata_expected,
        "q_metadata_complete": context.q_metadata_complete,
        "object_qobject_consistent": context.object_qobject_consistent,
        "lamp": (str(lamp) if lamp is not None else None),
        "observing_block": (str(block) if block is not None else None),
        "date": hdr.get("DATE"),
        "qra": context.qra,
        "qdec": context.qdec,
        **scientific,
    }


def _optional_float(value) -> Optional[float]:
    import math

    if value in (None, "", "nan"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _read_exposure_header_fields_from_tar(
    tar_path: str, member_name: str, *, frame_type: str
) -> Dict[str, Any]:
    """Read selected exposure-level keywords from a FITS member inside a tar.

    Returns dict with keys: qobject, qprog, pexptime, date, qra, qdec.
    """
    try:
        with tarfile.open(tar_path, mode="r") as tf:
            m = tf.getmember(member_name)
            ef = tf.extractfile(m) if m is not None else None
            if ef is None:
                return {"qobject": None, "qprog": None, "pexptime": None, "date": None, "qra": None, "qdec": None}
            with fits.open(ef, memmap=False) as hdul:  # type: ignore[arg-type]
                hdr = hdul[0].header
                return _exposure_header_fields(hdr, frame_type=frame_type)
    except Exception:
        return {"qobject": None, "qprog": None, "pexptime": None, "date": None, "qra": None, "qdec": None}


def _read_exposure_header_fields_from_date_tar(
    date_tar_path: str, outer_tar_member: str, member_name: str, *, frame_type: str
) -> Dict[str, Any]:
    """Read selected exposure-level keywords from a FITS member nested two tar levels deep."""
    from ..storage.filesystem import RawSource, read_member_bytes

    try:
        source = RawSource(
            path=Path(date_tar_path), tar_member=member_name,
            backend="date_tar", outer_tar_member=outer_tar_member,
        )
        blob = read_member_bytes(source)
        with fits.open(io.BytesIO(blob), memmap=False) as hdul:
            hdr = hdul[0].header
            return _exposure_header_fields(hdr, frame_type=frame_type)
    except Exception:
        return {"qobject": None, "qprog": None, "pexptime": None, "date": None, "qra": None, "qdec": None}


def _read_exposure_header_fields_from_file(path: str, *, frame_type: str) -> Dict[str, Any]:
    try:
        with fits.open(path, memmap=False) as hdul:
            hdr = hdul[0].header
            return _exposure_header_fields(hdr, frame_type=frame_type)
    except Exception:
        return {"qobject": None, "qprog": None, "pexptime": None, "date": None, "qra": None, "qdec": None}


def _zipcode_from_amp_token(amp_token: Optional[str], ifuslot_meta: Optional[Dict[str, Optional[str]]] = None) -> Optional[ZipCode]:
    """Construct a ZipCode from an amp token like '074LL' plus cached IFUSLOT metadata.

    - IFUSLOT: first 3 chars of token (digits)
    - AMP: remaining letters
    - IFUID/SPECID/CONTROLLER: from ifuslot_meta (read once per IFUSLOT), with safe fallbacks
    """
    if not amp_token or len(amp_token) < 4:
        return None
    ifuslot = amp_token[:3]
    amp = amp_token[3:]
    if not ifuslot.isdigit():
        return None
    meta = ifuslot_meta or {}
    ifuid = meta.get("ifuid") or "000"
    specid = meta.get("specid") or "000"
    controller = meta.get("controller") or "X"
    return ZipCode(ifuslot=ifuslot, ifuid=str(ifuid), specid=str(specid), amp=amp, controller=str(controller))


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


def register_raw_file(path: str, frame_type: Optional[str] = None, db_path: str = DEFAULT_RAW_DB_PATH, tar_member: Optional[str] = None, outer_tar_member: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> Optional[RawFileId]:
    """Register a raw FITS file in the DB unless it belongs to a test observation.

    Supports files inside tar archives by passing tar_member (member path inside the tar).

    Test observations are identified by enclosing directory or tarball named like
    'virusXXXXXXX' or 'virusXXXXXXX.tar' where the 7-digit number >= 999. Such
    files are ignored (not inserted into the DB), and the function returns None.
    """
    if conn is None:
        init_raw_db(db_path)
    # Check observation number in the path context (use tar filename if provided)
    obs_num = _extract_observation_number(path)
    if obs_num is not None and obs_num >= 999:
        # Ignore test frames
        return None

    # For files inside tar, parse metadata from the member name if available; else fallback to outer path
    parse_target = tar_member if tar_member else path
    exposure_id, ft, amp_token = _parse_filename_meta(parse_target)
    frame_type = frame_type or ft

    # For plain (non-nested) tar members, reuse the byte offset already indexed by
    # ensure_tar_index() to read the header directly in one seek, instead of the O(n)
    # tarfile.getmember() scan (repeated once per file, this made scanning an exposure's
    # ~300 amplifiers effectively quadratic in the tar's member count).
    # The same applies to the Corral date-tar backend (a tar of nested VIRUS tars):
    # ensure_date_tar_index() pre-computes each nested tar's member offsets as absolute
    # positions within the date-tar file, so the same offset-based header reader works.
    shared_header = None
    if tar_member and not outer_tar_member:
        tar_path_abs = os.path.abspath(path)
        tar_offset = _lookup_tar_member_offset(tar_path_abs, tar_member, conn=conn, db_path=db_path)
        if tar_offset is not None:
            shared_header = _read_header_via_tar_offset(path, tar_offset)
    elif tar_member and outer_tar_member:
        date_tar_path_abs = os.path.abspath(path)
        date_tar_offset = _lookup_date_tar_member_offset(
            date_tar_path_abs, outer_tar_member, tar_member, conn=conn, db_path=db_path
        )
        if date_tar_offset is not None:
            shared_header = _read_header_via_tar_offset(path, date_tar_offset)

    # Build a full ZipCode using filename-derived IFUSLOT/AMP plus minimal header metadata read
    zipcode = None
    if amp_token:
        ifuslot = amp_token[:3]
        # Populate IFUSLOT metadata cache on first encounter
        meta = _IFUSLOT_META_CACHE.get(ifuslot)
        if meta is None:
            if shared_header is not None:
                meta = _ifuslot_meta_from_header(shared_header)
            elif outer_tar_member:
                meta = _read_ifuslot_metadata_from_date_tar(path, outer_tar_member, tar_member)
            elif tar_member:
                meta = _read_ifuslot_metadata_from_tar(path, tar_member)
            else:
                meta = _read_ifuslot_metadata_from_file(path)
            _IFUSLOT_META_CACHE[ifuslot] = meta
        zipcode = _zipcode_from_amp_token(amp_token, meta)

    if shared_header is not None:
        hdr_fields = _exposure_header_fields(shared_header, frame_type=frame_type)
    elif outer_tar_member:
        hdr_fields = _read_exposure_header_fields_from_date_tar(
            path, outer_tar_member, tar_member, frame_type=frame_type
        )
    elif tar_member:
        hdr_fields = _read_exposure_header_fields_from_tar(
            path, tar_member, frame_type=frame_type
        )
    else:
        hdr_fields = _read_exposure_header_fields_from_file(path, frame_type=frame_type)
    scientific = scientific_metadata_for_database(hdr_fields)
    exposure_details_key = (str(Path(db_path).resolve()), exposure_id)
    storage_backend = "date_tar" if outer_tar_member else ("tar" if tar_member else "filesystem")

    def _do_insert(conn_: sqlite3.Connection) -> None:
        # Upsert amplifier mapping if available
        amp_key = None
        if zipcode is not None:
            upsert_amplifier(conn_, zipcode)
            amp_key = zipcode.key()

        # exposure
        when_utc = None
        try:
            # naive parse of first token as timestamp (YYYYMMDD)
            ts = exposure_id.split("T")[0]
            # store as compact YYYYMMDD to simplify range filtering
            datetime.strptime(ts, "%Y%m%d")  # validate
            when_utc = ts
        except Exception:
            when_utc = None
        conn_.execute(
            """
            INSERT INTO exposures(id, when_utc, frame_type) VALUES(?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                when_utc=COALESCE(excluded.when_utc, exposures.when_utc),
                frame_type=COALESCE(excluded.frame_type, exposures.frame_type)
            """,
            (exposure_id, when_utc, frame_type),
        )
        abs_path = os.path.abspath(path)
        conn_.execute(
            """
            INSERT INTO raw_files(
                exposure_id, frame_type, path, tar_member, outer_tar_member, storage_backend, amp_key,
                observation_time, ambient_temperature, humidity, pressure,
                program_id, object, rho_start, theta_start, phi_start, x_start, y_start
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exposure_id, frame_type, path, tar_member) DO UPDATE SET
                outer_tar_member=excluded.outer_tar_member,
                storage_backend=excluded.storage_backend,
                amp_key=excluded.amp_key,
                observation_time=excluded.observation_time,
                ambient_temperature=excluded.ambient_temperature,
                humidity=excluded.humidity,
                pressure=excluded.pressure,
                program_id=excluded.program_id,
                object=excluded.object,
                rho_start=excluded.rho_start,
                theta_start=excluded.theta_start,
                phi_start=excluded.phi_start,
                x_start=excluded.x_start,
                y_start=excluded.y_start
            """,
            (
                exposure_id, frame_type, abs_path, tar_member, outer_tar_member, storage_backend, amp_key,
                *(scientific[field] for field in SCIENTIFIC_METADATA_FIELDS),
            ),
        )

        # Populate exposure_details once per exposure in this process to avoid repeated header I/O
        if exposure_details_key not in _POPULATED_EXPOSURE_DETAILS:
            # Determine tar_path (only for tar/date_tar backends) and expnum (<999 considered non-test)
            tar_path = abs_path if storage_backend in ("tar", "date_tar") else None
            expn = _extract_observation_number(path)
            expnum = expn if (expn is not None and expn < 999) else None
            # Upsert into exposure_details; keep any pre-existing non-null values
            conn_.execute(
                """
                INSERT INTO exposure_details(
                    exposure_id, tar_path, expnum, qobject, qprog, pexptime, date, qra, qdec,
                    exptime, ambient_temperature, object_name, virus_object, requested_target,
                    requested_target_source, requested_ifuslot, het_track, observing_mode,
                    virus_primary, q_metadata_expected, q_metadata_complete,
                    object_qobject_consistent, lamp, observing_block
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exposure_id) DO UPDATE SET
                    tar_path=COALESCE(exposure_details.tar_path, excluded.tar_path),
                    expnum=COALESCE(exposure_details.expnum, excluded.expnum),
                    qobject=COALESCE(exposure_details.qobject, excluded.qobject),
                    qprog=COALESCE(exposure_details.qprog, excluded.qprog),
                    pexptime=COALESCE(exposure_details.pexptime, excluded.pexptime),
                    date=COALESCE(exposure_details.date, excluded.date),
                    qra=COALESCE(exposure_details.qra, excluded.qra),
                    qdec=COALESCE(exposure_details.qdec, excluded.qdec),
                    exptime=COALESCE(exposure_details.exptime, excluded.exptime),
                    ambient_temperature=COALESCE(exposure_details.ambient_temperature, excluded.ambient_temperature),
                    object_name=COALESCE(exposure_details.object_name, excluded.object_name),
                    virus_object=COALESCE(exposure_details.virus_object, excluded.virus_object),
                    requested_target=COALESCE(exposure_details.requested_target, excluded.requested_target),
                    requested_target_source=COALESCE(exposure_details.requested_target_source, excluded.requested_target_source),
                    requested_ifuslot=COALESCE(exposure_details.requested_ifuslot, excluded.requested_ifuslot),
                    het_track=COALESCE(exposure_details.het_track, excluded.het_track),
                    observing_mode=COALESCE(exposure_details.observing_mode, excluded.observing_mode),
                    virus_primary=COALESCE(exposure_details.virus_primary, excluded.virus_primary),
                    q_metadata_expected=COALESCE(exposure_details.q_metadata_expected, excluded.q_metadata_expected),
                    q_metadata_complete=COALESCE(exposure_details.q_metadata_complete, excluded.q_metadata_complete),
                    object_qobject_consistent=COALESCE(exposure_details.object_qobject_consistent, excluded.object_qobject_consistent),
                    lamp=COALESCE(exposure_details.lamp, excluded.lamp),
                    observing_block=COALESCE(exposure_details.observing_block, excluded.observing_block)
                """,
                (
                    exposure_id,
                    tar_path,
                    expnum,
                    hdr_fields.get("qobject"),
                    hdr_fields.get("qprog"),
                    _optional_float(hdr_fields.get("pexptime")),
                    hdr_fields.get("date"),
                    hdr_fields.get("qra"),
                    hdr_fields.get("qdec"),
                    _optional_float(hdr_fields.get("exptime")),
                    _optional_float(hdr_fields.get("ambient_temperature")),
                    hdr_fields.get("object_name"),
                    hdr_fields.get("virus_object"),
                    hdr_fields.get("requested_target"),
                    hdr_fields.get("requested_target_source"),
                    hdr_fields.get("requested_ifuslot"),
                    hdr_fields.get("het_track"),
                    hdr_fields.get("observing_mode"),
                    hdr_fields.get("virus_primary"),
                    hdr_fields.get("q_metadata_expected"),
                    hdr_fields.get("q_metadata_complete"),
                    hdr_fields.get("object_qobject_consistent"),
                    hdr_fields.get("lamp"),
                    hdr_fields.get("observing_block"),
                ),
            )
            _POPULATED_EXPOSURE_DETAILS.add(exposure_details_key)

    if conn is None:
        with connect(db_path) as conn_ctx:
            _do_insert(conn_ctx)
    else:
        _do_insert(conn)
    return RawFileId(
        exposure_id=exposure_id,
        frame_type=frame_type,
        path=os.path.abspath(path),
        tar_member=tar_member,
        storage_backend=storage_backend,
        zipcode=zipcode,
        outer_tar_member=outer_tar_member,
    )


def list_exposures(db_path: str = DEFAULT_RAW_DB_PATH) -> List[str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM exposures ORDER BY id").fetchall()
        return [r[0] for r in rows]


def observation_exposure_ids(
    observation_id: str,
    *,
    db_path: str = DEFAULT_RAW_DB_PATH,
) -> List[str]:
    """Resolve science-exposure membership from scanned observation metadata."""

    import re

    match = re.fullmatch(r"(\d{8})-OBSID(\d+)", str(observation_id), re.IGNORECASE)
    if match is None:
        raise ValueError("observation id must use YYYYMMDD-OBSID<number>")
    date_token, expnum = match.group(1), int(match.group(2))
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT e.id FROM exposures e JOIN exposure_details d ON d.exposure_id=e.id "
            "WHERE d.expnum=? AND e.id LIKE ? AND lower(e.frame_type)='sci' "
            "ORDER BY e.when_utc,e.id",
            (expnum, f"{date_token}%"),
        ).fetchall()
    return [str(row[0]) for row in rows]


def list_raw_files(exposure_id: Optional[str] = None, db_path: str = DEFAULT_RAW_DB_PATH) -> List[RawFileId]:
    with connect(db_path) as conn:
        if exposure_id:
            rows = conn.execute(
                "SELECT exposure_id, frame_type, path, tar_member, storage_backend, amp_key, outer_tar_member "
                "FROM raw_files WHERE exposure_id=?",
                (exposure_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT exposure_id, frame_type, path, tar_member, storage_backend, amp_key, outer_tar_member "
                "FROM raw_files",
            ).fetchall()
        out: List[RawFileId] = []
        for r in rows:
            zipcode = None
            if r[5]:
                try:
                    from ..core.identity import parse_zipcode_key

                    zipcode = parse_zipcode_key(str(r[5]))
                except (SystemExit, ValueError):
                    zipcode = None
            out.append(
                RawFileId(
                    exposure_id=r[0], frame_type=r[1], path=r[2], tar_member=r[3], storage_backend=r[4],
                    zipcode=zipcode, outer_tar_member=r[6],
                )
            )
        return out


def list_raw_file_rows(exposure_id: str, db_path: str = DEFAULT_RAW_DB_PATH) -> List[Tuple[int, RawFileId]]:
    """Return raw row identities with canonical amplifier identities for lineage."""

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, exposure_id, frame_type, path, tar_member, storage_backend, amp_key, outer_tar_member "
            "FROM raw_files WHERE exposure_id=? ORDER BY id",
            (str(exposure_id),),
        ).fetchall()
    out: List[Tuple[int, RawFileId]] = []
    for row in rows:
        zipcode = None
        if row[6]:
            try:
                from ..core.identity import parse_zipcode_key

                zipcode = parse_zipcode_key(str(row[6]))
            except (SystemExit, ValueError):
                zipcode = None
        out.append((
            int(row[0]),
            RawFileId(row[1], row[2], row[3], row[4], row[5], zipcode, outer_tar_member=row[7]),
        ))
    return out


def list_raw_scientific_metadata(
    raw_ids: Iterable[int], *, db_path: str = DEFAULT_RAW_DB_PATH
) -> List[Dict[str, Any]]:
    """Return compact raw scientific state for an already selected membership."""

    wanted = tuple(dict.fromkeys(int(value) for value in raw_ids))
    if not wanted:
        return []
    placeholders = ",".join("?" for _ in wanted)
    columns = ", ".join(SCIENTIFIC_METADATA_FIELDS)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT id AS raw_id, {columns} FROM raw_files "
            f"WHERE id IN ({placeholders})",
            wanted,
        ).fetchall()
    by_id = {int(row["raw_id"]): dict(row) for row in rows}
    return [by_id[value] for value in wanted if value in by_id]


def list_raw_files_scoped(
    frame_type: str,
    start_date: str,
    end_date: str,
    zipcode: Optional[ZipCode] = None,
    db_path: str = DEFAULT_RAW_DB_PATH,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> List[Tuple[int, RawFileId]]:
    """List raw files filtered by frame_type and exposure date window, optionally by zipcode.

    Returns list of (raw_db_id, RawFileId) tuples. Dates compare against exposures.when_utc (YYYYMMDD prefix).
    """
    # Normalize dates to YYYYMMDD using shared helper
    sd, ed = _date8(start_date), _date8(end_date)
    with connect(db_path) as conn:
        base = (
            "SELECT rf.id, rf.exposure_id, rf.frame_type, rf.path, rf.tar_member, rf.storage_backend, "
            "rf.amp_key, a.ifuslot, a.ifuid, a.specid, a.amp, a.controller, tm.offset, tm.size, rf.outer_tar_member "
            "FROM raw_files rf JOIN exposures e ON rf.exposure_id = e.id "
            "LEFT JOIN amplifiers a ON rf.amp_key = a.key "
            "LEFT JOIN tar_members tm ON tm.tar_path=rf.path AND tm.member=rf.tar_member "
            "WHERE LOWER(rf.frame_type)=LOWER(?) AND e.when_utc IS NOT NULL AND substr(replace(e.when_utc,'-',''),1,8) BETWEEN ? AND ?"
        )
        params: List[str] = [frame_type, sd, ed]
        if zipcode is not None:
            base += " AND rf.amp_key=?"
            params.append(zipcode.key())
        rows = conn.execute(base, tuple(params)).fetchall()
        if start_time is not None or end_time is not None:
            def _instant(value: str) -> Optional[datetime]:
                try:
                    return datetime.strptime(str(value), "%Y%m%dT%H%M%S.%f")
                except ValueError:
                    try:
                        return datetime.strptime(str(value).split(".", 1)[0], "%Y%m%dT%H%M%S")
                    except ValueError:
                        return None
            rows = [
                row for row in rows
                if (instant := _instant(row[1])) is None
                or ((start_time is None or instant >= start_time)
                    and (end_time is None or instant <= end_time))
            ]
        out: List[Tuple[int, RawFileId]] = []
        for r in rows:
            zc: Optional[ZipCode] = None
            if r[7] is not None:
                zc = ZipCode(ifuslot=r[7], ifuid=r[8], specid=r[9], amp=r[10], controller=r[11])
            elif r[6]:
                try:
                    from ..core.identity import parse_zipcode_key

                    zc = parse_zipcode_key(str(r[6]))
                except (SystemExit, ValueError):
                    zc = None
            rf = RawFileId(
                exposure_id=r[1], frame_type=r[2], path=r[3], tar_member=r[4],
                storage_backend=r[5], zipcode=zc, archive_offset=r[12], archive_size=r[13],
                outer_tar_member=r[14],
            )
            out.append((int(r[0]), rf))
        return out


def list_raw_files_by_ids(
    raw_ids: Iterable[int], *, db_path: str = DEFAULT_RAW_DB_PATH
) -> List[Tuple[int, RawFileId]]:
    """Resolve an already-planned raw membership without re-querying a window."""

    wanted = tuple(dict.fromkeys(int(value) for value in raw_ids))
    if not wanted:
        return []
    placeholders = ",".join("?" for _ in wanted)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT rf.id, rf.exposure_id, rf.frame_type, rf.path, rf.tar_member, "
            "rf.storage_backend, rf.amp_key, a.ifuslot, a.ifuid, a.specid, a.amp, "
            "a.controller, tm.offset, tm.size, rf.outer_tar_member FROM raw_files rf "
            "LEFT JOIN amplifiers a ON rf.amp_key=a.key "
            "LEFT JOIN tar_members tm ON tm.tar_path=rf.path AND tm.member=rf.tar_member "
            f"WHERE rf.id IN ({placeholders})",
            wanted,
        ).fetchall()
    by_id = {}
    for row in rows:
        zipcode = None
        if row[7] is not None:
            zipcode = ZipCode(row[7], row[8], row[9], row[10], row[11])
        by_id[int(row[0])] = RawFileId(
            exposure_id=row[1], frame_type=row[2], path=row[3], tar_member=row[4],
            storage_backend=row[5], zipcode=zipcode,
            archive_offset=row[12], archive_size=row[13], outer_tar_member=row[14],
        )
    missing = set(wanted) - set(by_id)
    if missing:
        raise KeyError(f"planned raw rows no longer exist: {sorted(missing)}")
    return [(value, by_id[value]) for value in wanted]


def list_calibration_grouping_rows(
    *, db_path: str, zipcode: ZipCode, frame_types: Iterable[str],
    start_date: Optional[str] = None, end_date: Optional[str] = None,
) -> List[dict]:
    """Return raw identities plus exposure metadata used only for grouping/reporting."""

    types = tuple(dict.fromkeys(str(value).lower() for value in frame_types))
    if not types:
        return []
    placeholders = ",".join("?" for _ in types)
    sql = (
        "SELECT rf.id AS raw_id, rf.exposure_id, lower(rf.frame_type) AS frame_type, "
        "e.when_utc, d.exptime, d.pexptime, d.ambient_temperature, d.object_name, "
        "d.qobject, d.lamp, d.observing_block, d.qprog "
        "FROM raw_files rf JOIN exposures e ON e.id=rf.exposure_id "
        "LEFT JOIN exposure_details d ON d.exposure_id=rf.exposure_id "
        f"WHERE rf.amp_key=? AND lower(rf.frame_type) IN ({placeholders})"
    )
    params: List[object] = [zipcode.key(), *types]
    if start_date:
        sql += " AND substr(replace(e.when_utc,'-',''),1,8) >= ?"
        params.append(_date8(start_date))
    if end_date:
        sql += " AND substr(replace(e.when_utc,'-',''),1,8) <= ?"
        params.append(_date8(end_date))
    sql += " ORDER BY rf.exposure_id, rf.id"
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def save_artifact(artifact, prov, db_path: str = DEFAULT_DB_PATH) -> int:
    """Persist artifact and provenance rows with basic retry on SQLite lock.

    In concurrent test/execution scenarios, a separate connection may hold a write lock
    momentarily. We enable autocommit on connections, but still add a bounded retry here
    to improve robustness.
    """
    import sqlite3 as _sqlite3
    import time as _time

    last_err: Exception | None = None
    for attempt in range(5):
        try:
            with connect(db_path) as conn:
                # Normalize artifact fields from either a dict-like or object with attributes
                akind = artifact.get("kind") if isinstance(artifact, dict) else getattr(artifact, "kind", None)
                aname = artifact.get("name") if isinstance(artifact, dict) else getattr(artifact, "name", None)
                apath = artifact.get("path") if isinstance(artifact, dict) else getattr(artifact, "path", None)
                azip = artifact.get("zipcode") if isinstance(artifact, dict) else getattr(artifact, "zipcode", None)
                vstart = artifact.get("validity_start") if isinstance(artifact, dict) else getattr(artifact, "validity_start", None)
                vend = artifact.get("validity_end") if isinstance(artifact, dict) else getattr(artifact, "validity_end", None)
                amp_key = azip.key() if azip is not None else None
                vstart_iso = vstart.isoformat() if hasattr(vstart, "isoformat") and vstart is not None else None
                vend_iso = vend.isoformat() if hasattr(vend, "isoformat") and vend is not None else None
                cur = conn.execute(
                    """
                    INSERT INTO artifacts(kind, name, path, amp_key, validity_start, validity_end)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        akind,
                        aname or akind,
                        apath,
                        amp_key,
                        vstart_iso,
                        vend_iso,
                    ),
                )
                artifact_id = cur.lastrowid
                # Normalize provenance fields from either dict-like or object
                def _get(obj, name):
                    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
                sw = _get(prov, "software_version")
                git = _get(prov, "git_commit")
                algo = _get(prov, "algorithm")
                phash = _get(prov, "parameters_hash")
                created = _get(prov, "created_at")
                parents_list = _get(prov, "parents") or []
                if isinstance(parents_list, str):
                    parents_list = [parents_list]
                parents_list = [str(p) for p in parents_list]
                created_iso = created.isoformat() if hasattr(created, "isoformat") and created is not None else datetime.utcnow().isoformat()
                parents = ",".join(parents_list)
                conn.execute(
                    """
                    INSERT INTO provenance(artifact_id, software_version, git_commit, algorithm, parameters_hash, created_at, parents)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        sw,
                        git,
                        algo,
                        phash,
                        created_iso,
                        parents,
                    ),
                )
                return artifact_id
        except _sqlite3.OperationalError as e:
            # Retry only on locking errors
            msg = str(e).lower()
            if "database is locked" in msg or "database locked" in msg or "busy" in msg:
                last_err = e
                from ..performance import phase
                with phase("database_lock_wait"):
                    _time.sleep(0.1 * (attempt + 1))
                continue
            raise
    # If retries exhausted, re-raise the last locking error
    if last_err:
        raise last_err
    raise RuntimeError("save_artifact failed unexpectedly without an exception")


def save_artifact_details(
    artifact_id: int,
    *,
    record: Dict[str, Any],
    components: Iterable[Dict[str, Any]],
    relations: Iterable[Dict[str, Any]],
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Persist canonical Artifact registry rows, components, relations, and state."""
    import json

    init_artifact_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO artifact_records(
                artifact_id, canonical_kind, role, payload_type, storage_format,
                physical_scope, exposure_id, observation_id, dither_set_id,
                revision, checksum, units_json, coordinates_json,
                configuration_refs_json, metadata_json, validity_policy,
                lifecycle, state, payload_bytes, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                canonical_kind=excluded.canonical_kind,
                role=excluded.role,
                payload_type=excluded.payload_type,
                storage_format=excluded.storage_format,
                physical_scope=excluded.physical_scope,
                exposure_id=excluded.exposure_id,
                observation_id=excluded.observation_id,
                dither_set_id=excluded.dither_set_id,
                revision=excluded.revision,
                checksum=excluded.checksum,
                units_json=excluded.units_json,
                coordinates_json=excluded.coordinates_json,
                configuration_refs_json=excluded.configuration_refs_json,
                metadata_json=excluded.metadata_json,
                validity_policy=excluded.validity_policy,
                lifecycle=excluded.lifecycle,
                state=excluded.state,
                payload_bytes=excluded.payload_bytes
            """,
            (
                int(artifact_id),
                record.get("canonical_kind"),
                record.get("role"),
                record.get("payload_type"),
                record.get("storage_format"),
                record.get("physical_scope"),
                record.get("exposure_id"),
                record.get("observation_id"),
                record.get("dither_set_id"),
                record.get("revision"),
                record.get("checksum"),
                json.dumps(record.get("units") or {}, sort_keys=True),
                json.dumps(record.get("coordinates") or {}, sort_keys=True),
                json.dumps(record.get("configuration_refs") or [], sort_keys=True),
                json.dumps(record.get("metadata") or {}, sort_keys=True, default=str),
                record.get("validity_policy"),
                record.get("lifecycle") or "canonical",
                record.get("state") or "active",
                int(record.get("payload_bytes") or 0),
                record.get("created_at") or datetime.utcnow().isoformat(),
            ),
        )
        scientific = scientific_metadata_for_database(record.get("scientific_metadata"))
        conn.execute(
            """
            INSERT INTO artifact_scientific_metadata(
                artifact_id, observation_time, ambient_temperature, humidity, pressure,
                program_id, object, rho_start, theta_start, phi_start, x_start, y_start
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                observation_time=excluded.observation_time,
                ambient_temperature=excluded.ambient_temperature,
                humidity=excluded.humidity,
                pressure=excluded.pressure,
                program_id=excluded.program_id,
                object=excluded.object,
                rho_start=excluded.rho_start,
                theta_start=excluded.theta_start,
                phi_start=excluded.phi_start,
                x_start=excluded.x_start,
                y_start=excluded.y_start
            """,
            (
                int(artifact_id),
                *(scientific[field] for field in SCIENTIFIC_METADATA_FIELDS),
            ),
        )
        for component in components:
            conn.execute(
                """
                INSERT INTO artifact_components(
                    artifact_id, name, model_type, path, payload_type,
                    storage_format, checksum, units, coordinates, metadata_json,
                    payload_bytes, dtype, shape_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(artifact_id, name) DO UPDATE SET
                    model_type=excluded.model_type,
                    path=excluded.path,
                    payload_type=excluded.payload_type,
                    storage_format=excluded.storage_format,
                    checksum=excluded.checksum,
                    units=excluded.units,
                    coordinates=excluded.coordinates,
                    metadata_json=excluded.metadata_json,
                    payload_bytes=excluded.payload_bytes,
                    dtype=excluded.dtype,
                    shape_json=excluded.shape_json
                """,
                (
                    int(artifact_id),
                    component.get("name"),
                    component.get("model_type"),
                    component.get("path"),
                    component.get("payload_type"),
                    component.get("storage_format"),
                    component.get("checksum"),
                    component.get("units"),
                    component.get("coordinates"),
                    json.dumps(component.get("metadata") or {}, sort_keys=True, default=str),
                    int(component.get("payload_bytes") or 0),
                    component.get("dtype"),
                    json.dumps(component.get("shape") or []),
                ),
            )
        for relation in relations:
            parent_id = int(relation["parent_id"])
            relation_name = str(relation.get("relation") or "derived_from")
            conn.execute(
                "INSERT OR IGNORE INTO dependencies(parent_id, child_id) VALUES(?,?)",
                (parent_id, int(artifact_id)),
            )
            conn.execute(
                "INSERT OR IGNORE INTO artifact_relations(parent_id, child_id, relation) VALUES(?,?,?)",
                (parent_id, int(artifact_id), relation_name),
            )


def get_artifact_details(artifact_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    import json

    init_artifact_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM artifact_records WHERE artifact_id=?", (int(artifact_id),)).fetchone()
        if row is None:
            return None
        out = dict(row)
        for column in ("units_json", "coordinates_json", "configuration_refs_json", "metadata_json"):
            key = column.removesuffix("_json")
            try:
                out[key] = json.loads(out.pop(column) or ("[]" if key == "configuration_refs" else "{}"))
            except Exception:
                out[key] = [] if key == "configuration_refs" else {}
        return out


def get_artifact_scientific_metadata(
    artifact_id: int, db_path: str = DEFAULT_DB_PATH
) -> Optional[Dict[str, Any]]:
    init_artifact_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM artifact_scientific_metadata WHERE artifact_id=?",
            (int(artifact_id),),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result.pop("artifact_id", None)
    return result


def get_artifact_by_revision(revision: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_artifact_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT artifact_id FROM artifact_records WHERE revision=?", (str(revision),)
        ).fetchone()
    return get_artifact(int(row[0]), db_path=db_path) if row is not None else None


def set_artifact_state(artifact_id: int, state: str, db_path: str = DEFAULT_DB_PATH) -> None:
    if state not in {"active", "obsolete", "evicted"}:
        raise ValueError(f"invalid artifact state: {state}")
    init_artifact_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE artifact_records SET state=? WHERE artifact_id=?",
            (state, int(artifact_id)),
        )


def list_artifact_components(artifact_id: int, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    import json

    init_artifact_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM artifact_components WHERE artifact_id=? ORDER BY name", (int(artifact_id),)
        ).fetchall()
        out = _rows_to_dicts(rows)
        for row in out:
            try:
                row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
            except Exception:
                row["metadata"] = {}
            try:
                row["shape"] = json.loads(row.pop("shape_json") or "[]")
            except Exception:
                row["shape"] = []
        return out


def list_artifact_relations(artifact_id: int, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_artifact_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT parent_id, child_id, relation FROM artifact_relations WHERE child_id=? ORDER BY parent_id, relation",
            (int(artifact_id),),
        ).fetchall()
        return _rows_to_dicts(rows)


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


def find_artifacts(
    *,
    kind: str,
    zipcode: Optional[ZipCode] = None,
    at_time: Optional[datetime] = None,
    db_path: str = DEFAULT_DB_PATH,
    limit: Optional[int] = None,
) -> List[Dict]:
    """Find artifacts by kind, optional zipcode scope, and optional validity at_time.

    Orders by provenance.created_at DESC (newest first).
    """
    # Normalize time to ISO for lexical compare
    at_iso: Optional[str] = _as_iso(at_time)
    with connect(db_path) as conn:
        sql = (
            "SELECT a.*, p.software_version, p.git_commit, p.algorithm, p.parameters_hash, p.created_at, p.parents "
            "FROM artifacts a LEFT JOIN provenance p ON a.id = p.artifact_id WHERE a.kind = ?"
        )
        params: List[Any] = [kind]
        zkey = _zc_key(zipcode)
        if zkey is not None:
            sql += " AND a.amp_key = ?"
            params.append(zkey)
        if at_iso is not None:
            # validity_start <= at_iso <= validity_end; allow NULLs to mean open intervals
            sql += (
                " AND (a.validity_start IS NULL OR a.validity_start <= ?)"
                " AND (a.validity_end IS NULL OR a.validity_end >= ?)"
            )
            params.extend([at_iso, at_iso])
        sql += " ORDER BY p.created_at DESC NULLS LAST, a.id DESC"
        if limit and int(limit) > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, tuple(params)).fetchall()
        return _rows_to_dicts(rows)


def list_artifacts(
    *,
    kind: Optional[str] = None,
    zipcode: Optional[ZipCode] = None,
    at_time: Optional[datetime] = None,
    db_path: str = DEFAULT_DB_PATH,
    limit: Optional[int] = None,
) -> List[Dict]:
    """List artifacts with optional filters.

    If kind is None, return all kinds. Optionally filter by zipcode and validity window.
    Orders by provenance.created_at DESC (newest first).
    """
    at_iso: Optional[str] = _as_iso(at_time)
    with connect(db_path) as conn:
        sql = (
            "SELECT a.*, p.software_version, p.git_commit, p.algorithm, p.parameters_hash, p.created_at, p.parents, "
            "q.status AS qa_status, q.metrics_json AS qa_metrics_json "
            "FROM artifacts a "
            "LEFT JOIN provenance p ON a.id = p.artifact_id "
            "LEFT JOIN qa_results q ON a.id = q.artifact_id "
            "WHERE 1=1"
        )
        params: List[Any] = []
        if kind:
            sql += " AND a.kind = ?"
            params.append(kind)
        zkey = _zc_key(zipcode)
        if zkey is not None:
            sql += " AND a.amp_key = ?"
            params.append(zkey)
        if at_iso is not None:
            sql += (
                " AND (a.validity_start IS NULL OR a.validity_start <= ?)"
                " AND (a.validity_end IS NULL OR a.validity_end >= ?)"
            )
            params.extend([at_iso, at_iso])
        sql += " ORDER BY p.created_at DESC NULLS LAST, a.id DESC"
        if limit and int(limit) > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, tuple(params)).fetchall()
        return _rows_to_dicts(rows)


def find_artifact_summaries(
    *,
    kind: Optional[str] = None,
    hardware_scope: Optional[ZipCode | str] = None,
    observation_time: Optional[Tuple[Optional[datetime], Optional[datetime]]] = None,
    ambient_temperature: Optional[Tuple[Optional[float], Optional[float]]] = None,
    humidity: Optional[Tuple[Optional[float], Optional[float]]] = None,
    pressure: Optional[Tuple[Optional[float], Optional[float]]] = None,
    program_id: Optional[str] = None,
    object_name: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Find compact Artifact state with one joined registry query."""

    init_artifact_db(db_path)
    sql = """
        WITH component_names AS (
            SELECT artifact_id, group_concat(name, char(31)) AS names
            FROM (
                SELECT artifact_id, name
                FROM artifact_components
                ORDER BY artifact_id, name
            )
            GROUP BY artifact_id
        ),
        parent_ids AS (
            SELECT child_id, group_concat(parent_id, char(31)) AS ids
            FROM (
                SELECT DISTINCT child_id, parent_id
                FROM artifact_relations
                ORDER BY child_id, parent_id
            )
            GROUP BY child_id
        )
        SELECT
            a.id AS artifact_id,
            COALESCE(ar.canonical_kind, a.kind) AS kind,
            a.amp_key,
            ar.physical_scope,
            ar.exposure_id,
            ar.observation_id,
            ar.dither_set_id,
            ar.state,
            a.validity_start,
            a.validity_end,
            p.created_at,
            COALESCE(qd.status, qr.status) AS qa_status,
            qd.usability,
            sm.observation_time,
            sm.ambient_temperature,
            sm.humidity,
            sm.pressure,
            sm.program_id,
            sm.object,
            sm.rho_start,
            sm.theta_start,
            sm.phi_start,
            sm.x_start,
            sm.y_start,
            component_names.names AS component_names,
            parent_ids.ids AS parent_ids
        FROM artifacts a
        JOIN artifact_records ar ON ar.artifact_id=a.id
        LEFT JOIN provenance p ON p.artifact_id=a.id
        LEFT JOIN qa_decisions qd ON qd.artifact_id=a.id
        LEFT JOIN qa_results qr ON qr.artifact_id=a.id
        LEFT JOIN artifact_scientific_metadata sm ON sm.artifact_id=a.id
        LEFT JOIN component_names ON component_names.artifact_id=a.id
        LEFT JOIN parent_ids ON parent_ids.child_id=a.id
        WHERE ar.state='active'
    """
    params: List[Any] = []
    if kind is not None:
        sql += " AND COALESCE(ar.canonical_kind, a.kind)=?"
        params.append(str(kind))
    if hardware_scope is not None:
        key = hardware_scope.key() if isinstance(hardware_scope, ZipCode) else str(hardware_scope)
        sql += " AND a.amp_key=?"
        params.append(key)

    if observation_time is not None:
        start, end = observation_time
        if start is not None:
            start_value = scientific_metadata_for_database(
                {"observation_time": start}
            )["observation_time"]
            if start_value is None:
                raise ValueError("invalid observation-time interval start")
            sql += " AND sm.observation_time>=?"
            params.append(start_value)
        if end is not None:
            end_value = scientific_metadata_for_database(
                {"observation_time": end}
            )["observation_time"]
            if end_value is None:
                raise ValueError("invalid observation-time interval end")
            sql += " AND sm.observation_time<=?"
            params.append(end_value)

    for column, bounds in (
        ("ambient_temperature", ambient_temperature),
        ("humidity", humidity),
        ("pressure", pressure),
    ):
        if bounds is None:
            continue
        minimum, maximum = bounds
        if minimum is not None:
            value = _optional_float(minimum)
            if value is None:
                raise ValueError(f"invalid {column} interval minimum")
            sql += f" AND sm.{column}>=?"
            params.append(value)
        if maximum is not None:
            value = _optional_float(maximum)
            if value is None:
                raise ValueError(f"invalid {column} interval maximum")
            sql += f" AND sm.{column}<=?"
            params.append(value)
    if program_id is not None:
        sql += " AND sm.program_id=?"
        params.append(str(program_id))
    if object_name is not None:
        sql += " AND sm.object=?"
        params.append(str(object_name))
    sql += " ORDER BY p.created_at DESC, a.id DESC"
    if limit is not None and int(limit) > 0:
        sql += " LIMIT ?"
        params.append(int(limit))

    with connect(db_path) as conn:
        rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
    from ..core.identity import parse_zipcode_key

    separator = chr(31)
    for row in rows:
        zc = None
        if row.get("amp_key"):
            try:
                zc = parse_zipcode_key(str(row["amp_key"]))
            except (SystemExit, ValueError):
                zc = None
        row["ifuslot"] = zc.ifuslot if zc else None
        row["ifuid"] = zc.ifuid if zc else None
        row["specid"] = zc.specid if zc else None
        row["amp"] = zc.amp if zc else None
        row["controller"] = zc.controller if zc else None
        row["component_names"] = (
            str(row["component_names"]).split(separator)
            if row.get("component_names")
            else []
        )
        row["parent_ids"] = (
            [int(value) for value in str(row["parent_ids"]).split(separator)]
            if row.get("parent_ids")
            else []
        )
    return rows


def save_qa_results(
    artifact_id: int,
    *,
    status: str,
    metrics: Optional[Dict[str, Any]] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Upsert QA results for an artifact.

    metrics is serialized to JSON for storage.
    """
    import json as _json
    with connect(db_path) as conn:
        payload = _json.dumps(metrics or {}, sort_keys=True, separators=(",", ":"))
        conn.execute(
            """
            INSERT INTO qa_results(artifact_id, status, metrics_json)
            VALUES(?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                status=excluded.status,
                metrics_json=excluded.metrics_json
            """,
            (int(artifact_id), str(status), payload),
        )


def save_qa_bundle(
    artifact_id: int,
    *,
    facts: Dict[str, Dict[str, Any]],
    status: str,
    usability: str,
    policy_version: str,
    rules: Iterable[Dict[str, Any]] = (),
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    import json

    init_artifact_db(db_path)
    with connect(db_path) as conn:
        for name, fact in facts.items():
            conn.execute(
                """
                INSERT INTO qa_facts(artifact_id, name, value_json, units, component)
                VALUES(?,?,?,?,?)
                ON CONFLICT(artifact_id, name) DO UPDATE SET
                    value_json=excluded.value_json,
                    units=excluded.units,
                    component=excluded.component
                """,
                (
                    int(artifact_id),
                    str(name),
                    json.dumps(fact.get("value"), default=str),
                    fact.get("units"),
                    fact.get("component"),
                ),
            )
        conn.execute(
            """
            INSERT INTO qa_decisions(artifact_id, status, usability, policy_version, rules_json)
            VALUES(?,?,?,?,?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                status=excluded.status,
                usability=excluded.usability,
                policy_version=excluded.policy_version,
                rules_json=excluded.rules_json
            """,
            (int(artifact_id), str(status), str(usability), str(policy_version), json.dumps(list(rules), default=str)),
        )
    save_qa_results(artifact_id, status=status, metrics={name: fact.get("value") for name, fact in facts.items()}, db_path=db_path)


def get_qa_results(artifact_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Fetch QA status and metrics for an artifact id.

    Returns a dict with keys: artifact_id, status, metrics (dict) or None if missing.
    """
    import json as _json
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT artifact_id, status, metrics_json FROM qa_results WHERE artifact_id=?",
            (int(artifact_id),),
        ).fetchone()
        if not row:
            return None
        try:
            metrics = _json.loads(row[2]) if row[2] else {}
        except Exception:
            metrics = {}
        return {"artifact_id": int(row[0]), "status": row[1], "metrics": metrics}


def get_qa_bundle(artifact_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Return normalized facts, decision, status, and usability for one Product."""

    import json as _json
    with connect(db_path) as conn:
        decision = conn.execute(
            "SELECT status, usability, policy_version, rules_json FROM qa_decisions WHERE artifact_id=?",
            (int(artifact_id),),
        ).fetchone()
        facts = conn.execute(
            "SELECT name, value_json, units, component FROM qa_facts WHERE artifact_id=? ORDER BY name",
            (int(artifact_id),),
        ).fetchall()
    if decision is None and not facts:
        return get_qa_results(artifact_id, db_path=db_path)
    fact_values = {}
    for row in facts:
        try:
            value = _json.loads(row[1])
        except Exception:
            value = row[1]
        fact_values[str(row[0])] = {"value": value, "units": row[2], "component": row[3]}
    try:
        rules = _json.loads(decision[3] or "[]") if decision is not None else []
    except Exception:
        rules = []
    return {
        "artifact_id": int(artifact_id),
        "status": decision[0] if decision is not None else None,
        "usability": decision[1] if decision is not None else None,
        "policy_version": decision[2] if decision is not None else None,
        "rules": rules,
        "facts": fact_values,
        "metrics": {name: value["value"] for name, value in fact_values.items()},
    }


def list_zipcodes(
    db_path: str = DEFAULT_RAW_DB_PATH,
    frame_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
) -> List[ZipCode]:
    """Discover unique ZipCodes that have raw files in an optional date window.

    Discovery uses raw_files.amp_key joined to amplifiers.key, which is populated
    during scanning/registration by parsing filenames (including tar member names).
    No files are opened during planning.
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
            base += "AND e.when_utc IS NOT NULL AND substr(replace(e.when_utc,'-',''),1,8) BETWEEN ? AND ? "
            params.extend([sd, ed])
        sql = base + "ORDER BY a.ifuslot, a.ifuid, a.specid, a.amp, a.controller"
        rows = conn.execute(sql, tuple(params)).fetchall()
        out: List[ZipCode] = [ZipCode(ifuslot=r[0], ifuid=r[1], specid=r[2], amp=r[3], controller=r[4]) for r in rows]
        if limit and limit > 0:
            out = out[: int(limit)]
        return out


def list_exposure_table(
    db_path: str = DEFAULT_RAW_DB_PATH,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    requested_target: Optional[str] = None,
    requested_program: Optional[str] = None,
    observing_mode: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[dict]:
    """Return joined exposure rows as dicts for a quick human-readable table.

    Joins exposures with exposure_details. Optional date window filters on exposures.when_utc
    (supports ISO-like or compact by normalizing to YYYYMMDD via substr(replace(...),1,8)).
    """
    def _d(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        return str(s).split("T", 1)[0]

    sd = _d(start_date)
    ed = _d(end_date)
    with connect(db_path) as conn:
        sql = (
            "SELECT e.id AS exposure_id, "
            "substr(replace(e.when_utc,'-',''),1,8) AS when_utc, "
            "e.frame_type, d.expnum, d.virus_object AS object, d.qobject, "
            "COALESCE(d.requested_target,d.qobject) AS requested_target, "
            "d.requested_target_source, d.qprog, d.qra, d.qdec, d.requested_ifuslot, "
            "d.het_track, d.observing_mode, d.virus_primary, d.q_metadata_expected, "
            "d.q_metadata_complete, d.object_qobject_consistent, d.exptime, d.pexptime, "
            "d.date, d.tar_path, d.object_name AS legacy_object_name "
            "FROM exposures e LEFT JOIN exposure_details d ON e.id = d.exposure_id "
            "WHERE 1=1 "
        )
        params: List[object] = []
        if sd and ed:
            sql += "AND e.when_utc IS NOT NULL AND substr(replace(e.when_utc,'-',''),1,8) BETWEEN ? AND ? "
            params.extend([sd, ed])
        if requested_target:
            sql += "AND COALESCE(d.requested_target,d.qobject)=? "
            params.append(str(requested_target))
        if requested_program:
            sql += "AND d.qprog=? "
            params.append(str(requested_program))
        if observing_mode:
            sql += "AND d.observing_mode=? "
            params.append(str(observing_mode).lower())
        sql += "ORDER BY e.id"
        if limit and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def get_exposure_metadata(exposure_id: str, db_path: str = DEFAULT_RAW_DB_PATH) -> Optional[dict]:
    """Return raw and interpreted metadata for one atomic exposure."""

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT e.id AS exposure_id, e.when_utc, e.frame_type, d.* "
            "FROM exposures e LEFT JOIN exposure_details d ON d.exposure_id=e.id "
            "WHERE e.id=?",
            (str(exposure_id),),
        ).fetchone()
    return dict(row) if row is not None else None


def ensure_tar_index(tar_path: str, db_path: str = DEFAULT_RAW_DB_PATH, conn: Optional[sqlite3.Connection] = None) -> None:
    """Ensure a DB-backed index of a tar member offsets/sizes exists for an uncompressed .tar.

    Stores validated (mtime,size) for the tar file. If metadata changed, reindex members.
    Silently no-ops for compressed tars or on any tar errors.
    """
    import os as _os
    import tarfile as _tarfile

    try:
        st = _os.stat(tar_path)
    except OSError:
        return
    meta = (st.st_mtime, st.st_size)

    def _needs_reindex(c: sqlite3.Connection) -> bool:
        row = c.execute("SELECT mtime, size FROM tar_files WHERE path=?", (tar_path,)).fetchone()
        if row is None:
            return True
        try:
            return (float(row[0]) != float(meta[0])) or (int(row[1]) != int(meta[1]))
        except Exception:
            return True

    def _reindex(c: sqlite3.Connection) -> None:
        # Attempt uncompressed open only
        try:
            with _tarfile.open(tar_path, mode="r:") as tf:
                members = [m for m in tf.getmembers() if m.isfile() and m.name.lower().endswith(".fits")] 
                rows = []
                for m in members:
                    if m.offset_data is None or m.size is None:
                        continue
                    rows.append((tar_path, m.name, int(m.offset_data), int(m.size)))
                c.execute("INSERT OR REPLACE INTO tar_files(path, mtime, size, n_members) VALUES(?, ?, ?, ?)", (tar_path, float(meta[0]), int(meta[1]), len(rows)))
                # Replace members for this tar
                c.execute("DELETE FROM tar_members WHERE tar_path=?", (tar_path,))
                if rows:
                    c.executemany("INSERT INTO tar_members(tar_path, member, offset, size) VALUES(?, ?, ?, ?)", rows)
        except Exception:
            # On any error (e.g., compressed tar), do not index
            return

    if conn is None:
        with connect(db_path) as c:
            if _needs_reindex(c):
                _reindex(c)
    else:
        if _needs_reindex(conn):
            _reindex(conn)


def ensure_date_tar_index(
    date_tar_path: str, outer_member: str, db_path: str = DEFAULT_RAW_DB_PATH,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Ensure a DB-backed index of a nested VIRUS tar's member offsets exists.

    Corral date-tars (e.g. 20260501.tar) contain nested VIRUS tars as ordinary
    members; each nested tar in turn contains ~300 FITS amplifier members. A
    freshly opened tarfile.TarFile has no member list yet, so calling
    .getmember() on the nested tar repeatedly (once per FITS file) forces a full
    rescan of that nested tar every time. This indexes it once per (date_tar_path,
    outer_member) pair by opening the underlying file directly and seeking to the
    nested tar's start offset, so the offsets tarfile records while scanning are
    already absolute positions within date_tar_path -- the same offset-based
    header reader used for the single-level tar backend applies unchanged.

    Silently no-ops on any tar error (e.g. compressed archives).
    """
    import os as _os
    import tarfile as _tarfile

    try:
        st = _os.stat(date_tar_path)
    except OSError:
        return
    meta = (st.st_mtime, st.st_size)

    def _needs_reindex(c: sqlite3.Connection) -> bool:
        row = c.execute(
            "SELECT mtime, size FROM date_tar_files WHERE date_tar_path=? AND outer_member=?",
            (date_tar_path, outer_member),
        ).fetchone()
        if row is None:
            return True
        try:
            return (float(row[0]) != float(meta[0])) or (int(row[1]) != int(meta[1]))
        except Exception:
            return True

    def _reindex(c: sqlite3.Connection) -> None:
        try:
            with open(date_tar_path, "rb") as fh:
                with _tarfile.open(fileobj=fh, mode="r:") as outer:
                    om = outer.getmember(outer_member)
                if om.offset_data is None:
                    return
                fh.seek(om.offset_data)
                with _tarfile.open(fileobj=fh, mode="r:") as inner:
                    members = [
                        m for m in inner.getmembers()
                        if m.isfile() and m.name.lower().endswith(".fits")
                    ]
                    rows = []
                    for m in members:
                        if m.offset_data is None or m.size is None:
                            continue
                        rows.append((date_tar_path, outer_member, m.name, int(m.offset_data), int(m.size)))
            c.execute(
                "INSERT OR REPLACE INTO date_tar_files(date_tar_path, outer_member, mtime, size, n_members) VALUES(?, ?, ?, ?, ?)",
                (date_tar_path, outer_member, float(meta[0]), int(meta[1]), len(rows)),
            )
            c.execute(
                "DELETE FROM date_tar_members WHERE date_tar_path=? AND outer_member=?",
                (date_tar_path, outer_member),
            )
            if rows:
                c.executemany(
                    "INSERT INTO date_tar_members(date_tar_path, outer_member, member, offset, size) VALUES(?, ?, ?, ?, ?)",
                    rows,
                )
        except Exception:
            # On any error (e.g., compressed tar, missing member), do not index
            return

    if conn is None:
        with connect(db_path) as c:
            if _needs_reindex(c):
                _reindex(c)
    else:
        if _needs_reindex(conn):
            _reindex(conn)
