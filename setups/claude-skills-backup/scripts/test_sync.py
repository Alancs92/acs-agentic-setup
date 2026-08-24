#!/usr/bin/env python3
"""Tests for sync.py -- OneDrive placement and scheduling.

Written before the implementation. Two of these functions touch the user's real
corporate OneDrive, so the cases that matter are the ones where a bug is
destructive rather than merely wrong:

  * degraded fallback  -- OneDrive offline must never abort a backup, so every
                          "cloud is missing/unwritable" shape has to land on the
                          local fallback instead of raising
  * mirror deletion    -- refresh_live_mirror() removes directories inside the
                          user's OneDrive. The guard that keeps those deletes
                          inside the configured mirror root, and stops them
                          following a symlink out of it, is the single most
                          important property in this module
  * atomic index       -- a torn index.db uploaded to the cloud is worse than a
                          stale one, so placement must be temp + os.replace
  * plist validity     -- a malformed plist fails silently at launchctl load
                          time; every rendered variant is checked with
                          `plutil -lint`

`redact` is built in parallel by another agent, so it is stubbed here through
sys.modules -- sync.py imports it lazily for exactly this reason.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backup_types import KIND_COMMAND, KIND_SKILL, Unit

import discovery
import sync


# --- redact stub ------------------------------------------------------------

class _StubRedact:
    """Stands in for redact.py. Records every call so the tests can assert that
    redaction is actually applied to every byte that reaches the mirror."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def redact_bytes(self, data: bytes, filename: str, secrets_config: dict):
        self.calls.append((filename, secrets_config))
        if b"SECRET-TOKEN" in data:
            return data.replace(b"SECRET-TOKEN", b"<<RED>>"), 1
        return data, 0


