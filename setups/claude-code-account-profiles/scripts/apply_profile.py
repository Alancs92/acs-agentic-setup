#!/usr/bin/env python3
"""Apply the shared lean-core Claude Code profile to a single account.

Reads profiles.json (core + per-account overlays), resolves the effective
plugin set for an account, and reconciles that account's settings.json and
skills. Plugins + marketplaces + a skill prune-list only; hooks/env/
permissions/model are never touched. See ../design.md.

Stdlib only. Safe by default: backs up before writing, validates JSON,
idempotent, and *moves* pruned skills to a trash dir rather than deleting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

DEFAULT_STORE = os.path.expanduser("~/.claude-accounts")
DEFAULT_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "profiles.json")
PRUNED_DIRNAME = ".profile-pruned-skills"


# ─── Pure resolution (unit-tested) ───────────────────────────────────────────

def resolve_plugins(core_plugins, add, remove):
    """Return (effective_plugins, warnings).

    effective = dedupe(core + add) - remove, order-preserving.
    """
    warnings = []
    core_set = set(core_plugins)
    for p in add:
        if p in core_set:
            warnings.append(f"add '{p}' duplicates core (no-op)")
    remove_set = set(remove)
    for p in remove:
        if p not in core_set and p not in set(add):
            warnings.append(f"remove '{p}' not present in core+add (no-op)")

    effective = []
    seen = set()
    for p in list(core_plugins) + list(add):
        if p in remove_set or p in seen:
            continue
        seen.add(p)
        effective.append(p)
    return effective, warnings


def plugins_object(effective):
    """Claude Code's on-disk shape: {plugin-id: true}."""
    return {p: True for p in effective}


def config_hash(effective):
    """SHA-256 of the enabledPlugins object with sorted keys.

    Matches how the audit's baseline hashes were computed, so the golden
    value in design.md is directly comparable.
    """
    obj = plugins_object(effective)
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def resolve_account(manifest, account):
    """Resolve everything needed to apply one account. Raises KeyError/ValueError."""
    core = manifest["core"]
    if not core.get("plugins"):
        raise ValueError("manifest core.plugins is empty")
    accounts = manifest.get("accounts", {})
    if account not in accounts:
        raise KeyError(f"account '{account}' not in manifest (have: {', '.join(sorted(accounts)) or 'none'})")
    ov = accounts[account]
    effective, warnings = resolve_plugins(
        core["plugins"], ov.get("plugins_add", []), ov.get("plugins_remove", [])
    )
    skills_prune = list(dict.fromkeys(core.get("skills_prune", []) + ov.get("skills_prune", [])))
    return {
        "effective": effective,
        "marketplaces": core.get("marketplaces", {}),
        "skills_prune": skills_prune,
        "warnings": warnings,
        "hash": config_hash(effective),
    }


# ─── Side effects ────────────────────────────────────────────────────────────

def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _timestamp():
    return time.strftime("%Y%m%dT%H%M%S")


