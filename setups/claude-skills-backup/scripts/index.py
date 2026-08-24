#!/usr/bin/env python3
"""SQLite index for claude-skills-backup -- the record of what exists and what
existed.

The blob store holds bytes; this module holds meaning. It answers "has this unit
changed since last run", "which snapshots may be pruned", "is this blob still
needed by anybody", and "what did the world look like on 2026-03-01".

Inputs
  db_path   Path to index.db (parents created on demand).
  Unit      Discovered units, from discovery.py.
  now       UTC ISO-8601 'Z' string, threaded in from the caller so every
            function is deterministic under test. Nothing here reads the clock.

Outputs
  Integer ids (unit_id, run_id, snapshot_id), Snapshot NamedTuples -- never raw
  sqlite3 rows -- and catalog.jsonl, the git-committed, byte-stable projection of
  the snapshot table.

Two invariants this module exists to protect:

  * mark_pruned is a TOMBSTONE, never a DELETE. The blob goes; the row stays, so
    "a version existed on this date" survives retention.
  * hash_is_referenced gates blob deletion. Dedup means two units can share one
    blob, so "this unit's snapshot was pruned" does NOT mean "unlink the file".
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# The shared types module is `backup_types`, not `types`: a `types.py` in this
# directory shadows the standard library's `types` and kills interpreter
# start-up. Do not rename it back.
from backup_types import (
    ALL_KINDS,
    KIND_AGENT,
    KIND_CLAUDE_MD,
    KIND_COMMAND,
    KIND_HOOK,
    KIND_PLUGIN_MANIFEST,
    KIND_SETTINGS,
    KIND_SKILL,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    FileEntry,
    Snapshot,
    Unit,
)

# Re-exported above purely so callers can `from index import Unit` without a
# second import line; flake8 would otherwise call these unused.
_REEXPORTED = (
    ALL_KINDS,
    KIND_AGENT,
    KIND_CLAUDE_MD,
    KIND_COMMAND,
    KIND_HOOK,
    KIND_PLUGIN_MANIFEST,
    KIND_SETTINGS,
    KIND_SKILL,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    FileEntry,
)

# --- schema -----------------------------------------------------------------
SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- A unit is any single backed-up thing, identified by (kind, name).
CREATE TABLE units (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL,   -- skill|command|agent|hook|settings|claude_md|plugin_manifest
  name           TEXT NOT NULL,
  source_path    TEXT NOT NULL,   -- resolved real path, symlinks followed
  first_seen_utc TEXT NOT NULL,
  last_seen_utc  TEXT NOT NULL,   -- bumped every run, even when unchanged
  deleted_utc    TEXT,            -- set when the unit vanishes from source
  UNIQUE(kind, name)
);

-- One row per *content change*. Unchanged units create no rows.
CREATE TABLE snapshots (
  id           INTEGER PRIMARY KEY,
  unit_id      INTEGER NOT NULL REFERENCES units(id),
  content_hash TEXT NOT NULL,     -- sha256 of the canonical manifest
  blob_key     TEXT NOT NULL,     -- objects/ab/cd/<sha256>.tar.xz
  size_bytes   INTEGER NOT NULL,
  file_count   INTEGER NOT NULL,
  created_utc  TEXT NOT NULL,
  run_id       INTEGER NOT NULL REFERENCES runs(id),
  pruned_utc   TEXT               -- tombstone: row survives, blob deleted
);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  started_utc TEXT NOT NULL, finished_utc TEXT,
  units_scanned INTEGER, snapshots_created INTEGER, blobs_pruned INTEGER,
  bytes_written INTEGER, bytes_reclaimed INTEGER,
  status TEXT,                    -- ok|degraded|failed
  error TEXT
);

CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);
"""

_SNAPSHOT_COLUMNS = (
    "id, unit_id, content_hash, blob_key, size_bytes, file_count, "
    "created_utc, run_id, pruned_utc"
)

# Newest first, with id as the tie-breaker so equal timestamps still order
# deterministically (same-second runs are routine).
_NEWEST_FIRST = "ORDER BY created_utc DESC, id DESC"

_RUN_STAT_KEYS = (
    "units_scanned",
    "snapshots_created",
    "blobs_pruned",
    "bytes_written",
    "bytes_reclaimed",
)


