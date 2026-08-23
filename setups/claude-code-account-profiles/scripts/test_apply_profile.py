#!/usr/bin/env python3
"""Tests for apply_profile.py. Stdlib unittest. Run: python3 test_apply_profile.py"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_profile as ap  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(HERE, os.pardir, "profiles.json")

# Personal profile as defined in the manifest = lean-18 + mattpocock-skills = 19 plugins.
# Base lean-18 was measured 2026-07-25 (audit: -8.7k / -20% startup tax, hash 59a9cd93...);
# the mattpocock-skills bundle was added to core 2026-07-27.
GOLDEN_PERSONAL_HASH = "d8e09889da378667d861ecc920e61440a6d14e78386a6eb1e0be2632b0dae057"


def load_manifest():
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class ResolvePlugins(unittest.TestCase):
    def test_dedupe_order_preserving(self):
        eff, warns = ap.resolve_plugins(["a", "b", "a", "c"], [], [])
        self.assertEqual(eff, ["a", "b", "c"])
        self.assertEqual(warns, [])

    def test_add_appends(self):
        eff, _ = ap.resolve_plugins(["a", "b"], ["c"], [])
        self.assertEqual(eff, ["a", "b", "c"])

    def test_remove_subtracts(self):
        eff, _ = ap.resolve_plugins(["a", "b", "c"], [], ["b"])
        self.assertEqual(eff, ["a", "c"])

    def test_add_then_remove_wins(self):
        eff, _ = ap.resolve_plugins(["a"], ["b"], ["b"])
        self.assertEqual(eff, ["a"])

    def test_warn_add_duplicate_of_core(self):
        _, warns = ap.resolve_plugins(["a"], ["a"], [])
        self.assertTrue(any("duplicates core" in w for w in warns))

    def test_warn_remove_absent(self):
        _, warns = ap.resolve_plugins(["a"], [], ["z"])
        self.assertTrue(any("not present" in w for w in warns))


class GoldenHash(unittest.TestCase):
    def test_personal_resolves_to_applied_lean_hash(self):
        manifest = load_manifest()
        resolved = ap.resolve_account(manifest, "personal")
        self.assertEqual(
            resolved["hash"], GOLDEN_PERSONAL_HASH,
            "manifest core.plugins no longer reproduces the applied personal lean profile",
        )
        self.assertEqual(len(resolved["effective"]), 19)

    def test_hash_is_object_sorted_keys(self):
        # Hash must match sha256 of {plugin:true} with sorted keys.
        import hashlib
        eff = ["b@m", "a@m"]
        expected = hashlib.sha256(
            json.dumps({"b@m": True, "a@m": True}, sort_keys=True).encode()
        ).hexdigest()
        self.assertEqual(ap.config_hash(eff), expected)


class ResolveAccountErrors(unittest.TestCase):
    def test_unknown_account_raises(self):
        with self.assertRaises(KeyError):
            ap.resolve_account(load_manifest(), "nope")

    def test_empty_core_raises(self):
        with self.assertRaises(ValueError):
            ap.resolve_account({"core": {"plugins": []}, "accounts": {"x": {}}}, "x")


class ApplyIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.manifest = load_manifest()

    def _make_account(self, name, plugins, skills=None):
        acct = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(acct, "skills"), exist_ok=True)
        settings = {
            "enabledPlugins": {p: True for p in plugins},
            "extraKnownMarketplaces": {},
            "model": "sonnet",              # must be preserved
            "hooks": {"Stop": [{"x": 1}]},  # must be preserved
        }
        with open(os.path.join(acct, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        for s in (skills or []):
            os.makedirs(os.path.join(acct, "skills", s), exist_ok=True)
        return acct

    def _read_settings(self, acct):
        with open(os.path.join(acct, "settings.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_apply_transforms_and_preserves_other_keys(self):
        acct = self._make_account("personal", ["old-plugin@m", "superpowers@claude-plugins-official"],
                                  skills=["cloudflare", "wrangler", "keep-me"])
        rc = ap.apply_account(self.manifest, "personal", self.tmp,
                              dry_run=False, assume_yes=True, prune_skills=True)
        self.assertEqual(rc, 0)
        s = self._read_settings(acct)
        self.assertEqual(len(s["enabledPlugins"]), 19)
        self.assertNotIn("old-plugin@m", s["enabledPlugins"])
        self.assertEqual(s["model"], "sonnet")            # preserved
        self.assertIn("Stop", s["hooks"])                 # preserved
        self.assertIn("harrison-engineering", s["extraKnownMarketplaces"])  # merged
        # pruned real-dir skills moved to trash; kept skill stays
        self.assertFalse(os.path.exists(os.path.join(acct, "skills", "cloudflare")))
        self.assertTrue(os.path.exists(os.path.join(acct, "skills", ap.PRUNED_DIRNAME, "cloudflare")))
        self.assertTrue(os.path.exists(os.path.join(acct, "skills", "keep-me")))

    def test_skills_untouched_without_prune_flag(self):
        acct = self._make_account("personal", ["old@m"], skills=["cloudflare", "keep-me"])
        rc = ap.apply_account(self.manifest, "personal", self.tmp,
                              dry_run=False, assume_yes=True)  # prune_skills defaults False
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(acct, "skills", "cloudflare")))  # not pruned
        self.assertFalse(os.path.exists(os.path.join(acct, "skills", ap.PRUNED_DIRNAME)))
        self.assertEqual(len(self._read_settings(acct)["enabledPlugins"]), 19)  # plugins still applied

    def test_backup_created(self):
        acct = self._make_account("personal", ["old@m"])
        ap.apply_account(self.manifest, "personal", self.tmp, dry_run=False, assume_yes=True)
        backups = [f for f in os.listdir(acct) if f.startswith("settings.json.bak-")]
        self.assertEqual(len(backups), 1)

    def test_idempotent_second_run_is_noop(self):
        self._make_account("personal", ["old@m"])
        ap.apply_account(self.manifest, "personal", self.tmp, dry_run=False, assume_yes=True)
        acct = os.path.join(self.tmp, "personal")
        before = sorted(os.listdir(acct))
        rc = ap.apply_account(self.manifest, "personal", self.tmp, dry_run=False, assume_yes=True)
        after = sorted(os.listdir(acct))
        self.assertEqual(rc, 0)
        self.assertEqual(before, after, "second run should create no new backup / no changes")

    def test_dry_run_writes_nothing(self):
        acct = self._make_account("personal", ["old@m"], skills=["cloudflare"])
        before = sorted(os.listdir(acct))
        rc = ap.apply_account(self.manifest, "personal", self.tmp,
                              dry_run=True, assume_yes=True, prune_skills=True)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(os.listdir(acct)), before)
        self.assertTrue(os.path.exists(os.path.join(acct, "skills", "cloudflare")))  # untouched

    def test_missing_account_dir(self):
        rc = ap.apply_account(self.manifest, "personal", self.tmp, dry_run=False, assume_yes=True)
        self.assertEqual(rc, 1)

    def test_symlink_skill_skipped_not_moved(self):
        acct = self._make_account("personal", ["old@m"])
        # make 'cloudflare' a symlink → should be skipped in v1
        target = os.path.join(self.tmp, "some-target")
        os.makedirs(target, exist_ok=True)
        os.symlink(target, os.path.join(acct, "skills", "cloudflare"))
        ap.apply_account(self.manifest, "personal", self.tmp,
                         dry_run=False, assume_yes=True, prune_skills=True)
        # symlink still present (skipped), not moved to trash
        self.assertTrue(os.path.islink(os.path.join(acct, "skills", "cloudflare")))
        self.assertFalse(os.path.exists(os.path.join(acct, "skills", ap.PRUNED_DIRNAME, "cloudflare")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
