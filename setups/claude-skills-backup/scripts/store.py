#!/usr/bin/env python3
"""Content-addressed blob store for claude-skills-backup.

A blob is one unit's files packed into a single `tar.xz`, named after the
canonical content hash of those files. Identical content therefore lands on
identical bytes at an identical path, which is what makes dedup free: if the
key already exists, there is nothing to write.

Inputs
  root            Path -- the active blob root (OneDrive, or the local
                  fallback). `objects/` is created underneath it.
  content_hash    str  -- canonical hash from hashing.content_hash, lowercase
                  hex. The blob's identity; never derived here.
  unit, files     the Unit being stored and the files discovery selected for it.
  secrets_config  dict -- passed straight through to redact.redact_bytes.

Outputs
  blob keys (POSIX-relative strings, stored in SQLite), byte counts, and
  extracted trees.

Two invariants this module exists to hold:

  REDACTION IS UPSTREAM OF THE ARCHIVE. Every file's bytes go through
  redact.redact_bytes before they are handed to tarfile. There is no code path
  that puts an unredacted byte into a blob -- these archives are written into
  corporate OneDrive, so "we redact it later" is not an option.

  EXTRACTION NEVER ESCAPES ITS DESTINATION. Archives are data; a corrupt or
  hostile one must not be able to write outside the directory it was asked to
  restore into. read_blob validates every member up front and refuses the whole
  archive if any one of them is suspicious.
"""
from __future__ import annotations

import hashlib
import io
import lzma  # noqa: F401  (imported for the explicit xz dependency; tarfile uses it)
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import List, Tuple

import hashing
import redact
# Named backup_types, not types: a types.py in this directory shadows the
# stdlib module of that name and breaks interpreter start-up.
from backup_types import FileEntry, Unit, expand

# Fixed so the same content always compresses to the same bytes, regardless of
# which Python or platform wrote it.
_XZ_PRESET = 6

# Normalised member modes. Only the executable bit survives from the source;
# everything else about the file's permissions is noise for reproducibility.
_MODE_EXEC = 0o755
_MODE_PLAIN = 0o644


# --- keys -------------------------------------------------------------------

def blob_key(content_hash: str) -> str:
    """'objects/<h[0:2]>/<h[2:4]>/<h>.tar.xz' -- POSIX separators always.

    Two levels of sharding keep any one directory to a few hundred entries,
    which OneDrive and Finder both cope with far better than one flat folder.

    The separator is hard-coded rather than taken from os.sep: this string is
    written into SQLite and into catalog.jsonl, so it has to mean the same
    thing on every machine that ever reads the store.
    """
    if not content_hash or len(content_hash) < 4:
        raise ValueError(f"implausible content hash: {content_hash!r}")
    return f"objects/{content_hash[0:2]}/{content_hash[2:4]}/{content_hash}.tar.xz"


def _blob_path(root: Path, key: str) -> Path:
    """Resolve a POSIX blob key against a root on the local filesystem."""
    return Path(expand(root), *PurePosixPath(key).parts)


def _member_name(unit: Unit, path: Path) -> str:
    """The unit-relative POSIX name a file is archived under.

    For a single-file unit the unit root *is* the file, so the member is just
    its basename; the unit's own name carries the account/kind context.
    """
    if unit.is_file:
        return path.name
    try:
        return path.resolve().relative_to(Path(unit.source_path).resolve()).as_posix()
    except ValueError:
        # File outside the unit root -- should not happen, but never lose it.
        return path.name


# --- write ------------------------------------------------------------------

def write_blob(root: Path, content_hash: str, unit: Unit, files: List[Path],
               secrets_config: dict) -> Tuple[str, int]:
    """Write the unit's files as a tar.xz at blob_key(content_hash) under root.

    Returns (blob_key, size_bytes), where size_bytes is the size of the blob on
    disk. If the blob already exists it is returned untouched -- the dedup path,
    and the reason an unchanged 85-skill tree costs nothing to back up again.
    """
    key = blob_key(content_hash)
    target = _blob_path(root, key)

    if target.exists():
        # Dedup: same content hash means same bytes. Re-archiving would burn
        # CPU and, on OneDrive, trigger a pointless re-upload of every blob.
        return key, target.stat().st_size

    target.parent.mkdir(parents=True, exist_ok=True)

    members = sorted(
        ((_member_name(unit, f), Path(f)) for f in files),
        key=lambda pair: pair[0],
    )

    # Temp file in the *same directory* so os.replace is a same-filesystem
    # rename, i.e. atomic. A killed run leaves a stray .tmp-* file, never a
    # truncated archive sitting at a valid key that later reads would trust.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp-" + content_hash[:12] + "-",
        suffix=".tar.xz",
    )
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        with tarfile.open(tmp_name, "w:xz", preset=_XZ_PRESET,
                          format=tarfile.PAX_FORMAT) as tar:
            for name, source in members:
                raw = source.read_bytes()
                # SECURITY: redaction happens here, before the bytes are handed
                # to tarfile. Nothing unredacted ever reaches the archive.
                data, _count = redact.redact_bytes(raw, name, secrets_config)

                info = tarfile.TarInfo(name=name)
                info.type = tarfile.REGTYPE
                info.size = len(data)
                info.mode = (_MODE_EXEC if source.stat().st_mode & 0o111
                             else _MODE_PLAIN)
                # Zeroed so the archive is REPRODUCIBLE. mtime and ownership
                # differ between machines and after every OneDrive resync; if
                # they landed in the archive, identical content would compress
                # to different bytes and the store would never dedup.
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                tar.addfile(info, io.BytesIO(data))

        size = tmp_path.stat().st_size
        os.replace(tmp_name, str(target))
    except BaseException:
        # Includes KeyboardInterrupt: an interrupted run must not litter.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    return key, size