# --- connection -------------------------------------------------------------
def connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    """Open (creating parents), apply schema if absent, set WAL + foreign_keys.

    Idempotent: an existing database is opened untouched. The schema is only
    executed when the tables are genuinely missing, which is why SCHEMA_SQL can
    stay byte-identical to the design spec (no `IF NOT EXISTS` noise).
    """
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # WAL keeps a long backup run from blocking `claude-acs backup status`.
    conn.execute("PRAGMA journal_mode = WAL")
    # Off by default in sqlite3; without it the snapshots -> units reference is
    # decoration rather than a constraint.
    conn.execute("PRAGMA foreign_keys = ON")

    if not _has_schema(conn):
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        conn.commit()
    return conn


def _has_schema(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
        "('units', 'snapshots', 'runs', 'schema_meta')"
    ).fetchone()
    return row[0] == 4


def schema_version(conn: sqlite3.Connection) -> Optional[int]:
    """Stored schema version, or None on a database that predates the marker."""
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row[0]) if row is not None else None


# --- runs -------------------------------------------------------------------
def start_run(conn: sqlite3.Connection, now: str) -> int:
    """Open a run row and return its id. Unfinished rows are how a crashed run
    stays visible afterwards (finished_utc IS NULL)."""
    cur = conn.execute("INSERT INTO runs (started_utc) VALUES (?)", (now,))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    now: str,
    status: str,
    stats: Dict[str, Any],
    error: Optional[str] = None,
) -> None:
    """Close a run: stamp finished_utc, status and counters.

    Missing keys in `stats` record as 0 rather than NULL, so `status` arithmetic
    never has to cope with None.
    """
    values = [int(stats.get(key, 0) or 0) for key in _RUN_STAT_KEYS]
    conn.execute(
        "UPDATE runs SET finished_utc = ?, units_scanned = ?, snapshots_created = ?,"
        " blobs_pruned = ?, bytes_written = ?, bytes_reclaimed = ?, status = ?,"
        " error = ? WHERE id = ?",
        [now] + values + [status, error, run_id],
    )
    conn.commit()


# --- units ------------------------------------------------------------------
def upsert_unit(conn: sqlite3.Connection, unit: Unit, now: str) -> int:
    """Insert or touch a unit; always bumps last_seen_utc. Clears deleted_utc on
    reappearance. Returns unit_id.

    last_seen_utc moves every run even when nothing changed -- that is what makes
    "no change means no snapshot" safe, and what the stability-collapse rule in
    retention.py keys on. Clearing deleted_utc matters because a skill that is
    restored from a backup (or reinstalled) must stop reading as deleted, or it
    is excluded from future scans forever.
    """
    source_path = str(unit.source_path)
    conn.execute(
        "INSERT INTO units (kind, name, source_path, first_seen_utc, last_seen_utc,"
        " deleted_utc) VALUES (?, ?, ?, ?, ?, NULL)"
        " ON CONFLICT(kind, name) DO UPDATE SET"
        "   source_path = excluded.source_path,"
        "   last_seen_utc = excluded.last_seen_utc,"
        "   deleted_utc = NULL",
        (unit.kind, unit.name, source_path, now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM units WHERE kind = ? AND name = ?", (unit.kind, unit.name)
    ).fetchone()
    return int(row[0])


def mark_missing_units(
    conn: sqlite3.Connection,
    seen_unit_ids: Sequence[int],
    now: str,
    enabled_kinds: Optional[Iterable[str]] = None,
) -> int:
    """Set deleted_utc on units of an enabled kind that were not seen this run.
    Returns the number newly tombstoned.

    `enabled_kinds` is the set of kinds actually SCANNED this run -- backup.py
    reads it from config. Passing it explicitly is what lets the one case that
    matters most be detected: a kind is enabled and EVERY one of its units was
    deleted. Inference structurally cannot see that, because a fully-emptied kind
    and a kind that was never scanned produce the same evidence (no seen ids).

    When `enabled_kinds` is None, fall back to inferring scope from the kinds
    present among `seen_unit_ids`. That keeps the fail-safe for an aborted or
    partial scan: no seen ids and no declared scope tombstones nothing, so a
    crashed run can never mass-delete the index. An explicitly empty
    `enabled_kinds` likewise means "nothing was scanned" and marks nothing.

    Already-tombstoned units keep their original deleted_utc and are not counted
    again, so re-running never rewrites a deletion date.
    """
    seen = [int(i) for i in seen_unit_ids]

    if enabled_kinds is None:
        if not seen:
            return 0
        seen_placeholders = ",".join("?" for _ in seen)
        kinds = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT kind FROM units WHERE id IN (%s)" % seen_placeholders,
                seen,
            )
        ]
    else:
        # dict.fromkeys: de-duplicate while keeping a stable parameter order.
        kinds = list(dict.fromkeys(str(k) for k in enabled_kinds))

    if not kinds:
        return 0

    kind_placeholders = ",".join("?" for _ in kinds)
    sql = (
        "UPDATE units SET deleted_utc = ? WHERE deleted_utc IS NULL"
        " AND kind IN (%s)" % kind_placeholders
    )
    params: List[Any] = [now] + kinds
    if seen:
        sql += " AND id NOT IN (%s)" % ",".join("?" for _ in seen)
        params += seen

    cur = conn.execute(sql, params)
    conn.commit()
    return int(cur.rowcount)