def apply_account(manifest, account, store, dry_run, assume_yes, prune_skills=False):
    resolved = resolve_account(manifest, account)
    acct_dir = os.path.join(store, account)
    settings_path = os.path.join(acct_dir, "settings.json")

    if not os.path.isdir(acct_dir):
        print(f"❌ account dir not found: {acct_dir}", file=sys.stderr)
        return 1
    if not os.path.isfile(settings_path):
        print(f"❌ settings.json not found: {settings_path}", file=sys.stderr)
        return 1

    for w in resolved["warnings"]:
        print(f"⚠️  {w}")

    settings = _load_json(settings_path)
    old_plugins = list(settings.get("enabledPlugins", {}).keys())
    new_plugins = resolved["effective"]

    added = [p for p in new_plugins if p not in old_plugins]
    removed = [p for p in old_plugins if p not in new_plugins]

    # Marketplace union (never removes an existing marketplace).
    existing_mkt = settings.get("extraKnownMarketplaces", {})
    mkt_added = [k for k in resolved["marketplaces"] if k not in existing_mkt]

    # Skills that actually exist in this account's skills dir.
    skills_dir = os.path.join(acct_dir, "skills")
    prune_real, prune_symlink, prune_absent = _classify_skills(skills_dir, resolved["skills_prune"])
    # The account's skills dir may itself be a symlink to a SHARED location
    # (e.g. personal/skills -> ~/.claude/skills). Pruning then affects every
    # account that shares it — a bigger blast radius than "this account".
    skills_shared = os.path.islink(skills_dir)

    plugins_changed = bool(added or removed)
    mkt_changed = bool(mkt_added)
    skills_changed = bool(prune_real) and prune_skills
    changed = plugins_changed or mkt_changed or skills_changed

    # ── Report ──
    print(f"\n▶ account: {account}")
    print(f"  config hash: {resolved['hash']}  ({len(new_plugins)} plugins)")
    if added:
        print("  + plugins: " + ", ".join(added))
    if removed:
        print("  - plugins: " + ", ".join(removed))
    if mkt_added:
        print("  + marketplaces: " + ", ".join(mkt_added))
    if prune_real:
        if prune_skills:
            print("  ✂ skills to prune (real dir → trash): " + ", ".join(prune_real))
        else:
            print("  ○ skills prunable (pass --prune-skills to remove): " + ", ".join(prune_real))
    if prune_symlink:
        print("  ↷ skills skipped (bootstrap symlink, v1): " + ", ".join(prune_symlink))
    if prune_absent:
        print("  · skills not present (skip): " + ", ".join(prune_absent))
    if prune_skills and prune_real and skills_shared:
        print(f"  ⚠️  skills dir is a symlink ({skills_dir} → {os.path.realpath(skills_dir)});")
        print("      pruning affects ALL accounts that share it, not just this one.")
    if not changed:
        print("  ✅ already in sync — nothing to do")
        return 0

    if dry_run:
        print("  (dry-run — no changes written)")
        return 0

    if not assume_yes:
        if not sys.stdin.isatty():
            print("  ⏸  changes pending; re-run with --yes to apply (non-interactive).", file=sys.stderr)
            return 2
        reply = input("  Apply these changes? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("  aborted.")
            return 0

    # ── Write (backup first) ──
    backup = f"{settings_path}.bak-{_timestamp()}"
    if os.path.exists(backup):  # non-clobbering guard
        backup = f"{backup}-{os.getpid()}"
    shutil.copy2(settings_path, backup)

    settings["enabledPlugins"] = plugins_object(new_plugins)
    merged_mkt = dict(existing_mkt)
    merged_mkt.update(resolved["marketplaces"])
    settings["extraKnownMarketplaces"] = merged_mkt

    tmp = f"{settings_path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")
    # Validate the file we just wrote before swapping it in.
    try:
        _load_json(tmp)
    except json.JSONDecodeError as exc:
        os.remove(tmp)
        print(f"❌ produced invalid JSON, aborting (backup kept at {backup}): {exc}", file=sys.stderr)
        return 1
    os.replace(tmp, settings_path)
    print(f"  💾 wrote settings.json (backup: {os.path.basename(backup)})")

    if prune_skills and prune_real:
        _prune_skills(skills_dir, prune_real)
        print(f"  🗂  moved {len(prune_real)} skill(s) → {PRUNED_DIRNAME}/ (restorable)")

    return 0


def _classify_skills(skills_dir, names):
    """Split prune names into (real_dirs, symlinks, absent) for this skills dir."""
    real, symlink, absent = [], [], []
    for name in names:
        path = os.path.join(skills_dir, name)
        if os.path.islink(path):
            symlink.append(name)
        elif os.path.isdir(path):
            real.append(name)
        else:
            absent.append(name)
    return real, symlink, absent


def _prune_skills(skills_dir, real_names):
    """Move real-dir skills to the account's prune trash dir (reversible)."""
    if not real_names:
        return
    trash = os.path.join(skills_dir, PRUNED_DIRNAME)
    os.makedirs(trash, exist_ok=True)
    for name in real_names:
        src = os.path.join(skills_dir, name)
        dst = os.path.join(trash, name)
        if os.path.exists(dst):
            dst = f"{dst}.{_timestamp()}"
        shutil.move(src, dst)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="Apply the shared lean-core profile to a Claude Code account.")
    ap.add_argument("--account", required=True, help="account name (dir under --store)")
    ap.add_argument("--store", default=DEFAULT_STORE, help="account store dir (default: ~/.claude-accounts)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST, help="path to profiles.json")
    ap.add_argument("--dry-run", action="store_true", help="show changes without writing")
    ap.add_argument("--yes", action="store_true", help="apply without interactive confirmation")
    ap.add_argument("--prune-skills", action="store_true",
                    help="also prune real-dir skills (OFF by default; the skills dir may be shared across accounts)")
    args = ap.parse_args(argv)

    try:
        manifest = _load_json(args.manifest)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ cannot read manifest {args.manifest}: {exc}", file=sys.stderr)
        return 1

    try:
        return apply_account(manifest, args.account, args.store, args.dry_run, args.yes, args.prune_skills)
    except (KeyError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
