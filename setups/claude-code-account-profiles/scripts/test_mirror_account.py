#!/usr/bin/env python3
"""Tests for mirror_account.py. Stdlib unittest, temp dirs only.

Run: python3 test_mirror_account.py
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_profile  # noqa: E402
import mirror_account as mm  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class MirrorTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = os.path.join(self.tmp, "store")
        self.canon = os.path.join(self.tmp, "canon")
        self.src = os.path.join(self.store, "personal")
        self.dst = os.path.join(self.store, "terradev")
        for d in ("skills", "commands", "plugins"):
            os.makedirs(os.path.join(self.canon, d))
            os.makedirs(self.src, exist_ok=True)
            os.symlink(os.path.join(self.canon, d), os.path.join(self.src, d))
        write(os.path.join(self.src, "CLAUDE.md"), "# personal\n")
        write(os.path.join(self.src, "hooks", "h.sh"), "echo hi\n")
        write(os.path.join(self.src, "memory", "MEMORY.md"), "- fact\n")
        write(os.path.join(self.src, "projects", "-repo-a", "memory", "m.md"), "a\n")
        os.makedirs(os.path.join(self.src, "projects", "-repo-nomem"))
        self.settings = {
            "model": "opus",
            "hooks": {"Stop": [{"command": f"bash '{self.src}/hooks/h.sh'"}]},
        }
        write(os.path.join(self.src, "settings.json"), json.dumps(self.settings))
        write(os.path.join(self.src, ".claude.json"), json.dumps({
            "oauthAccount": {"emailAddress": "source@example.com"},
            "userID": "src-user",
            "mcpServers": {"ledger": {"type": "http", "url": "https://x/mcp"}},
            "projects": {"/r/a": {"hasTrustDialogAccepted": True, "history": ["secret"]},
                         "/r/b": {"hasTrustDialogAccepted": False}},
        }))

    def run_mirror(self, mode="apply"):
        return mm.Mirror(self.store, "personal", "terradev", mode).run()

    # ── links ──
    def test_links_point_at_source_and_canonical(self):
        self.run_mirror()
        for name in ("CLAUDE.md", "hooks", "memory"):
            self.assertEqual(os.readlink(os.path.join(self.dst, name)), os.path.join(self.src, name))
        self.assertEqual(os.readlink(os.path.join(self.dst, "skills")), os.path.join(self.canon, "skills"))
        self.assertFalse(os.path.lexists(os.path.join(self.dst, "agents")), "absent on source -> not linked")

    def test_memory_written_on_target_lands_on_source(self):
        self.run_mirror()
        write(os.path.join(self.dst, "memory", "new.md"), "learned on terradev\n")
        self.assertEqual(read(os.path.join(self.src, "memory", "new.md")), "learned on terradev\n")
        self.assertEqual(read(os.path.join(self.dst, "projects", "-repo-a", "memory", "m.md")), "a\n")
        self.assertFalse(os.path.lexists(os.path.join(self.dst, "projects", "-repo-nomem", "memory")))

    def test_target_only_project_memory_is_adopted_not_lost(self):
        write(os.path.join(self.dst, "projects", "-repo-new", "memory", "x.md"), "x\n")
        self.run_mirror()
        self.assertEqual(read(os.path.join(self.src, "projects", "-repo-new", "memory", "x.md")), "x\n")
        self.assertTrue(os.path.islink(os.path.join(self.dst, "projects", "-repo-new", "memory")))

    def test_real_memory_on_both_sides_is_a_conflict_and_untouched(self):
        write(os.path.join(self.dst, "projects", "-repo-a", "memory", "mine.md"), "t\n")
        m = self.run_mirror()
        self.assertEqual(len(m.conflicts), 1)
        self.assertEqual(read(os.path.join(self.dst, "projects", "-repo-a", "memory", "mine.md")), "t\n")

    def test_existing_real_dir_is_moved_aside_never_deleted(self):
        write(os.path.join(self.dst, "hooks", "old.sh"), "old\n")
        self.run_mirror()
        aside = [n for n in os.listdir(self.dst) if n.startswith("hooks.pre-mirror-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual(read(os.path.join(self.dst, aside[0], "old.sh")), "old\n")

    # ── copies ──
    def test_settings_copied_with_account_paths_rewritten(self):
        self.run_mirror()
        got = json.loads(read(os.path.join(self.dst, "settings.json")))
        self.assertEqual(got["model"], "opus")
        cmd = got["hooks"]["Stop"][0]["command"]
        self.assertIn(f"{self.dst}/hooks/h.sh", cmd)
        self.assertNotIn(f"{self.src}/", cmd)

    def test_drifted_settings_are_reset_with_backup(self):
        self.run_mirror()
        write(os.path.join(self.dst, "settings.json"), json.dumps({"model": "haiku"}))
        m = self.run_mirror()
        self.assertTrue(any("model" in c for c in m.changes))
        self.assertEqual(json.loads(read(os.path.join(self.dst, "settings.json")))["model"], "opus")
        self.assertTrue(any(n.startswith("settings.json.bak-mirror-") for n in os.listdir(self.dst)))

    def test_claude_json_takes_only_mcp_and_trust_never_identity(self):
        write(os.path.join(self.dst, ".claude.json"), json.dumps({
            "oauthAccount": {"emailAddress": "terradev@example.com"}, "userID": "dst-user"}))
        self.run_mirror()
        got = json.loads(read(os.path.join(self.dst, ".claude.json")))
        self.assertEqual(got["oauthAccount"]["emailAddress"], "terradev@example.com")
        self.assertEqual(got["userID"], "dst-user")
        self.assertIn("ledger", got["mcpServers"])
        self.assertEqual(got["projects"], {"/r/a": {"hasTrustDialogAccepted": True}})

    def test_fresh_target_gets_minimal_claude_json(self):
        self.run_mirror()
        got = json.loads(read(os.path.join(self.dst, ".claude.json")))
        self.assertNotIn("oauthAccount", got)
        self.assertNotIn("userID", got)
        self.assertFalse(mm.logged_in(self.dst))

    # ── modes ──
    def test_second_run_is_a_no_op(self):
        self.run_mirror()
        self.assertEqual(self.run_mirror().changes, [])

    def test_dry_run_and_check_write_nothing(self):
        for mode in ("dry-run", "check"):
            m = self.run_mirror(mode)
            self.assertTrue(m.changes)
            self.assertFalse(os.path.exists(self.dst))

    def test_cli_check_exits_1_on_drift_0_in_parity(self):
        manifest = os.path.join(self.tmp, "profiles.json")
        write(manifest, json.dumps({"mirrors": {"_comment": "x", "terradev": "personal"}}))
        argv = ["--store", self.store, "--manifest", manifest]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(mm.main(argv + ["--check"]), 1)
            self.assertEqual(mm.main(argv), 0)
            self.assertEqual(mm.main(argv + ["--check"]), 0)
            self.assertEqual(mm.main(argv + ["nope"]), 1)

    def test_apply_profile_refuses_a_mirror(self):
        manifest = {"core": {"plugins": ["a@b"]}, "accounts": {"personal": {}},
                    "mirrors": {"terradev": "personal"}}
        with self.assertRaises(KeyError) as ctx:
            apply_profile.resolve_account(manifest, "terradev")
        self.assertIn("claude-acs mirror terradev", str(ctx.exception))


class RealManifest(unittest.TestCase):
    def test_terradev_mirrors_personal(self):
        self.assertEqual(mm.load_mirrors(mm.DEFAULT_MANIFEST).get("terradev"), "personal")


if __name__ == "__main__":
    unittest.main(verbosity=1)
