from __future__ import annotations

from typing import List


def format_artifacts_table(rows: List[dict], *, csv: bool = False, include_summary: bool = False) -> str:
    """Render artifact rows as CSV or a fixed-width text table.

    Expected row keys: id, kind, name, path, amp_key, validity_start, validity_end, created_at, qa_status.
    If include_summary=True, also include a 'summary' column when present in rows.
    """
    if csv:
        import csv as _csv
        import io as _io
        if not rows:
            return ""
        cols = [
            "id",
            "kind",
            "name",
            "path",
            "amp_key",
            "validity_start",
            "validity_end",
            "created_at",
            "qa_status",
        ]
        if include_summary:
            cols.append("summary")
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])
        return buf.getvalue()
    # text table formatting
    cols = [
        ("id", "ID"),
        ("kind", "KIND"),
        ("name", "NAME"),
        ("path", "PATH"),
        ("amp_key", "ZIPCODE"),
        ("validity_start", "VSTART"),
        ("validity_end", "VEND"),
        ("created_at", "CREATED"),
        ("qa_status", "QA"),
    ]
    if include_summary:
        cols.append(("summary", "SUMMARY"))
    widths: List[int] = []
    for key, title in cols:
        w = len(title)
        for r in rows:
            v = r.get(key)
            s = "" if v is None else str(v)
            if len(s) > w:
                w = len(s)
        widths.append(w)
    header = " ".join([title.ljust(w) for (_k, title), w in zip(cols, widths)])
    sep = " ".join(["-" * w for w in widths])
    lines = [header, sep]
    for r in rows:
        fields: List[str] = []
        for (key, _title), w in zip(cols, widths):
            s = "" if r.get(key) is None else str(r.get(key))
            if len(s) > w:
                s = s[: w - 1] + "…" if w > 1 else s[:w]
            fields.append(s.ljust(w))
        lines.append(" ".join(fields))
    return "\n".join(lines)


def format_exposures_table(rows: List[dict], *, csv: bool = False) -> str:
    """Render joined exposure rows as CSV or fixed-width table.

    Raw OBJECT and Q* values remain distinct from interpreted target/context fields.
    """
    if csv:
        import csv as _csv
        import io as _io
        if not rows:
            return ""
        cols = [
            "exposure_id",
            "when_utc",
            "frame_type",
            "expnum",
            "object",
            "qobject",
            "requested_target",
            "qprog",
            "qra",
            "qdec",
            "observing_mode",
            "virus_primary",
            "requested_ifuslot",
            "het_track",
            "q_metadata_expected",
            "q_metadata_complete",
            "object_qobject_consistent",
            "exptime",
            "pexptime",
            "date",
            "tar_path",
        ]
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])
        return buf.getvalue()
    # text table
    cols = [
        ("exposure_id", "EXPOSURE"),
        ("when_utc", "DATE"),
        ("frame_type", "TYPE"),
        ("expnum", "EXP#"),
        ("object", "OBJECT"),
        ("qobject", "QOBJECT"),
        ("requested_target", "TARGET"),
        ("observing_mode", "MODE"),
        ("virus_primary", "PRIMARY"),
        ("requested_ifuslot", "PLACED"),
        ("het_track", "TRACK"),
        ("qprog", "QPROG"),
        ("qra", "QRA"),
        ("qdec", "QDEC"),
        ("pexptime", "PEXPTIME"),
        ("tar_path", "TAR")
    ]
    widths = []
    for key, title in cols:
        w = len(title)
        for r in rows:
            v = r.get(key)
            s = "" if v is None else str(v)
            if len(s) > w:
                w = len(s)
        widths.append(w)
    parts = []
    for (_key, title), w in zip(cols, widths):
        parts.append(title.ljust(w))
    out = [" ".join(parts)]
    out.append(" ".join(["-" * w for w in widths]))
    for r in rows:
        fields = []
        for (key, _title), w in zip(cols, widths):
            v = r.get(key)
            s = "" if v is None else str(v)
            if len(s) > w:
                s = s[: w - 1] + "…" if w > 1 else s[:w]
            fields.append(s.ljust(w))
        out.append(" ".join(fields))
    return "\n".join(out)
