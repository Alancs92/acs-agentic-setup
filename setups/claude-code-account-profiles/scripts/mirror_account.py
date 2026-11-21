#!/usr/bin/env python3
"""Keep a mirror account in parity with its source account.

A mirror account (e.g. `terradev`) is the same setup as its source (`personal`)
under a different Claude login. Mirrors are declared in profiles.json
(`"mirrors": {"terradev": "personal"}`).

What happens to each part of the target account dir:

  linked   -> source   CLAUDE.md, hooks/, agents/, memory/, and every
                       projects/<slug>/memory/ (auto-memory). Parity is live:
                       an edit on either account is the same file.
  linked   -> canonical  skills/, commands/, plugins/ (same targets the source uses).
  copied   <- source   settings.json, settings.local.json, .mcp.json. Claude Code
                       rewrites these itself (atomic rename would replace a
                       symlink), so they are copied, with "<store>/<source>/" paths
                       rewritten to "<store>/<target>/". Source wins; the
                       target's old file is backed up first.
  merged   <- source   .claude.json: only `mcpServers` and per-project
                       `hasTrustDialogAccepted`. Login/identity keys are never
                       touched.
  untouched            credentials, history, sessions, transcripts, caches.

Auto-memory a target created on its own (projects/<slug>/memory exists as a real
dir on the target but not on the source) is moved into the source and linked
back, so nothing is lost. If both sides have a real dir for the same project,
it is reported as a conflict and left alone.

Stdlib only. Run:  python3 mirror_account.py [target ...] [--check|--dry-run]
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE = os.path.expanduser("~/.claude-accounts")
DEFAULT_MANIFEST = os.path.join(HERE, os.pardir, "profiles.json")

LINK_TO_SOURCE = ["CLAUDE.md", "hooks", "agents", "memory"]
LINK_SHARED = ["skills", "commands", "plugins"]  # follow whatever the source links to
COPY_FILES = ["settings.json", "settings.local.json", ".mcp.json"]


def _ts():
    return time.strftime("%Y%m%dT%H%M%S")


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def rewrite_paths(text, store, source, target):
    """Point account-dir paths inside a copied file at the target account."""
    src = os.path.join(store, source) + os.sep
    dst = os.path.join(store, target) + os.sep
    return text.replace(src, dst)


def merge_claude_json(target_doc, source_doc):
    """Return target .claude.json with the source's MCP servers + trust flags."""
    out = dict(target_doc)
    if "mcpServers" in source_doc:
        out["mcpServers"] = source_doc["mcpServers"]
    projects = dict(out.get("projects", {}))
    for path, proj in source_doc.get("projects", {}).items():
        if isinstance(proj, dict) and proj.get("hasTrustDialogAccepted"):
            entry = dict(projects.get(path, {}))
            entry["hasTrustDialogAccepted"] = True
            projects[path] = entry
    if projects:
        out["projects"] = projects
    return out


