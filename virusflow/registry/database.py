from __future__ import annotations

import os
import sqlite3
import tarfile
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Set

from astropy.io import fits

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
    storage_backend TEXT,
    amp_key TEXT,
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
# Track which exposure_ids we've already populated exposure_details for in this process
_POPULATED_EXPOSURE_DETAILS: Set[str] = set()


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
                hdr = hdul[0].header
                return {
                    "ifuid": hdr.get("IFUID"),
                    "specid": hdr.get("SPECID"),
                    "controller": hdr.get("CONTID") or hdr.get("CONTROLLER"),
                }
    except Exception:
        return {"ifuid": None, "specid": None, "controller": None}


def _read_ifuslot_metadata_from_file(path: str) -> Dict[str, Optional[str]]:
    try:
        with fits.open(path, memmap=False) as hdul:
            hdr = hdul[0].header
            return {
                "ifuid": hdr.get("IFUID"),
                "specid": hdr.get("SPECID"),
                "controller": hdr.get("CONTID") or hdr.get("CONTROLLER"),
            }
    except Exception:
        return {"ifuid": None, "specid": None, "controller": None}


def _read_exposure_header_fields_from_tar(tar_path: str, member_name: str) -> Dict[str, Optional[str]]:
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
                return {
                    "qobject": hdr.get("QOBJECT"),
                    "qprog": hdr.get("QPROG"),
                    "pexptime": (str(hdr.get("PEXPTIME")) if hdr.get("PEXPTIME") is not None else None),
                    "date": hdr.get("DATE"),
                    "qra": hdr.get("QRA"),
                    "qdec": hdr.get("QDEC"),
                }
    except Exception:
        return {"qobject": None, "qprog": None, "pexptime": None, "date": None, "qra": None, "qdec": None}


def _read_exposure_header_fields_from_file(path: str) -> Dict[str, Optional[str]]:
    try:
        with fits.open(path, memmap=False) as hdul:
            hdr = hdul[0].header
            return {
                "qobject": hdr.get("QOBJECT"),
                "qprog": hdr.get("QPROG"),
                "pexptime": (str(hdr.get("PEXPTIME")) if hdr.get("PEXPTIME") is not None else None),
                "date": hdr.get("DATE"),
                "qra": hdr.get("QRA"),
                "qdec": hdr.get("QDEC"),
            }
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