class _SyncTestCase(unittest.TestCase):
    """Temp dir per test, plus the redact stub installed into sys.modules."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # macOS hands out /var/folders/... which is a symlink to /private/var.
        # Resolve up front so the mirror-root "must be resolved" guard is
        # exercised deliberately rather than tripped by the test harness.
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

        self.stub = _StubRedact()
        self._prev_redact = sys.modules.get("redact")
        sys.modules["redact"] = self.stub
        self.addCleanup(self._restore_redact)

    def _restore_redact(self) -> None:
        if self._prev_redact is None:
            sys.modules.pop("redact", None)
        else:
            sys.modules["redact"] = self._prev_redact

    # -- helpers --
    def make_skill(self, root: Path, name: str, files: dict) -> Unit:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        return Unit(kind=KIND_SKILL, name=name, source_path=d.resolve(),
                    is_file=False)


# --- resolve_storage --------------------------------------------------------

class TestResolveStorage(_SyncTestCase):

    def _config(self, onedrive: Path, fallback: Path) -> dict:
        return {"storage": {"onedrive_root": str(onedrive),
                            "local_fallback": str(fallback)}}

    def test_onedrive_present_and_writable_is_preferred(self):
        od = self.tmp / "OneDrive" / "Claude Skills Backup"
        od.mkdir(parents=True)
        fb = self.tmp / "cache"
        root, degraded = sync.resolve_storage(self._config(od, fb))
        self.assertEqual(root, od)
        self.assertFalse(degraded)

    def test_onedrive_absent_falls_back_and_flags_degraded(self):
        # Whole CloudStorage tree missing: OneDrive is not installed/mounted.
        od = self.tmp / "nope" / "OneDrive" / "Claude Skills Backup"
        fb = self.tmp / "cache"
        root, degraded = sync.resolve_storage(self._config(od, fb))
        self.assertEqual(root, fb)
        self.assertTrue(degraded)
        self.assertTrue(root.is_dir(), "fallback root must be created")
        self.assertFalse(od.exists(), "must not create the missing cloud tree")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses permission bits")
    def test_onedrive_exists_but_not_writable_falls_back(self):
        od = self.tmp / "OneDrive" / "Claude Skills Backup"
        od.mkdir(parents=True)
        os.chmod(od, stat.S_IRUSR | stat.S_IXUSR)  # r-x------
        self.addCleanup(os.chmod, od, 0o700)
        fb = self.tmp / "cache"
        root, degraded = sync.resolve_storage(self._config(od, fb))
        self.assertEqual(root, fb)
        self.assertTrue(degraded)

    def test_onedrive_root_created_when_parent_is_writable(self):
        # First ever run: OneDrive is mounted but our subfolder does not exist.
        parent = self.tmp / "OneDrive"
        parent.mkdir()
        od = parent / "Claude Skills Backup"
        fb = self.tmp / "cache"
        root, degraded = sync.resolve_storage(self._config(od, fb))
        self.assertEqual(root, od)
        self.assertFalse(degraded)
        self.assertTrue(od.is_dir())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses permission bits")
    def test_onedrive_parent_unwritable_falls_back(self):
        parent = self.tmp / "OneDrive"
        parent.mkdir()
        os.chmod(parent, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, parent, 0o700)
        od = parent / "Claude Skills Backup"
        fb = self.tmp / "cache"
        root, degraded = sync.resolve_storage(self._config(od, fb))
        self.assertEqual(root, fb)
        self.assertTrue(degraded)

    def test_onedrive_path_is_a_file_falls_back(self):
        od = self.tmp / "OneDrive"
        od.write_text("not a directory")
        fb = self.tmp / "cache"
        root, degraded = sync.resolve_storage(self._config(od, fb))
        self.assertEqual(root, fb)
        self.assertTrue(degraded)

    def test_missing_or_blank_onedrive_config_is_degraded_not_fatal(self):
        fb = self.tmp / "cache"
        for storage in ({"local_fallback": str(fb)},
                        {"onedrive_root": "", "local_fallback": str(fb)},
                        {"onedrive_root": None, "local_fallback": str(fb)}):
            with self.subTest(storage=storage):
                root, degraded = sync.resolve_storage({"storage": storage})
                self.assertEqual(root, fb)
                self.assertTrue(degraded)

    def test_returned_root_is_absolute_and_expanded(self):
        fb = self.tmp / "cache"
        root, _ = sync.resolve_storage(
            {"storage": {"onedrive_root": "~/definitely/not/here/xyzzy",
                         "local_fallback": str(fb)}}
        )
        self.assertTrue(root.is_absolute())
        self.assertEqual(root, root.resolve())
        self.assertNotIn("~", str(root))


# --- refresh_live_mirror ----------------------------------------------------

class TestRefreshLiveMirror(_SyncTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.mirror = self.tmp / "mirror"
        self.secrets = {"mode": "redact", "key_patterns": ["token"]}

    def refresh(self, units, excludes=None):
        return sync.refresh_live_mirror(
            units, self.mirror, excludes or [], self.secrets
        )

    def test_adds_new_skills_and_creates_missing_mirror_root(self):
        a = self.make_skill(self.src, "deep-research", {"SKILL.md": b"# a"})
        b = self.make_skill(self.src, "tide-sync",
                            {"SKILL.md": b"# b", "scripts/run.py": b"print(1)"})
        stats = self.refresh([a, b])
        self.assertEqual(stats, {"added": 2, "updated": 0, "removed": 0})
        self.assertEqual((self.mirror / "deep-research" / "SKILL.md").read_bytes(),
                         b"# a")
        self.assertEqual(
            (self.mirror / "tide-sync" / "scripts" / "run.py").read_bytes(),
            b"print(1)")

    def test_second_run_with_no_changes_is_a_no_op(self):
        a = self.make_skill(self.src, "alpha", {"SKILL.md": b"# a"})
        self.refresh([a])
        stats = self.refresh([a])
        self.assertEqual(stats, {"added": 0, "updated": 0, "removed": 0})

    def test_changed_content_counts_as_updated(self):
        a = self.make_skill(self.src, "alpha", {"SKILL.md": b"# a"})
        self.refresh([a])
        (self.src / "alpha" / "SKILL.md").write_bytes(b"# a v2")
        stats = self.refresh([a])
        self.assertEqual(stats, {"added": 0, "updated": 1, "removed": 0})
        self.assertEqual((self.mirror / "alpha" / "SKILL.md").read_bytes(),
                         b"# a v2")

    def test_new_file_in_existing_skill_counts_as_updated(self):
        a = self.make_skill(self.src, "alpha", {"SKILL.md": b"# a"})
        self.refresh([a])
        (self.src / "alpha" / "reference.md").write_bytes(b"ref")
        stats = self.refresh([a])
        self.assertEqual(stats["updated"], 1)
        self.assertTrue((self.mirror / "alpha" / "reference.md").exists())

    def test_file_deleted_from_source_is_deleted_from_mirror(self):
        a = self.make_skill(self.src, "alpha",
                            {"SKILL.md": b"# a", "old.md": b"stale"})
        self.refresh([a])
        (self.src / "alpha" / "old.md").unlink()
        stats = self.refresh([a])
        self.assertEqual(stats["updated"], 1)
        self.assertFalse((self.mirror / "alpha" / "old.md").exists())
        self.assertTrue((self.mirror / "alpha" / "SKILL.md").exists())

    def test_executable_bit_is_mirrored_and_changes_count_as_update(self):
        a = self.make_skill(self.src, "alpha", {"bin/run.sh": b"#!/bin/sh\n"})
        self.refresh([a])
        mirrored = self.mirror / "alpha" / "bin" / "run.sh"
        self.assertFalse(os.access(mirrored, os.X_OK))
        os.chmod(self.src / "alpha" / "bin" / "run.sh", 0o755)
        stats = self.refresh([a])
        self.assertEqual(stats["updated"], 1)
        self.assertTrue(os.access(mirrored, os.X_OK))

    def test_skill_removed_from_source_is_removed_from_mirror(self):
        a = self.make_skill(self.src, "alpha", {"SKILL.md": b"# a"})
        b = self.make_skill(self.src, "beta", {"SKILL.md": b"# b"})
        self.refresh([a, b])
        stats = self.refresh([a])  # beta no longer discovered
        self.assertEqual(stats, {"added": 0, "updated": 0, "removed": 1})
        self.assertFalse((self.mirror / "beta").exists())
        self.assertTrue((self.mirror / "alpha").exists())

    def test_all_skills_gone_removes_all_but_never_the_root(self):
        a = self.make_skill(self.src, "alpha", {"SKILL.md": b"# a"})
        self.refresh([a])
        stats = self.refresh([])
        self.assertEqual(stats, {"added": 0, "updated": 0, "removed": 1})
        self.assertTrue(self.mirror.is_dir(), "mirror root itself must survive")

    def test_only_skill_units_are_mirrored(self):
        a = self.make_skill(self.src, "alpha", {"SKILL.md": b"# a"})
        cmd_dir = self.src / "cmd"
        cmd_dir.mkdir()
        (cmd_dir / "x.md").write_bytes(b"cmd")
        cmd = Unit(kind=KIND_COMMAND, name="cmd", source_path=cmd_dir.resolve(),
                   is_file=False)
        stats = self.refresh([a, cmd])
        self.assertEqual(stats["added"], 1)
        self.assertFalse((self.mirror / "cmd").exists())

    def test_excluded_globs_never_reach_the_mirror(self):
        a = self.make_skill(self.src, "alpha", {
            "SKILL.md": b"# a",
            ".DS_Store": b"junk",
            "__pycache__/x.pyc": b"junk",
            ".git/config": b"junk",
            "node_modules/pkg/index.js": b"junk",
        })
        stats = self.refresh([a], excludes=[
            "**/.git/**", "**/.DS_Store", "**/node_modules/**",
            "**/__pycache__/**", "**/*.pyc",
        ])
        self.assertEqual(stats["added"], 1)
        skill = self.mirror / "alpha"
        self.assertTrue((skill / "SKILL.md").exists())
        for junk in (".DS_Store", "__pycache__", ".git", "node_modules"):
            self.assertFalse((skill / junk).exists(), junk)

    def test_redaction_is_applied_to_every_mirrored_file(self):
        a = self.make_skill(self.src, "alpha", {
            "SKILL.md": b"# a",
            "config.json": b'{"token": "SECRET-TOKEN"}',
        })
        self.refresh([a])
        self.assertEqual(
            (self.mirror / "alpha" / "config.json").read_bytes(),
            b'{"token": "<<RED>>"}')
        seen = {name for name, _ in self.stub.calls}
        self.assertEqual(seen, {"SKILL.md", "config.json"},
                         "every file must pass through redact_bytes")
        for _, cfg in self.stub.calls:
            self.assertIs(cfg, self.secrets)

    def test_redaction_difference_alone_triggers_an_update(self):
        # The mirror stores redacted bytes, so comparison must be against the
        # redacted form -- otherwise every run would report a spurious update.
        a = self.make_skill(self.src, "alpha",
                            {"config.json": b'{"token": "SECRET-TOKEN"}'})
        self.assertEqual(self.refresh([a])["added"], 1)
        self.assertEqual(self.refresh([a]),
                         {"added": 0, "updated": 0, "removed": 0})

    def test_single_file_skill_unit_is_mirrored_into_its_own_directory(self):
        p = self.src / "loose.md"
        p.write_bytes(b"# loose")
        u = Unit(kind=KIND_SKILL, name="loose", source_path=p.resolve(),
                 is_file=True)
        stats = self.refresh([u])
        self.assertEqual(stats["added"], 1)
        self.assertEqual((self.mirror / "loose" / "loose.md").read_bytes(),
                         b"# loose")

    def test_dot_entries_at_mirror_root_are_left_alone(self):
        # OneDrive drops .DS_Store and friends in there; they are not stale
        # skills and deleting them is not our business.
        self.mirror.mkdir(parents=True)
        (self.mirror / ".DS_Store").write_bytes(b"junk")
        stats = self.refresh([])
        self.assertEqual(stats["removed"], 0)
        self.assertTrue((self.mirror / ".DS_Store").exists())


# --- exclusion parity with discovery ----------------------------------------

class TestExclusionParity(_SyncTestCase):
    """`exclude_globs` is one config key feeding two consumers: the blobs (via
    discovery.iter_files) and the mirror (via refresh_live_mirror). If the two
    disagree, a file gets excluded from the backup and copied into corporate
    OneDrive anyway -- the worst direction for the disagreement to run. So the
    mirror uses discovery's compiled matcher, and this asserts they agree.

    The tree deliberately contains no symlinked directories: discovery walks with
    followlinks=True (plus a cycle guard) while the mirror walks with
    followlinks=False on purpose, so a symlinked tree is a legitimate difference
    and would make this test about the wrong thing.
    """

    # A slash-less pattern is the case a naive fnmatch port gets wrong: git (and
    # discovery) match it at any depth, fnmatch against a relative path does not.
    PATTERNS = [".DS_Store", "*.pyc", "secrets.env",
                "**/.git/**", "**/node_modules/**", "**/__pycache__/**"]

    TREE = {
        "SKILL.md": b"# alpha",
        ".DS_Store": b"junk",
        "build.pyc": b"junk",
        "secrets.env": b"TOKEN=1",
        "nested/keep.md": b"keep",
        "nested/.DS_Store": b"junk",          # slash-less, at depth
        "nested/app.pyc": b"junk",
        "nested/secrets.env": b"TOKEN=2",
        "deep/a/b/c.md": b"deep",
        ".git/config": b"junk",
        "node_modules/pkg/index.js": b"junk",
        "__pycache__/x.pyc": b"junk",
    }

    def test_mirror_and_discovery_agree_on_every_exclusion(self):
        unit = self.make_skill(self.tmp / "src", "alpha", self.TREE)
        mirror = self.tmp / "mirror"
        sync.refresh_live_mirror([unit], mirror, self.PATTERNS, {})

        skill_dir = mirror / "alpha"
        mirrored = {p.relative_to(skill_dir).as_posix()
                    for p in skill_dir.rglob("*") if p.is_file()}
        blobbed = {p.relative_to(unit.source_path).as_posix()
                   for p in discovery.iter_files(unit, self.PATTERNS)}

        self.assertEqual(mirrored, blobbed)
        self.assertEqual(mirrored, {"SKILL.md", "nested/keep.md", "deep/a/b/c.md"})

    def test_slash_less_pattern_excludes_at_depth(self):
        # The specific divergence a second glob engine introduces.
        unit = self.make_skill(self.tmp / "src", "alpha", self.TREE)
        mirror = self.tmp / "mirror"
        sync.refresh_live_mirror([unit], mirror, [".DS_Store"], {})
        self.assertFalse((mirror / "alpha" / "nested" / ".DS_Store").exists())
        self.assertFalse((mirror / "alpha" / ".DS_Store").exists())
        self.assertTrue((mirror / "alpha" / "nested" / "keep.md").exists())

    def test_patterns_are_not_matched_against_the_absolute_path(self):
        # A skill that merely lives under some ".../cache/..." directory must not
        # be emptied by "**/cache/**". local_fallback is ~/.cache/..., so this is
        # a live shape, not a hypothetical.
        src = self.tmp / "cache" / "skills"
        unit = self.make_skill(src, "alpha", {"SKILL.md": b"# a",
                                              "cache/inner.md": b"excluded"})
        mirror = self.tmp / "mirror"
        stats = sync.refresh_live_mirror([unit], mirror, ["**/cache/**"], {})
        self.assertEqual(stats["added"], 1)
        self.assertTrue((mirror / "alpha" / "SKILL.md").exists(),
                        "absolute-path matching emptied the skill")
        self.assertFalse((mirror / "alpha" / "cache").exists(),
                         "unit-relative match should still prune the inner dir")
        blobbed = {p.relative_to(unit.source_path).as_posix()
                   for p in discovery.iter_files(unit, ["**/cache/**"])}
        self.assertEqual(blobbed, {"SKILL.md"})


# --- the deletion guard -----------------------------------------------------

class TestMirrorDeletionGuard(_SyncTestCase):
    """The mirror removal path unlinks directories inside the user's real
    OneDrive. Everything here is about it refusing to do that anywhere else."""

    def setUp(self) -> None:
        super().setUp()
        self.mirror = self.tmp / "mirror"
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.precious = self.outside / "precious.txt"
        self.precious.write_bytes(b"do not delete me")

    def test_relative_mirror_root_is_refused(self):
        with self.assertRaises(ValueError):
            sync.refresh_live_mirror([], Path("relative/mirror"), [], {})

    def test_unresolved_mirror_root_is_refused(self):
        # A symlinked component means the caller did not run the path through
        # expand(); every later "is this inside the root" check would compare
        # against the wrong tree.
        real = self.tmp / "real"
        real.mkdir()
        link = self.tmp / "link"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            sync.refresh_live_mirror([], link, [], {})

    def test_filesystem_root_and_shallow_paths_are_refused(self):
        for bad in (Path("/"), Path("/Users"), Path.home()):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sync.refresh_live_mirror([], bad, [], {})

    def test_guard_rejects_a_target_outside_the_root(self):
        self.mirror.mkdir()
        with self.assertRaises(ValueError):
            sync._assert_inside_mirror(self.mirror, self.outside)
        with self.assertRaises(ValueError):
            sync._assert_inside_mirror(self.mirror, self.mirror.parent)
        with self.assertRaises(ValueError):
            sync._assert_inside_mirror(self.mirror, self.mirror)
        self.assertTrue(self.precious.exists())

    def test_guard_accepts_a_target_inside_the_root(self):
        self.mirror.mkdir()
        child = self.mirror / "alpha"
        child.mkdir()
        sync._assert_inside_mirror(self.mirror, child)          # no raise
        sync._assert_inside_mirror(self.mirror, child / "deep" / "file.md")

    def test_stale_entry_that_is_a_symlink_out_is_unlinked_not_followed(self):
        self.mirror.mkdir()
        ghost = self.mirror / "ghost-skill"
        ghost.symlink_to(self.outside, target_is_directory=True)
        stats = sync.refresh_live_mirror([], self.mirror, [], {})
        self.assertEqual(stats["removed"], 1)
        self.assertFalse(ghost.exists() or ghost.is_symlink())
        self.assertTrue(self.precious.exists(),
                        "deletion followed a symlink out of the mirror root")
        self.assertTrue(self.outside.is_dir())

    def test_symlink_nested_in_a_stale_skill_is_not_followed(self):
        self.mirror.mkdir()
        stale = self.mirror / "stale-skill"
        stale.mkdir()
        (stale / "SKILL.md").write_bytes(b"# stale")
        (stale / "escape").symlink_to(self.outside, target_is_directory=True)
        stats = sync.refresh_live_mirror([], self.mirror, [], {})
        self.assertEqual(stats["removed"], 1)
        self.assertFalse(stale.exists())
        self.assertTrue(self.precious.exists())

    def test_unit_name_that_escapes_the_root_is_refused(self):
        src = self.tmp / "src"
        src.mkdir()
        d = src / "evil"
        d.mkdir()
        (d / "SKILL.md").write_bytes(b"# evil")
        for bad_name in ("../escape", "/absolute", "a/../../b"):
            with self.subTest(name=bad_name):
                u = Unit(kind=KIND_SKILL, name=bad_name,
                         source_path=d.resolve(), is_file=False)
                with self.assertRaises(ValueError):
                    sync.refresh_live_mirror([u], self.mirror, [], {})

    def test_removal_never_touches_a_sibling_of_the_mirror_root(self):
        self.mirror.mkdir()
        sibling = self.tmp / "mirror-sibling"
        sibling.mkdir()
        (sibling / "keep.txt").write_bytes(b"keep")
        (self.mirror / "stale").mkdir()
        sync.refresh_live_mirror([], self.mirror, [], {})
        self.assertTrue((sibling / "keep.txt").exists())


# --- place_index ------------------------------------------------------------

class TestPlaceIndex(_SyncTestCase):

    def test_copies_index_beside_objects(self):
        db = self.tmp / "local" / "index.db"
        db.parent.mkdir()
        db.write_bytes(b"fake-db-bytes")
        root = self.tmp / "blobs"
        (root / "objects").mkdir(parents=True)
        sync.place_index(db, root)
        self.assertEqual((root / "index.db").read_bytes(), b"fake-db-bytes")

    def test_creates_blob_root_when_missing(self):
        db = self.tmp / "index.db"
        db.write_bytes(b"x")
        root = self.tmp / "blobs" / "nested"
        sync.place_index(db, root)
        self.assertTrue((root / "index.db").exists())

    def test_overwrites_previous_copy_and_leaves_no_temp_files(self):
        db = self.tmp / "index.db"
        db.write_bytes(b"v1")
        root = self.tmp / "blobs"
        root.mkdir()
        sync.place_index(db, root)
        db.write_bytes(b"v2-longer")
        sync.place_index(db, root)
        self.assertEqual((root / "index.db").read_bytes(), b"v2-longer")
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["index.db"])

    def test_real_sqlite_database_survives_the_round_trip(self):
        db = self.tmp / "index.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE units (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO units (name) VALUES ('deep-research')")
        conn.commit()
        conn.close()
        root = self.tmp / "blobs"
        sync.place_index(db, root)
        placed = sqlite3.connect(str(root / "index.db"))
        rows = placed.execute("SELECT name FROM units").fetchall()
        placed.close()
        self.assertEqual(rows, [("deep-research",)])

    def test_wal_mode_database_is_placed_with_its_committed_rows(self):
        # index.py opens the DB in WAL mode, so recent commits may live in
        # index.db-wal rather than index.db. A naive byte copy would place a
        # database that is missing this run's rows.
        db = self.tmp / "index.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO runs (status) VALUES ('ok')")
        conn.commit()
        root = self.tmp / "blobs"
        sync.place_index(db, root)
        conn.close()
        placed = sqlite3.connect(str(root / "index.db"))
        rows = placed.execute("SELECT status FROM runs").fetchall()
        placed.close()
        self.assertEqual(rows, [("ok",)])

    def test_missing_source_raises(self):
        with self.assertRaises(FileNotFoundError):
            sync.place_index(self.tmp / "nope.db", self.tmp / "blobs")


# --- render_launchd_plist ---------------------------------------------------

PLUTIL = shutil.which("plutil")


class TestRenderLaunchdPlist(_SyncTestCase):

    def config(self, interval_days: int, hour: int = 3, minute: int = 0,
               label: str = "com.acs.claude-skills-backup") -> dict:
        return {"schedule": {"interval_days": interval_days, "hour": hour,
                             "minute": minute, "label": label}}

    def render(self, interval_days: int, **kw) -> tuple[str, dict]:
        xml = sync.render_launchd_plist(
            self.config(interval_days, **kw), Path("/opt/acs/backup.py")
        )
        self.lint(xml)
        return xml, plistlib.loads(xml.encode("utf-8"))

    def lint(self, xml: str) -> None:
        """A malformed plist fails silently at `launchctl load` time, so the
        real macOS parser gets a vote before this leaves the test suite."""
        self.assertTrue(xml.startswith("<?xml"))
        plistlib.loads(xml.encode("utf-8"))  # stdlib parser
        if not PLUTIL:
            self.skipTest("plutil unavailable")
        p = self.tmp / "candidate.plist"
        p.write_text(xml, encoding="utf-8")
        proc = subprocess.run([PLUTIL, "-lint", str(p)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         f"plutil -lint failed: {proc.stdout}{proc.stderr}")
        p.unlink()

    def test_daily_uses_start_calendar_interval_without_weekday(self):
        _, d = self.render(1, hour=3, minute=15)
        self.assertNotIn("StartInterval", d)
        cal = d["StartCalendarInterval"]
        self.assertEqual(cal, {"Hour": 3, "Minute": 15})
        self.assertNotIn("Weekday", cal)

    def test_weekly_uses_start_calendar_interval_with_weekday_zero(self):
        _, d = self.render(7, hour=3, minute=0)
        self.assertNotIn("StartInterval", d)
        self.assertEqual(d["StartCalendarInterval"],
                         {"Weekday": 0, "Hour": 3, "Minute": 0})

    def test_other_intervals_use_start_interval_seconds(self):
        _, d = self.render(3)
        self.assertNotIn("StartCalendarInterval", d)
        self.assertEqual(d["StartInterval"], 3 * 86400)

    def test_label_comes_from_config(self):
        _, d = self.render(7, label="com.example.other")
        self.assertEqual(d["Label"], "com.example.other")

    def test_program_arguments_invoke_the_script(self):
        _, d = self.render(7)
        args = d["ProgramArguments"]
        self.assertIn("/opt/acs/backup.py", args)
        self.assertEqual(args[-1], "run")
        self.assertTrue(args[0].endswith("python3"))

    def test_follows_tide_sync_conventions(self):
        xml, d = self.render(7)
        self.assertIn("<!DOCTYPE plist PUBLIC", xml)
        self.assertIn('<plist version="1.0">', xml)
        self.assertIs(d["RunAtLoad"], False)
        self.assertEqual(d["ProcessType"], "Background")
        self.assertEqual(d["StandardOutPath"], d["StandardErrorPath"])
        self.assertTrue(Path(d["StandardOutPath"]).is_absolute())
        env = d["EnvironmentVariables"]
        self.assertEqual(env["HOME"], str(Path.home()))
        self.assertIn("/usr/bin", env["PATH"].split(":"))

    def test_log_path_can_be_overridden(self):
        cfg = self.config(7)
        cfg["schedule"]["log_path"] = "~/tmp/backup.log"
        xml = sync.render_launchd_plist(cfg, Path("/opt/acs/backup.py"))
        self.lint(xml)
        d = plistlib.loads(xml.encode("utf-8"))
        self.assertEqual(d["StandardOutPath"],
                         str(Path("~/tmp/backup.log").expanduser()))

    def test_xml_special_characters_are_escaped(self):
        xml = sync.render_launchd_plist(
            self.config(3, label="a&b<c>"), Path("/opt/a & b/backup.py")
        )
        self.lint(xml)
        d = plistlib.loads(xml.encode("utf-8"))
        self.assertEqual(d["Label"], "a&b<c>")
        self.assertIn("/opt/a & b/backup.py", d["ProgramArguments"])
        self.assertIn("&amp;", xml)

    def test_zero_or_negative_interval_is_refused(self):
        for bad in (0, -1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sync.render_launchd_plist(self.config(bad),
                                          Path("/opt/acs/backup.py"))

    def test_out_of_range_clock_values_are_refused(self):
        for hour, minute in ((24, 0), (-1, 0), (3, 60), (3, -1)):
            with self.subTest(hour=hour, minute=minute):
                with self.assertRaises(ValueError):
                    sync.render_launchd_plist(
                        self.config(1, hour=hour, minute=minute),
                        Path("/opt/acs/backup.py"))

    def test_real_config_json_renders_and_lints(self):
        import json
        cfg_path = Path(__file__).resolve().parent.parent / "config.json"
        cfg = json.loads(cfg_path.read_text())
        xml = sync.render_launchd_plist(cfg, Path("/opt/acs/backup.py"))
        self.lint(xml)
        d = plistlib.loads(xml.encode("utf-8"))
        self.assertEqual(d["Label"], "com.acs.claude-skills-backup")
        self.assertEqual(d["StartCalendarInterval"],
                         {"Weekday": 0, "Hour": 3, "Minute": 0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
