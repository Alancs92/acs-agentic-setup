#!/usr/bin/env python3
"""Shared vocabulary for claude-skills-backup.

Every other module imports from here and nowhere else for these names, so the
definition of "unit", "snapshot" and "now" is identical across the pipeline.

LEGEND
  unit      One backed-up thing, identified by (kind, name). A skill, a hook, a
            settings file. Uniform across sources so there is one pipeline.
  snapshot  A record of one *content change* to a unit. Unchanged units create
            no snapshots -- that is the whole point of hashing.
  blob      The tar.xz archive holding a snapshot's bytes, named by content hash
            so identical content is stored exactly once.
  tombstone A snapshot row whose blob was pruned. pruned_utc is set, the row
            survives, so history of what existed is never lost.

Inputs:  nothing (pure definitions)
Outputs: type definitions and utcnow_iso()
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import NamedTuple, Optional

# --- unit kinds -------------------------------------------------------------
KIND_SKILL = "skill"
KIND_COMMAND = "command"
KIND_AGENT = "agent"
KIND_HOOK = "hook"
KIND_SETTINGS = "settings"
KIND_CLAUDE_MD = "claude_md"
KIND_PLUGIN_MANIFEST = "plugin_manifest"

ALL_KINDS = (
    KIND_SKILL,
    KIND_COMMAND,
    KIND_AGENT,
    KIND_HOOK,
    KIND_SETTINGS,
    KIND_CLAUDE_MD,
    KIND_PLUGIN_MANIFEST,
)

# --- run statuses -----------------------------------------------------------
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"   # completed, but blobs went to the local fallback
STATUS_FAILED = "failed"


class Unit(NamedTuple):
    """One backed-up thing. source_path is always resolved -- symlinks followed,
    so a symlinked skill is captured by content rather than as a dangling link."""

    kind: str
    name: str
    source_path: Path
    is_file: bool


class FileEntry(NamedTuple):
    """One file inside a unit, reduced to only what the content hash may depend
    on. Deliberately carries no timestamps."""

    relpath: str      # POSIX-style, relative to the unit root
    executable: bool  # mode & 0o111
    sha256: str       # lowercase hex of the file's bytes


class Snapshot(NamedTuple):
    """A recorded content change. pruned_utc set means the blob is gone but the
    record remains (tombstone)."""

    id: int
    unit_id: int
    content_hash: str
    blob_key: str
    size_bytes: int
    file_count: int
    created_utc: str
    run_id: int
    pruned_utc: Optional[str]


def utcnow_iso() -> str:
    """Current UTC time as '2026-08-25T03:00:00Z'.

    Call this once at the top of a run and thread the value through. Modules take
    `now` as an argument rather than calling this themselves so their behaviour is
    deterministic under test.
    """
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> _dt.datetime:
    """Parse the ISO form above back to an aware UTC datetime."""
    return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc
    )


def expand(path_like) -> Path:
    """Expand '~' and make absolute. Every path crossing a module boundary has
    already been through this."""
    return Path(path_like).expanduser().resolve()
