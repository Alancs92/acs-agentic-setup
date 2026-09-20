#!/usr/bin/env python3
"""Recovery paths for claude-skills-backup: restore a unit, and verify the store.

This is the module that justifies the whole system, so it errs toward refusing
rather than guessing: an ambiguous unit name, a pruned snapshot, or a non-empty
destination all stop with an explanation instead of doing something plausible.

Inputs:  an open index connection, the active blob root
Outputs: files written under the restore destination; verification findings
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

import index as index_mod
import store
from backup_types import parse_iso


def _resolve_unit(conn, wanted: str) -> List[Tuple[int, str, str]]:
    """Match a user-supplied name against units.

    Accepts either the bare name ("deep-research") or the qualified form
    ("skill/deep-research"). Returns every match so the caller can refuse an
    ambiguous one rather than silently restoring the wrong kind.
    """
    matches = []
    for unit_id, kind, name in index_mod.iter_unit_ids(conn):
        if wanted in (name, f"{kind}/{name}"):
            matches.append((unit_id, kind, name))
    return matches


def _pick_snapshot(snapshots, at: Optional[str]):
    """Newest snapshot at or before `at` (YYYY-MM-DD), else the newest.

    `snapshots` arrives newest-first. Tombstoned snapshots are skipped: the row
    proves a version existed, but its bytes are gone, so it cannot be restored.
    """
    live = [s for s in snapshots if not s.pruned_utc]
    if not live:
        return None
    if not at:
        return live[0]

    cutoff = f"{at}T23:59:59Z"
    for snap in live:  # newest-first, so the first match is the newest at-or-before
        if snap.created_utc <= cutoff:
            return snap
    return None


def restore(conn, blob_root: Path, unit: str, at: Optional[str] = None,
            dest: Optional[Path] = None, force: bool = False) -> int:
    """Restore one unit's content to a directory. Returns a process exit code."""
    matches = _resolve_unit(conn, unit)
    if not matches:
        print(f"no such unit: {unit}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"ambiguous unit '{unit}', qualify it as kind/name:", file=sys.stderr)
        for _, kind, name in matches:
            print(f"  {kind}/{name}", file=sys.stderr)
        return 1

    unit_id, kind, name = matches[0]
    snapshots = index_mod.snapshots_for_unit(conn, unit_id, include_pruned=True)
    if not snapshots:
        print(f"{kind}/{name} has no snapshots", file=sys.stderr)
        return 1

    newest_live = next((s for s in snapshots if not s.pruned_utc), None)
    snap = _pick_snapshot(snapshots, at)
    if snap is None:
        print(f"no restorable snapshot for {kind}/{name}"
              + (f" at or before {at}" if at else ""), file=sys.stderr)
        pruned = [s for s in snapshots if s.pruned_utc]
        if pruned:
            print(f"  {len(pruned)} tombstoned snapshot(s) exist but their blobs "
                  f"were pruned; oldest surviving record: "
                  f"{pruned[-1].created_utc}", file=sys.stderr)
        return 1

    target = Path(dest) if dest else Path.cwd() / "restored" / name
    target = target.expanduser()
    if target.exists() and any(target.iterdir()) and not force:
        print(f"destination not empty: {target}\n"
              f"  re-run with --force to write into it anyway", file=sys.stderr)
        return 1
    target.mkdir(parents=True, exist_ok=True)

    try:
        file_count = store.read_blob(blob_root, snap.blob_key, target)
    except FileNotFoundError:
        print(f"blob missing from store: {snap.blob_key}\n"
              f"  the index says it should exist -- run: claude-acs backup verify",
              file=sys.stderr)
        return 1

    print(f"restored   {kind}/{name}")
    print(f"           snapshot {snap.created_utc}  {snap.content_hash[:12]}")
    print(f"           {file_count} files -> {target}")
    if newest_live is not None and snap.id != newest_live.id:
        print(f"           NOTE: not the newest -- newest is "
              f"{newest_live.created_utc}")
    return 0


def verify(conn, blob_root: Path) -> Tuple[List[str], List[str], int]:
    """Hash-check every live blob and find orphans.

    Returns (corrupt_keys, orphan_keys, checked_count).

    An orphan is a blob on disk that no live snapshot references. They accumulate
    from runs interrupted between writing a blob and committing its row, so
    finding some is normal, not alarming.
    """
    referenced = {}
    for unit_id, _kind, _name in index_mod.iter_unit_ids(conn):
        for snap in index_mod.snapshots_for_unit(conn, unit_id, include_pruned=False):
            referenced[snap.blob_key] = snap.content_hash

    corrupt = []
    for blob_key, expected_hash in sorted(referenced.items()):
        if not store.verify_blob(blob_root, blob_key, expected_hash):
            corrupt.append(blob_key)

    objects_root = Path(blob_root) / "objects"
    on_disk = set()
    if objects_root.exists():
        for path in objects_root.rglob("*.tar.xz"):
            on_disk.add(path.relative_to(blob_root).as_posix())

    orphans = sorted(on_disk - set(referenced))
    return corrupt, orphans, len(referenced)