def register_raw_file(path: str, frame_type: Optional[str] = None, db_path: str = DEFAULT_DB_PATH, tar_member: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> Optional[RawFileId]:
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

    # Build a full ZipCode using filename-derived IFUSLOT/AMP plus minimal header metadata read
    zipcode = None
    if amp_token:
        ifuslot = amp_token[:3]
        # Populate IFUSLOT metadata cache on first encounter
        meta = _IFUSLOT_META_CACHE.get(ifuslot)
        if meta is None:
            if tar_member:
                meta = _read_ifuslot_metadata_from_tar(path, tar_member)
            else:
                meta = _read_ifuslot_metadata_from_file(path)
            _IFUSLOT_META_CACHE[ifuslot] = meta
        zipcode = _zipcode_from_amp_token(amp_token, meta)

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
        storage_backend = "tar" if tar_member else "filesystem"
        abs_path = os.path.abspath(path)
        conn_.execute(
            """
            INSERT OR IGNORE INTO raw_files(exposure_id, frame_type, path, tar_member, storage_backend, amp_key)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (exposure_id, frame_type, abs_path, tar_member, storage_backend, amp_key),
        )

        # Populate exposure_details once per exposure in this process to avoid repeated header I/O
        if exposure_id not in _POPULATED_EXPOSURE_DETAILS:
            # Determine tar_path (only for tar backend) and expnum (<999 considered non-test)
            tar_path = abs_path if storage_backend == "tar" else None
            expn = _extract_observation_number(path)
            expnum = expn if (expn is not None and expn < 999) else None
            # Read selected header fields from this representative file
            if tar_member:
                hdr_fields = _read_exposure_header_fields_from_tar(path, tar_member)
            else:
                hdr_fields = _read_exposure_header_fields_from_file(path)
            # Upsert into exposure_details; keep any pre-existing non-null values
            conn_.execute(
                """
                INSERT INTO exposure_details(exposure_id, tar_path, expnum, qobject, qprog, pexptime, date, qra, qdec)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exposure_id) DO UPDATE SET
                    tar_path=COALESCE(exposure_details.tar_path, excluded.tar_path),
                    expnum=COALESCE(exposure_details.expnum, excluded.expnum),
                    qobject=COALESCE(exposure_details.qobject, excluded.qobject),
                    qprog=COALESCE(exposure_details.qprog, excluded.qprog),
                    pexptime=COALESCE(exposure_details.pexptime, excluded.pexptime),
                    date=COALESCE(exposure_details.date, excluded.date),
                    qra=COALESCE(exposure_details.qra, excluded.qra),
                    qdec=COALESCE(exposure_details.qdec, excluded.qdec)
                """,
                (
                    exposure_id,
                    tar_path,
                    expnum,
                    hdr_fields.get("qobject"),
                    hdr_fields.get("qprog"),
                    (float(hdr_fields["pexptime"]) if (hdr_fields.get("pexptime") not in (None, "", "nan")) else None),
                    hdr_fields.get("date"),
                    hdr_fields.get("qra"),
                    hdr_fields.get("qdec"),
                ),
            )
            _POPULATED_EXPOSURE_DETAILS.add(exposure_id)

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
        storage_backend=("tar" if tar_member else "filesystem"),
        zipcode=zipcode,
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

    Returns list of (raw_db_id, RawFileId) tuples. Dates compare against exposures.when_utc (YYYYMMDD prefix).
    """
    # Normalize dates to YYYYMMDD
    def _d(s: str) -> str:
        return s.split("T", 1)[0]

    sd, ed = _d(start_date), _d(end_date)
    with connect(db_path) as conn:
        base = (
            "SELECT rf.id, rf.exposure_id, rf.frame_type, rf.path, rf.tar_member, rf.storage_backend, "
            "a.ifuslot, a.ifuid, a.specid, a.amp, a.controller "
            "FROM raw_files rf JOIN exposures e ON rf.exposure_id = e.id "
            "LEFT JOIN amplifiers a ON rf.amp_key = a.key "
            "WHERE LOWER(rf.frame_type)=LOWER(?) AND e.when_utc IS NOT NULL AND substr(replace(e.when_utc,'-',''),1,8) BETWEEN ? AND ?"
        )
        params: List[str] = [frame_type, sd, ed]
        if zipcode is not None:
            base += " AND a.ifuslot=? AND a.ifuid=? AND a.specid=? AND a.amp=? AND a.controller=?"
            params.extend([zipcode.ifuslot, zipcode.ifuid, zipcode.specid, zipcode.amp, zipcode.controller])
        rows = conn.execute(base, tuple(params)).fetchall()
        out: List[Tuple[int, RawFileId]] = []
        for r in rows:
            zc: Optional[ZipCode] = None
            if r[6] is not None:
                zc = ZipCode(ifuslot=r[6], ifuid=r[7], specid=r[8], amp=r[9], controller=r[10])
            rf = RawFileId(
                exposure_id=r[1], frame_type=r[2], path=r[3], tar_member=r[4], storage_backend=r[5], zipcode=zc
            )
            out.append((int(r[0]), rf))
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
    db_path: str = DEFAULT_DB_PATH,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
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
            "e.frame_type, d.expnum, d.qobject, d.qprog, d.pexptime, d.date, d.qra, d.qdec, d.tar_path "
            "FROM exposures e LEFT JOIN exposure_details d ON e.id = d.exposure_id "
            "WHERE 1=1 "
        )
        params: List[object] = []
        if sd and ed:
            sql += "AND e.when_utc IS NOT NULL AND substr(replace(e.when_utc,'-',''),1,8) BETWEEN ? AND ? "
            params.extend([sd, ed])
        sql += "ORDER BY e.id"
        if limit and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def ensure_tar_index(tar_path: str, db_path: str = DEFAULT_DB_PATH, conn: Optional[sqlite3.Connection] = None) -> None:
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