class Mirror:
    def __init__(self, store, source, target, mode):
        self.store, self.source, self.target = store, source, target
        self.src_dir = os.path.join(store, source)
        self.dst_dir = os.path.join(store, target)
        self.mode = mode  # "apply" | "dry-run" | "check"
        self.changes, self.conflicts = [], []

    @property
    def writing(self):
        return self.mode == "apply"

    def _note(self, msg):
        self.changes.append(msg)

    # ── links ────────────────────────────────────────────────────────────
    def _ensure_link(self, link_path, want):
        name = os.path.relpath(link_path, self.dst_dir)
        if os.path.islink(link_path):
            if os.readlink(link_path) == want:
                return
            self._note(f"relink {name} -> {want}")
            if self.writing:
                os.unlink(link_path)
                os.symlink(want, link_path)
            return
        if os.path.exists(link_path):
            backup = f"{link_path}.pre-mirror-{_ts()}"
            self._note(f"move aside {name} -> {os.path.basename(backup)}, link -> {want}")
            if self.writing:
                os.rename(link_path, backup)
                os.symlink(want, link_path)
            return
        self._note(f"link {name} -> {want}")
        if self.writing:
            os.makedirs(os.path.dirname(link_path), exist_ok=True)
            os.symlink(want, link_path)

    def links(self):
        for name in LINK_TO_SOURCE:
            src = os.path.join(self.src_dir, name)
            if os.path.lexists(src):
                self._ensure_link(os.path.join(self.dst_dir, name), src)
        for name in LINK_SHARED:
            src = os.path.join(self.src_dir, name)
            if os.path.islink(src):
                want = os.readlink(src)
            elif os.path.isdir(src):
                want = src
            else:
                continue
            self._ensure_link(os.path.join(self.dst_dir, name), want)

    def project_memory(self):
        src_projects = os.path.join(self.src_dir, "projects")
        dst_projects = os.path.join(self.dst_dir, "projects")
        # Adopt memory the target created on its own, before linking.
        if os.path.isdir(dst_projects):
            for slug in sorted(os.listdir(dst_projects)):
                dst_mem = os.path.join(dst_projects, slug, "memory")
                src_mem = os.path.join(src_projects, slug, "memory")
                if os.path.isdir(dst_mem) and not os.path.islink(dst_mem):
                    if os.path.lexists(src_mem):
                        self.conflicts.append(
                            f"projects/{slug}/memory is a real dir on both accounts; merge by hand")
                        continue
                    self._note(f"adopt {self.target} projects/{slug}/memory into {self.source}")
                    if self.writing:
                        os.makedirs(os.path.dirname(src_mem), exist_ok=True)
                        shutil.move(dst_mem, src_mem)
                        os.symlink(src_mem, dst_mem)
        if not os.path.isdir(src_projects):
            return
        for slug in sorted(os.listdir(src_projects)):
            src_mem = os.path.join(src_projects, slug, "memory")
            if not os.path.isdir(src_mem):
                continue
            dst_mem = os.path.join(dst_projects, slug, "memory")
            if os.path.isdir(dst_mem) and not os.path.islink(dst_mem):
                continue  # conflict already reported
            self._ensure_link(dst_mem, src_mem)

    # ── copies ───────────────────────────────────────────────────────────
    def copies(self):
        for name in COPY_FILES:
            src = os.path.join(self.src_dir, name)
            dst = os.path.join(self.dst_dir, name)
            if not os.path.isfile(src):
                continue
            with open(src, encoding="utf-8") as fh:
                want = rewrite_paths(fh.read(), self.store, self.source, self.target)
            if name.endswith(".json"):
                json.loads(want)  # never write a file Claude Code would silently ignore
            have = None
            if os.path.isfile(dst) and not os.path.islink(dst):
                with open(dst, encoding="utf-8") as fh:
                    have = fh.read()
            if have == want:
                continue
            self._note(f"{'update' if have is not None else 'create'} {name}"
                       + (f" ({self._key_diff(have, want)})" if have is not None else ""))
            if self.writing:
                if have is not None:
                    shutil.copy2(dst, f"{dst}.bak-mirror-{_ts()}")
                self._write(dst, want)

    @staticmethod
    def _key_diff(have, want):
        try:
            a, b = json.loads(have), json.loads(want)
        except ValueError:
            return "content differs"
        keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        return "keys: " + ", ".join(keys) if keys else "formatting only"

    def claude_json(self):
        src = os.path.join(self.src_dir, ".claude.json")
        dst = os.path.join(self.dst_dir, ".claude.json")
        if not os.path.isfile(src):
            return
        with open(src, encoding="utf-8") as fh:
            source_doc = json.loads(rewrite_paths(fh.read(), self.store, self.source, self.target))
        target_doc = _load_json(dst) if os.path.isfile(dst) else {}
        merged = merge_claude_json(target_doc, source_doc)
        if merged == target_doc and os.path.isfile(dst):
            return
        added = sorted(set(merged.get("mcpServers", {})) - set(target_doc.get("mcpServers", {})))
        self._note(f"{'update' if target_doc else 'create'} .claude.json "
                   f"(mcpServers + trust flags{'; new: ' + ', '.join(added) if added else ''})")
        if self.writing:
            if os.path.isfile(dst):
                shutil.copy2(dst, f"{dst}.bak-mirror-{_ts()}")
            self._write(dst, _dump_json(merged))

    @staticmethod
    def _write(path, text):
        # Write in place (not rename) so the file keeps its permissions.
        mode = "r+" if os.path.exists(path) else "w"
        with open(path, mode, encoding="utf-8") as fh:
            fh.seek(0)
            fh.write(text)
            fh.truncate()
        if mode == "w":
            os.chmod(path, 0o600)

    def run(self):
        if not os.path.isdir(self.src_dir):
            raise SystemExit(f"❌ source account not found: {self.src_dir}")
        if self.writing and not os.path.isdir(self.dst_dir):
            os.makedirs(self.dst_dir, mode=0o700)
        self.links()
        self.project_memory()
        self.copies()
        self.claude_json()
        return self


def logged_in(acct_dir):
    """True once Claude Code has recorded a login (macOS keeps the token in Keychain)."""
    path = os.path.join(acct_dir, ".claude.json")
    try:
        return bool(_load_json(path).get("oauthAccount"))
    except (OSError, ValueError):
        return False


def load_mirrors(manifest_path):
    return {k: v for k, v in _load_json(manifest_path).get("mirrors", {}).items() if not k.startswith("_")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Keep mirror accounts in parity with their source.")
    ap.add_argument("targets", nargs="*", help="mirror accounts to sync (default: all in profiles.json)")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    group.add_argument("--check", action="store_true", help="exit 1 if any mirror has drifted")
    args = ap.parse_args(argv)

    mirrors = load_mirrors(args.manifest)
    targets = args.targets or sorted(mirrors)
    if not targets:
        print("No mirrors declared in profiles.json (\"mirrors\": {\"<target>\": \"<source>\"}).")
        return 1
    mode = "check" if args.check else "dry-run" if args.dry_run else "apply"
    drifted = False
    for target in targets:
        if target not in mirrors:
            print(f"❌ '{target}' is not a declared mirror (have: {', '.join(sorted(mirrors)) or 'none'})")
            return 1
        m = Mirror(args.store, mirrors[target], target, mode).run()
        verb = {"apply": "applied", "dry-run": "would apply", "check": "drift"}[mode]
        print(f"🪞 {target} ← {mirrors[target]}: "
              + (f"{len(m.changes)} change(s) {verb}" if m.changes else "in parity"))
        for c in m.changes:
            print(f"   · {c}")
        for c in m.conflicts:
            print(f"   ⚠️  {c}")
        drifted = drifted or bool(m.changes or m.conflicts)
        if mode == "apply" and not logged_in(m.dst_dir):
            print(f"   ℹ️  not logged in yet: claude-acs sw {target} && claude  → /login")
    return 1 if (mode == "check" and drifted) else 0


if __name__ == "__main__":
    sys.exit(main())
