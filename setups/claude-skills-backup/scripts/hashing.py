#!/usr/bin/env python3
"""Canonical content hash for a unit -- the change-detection primitive.

The index stores one snapshot per *content change*. Whether a change happened is
decided here and nowhere else, so this module defines exactly what "the same
content" means.

LEGEND
  manifest  The sorted list of FileEntry for a unit: (relpath, executable,
            sha256). The only three facts the hash is allowed to depend on.
  canonical The manifest serialised one entry per three lines, so the hash is a
            hash of text a human can reconstruct by hand, not of a pickle.

WHAT IS DELIBERATELY EXCLUDED, and why it matters

  mtime / ctime / atime   A `touch`, a `git checkout`, a Time Machine restore
                          or a OneDrive resync rewrites timestamps without
                          changing a byte of content. If any of them fed the
                          hash, every scheduled run would fabricate a snapshot
                          for all 85 skills and the store would grow without
                          bound -- the exact failure this system exists to
                          avoid.
  uid / gid / full mode   Ownership travels with the machine, not the content.
                          Only the executable bit is kept, because losing it
                          genuinely breaks a restored hook or script.
  absolute paths          Hashing must give the same answer for a skill in
                          ~/.claude/skills, in a temp dir under test, and in a
                          restored tree. Paths are recorded unit-relative.
  unit kind / name        Renaming a skill is an index-level event, not a
                          content change; identical content must dedup to one
                          blob regardless of which unit it came from.

WHAT IS HASHED: THE STORED BYTES, NOT THE ON-DISK BYTES

  store.write_blob redacts each file on its way into the tar. So when a
  secrets_config is supplied the manifest is built from the redacted form,
  because that is what the blob actually contains. Hashing the raw file instead
  gives the index a content_hash that does not match its own blob, and
  verify_blob -- which extracts and recomputes -- then reports the unit corrupt
  forever. Change detection is unharmed: the redaction marker carries a sha256
  prefix of the original value, so rotating a token still moves the hash.

Inputs:  a Unit, the list of Paths belonging to it (from discovery.iter_files),
         and optionally config.json's `secrets` block
Outputs: a sorted FileEntry manifest, and the lowercase-hex sha256 over it
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import List

from backup_types import FileEntry, Unit

# Files are read incrementally: some skills carry multi-megabyte reference PDFs
# and a scheduled run hashes the whole tree, so nothing is ever slurped whole.
CHUNK_SIZE = 1 << 16  # 64 KiB

# Any of owner/group/other execute. `mode & 0o111` per the shared type comment.
_EXEC_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def _relpath(unit: Unit, path: Path) -> str:
    """Unit-relative POSIX path.

    A single-file unit (settings.json, CLAUDE.md) has no directory to be
    relative to, so its manifest uses the bare filename -- that is also the name
    the member gets inside the tar, keeping store/restore symmetrical.
    """
    if unit.is_file:
        return path.name
    return path.relative_to(unit.source_path).as_posix()


def _sha256_file(path: Path) -> str:
    """Lowercase hex sha256 of a file's bytes, read in bounded chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_executable(path: Path) -> bool:
    """Executable bit, following symlinks -- a symlinked skill is captured by
    the content and mode of its target, never as a link."""
    return bool(os.stat(path).st_mode & _EXEC_BITS)


def _sha256_stored(relpath: str, path: Path, secrets_config) -> str:
    """sha256 of the bytes that will actually be ARCHIVED for this file.

    store.write_blob runs every file through redact.redact_bytes on its way into
    the tar, so the manifest has to describe the redacted form. Hashing the raw
    on-disk bytes instead would give the index a content_hash that no longer
    matches the blob it names, and verify_blob -- which extracts and recomputes
    -- would brand the unit corrupt forever.

    Only JSON is materialised in full, because only JSON is ever rewritten;
    everything else keeps the bounded-chunk path, so a multi-megabyte reference
    PDF is still never held in memory.
    """
    # Imported lazily so `import hashing` stays dependency-free for tools that
    # only want the pure canonicalisation. The ImportError is deliberately NOT
    # caught: if redaction is unavailable we must fail loudly rather than hash
    # and store an unredacted token into corporate OneDrive.
    import redact

    if not redact.looks_like_json(relpath):
        return _sha256_file(path)

    raw = path.read_bytes()
    stored, _count = redact.redact_bytes(raw, relpath, secrets_config)
    return hashlib.sha256(stored).hexdigest()


def file_entries(unit: Unit, files: List[Path], secrets_config=None) -> List[FileEntry]:
    """Build the sorted FileEntry manifest for a unit.

    Sorted by relpath, so the caller's directory-iteration order -- which os.walk
    does not guarantee across filesystems -- cannot influence the hash.

    When `secrets_config` is provided, each file's bytes are passed through
    redact.redact_bytes BEFORE hashing, so the manifest describes the bytes that
    will actually be archived. Without it, behaviour is unchanged (raw bytes).

    Hashing the stored form is the correct default for every consumer of this
    hash, not a workaround: blob addressing and dedup key on stored content,
    verify recomputes from stored content, and change detection still works
    because the redaction marker embeds a sha256 prefix of the original value --
    so rotating a token changes the hash even though the token never appears.

    `relpath` is passed to redact as the filename, and it is computed by exactly
    the rule store._member_name uses, so JSON detection cannot diverge between
    what is hashed and what is written.
    """
    entries = []
    for path in files:
        rel = _relpath(unit, path)
        entries.append(
            FileEntry(
                relpath=rel,
                executable=_is_executable(path),
                sha256=(
                    _sha256_file(path)
                    if secrets_config is None
                    else _sha256_stored(rel, path, secrets_config)
                ),
            )
        )
    entries.sort(key=lambda entry: entry.relpath)
    return entries


def content_hash(entries: List[FileEntry]) -> str:
    """Canonical sha256 over the manifest. Lowercase hex.

    Hashed input is, for each entry sorted by relpath:

        f"{relpath}\\n{int(executable)}\\n{sha256}\\n"

    concatenated and UTF-8 encoded. Sorting is re-applied here rather than
    trusted from the caller: this function is the last line of defence for the
    property the whole design rests on, and sorting a sorted list is free.

    An empty manifest hashes the empty string, so an empty unit has a stable,
    well-defined hash instead of a special case.
    """
    buffer = bytearray()
    for entry in sorted(entries, key=lambda e: e.relpath):
        line = f"{entry.relpath}\n{int(entry.executable)}\n{entry.sha256}\n"
        buffer.extend(line.encode("utf-8"))
    return hashlib.sha256(bytes(buffer)).hexdigest()