def iter_unit_ids(conn: sqlite3.Connection) -> List[Tuple[int, str, str]]:
    """Every unit as (unit_id, kind, name), sorted by (kind, name).

    Deleted units are INCLUDED on purpose: a skill that vanished last month still
    has restorable snapshot history, and `backup list` / `backup restore` have to
    be able to reach it. Filtering on deleted_utc here would make deletion look
    like erasure.
    """
    return [
        (int(row["id"]), row["kind"], row["name"])
        for row in conn.execute(
            "SELECT id, kind, name FROM units ORDER BY kind ASC, name ASC, id ASC"
        )
    ]


def last_run(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Most recent run as a dict, or None when no runs exist.

    Keys: id, started_utc, finished_utc, status, units_scanned,
    snapshots_created, blobs_pruned, bytes_written, bytes_reclaimed, error.
    A crashed run comes back with finished_utc None, which is exactly what
    `backup status` should surface.
    """
    row = conn.execute(
        "SELECT id, started_utc, finished_utc, status, units_scanned,"
        " snapshots_created, blobs_pruned, bytes_written, bytes_reclaimed, error"
        " FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "started_utc": row["started_utc"],
        "finished_utc": row["finished_utc"],
        "status": row["status"],
        "units_scanned": row["units_scanned"],
        "snapshots_created": row["snapshots_created"],
        "blobs_pruned": row["blobs_pruned"],
        "bytes_written": row["bytes_written"],
        "bytes_reclaimed": row["bytes_reclaimed"],
        "error": row["error"],
    }


def summary_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """{'live_units', 'deleted_units', 'live_snapshots', 'tombstones'}.

    One pass over each table; powers `backup status`. live == not tombstoned.
    """
    units = conn.execute(
        "SELECT SUM(deleted_utc IS NULL), SUM(deleted_utc IS NOT NULL) FROM units"
    ).fetchone()
    snaps = conn.execute(
        "SELECT SUM(pruned_utc IS NULL), SUM(pruned_utc IS NOT NULL) FROM snapshots"
    ).fetchone()
    return {
        "live_units": int(units[0] or 0),
        "deleted_units": int(units[1] or 0),
        "live_snapshots": int(snaps[0] or 0),
        "tombstones": int(snaps[1] or 0),
    }


# --- snapshots --------------------------------------------------------------
def _to_snapshot(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        id=int(row["id"]),
        unit_id=int(row["unit_id"]),
        content_hash=row["content_hash"],
        blob_key=row["blob_key"],
        size_bytes=int(row["size_bytes"]),
        file_count=int(row["file_count"]),
        created_utc=row["created_utc"],
        run_id=int(row["run_id"]),
        pruned_utc=row["pruned_utc"],
    )


def latest_snapshot(conn: sqlite3.Connection, unit_id: int) -> Optional[Snapshot]:
    """Newest non-pruned snapshot, or None.

    Pruned rows are excluded because callers use this to answer "did the content
    change" and to restore -- and a tombstone has no blob to compare or restore.
    """
    row = conn.execute(
        "SELECT %s FROM snapshots WHERE unit_id = ? AND pruned_utc IS NULL %s LIMIT 1"
        % (_SNAPSHOT_COLUMNS, _NEWEST_FIRST),
        (unit_id,),
    ).fetchone()
    return _to_snapshot(row) if row is not None else None


def record_snapshot(
    conn: sqlite3.Connection,
    unit_id: int,
    content_hash: str,
    blob_key: str,
    size_bytes: int,
    file_count: int,
    run_id: int,
    now: str,
) -> int:
    """Record one content change and return the snapshot id."""
    cur = conn.execute(
        "INSERT INTO snapshots (unit_id, content_hash, blob_key, size_bytes,"
        " file_count, created_utc, run_id, pruned_utc)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            unit_id,
            content_hash,
            blob_key,
            int(size_bytes),
            int(file_count),
            now,
            run_id,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def snapshots_for_unit(
    conn: sqlite3.Connection, unit_id: int, include_pruned: bool = True
) -> List[Snapshot]:
    """Newest first. Tombstones included by default -- `backup list` shows them
    so a pruned version still reports as having existed."""
    sql = "SELECT %s FROM snapshots WHERE unit_id = ?" % _SNAPSHOT_COLUMNS
    if not include_pruned:
        sql += " AND pruned_utc IS NULL"
    sql += " " + _NEWEST_FIRST
    return [_to_snapshot(row) for row in conn.execute(sql, (unit_id,))]


def mark_pruned(
    conn: sqlite3.Connection, snapshot_ids: Sequence[int], now: str
) -> None:
    """Tombstone: sets pruned_utc, keeps the row.

    This must never DELETE. The row is the only surviving evidence that a version
    existed on a given date once its blob is gone. Rows already tombstoned keep
    their original pruned_utc, so re-running prune does not rewrite history.
    """
    ids = [int(i) for i in snapshot_ids]
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        "UPDATE snapshots SET pruned_utc = ? WHERE pruned_utc IS NULL AND id IN (%s)"
        % placeholders,
        [now] + ids,
    )
    conn.commit()


def hash_is_referenced(conn: sqlite3.Connection, content_hash: str) -> bool:
    """True if any non-pruned snapshot still points at this hash. Blobs must not
    be unlinked while this is True -- dedup lets units share a blob.

    Identical content stores exactly one blob, so two unrelated units (or an
    older snapshot of the same unit) can depend on the same file. Pruning one
    snapshot and unlinking on that basis alone destroys the other's only copy,
    silently, and the failure surfaces months later at restore time.
    """
    row = conn.execute(
        "SELECT 1 FROM snapshots WHERE content_hash = ? AND pruned_utc IS NULL LIMIT 1",
        (content_hash,),
    ).fetchone()
    return row is not None


# --- catalog ----------------------------------------------------------------
def export_catalog(conn: sqlite3.Connection, out_path: Union[str, Path]) -> int:
    """Write catalog.jsonl, one line per snapshot, sorted by (kind, name,
    created_utc). Keys in fixed order: kind, name, hash, created, bytes, files,
    pruned. Returns rows written.

    This file is committed to git, so the output is byte-stable for a given
    database: the sort is fully specified (snapshot id breaks ties), separators
    are compact and fixed, ensure_ascii keeps the encoding independent of locale,
    and dict insertion order fixes the key order. Written atomically so a crashed
    run cannot leave a half-file in the repo.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = conn.execute(
        "SELECT u.kind AS kind, u.name AS name, s.content_hash AS hash,"
        " s.created_utc AS created, s.size_bytes AS bytes, s.file_count AS files,"
        " s.pruned_utc AS pruned"
        " FROM snapshots s JOIN units u ON u.id = s.unit_id"
        " ORDER BY u.kind ASC, u.name ASC, s.created_utc ASC, s.id ASC"
    ).fetchall()

    lines = []
    for row in rows:
        record = {
            "kind": row["kind"],
            "name": row["name"],
            "hash": row["hash"],
            "created": row["created"],
            "bytes": int(row["bytes"]),
            "files": int(row["files"]),
            "pruned": row["pruned"],
        }
        lines.append(json.dumps(record, ensure_ascii=True, separators=(",", ":")))

    payload = "".join(line + "\n" for line in lines).encode("utf-8")
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(tmp), str(out))
    return len(lines)
