#!/usr/bin/env python3
"""claude-acs backup -- managed, versioned, deduplicated backup of hand-authored
Claude configuration (skills, commands, agents, hooks, settings, plugin manifests).

This is the CLI and the orchestrator. All real work lives in the sibling modules,
whose signatures are frozen in contracts.md. See ../README.md for the LEGEND
defining unit / snapshot / blob / tombstone / prune.

NOTE: unrelated to `claude-acs save`, which saves an *account profile*.

Inputs:  ../config.json (or --config), the live filesystem
Outputs: blobs + index.db under the active storage root, catalog.jsonl in-repo
Exit:    0 ok, 1 failed. A `degraded` run (OneDrive away) still exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery              # noqa: E402
import hashing                # noqa: E402
import index as index_mod     # noqa: E402
import restore as restore_mod # noqa: E402
import retention              # noqa: E402
import store                  # noqa: E402
import sync                   # noqa: E402
from backup_types import (    # noqa: E402
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
    expand,
    utcnow_iso,
)

SETUP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SETUP_DIR / "config.json"
CATALOG_PATH = SETUP_DIR / "catalog.jsonl"


# --- config -----------------------------------------------------------------

def load_config(path: Path) -> dict:
    """Read config.json. Fails loudly: a silently-defaulted backup policy is
    worse than no backup, because it looks like it worked."""
    with open(path, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    for required in ("sources", "storage", "retention", "secrets", "schedule"):
        if required not in config:
            raise SystemExit(f"config.json missing required section: {required}")
    return config


#: config source key -> the unit kinds that source produces.
_SOURCE_KINDS = {
    "skills": (KIND_SKILL,),
    "commands": (KIND_COMMAND,),
    "agents": (KIND_AGENT,),
    "hooks": (KIND_HOOK,),
    "settings": (KIND_SETTINGS, KIND_CLAUDE_MD),
    "plugins": (KIND_PLUGIN_MANIFEST,),
}


def _scanned_kinds(config: dict, settings_skipped: bool) -> list:
    """Which kinds this run actually looked at.

    index.mark_missing_units needs this to tell "kind enabled, every unit
    deleted" apart from "kind never scanned" -- both produce zero seen ids.
    Getting it wrong in the other direction is the dangerous one: declaring a
    kind scanned when it wasn't tombstones every unit of that kind. Hence
    settings_skipped, which drops settings when secrets.mode == "skip".
    """
    kinds = []
    for key, source in config.get("sources", {}).items():
        if not source.get("enabled"):
            continue
        for kind in _SOURCE_KINDS.get(key, ()):
            if settings_skipped and kind == KIND_SETTINGS:
                continue
            kinds.append(kind)
    return kinds


def _local_db_path(config: dict) -> Path:
    """index.db always has a local home, even when OneDrive is present -- the
    cloud copy is a placed artefact, never the working database. Writing SQLite
    directly onto a sync-backed folder invites a torn file mid-upload."""
    return expand(config["storage"]["local_fallback"]) / "index.db"


# --- run --------------------------------------------------------------------

def cmd_run(config: dict, args) -> int:
    now = utcnow_iso()
    blob_root, degraded = sync.resolve_storage(config)
    db_path = _local_db_path(config)
    conn = index_mod.connect(db_path)

    run_id = index_mod.start_run(conn, now)
    stats = {
        "units_scanned": 0, "snapshots_created": 0, "blobs_pruned": 0,
        "bytes_written": 0, "bytes_reclaimed": 0,
    }
    skipped: list[str] = []

    try:
        units = discovery.discover(config)
        exclude = config.get("exclude_globs", [])
        secrets = config["secrets"]

        # secrets.mode == "skip" means settings never leave the machine at all.
        # redact.py only implements redaction, so excluding the units is the
        # caller's job -- if nobody did it here, "skip" would silently redact
        # instead, which is a weaker guarantee than the user asked for.
        settings_skipped = secrets.get("mode") == "skip"
        if settings_skipped:
            dropped = [u for u in units if u.kind == KIND_SETTINGS]
            units = [u for u in units if u.kind != KIND_SETTINGS]
            if dropped:
                skipped.append(
                    f"{len(dropped)} settings unit(s) excluded (secrets.mode=skip)"
                )

        seen_ids: list[int] = []

        for unit in units:
            stats["units_scanned"] += 1
            try:
                files = discovery.iter_files(unit, exclude)
                # secrets_config is passed so the manifest hashes the REDACTED
                # bytes -- the same bytes store.write_blob archives. Hashing the
                # raw file instead would record a content_hash that no longer
                # matches its own blob, and verify would call the unit corrupt.
                entries = hashing.file_entries(unit, files, secrets_config=secrets)
                content_hash = hashing.content_hash(entries)
            except OSError as exc:
                # An unreadable source must never abort the whole run.
                skipped.append(f"{unit.kind}/{unit.name}: {exc}")
                continue

            unit_id = index_mod.upsert_unit(conn, unit, now)
            seen_ids.append(unit_id)

            latest = index_mod.latest_snapshot(conn, unit_id)
            if latest is not None and latest.content_hash == content_hash:
                continue  # unchanged -- the whole point: write nothing

            blob_key, size_bytes = store.write_blob(
                blob_root, content_hash, unit, files, secrets
            )
            index_mod.record_snapshot(
                conn, unit_id, content_hash, blob_key,
                size_bytes, len(entries), run_id, now,
            )
            stats["snapshots_created"] += 1
            stats["bytes_written"] += size_bytes

        index_mod.mark_missing_units(
            conn, seen_ids, now,
            enabled_kinds=_scanned_kinds(config, settings_skipped),
        )

        if not args.no_prune:
            pruned, reclaimed = _prune(conn, config, blob_root, now, dry_run=False)
            stats["blobs_pruned"] = pruned
            stats["bytes_reclaimed"] = reclaimed

        if config["storage"].get("refresh_live_mirror", True):
            mirror_root = expand(config["storage"]["live_mirror"])
            skills = [u for u in units if u.kind == KIND_SKILL]
            try:
                mirror_stats = sync.refresh_live_mirror(
                    skills, mirror_root, exclude, secrets
                )
            except OSError as exc:
                skipped.append(f"live mirror: {exc}")
                mirror_stats = None
        else:
            mirror_stats = None

        index_mod.export_catalog(conn, CATALOG_PATH)
        conn.commit()
        sync.place_index(db_path, blob_root)

        status = STATUS_DEGRADED if degraded else STATUS_OK
        index_mod.finish_run(conn, run_id, utcnow_iso(), status, stats)
        conn.commit()

    except Exception as exc:  # noqa: BLE001 -- record then re-raise the message
        index_mod.finish_run(
            conn, run_id, utcnow_iso(), STATUS_FAILED, stats, str(exc)
        )
        conn.commit()
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1

    _print_run_summary(stats, blob_root, degraded, skipped, mirror_stats)
    return 0


def _print_run_summary(stats, blob_root, degraded, skipped, mirror_stats) -> None:
    print(f"scanned    {stats['units_scanned']} units")
    print(f"snapshots  {stats['snapshots_created']} new "
          f"({_human(stats['bytes_written'])} written)")
    print(f"pruned     {stats['blobs_pruned']} blobs "
          f"({_human(stats['bytes_reclaimed'])} reclaimed)")
    if mirror_stats:
        print(f"mirror     +{mirror_stats['added']} ~{mirror_stats['updated']} "
              f"-{mirror_stats['removed']}")
    print(f"store      {blob_root}")
    if degraded:
        print("status     DEGRADED -- OneDrive unavailable, wrote to local "
              "fallback. Re-run when it returns to reconcile.")
    if skipped:
        print(f"skipped    {len(skipped)} unreadable:")
        for line in skipped[:10]:
            print(f"           {line}")


# --- prune ------------------------------------------------------------------

def _prune(conn, config: dict, blob_root: Path, now: str, dry_run: bool):
    """Apply retention across every unit.

    Ordering matters: tombstone the snapshot rows FIRST, then ask whether each
    hash is still referenced. Dedup means two units can share one blob, so a
    blob may only be unlinked once no live snapshot points at it.
    """
    policy = config["retention"]
    total_pruned = 0
    total_reclaimed = 0

    for unit_id, kind, name in index_mod.iter_unit_ids(conn):
        snapshots = index_mod.snapshots_for_unit(conn, unit_id, include_pruned=False)
        prunable_ids = retention.select_prunable(snapshots, policy, now)
        if not prunable_ids:
            continue

        by_id = {s.id: s for s in snapshots}
        if dry_run:
            for sid in prunable_ids:
                snap = by_id[sid]
                print(f"would prune  {kind}/{name}  {snap.created_utc}  "
                      f"{_human(snap.size_bytes)}  {snap.content_hash[:12]}")
                total_pruned += 1
                total_reclaimed += snap.size_bytes
            continue

        index_mod.mark_pruned(conn, prunable_ids, now)
        for sid in prunable_ids:
            snap = by_id[sid]
            if index_mod.hash_is_referenced(conn, snap.content_hash):
                continue  # shared blob still in use -- keep the bytes
            total_reclaimed += store.delete_blob(blob_root, snap.blob_key)
            total_pruned += 1

    return total_pruned, total_reclaimed


def cmd_prune(config: dict, args) -> int:
    now = utcnow_iso()
    blob_root, _ = sync.resolve_storage(config)
    conn = index_mod.connect(_local_db_path(config))
    pruned, reclaimed = _prune(conn, config, blob_root, now, dry_run=args.dry_run)
    if not args.dry_run:
        index_mod.export_catalog(conn, CATALOG_PATH)
        conn.commit()
    verb = "would prune" if args.dry_run else "pruned"
    print(f"{verb} {pruned} blobs, {_human(reclaimed)}")
    return 0


# --- status / list ----------------------------------------------------------

def cmd_status(config: dict, args) -> int:
    blob_root, degraded = sync.resolve_storage(config)
    db_path = _local_db_path(config)
    if not db_path.exists():
        print("no backups yet -- run: claude-acs backup run")
        return 0

    conn = index_mod.connect(db_path)
    last = index_mod.last_run(conn)
    if last:
        print(f"last run   {last['finished_utc'] or last['started_utc']}  "
              f"[{last['status']}]")
        print(f"           {last['snapshots_created']} snapshots, "
              f"{last['blobs_pruned']} pruned")
        if last.get("error"):
            print(f"           error: {last['error']}")
    counts = index_mod.summary_counts(conn)
    print(f"units      {counts['live_units']} live, "
          f"{counts['deleted_units']} deleted")
    print(f"snapshots  {counts['live_snapshots']} live, "
          f"{counts['tombstones']} tombstoned")
    print(f"store      {blob_root}"
          + ("  (DEGRADED -- OneDrive unavailable)" if degraded else ""))
    print(f"           {_human(_dir_size(blob_root / 'objects'))} on disk")
    return 0


def cmd_list(config: dict, args) -> int:
    conn = index_mod.connect(_local_db_path(config))
    for unit_id, kind, name in index_mod.iter_unit_ids(conn):
        if args.unit and args.unit not in (name, f"{kind}/{name}"):
            continue
        snapshots = index_mod.snapshots_for_unit(conn, unit_id, include_pruned=True)
        if not snapshots:
            continue
        print(f"{kind}/{name}")
        for snap in snapshots:
            mark = "  [pruned]" if snap.pruned_utc else ""
            print(f"    {snap.created_utc}  {snap.content_hash[:12]}  "
                  f"{_human(snap.size_bytes):>9}  {snap.file_count:>3} files{mark}")
    return 0


# --- restore / verify -------------------------------------------------------

def cmd_restore(config: dict, args) -> int:
    blob_root, _ = sync.resolve_storage(config)
    conn = index_mod.connect(_local_db_path(config))
    return restore_mod.restore(
        conn, blob_root, args.unit, at=args.at, dest=args.to, force=args.force
    )


def cmd_verify(config: dict, args) -> int:
    blob_root, _ = sync.resolve_storage(config)
    conn = index_mod.connect(_local_db_path(config))
    bad, orphans, checked = restore_mod.verify(conn, blob_root)
    print(f"checked    {checked} blobs")
    print(f"corrupt    {len(bad)}")
    for key in bad[:20]:
        print(f"           {key}")
    print(f"orphans    {len(orphans)} blobs on disk with no live snapshot")
    if orphans and args.reap:
        reclaimed = sum(store.delete_blob(blob_root, k) for k in orphans)
        print(f"reaped     {_human(reclaimed)}")
    elif orphans:
        print("           re-run with --reap to remove them")
    return 1 if bad else 0


# --- schedule ---------------------------------------------------------------

def cmd_install_schedule(config: dict, args) -> int:
    label = config["schedule"]["label"]
    # The label becomes a filename under ~/Library/LaunchAgents. Constrain it so
    # a hand-edited config cannot write a plist outside that directory.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", label):
        raise SystemExit(
            f"schedule.label must match [A-Za-z0-9._-]+, got: {label!r}"
        )
    plist_xml = sync.render_launchd_plist(config, Path(__file__).resolve())
    target = expand("~/Library/LaunchAgents") / f"{label}.plist"

    if args.print_only:
        print(plist_xml)
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    ) as tmp:
        tmp.write(plist_xml)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)

    # subprocess with an argv list, never a shell string: `target` is built from
    # a config-supplied label and must not be able to reach a shell.
    subprocess.run(["launchctl", "unload", str(target)],
                   capture_output=True, check=False)
    completed = subprocess.run(["launchctl", "load", str(target)],
                               capture_output=True, check=False)
    rc = completed.returncode
    if rc != 0:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    print(f"installed  {target}")
    print(f"cadence    every {config['schedule']['interval_days']} day(s) at "
          f"{config['schedule']['hour']:02d}:{config['schedule']['minute']:02d}")
    return 0 if rc == 0 else 1


# --- helpers ----------------------------------------------------------------

def _human(n: int) -> str:
    step = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if step < 1024 or unit == "GB":
            return f"{step:.0f}{unit}" if unit == "B" else f"{step:.1f}{unit}"
        step /= 1024
    return f"{step:.1f}GB"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-acs backup",
        description="Versioned, deduplicated backup of hand-authored Claude "
                    "config. Unrelated to `claude-acs save` (account profiles).",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="path to config.json")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="snapshot changed units, prune, sync")
    p_run.add_argument("--no-prune", action="store_true",
                       help="snapshot and sync, skip retention")
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("status", help="last run, store size, drift").set_defaults(
        func=cmd_status)

    p_list = sub.add_parser("list", help="snapshot history incl. tombstones")
    p_list.add_argument("unit", nargs="?", help="unit name, or kind/name")
    p_list.set_defaults(func=cmd_list)

    p_res = sub.add_parser("restore", help="restore a unit to a directory")
    p_res.add_argument("unit")
    p_res.add_argument("--at", help="restore newest snapshot at or before this "
                                    "UTC date (YYYY-MM-DD)")
    p_res.add_argument("--to", type=Path, help="destination dir (default: ./restored/<unit>)")
    p_res.add_argument("--force", action="store_true",
                       help="allow writing into a non-empty destination")
    p_res.set_defaults(func=cmd_restore)

    p_prune = sub.add_parser("prune", help="apply retention only")
    p_prune.add_argument("--dry-run", action="store_true",
                         help="print what would be deleted, touch nothing")
    p_prune.set_defaults(func=cmd_prune)

    p_ver = sub.add_parser("verify", help="hash-check blobs, find orphans")
    p_ver.add_argument("--reap", action="store_true", help="delete orphan blobs")
    p_ver.set_defaults(func=cmd_verify)

    p_sch = sub.add_parser("install-schedule", help="generate + load launchd job")
    p_sch.add_argument("--print-only", action="store_true",
                       help="print the plist, install nothing")
    p_sch.set_defaults(func=cmd_install_schedule)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    config = load_config(args.config)
    return args.func(config, args)


if __name__ == "__main__":
    sys.exit(main())
