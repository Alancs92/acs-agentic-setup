#!/usr/bin/env python3
"""End-to-end tests for claude-skills-backup.

Exercises the real pipeline across module boundaries on a temp tree: discover ->
hash -> store -> index -> retain -> sync -> restore. Unit tests prove each module
in isolation; these prove the seams between them, which is where a frozen-contract
build actually breaks.

Nothing here touches the user's real ~/.claude, OneDrive, or LaunchAgents.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backup                 # noqa: E402
import index as index_mod     # noqa: E402
import restore as restore_mod # noqa: E402
import store                  # noqa: E402
import sync                   # noqa: E402


class _Args:
    """Stand-in for the argparse namespace backup.cmd_* expects."""

    def __init__(self, **kw):
        self.no_prune = kw.pop("no_prune", False)
        self.dry_run = kw.pop("dry_run", False)
        for key, value in kw.items():
            setattr(self, key, value)


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        # A fake ~/.claude with two skills, a command and a hook.
        self.skills = self.root / "claude" / "skills"
        (self.skills / "alpha").mkdir(parents=True)
        (self.skills / "alpha" / "SKILL.md").write_text("# alpha v1\n")
        (self.skills / "beta").mkdir(parents=True)
        (self.skills / "beta" / "SKILL.md").write_text("# beta v1\n")

        self.commands = self.root / "claude" / "commands"
        self.commands.mkdir(parents=True)
        (self.commands / "hello.md").write_text("hello\n")

        self.hooks = self.root / "claude" / "hooks"
        self.hooks.mkdir(parents=True)
        (self.hooks / "on-start.sh").write_text("#!/bin/sh\necho hi\n")

        # A fake account carrying a secret, to prove redaction reaches the blob.
        self.accounts = self.root / "accounts"
        (self.accounts / "personal").mkdir(parents=True)
        (self.accounts / "personal" / "settings.json").write_text(
            json.dumps({
                "model": "claude-opus-5",
                "env": {"ANTHROPIC_AUTH_TOKEN": "sk-ant-api03-SUPERSECRET"},
            }, indent=2)
        )
        (self.accounts / "personal" / "CLAUDE.md").write_text("# rules\n")

        self.onedrive = self.root / "onedrive"
        self.fallback = self.root / "cache"

        self.config = {
            "sources": {
                "skills": {"enabled": True, "path": str(self.skills)},
                "commands": {"enabled": True, "path": str(self.commands)},
                "agents": {"enabled": False, "path": str(self.root / "nope")},
                "hooks": {"enabled": True, "path": str(self.hooks)},
                "settings": {"enabled": True, "path": str(self.accounts),
                             "accounts": ["personal"]},
                "plugins": {"enabled": False, "path": str(self.root / "plugins"),
                            "mode": "manifests"},
            },
            "exclude_globs": ["**/.git/**", "**/.DS_Store"],
            "storage": {
                "onedrive_root": str(self.onedrive / "Claude Skills Backup"),
                "live_mirror": str(self.onedrive / "Claude User Skills"),
                "local_fallback": str(self.fallback),
                "refresh_live_mirror": True,
                "compression": "xz",
            },
            "retention": {
                "always_keep_latest": 2,
                "keep_all_within_days": 30,
                "tiers": [{"older_than_days": 30, "thin_to": "weekly"},
                          {"older_than_days": 90, "thin_to": "monthly"}],
                "stable_after_days": 180,
                "stable_keep": 2,
                "never_delete_only_copy": True,
            },
            "secrets": {
                "mode": "redact",
                "key_patterns": ["token", "secret", "password", "apikey"],
                "value_patterns": ["^sk-ant-"],
            },
            "schedule": {"interval_days": 7, "hour": 3, "minute": 0,
                         "label": "com.acs.test-backup"},
        }
        self.onedrive.mkdir(parents=True)

        # Keep catalog.jsonl out of the real repo during tests.
        self._real_catalog = backup.CATALOG_PATH
        backup.CATALOG_PATH = self.root / "catalog.jsonl"

    def tearDown(self):
        backup.CATALOG_PATH = self._real_catalog
        self._tmp.cleanup()

    def _run(self, **kw):
        return backup.cmd_run(self.config, _Args(**kw))

    def _conn(self):
        return index_mod.connect(backup._local_db_path(self.config))

    # --- the core promise ---------------------------------------------------

    def test_first_run_snapshots_everything(self):
        self.assertEqual(0, self._run())
        conn = self._conn()
        units = index_mod.iter_unit_ids(conn)
        names = {f"{k}/{n}" for _, k, n in units}
        self.assertIn("skill/alpha", names)
        self.assertIn("skill/beta", names)
        self.assertIn("command/hello.md", names)
        self.assertIn("settings/personal/settings.json", names)
        # Every unit got exactly one snapshot on a cold start.
        for unit_id, _k, _n in units:
            self.assertEqual(1, len(index_mod.snapshots_for_unit(conn, unit_id)))

    def test_second_run_with_no_changes_writes_nothing(self):
        """The central economy claim: unchanged skills cost zero new storage."""
        self._run()
        conn = self._conn()
        before = {uid: len(index_mod.snapshots_for_unit(conn, uid))
                  for uid, _k, _n in index_mod.iter_unit_ids(conn)}
        conn.close()

        self._run()
        conn = self._conn()
        after = {uid: len(index_mod.snapshots_for_unit(conn, uid))
                 for uid, _k, _n in index_mod.iter_unit_ids(conn)}
        self.assertEqual(before, after, "an unchanged run created snapshots")

    def test_touch_does_not_fabricate_a_snapshot(self):
        """mtime must not reach the hash, or every OneDrive resync costs storage."""
        self._run()
        os.utime(self.skills / "alpha" / "SKILL.md", (0, 0))
        conn = self._conn()
        alpha = [u for u in index_mod.iter_unit_ids(conn) if u[2] == "alpha"][0]
        before = len(index_mod.snapshots_for_unit(conn, alpha[0]))
        conn.close()

        self._run()
        conn = self._conn()
        self.assertEqual(before, len(index_mod.snapshots_for_unit(conn, alpha[0])))

    def test_edit_creates_a_second_snapshot(self):
        self._run()
        (self.skills / "alpha" / "SKILL.md").write_text("# alpha v2 CHANGED\n")
        self._run()

        conn = self._conn()
        alpha = [u for u in index_mod.iter_unit_ids(conn) if u[2] == "alpha"][0]
        snaps = index_mod.snapshots_for_unit(conn, alpha[0])
        self.assertEqual(2, len(snaps))
        self.assertNotEqual(snaps[0].content_hash, snaps[1].content_hash)

    # --- recovery -----------------------------------------------------------

    def test_restore_round_trip_reproduces_content(self):
        self._run()
        dest = self.root / "restored-alpha"
        conn = self._conn()
        blob_root, _ = sync.resolve_storage(self.config)
        rc = restore_mod.restore(conn, blob_root, "skill/alpha", dest=dest)
        self.assertEqual(0, rc)
        self.assertEqual("# alpha v1\n", (dest / "SKILL.md").read_text())

    def test_restore_at_an_earlier_date_returns_the_old_content(self):
        """The actual reason this system exists."""
        self._run()
        (self.skills / "alpha" / "SKILL.md").write_text("# alpha v2\n")
        self._run()

        conn = self._conn()
        alpha = [u for u in index_mod.iter_unit_ids(conn) if u[2] == "alpha"][0]
        snaps = index_mod.snapshots_for_unit(conn, alpha[0])  # newest first
        oldest = snaps[-1]

        blob_root, _ = sync.resolve_storage(self.config)
        dest = self.root / "restored-old"
        dest.mkdir()
        store.read_blob(blob_root, oldest.blob_key, dest)
        self.assertEqual("# alpha v1\n", (dest / "SKILL.md").read_text())

    def test_deleted_skill_is_tombstoned_but_still_restorable(self):
        self._run()
        import shutil
        shutil.rmtree(self.skills / "beta")
        self._run()

        conn = self._conn()
        beta = [u for u in index_mod.iter_unit_ids(conn) if u[2] == "beta"]
        self.assertTrue(beta, "deleted unit vanished from the index entirely")
        snaps = index_mod.snapshots_for_unit(conn, beta[0][0])
        self.assertTrue(snaps, "deleted unit lost its snapshot history")

        blob_root, _ = sync.resolve_storage(self.config)
        dest = self.root / "restored-beta"
        rc = restore_mod.restore(conn, blob_root, "skill/beta", dest=dest)
        self.assertEqual(0, rc, "a deleted skill must still be recoverable")
        self.assertEqual("# beta v1\n", (dest / "SKILL.md").read_text())

    # --- security -----------------------------------------------------------

    def test_secret_never_reaches_the_blob(self):
        """These blobs land in corporate OneDrive. A leak here is an incident."""
        self._run()
        blob_root, _ = sync.resolve_storage(self.config)
        leaked = []
        for path in (Path(blob_root) / "objects").rglob("*.tar.xz"):
            if b"SUPERSECRET" in path.read_bytes():
                leaked.append(path)
        self.assertEqual([], leaked, "an unredacted secret reached a blob")

        dest = self.root / "restored-settings"
        conn = self._conn()
        restore_mod.restore(conn, blob_root,
                            "settings/personal/settings.json", dest=dest)
        body = (dest / "settings.json").read_text()
        self.assertNotIn("SUPERSECRET", body)
        self.assertIn("REDACTED", body)

    # --- degraded mode ------------------------------------------------------

    def test_missing_onedrive_degrades_instead_of_failing(self):
        self.config["storage"]["onedrive_root"] = str(
            self.root / "no-such-cloud" / "Backup")
        self.config["storage"]["refresh_live_mirror"] = False
        self.assertEqual(0, self._run(), "a missing cloud aborted the backup")

        conn = self._conn()
        last = index_mod.last_run(conn)
        self.assertEqual("degraded", last["status"])
        # ...and the data still landed locally.
        self.assertTrue(any((self.fallback / "objects").rglob("*.tar.xz")))

    # --- catalog ------------------------------------------------------------

    def test_catalog_is_committed_and_byte_stable(self):
        self._run()
        first = backup.CATALOG_PATH.read_bytes()
        self.assertTrue(first, "catalog.jsonl was not written")

        conn = self._conn()
        index_mod.export_catalog(conn, backup.CATALOG_PATH)
        self.assertEqual(first, backup.CATALOG_PATH.read_bytes(),
                         "catalog.jsonl is not byte-stable; it will churn in git")

        for line in first.decode().strip().splitlines():
            row = json.loads(line)
            self.assertEqual(
                ["kind", "name", "hash", "created", "bytes", "files", "pruned"],
                list(row.keys()))

    # --- verify -------------------------------------------------------------

    def test_verify_is_clean_after_a_fresh_run(self):
        self._run()
        conn = self._conn()
        blob_root, _ = sync.resolve_storage(self.config)
        corrupt, orphans, checked = restore_mod.verify(conn, blob_root)
        self.assertEqual([], corrupt)
        self.assertEqual([], orphans)
        self.assertGreater(checked, 0)

    def test_verify_detects_a_corrupted_blob(self):
        self._run()
        blob_root, _ = sync.resolve_storage(self.config)
        victim = next((Path(blob_root) / "objects").rglob("*.tar.xz"))
        victim.write_bytes(b"not a tarball at all")

        conn = self._conn()
        corrupt, _orphans, _checked = restore_mod.verify(conn, blob_root)
        self.assertTrue(corrupt, "a corrupted blob went undetected")

    # --- live mirror --------------------------------------------------------

    def test_live_mirror_tracks_skills_only(self):
        self._run()
        mirror = self.onedrive / "Claude User Skills"
        self.assertTrue((mirror / "alpha" / "SKILL.md").exists())
        self.assertTrue((mirror / "beta" / "SKILL.md").exists())
        # Commands and hooks are backed up but deliberately not mirrored.
        self.assertFalse((mirror / "hello.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