# --- read -------------------------------------------------------------------

def _safe_member_path(member: tarfile.TarInfo, dest: Path) -> Path:
    """Validate one member and return where it may be written.

    Raises ValueError for anything that could land outside `dest`. This is the
    path-traversal guard: a blob is untrusted input (it may have been sitting
    in a shared cloud folder), and `tar` has a long history of archives whose
    member names are '../../etc/something' or an absolute path.
    """
    name = member.name

    if not name or name in (".", "/"):
        raise ValueError(f"archive member has no usable name: {name!r}")

    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
        # Links are their own escape hatch: extract a symlink pointing outside
        # dest, then a later member writes straight through it.
        raise ValueError(f"archive member is not a regular file or dir: {name!r}")

    if name.startswith("/") or name.startswith("\\") or PurePosixPath(name).is_absolute():
        raise ValueError(f"archive member has an absolute path: {name!r}")

    parts = PurePosixPath(name).parts
    if any(part == ".." for part in parts):
        raise ValueError(f"archive member escapes the destination: {name!r}")

    # Belt and braces: normalise the join and confirm it is still under dest.
    base = os.path.realpath(str(dest))
    candidate = os.path.normpath(os.path.join(base, *parts))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise ValueError(f"archive member escapes the destination: {name!r}")

    return Path(candidate)


def read_blob(root: Path, blob_key: str, dest: Path) -> int:
    """Extract to dest, returns the count of regular files written.

    Every member is validated before *any* member is extracted, so one hostile
    entry aborts the whole restore rather than leaving a half-applied tree.
    """
    src = _blob_path(root, blob_key)
    if not src.is_file():
        raise FileNotFoundError(f"no blob at {blob_key}")

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    with tarfile.open(str(src), "r:xz") as tar:
        members = tar.getmembers()
        for member in members:
            _safe_member_path(member, dest)  # raises on anything suspicious

        count = 0
        for member in members:
            out = _safe_member_path(member, dest)
            if member.isdir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            data = b"" if extracted is None else extracted.read()
            out.write_bytes(data)
            os.chmod(str(out), _MODE_EXEC if member.mode & 0o111 else _MODE_PLAIN)
            count += 1

    return count


# --- delete -----------------------------------------------------------------

def delete_blob(root: Path, blob_key: str) -> int:
    """Unlink, return bytes reclaimed. Missing blob returns 0, never raises.

    Retention calls this in a loop over hundreds of keys; one unreadable path
    must not abort the sweep. Now-empty shard directories are pruned too, but
    `objects/` itself always stays -- other code treats its absence as "no
    store here".
    """
    try:
        target = _blob_path(root, blob_key)
        if not target.is_file():
            return 0
        size = target.stat().st_size
        target.unlink()
    except OSError:
        return 0

    objects_root = _blob_path(root, "objects")
    parent = target.parent
    while parent != objects_root and objects_root in parent.parents:
        try:
            parent.rmdir()  # only succeeds while empty
        except OSError:
            break
        parent = parent.parent

    return size


# --- verify -----------------------------------------------------------------

def verify_blob(root: Path, blob_key: str, expected_hash: str) -> bool:
    """Extract to temp, recompute the canonical content hash, compare.

    Returns False rather than raising for a missing, corrupt or hostile blob:
    the caller's job is to re-snapshot, and a bad blob in the cloud folder is a
    condition to report, not a crash.
    """
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="cskb-verify-")
        read_blob(root, blob_key, Path(tmp_dir))
        actual = hashing.content_hash(_entries_of_tree(Path(tmp_dir)))
        return actual == (expected_hash or "").lower()
    except (OSError, ValueError, tarfile.TarError, lzma.LZMAError, EOFError):
        return False
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _entries_of_tree(root: Path) -> List[FileEntry]:
    """FileEntry manifest for an extracted tree, matching hashing.file_entries."""
    entries = []
    for dirpath, _dirnames, filenames in os.walk(str(root)):
        for filename in filenames:
            path = Path(dirpath) / filename
            entries.append(
                FileEntry(
                    relpath=path.relative_to(root).as_posix(),
                    executable=bool(path.stat().st_mode & 0o111),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    entries.sort(key=lambda e: e.relpath)
    return entries
