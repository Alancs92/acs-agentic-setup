#!/usr/bin/env python3
"""Tests for discovery.py -- unit enumeration and file walking.

Every test builds a throwaway tree under tempfile.TemporaryDirectory and hands
discovery a config pointing at it, so nothing here touches the real ~/.claude.
The three behaviours worth guarding are the ones that silently lose data:
symlink resolution (a symlinked skill must back up as content), exclusion globs
(a .git dir must never enter a blob), and missing sources (must never abort).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import discovery
from backup_types import (
    KIND_AGENT,
    KIND_CLAUDE_MD,
    KIND_COMMAND,
    KIND_HOOK,
    KIND_PLUGIN_MANIFEST,
    KIND_SETTINGS,
    KIND_SKILL,
)


def write(path: Path, text: str = "x", mode: int | None = None) -> Path:
    """Create a file (and parents) with `text`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)
    return path


def base_config(root: Path, **overrides) -> dict:
    """A config with every source disabled, rooted at `root`."""
    config = {
        "sources": {
            "skills": {"enabled": False, "path": str(root / "skills")},
            "commands": {"enabled": False, "path": str(root / "commands")},
            "agents": {"enabled": False, "path": str(root / "agents")},
            "hooks": {"enabled": False, "path": str(root / "hooks")},
            "settings": {
                "enabled": False,
                "path": str(root / "accounts"),
                "accounts": ["personal", "work"],
            },
            "plugins": {
                "enabled": False,
                "path": str(root / "plugins"),
                "mode": "manifests",
            },
        },
        "exclude_globs": [
            "**/.git/**",
            "**/.DS_Store",
            "**/node_modules/**",
            "**/__pycache__/**",
            "**/*.pyc",
        ],
    }
    for key, value in overrides.items():
        config["sources"][key].update(value)
    return config


def names(units, kind=None):
    return [u.name for u in units if kind is None or u.kind == kind]


class TempTreeCase(unittest.TestCase):
    """Base class handing each test a private temp root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)


# --- directory sources ------------------------------------------------------
class TestDirectorySources(TempTreeCase):
    def test_each_top_level_entry_becomes_a_unit(self):
        write(self.root / "skills" / "alpha" / "SKILL.md")
        write(self.root / "skills" / "beta" / "SKILL.md")
        config = base_config(self.root, skills={"enabled": True})

        units = discovery.discover(config)

        self.assertEqual(names(units), ["alpha", "beta"])
        self.assertTrue(all(u.kind == KIND_SKILL for u in units))
        self.assertFalse(units[0].is_file)

    def test_file_entries_become_is_file_units(self):
        write(self.root / "hooks" / "notify.sh")
        write(self.root / "agents" / "doc-scribe.md")
        config = base_config(
            self.root, hooks={"enabled": True}, agents={"enabled": True}
        )

        units = discovery.discover(config)

        by_kind = {u.kind: u for u in units}
        self.assertEqual(by_kind[KIND_HOOK].name, "notify.sh")
        self.assertTrue(by_kind[KIND_HOOK].is_file)
        self.assertEqual(by_kind[KIND_AGENT].name, "doc-scribe.md")
        self.assertTrue(by_kind[KIND_AGENT].is_file)

    def test_kinds_map_to_the_right_source_keys(self):
        for key, kind in (
            ("skills", KIND_SKILL),
            ("commands", KIND_COMMAND),
            ("agents", KIND_AGENT),
            ("hooks", KIND_HOOK),
        ):
            write(self.root / key / "thing" / "f.md")
        config = base_config(
            self.root,
            skills={"enabled": True},
            commands={"enabled": True},
            agents={"enabled": True},
            hooks={"enabled": True},
        )

        units = discovery.discover(config)

        self.assertEqual(
            sorted(u.kind for u in units),
            sorted([KIND_SKILL, KIND_COMMAND, KIND_AGENT, KIND_HOOK]),
        )

    def test_hidden_top_level_entries_are_not_units(self):
        write(self.root / "skills" / "alpha" / "SKILL.md")
        write(self.root / "skills" / ".gitignore")
        write(self.root / "skills" / ".DS_Store")
        (self.root / "skills" / ".git").mkdir()
        config = base_config(self.root, skills={"enabled": True})

        self.assertEqual(names(discovery.discover(config)), ["alpha"])

    def test_disabled_source_yields_nothing(self):
        write(self.root / "skills" / "alpha" / "SKILL.md")

        self.assertEqual(discovery.discover(base_config(self.root)), [])

    def test_units_sorted_by_kind_then_name(self):
        for name in ("zeta", "alpha", "mid"):
            write(self.root / "skills" / name / "SKILL.md")
            write(self.root / "commands" / name / "CMD.md")
        config = base_config(
            self.root, skills={"enabled": True}, commands={"enabled": True}
        )

        units = discovery.discover(config)

        self.assertEqual([(u.kind, u.name) for u in units], sorted(
            [(u.kind, u.name) for u in units]))
        self.assertEqual(names(units, KIND_COMMAND), ["alpha", "mid", "zeta"])


# --- symlinks ---------------------------------------------------------------
class TestSymlinkResolution(TempTreeCase):
    def test_symlinked_unit_resolves_to_real_content(self):
        real = self.root / "bootstrap" / "security"
        write(real / "SKILL.md", "real content")
        (self.root / "skills").mkdir()
        (self.root / "skills" / "security").symlink_to(real)
        config = base_config(self.root, skills={"enabled": True})

        (unit,) = discovery.discover(config)

        self.assertEqual(unit.name, "security")
        self.assertFalse(unit.is_file)
        self.assertEqual(unit.source_path, real.resolve())
        self.assertNotIn("skills", unit.source_path.parts[-3:])

        files = discovery.iter_files(unit, config["exclude_globs"])
        self.assertEqual([p.name for p in files], ["SKILL.md"])
        self.assertEqual(files[0].read_text(), "real content")

    def test_symlink_to_file_is_a_file_unit(self):
        real = write(self.root / "elsewhere" / "notify.sh", "#!/bin/sh\n")
        (self.root / "hooks").mkdir()
        (self.root / "hooks" / "notify.sh").symlink_to(real)
        config = base_config(self.root, hooks={"enabled": True})

        (unit,) = discovery.discover(config)

        self.assertTrue(unit.is_file)
        self.assertEqual(unit.source_path, real.resolve())

    def test_symlink_nested_inside_a_unit_is_followed(self):
        target = write(self.root / "shared" / "ref.md", "shared")
        unit_dir = self.root / "skills" / "alpha"
        write(unit_dir / "SKILL.md")
        (unit_dir / "ref.md").symlink_to(target)
        config = base_config(self.root, skills={"enabled": True})

        (unit,) = discovery.discover(config)
        files = discovery.iter_files(unit, config["exclude_globs"])

        self.assertEqual(sorted(p.name for p in files), ["SKILL.md", "ref.md"])

    def test_broken_symlink_is_dropped_not_fatal(self):
        (self.root / "skills").mkdir()
        (self.root / "skills" / "ghost").symlink_to(self.root / "nope")
        write(self.root / "skills" / "alpha" / "SKILL.md")
        config = base_config(self.root, skills={"enabled": True})

        self.assertEqual(names(discovery.discover(config)), ["alpha"])

    def test_symlink_loop_inside_a_unit_terminates(self):
        unit_dir = self.root / "skills" / "alpha"
        write(unit_dir / "SKILL.md")
        (unit_dir / "loop").symlink_to(unit_dir)
        config = base_config(self.root, skills={"enabled": True})

        (unit,) = discovery.discover(config)
        files = discovery.iter_files(unit, config["exclude_globs"])

        self.assertIn("SKILL.md", [p.name for p in files])


# --- missing / unreadable sources ------------------------------------------
class TestMissingSources(TempTreeCase):
    def test_missing_source_is_skipped_never_raised(self):
        write(self.root / "commands" / "alpha" / "CMD.md")
        config = base_config(
            self.root,
            skills={"enabled": True, "path": str(self.root / "does-not-exist")},
            commands={"enabled": True},
        )

        units = discovery.discover(config)

        self.assertEqual(names(units), ["alpha"])

    def test_missing_source_is_recorded_as_a_problem(self):
        config = base_config(
            self.root,
            skills={"enabled": True, "path": str(self.root / "does-not-exist")},
        )

        units, problems = discovery.discover_with_problems(config)

        self.assertEqual(units, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("does-not-exist", problems[0])

    def test_source_path_that_is_a_file_is_skipped(self):
        write(self.root / "skills")
        config = base_config(self.root, skills={"enabled": True})

        units, problems = discovery.discover_with_problems(config)

        self.assertEqual(units, [])
        self.assertEqual(len(problems), 1)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root ignores directory permissions")
    def test_unreadable_source_is_skipped_never_raised(self):
        skills = self.root / "skills"
        write(skills / "alpha" / "SKILL.md")
        skills.chmod(0o000)
        self.addCleanup(skills.chmod, 0o755)
        config = base_config(self.root, skills={"enabled": True})

        units, problems = discovery.discover_with_problems(config)

        self.assertEqual(units, [])
        self.assertEqual(len(problems), 1)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root ignores directory permissions")
    def test_unreadable_subdir_inside_a_unit_does_not_raise(self):
        unit_dir = self.root / "skills" / "alpha"
        write(unit_dir / "SKILL.md")
        locked = unit_dir / "locked"
        write(locked / "hidden.md")
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o755)
        config = base_config(self.root, skills={"enabled": True})

        (unit,) = discovery.discover(config)
        files = discovery.iter_files(unit, config["exclude_globs"])

        self.assertEqual([p.name for p in files], ["SKILL.md"])


# --- exclusion globs --------------------------------------------------------
class TestExcludeGlobs(TempTreeCase):
    def _alpha(self):
        write(self.root / "skills" / "alpha" / "SKILL.md")
        config = base_config(self.root, skills={"enabled": True})
        (unit,) = discovery.discover(config)
        return unit, config["exclude_globs"]

    def test_dot_git_directory_is_excluded(self):
        write(self.root / "skills" / "alpha" / ".git" / "config", "gitconfig")
        write(self.root / "skills" / "alpha" / ".git" / "objects" / "ab" / "cd")
        unit, globs = self._alpha()

        rels = {p.name for p in discovery.iter_files(unit, globs)}

        self.assertEqual(rels, {"SKILL.md"})

    def test_nested_dot_git_is_excluded(self):
        write(self.root / "skills" / "alpha" / "deep" / "nest" / ".git" / "HEAD")
        unit, globs = self._alpha()

        self.assertEqual(
            [p.name for p in discovery.iter_files(unit, globs)], ["SKILL.md"]
        )

    def test_ds_store_excluded_at_every_depth(self):
        write(self.root / "skills" / "alpha" / ".DS_Store")
        write(self.root / "skills" / "alpha" / "sub" / ".DS_Store")
        unit, globs = self._alpha()

        self.assertEqual(
            [p.name for p in discovery.iter_files(unit, globs)], ["SKILL.md"]
        )

    def test_pycache_and_pyc_excluded(self):
        write(self.root / "skills" / "alpha" / "__pycache__" / "m.cpython.pyc")
        write(self.root / "skills" / "alpha" / "scripts" / "m.pyc")
        write(self.root / "skills" / "alpha" / "scripts" / "m.py")
        unit, globs = self._alpha()

        self.assertEqual(
            sorted(p.name for p in discovery.iter_files(unit, globs)),
            ["SKILL.md", "m.py"],
        )

    def test_node_modules_excluded(self):
        write(self.root / "skills" / "alpha" / "node_modules" / "x" / "index.js")
        unit, globs = self._alpha()

        self.assertEqual(
            [p.name for p in discovery.iter_files(unit, globs)], ["SKILL.md"]
        )

    def test_empty_globs_keep_everything(self):
        write(self.root / "skills" / "alpha" / ".git" / "config")
        unit, _ = self._alpha()

        self.assertEqual(len(discovery.iter_files(unit, [])), 2)

    def test_bare_name_pattern_matches_at_any_depth(self):
        write(self.root / "skills" / "alpha" / "sub" / "notes.txt")
        unit, _ = self._alpha()

        self.assertEqual(
            [p.name for p in discovery.iter_files(unit, ["*.txt"])], ["SKILL.md"]
        )


# --- iter_files -------------------------------------------------------------
class TestIterFiles(TempTreeCase):
    def test_returns_sorted_paths(self):
        unit_dir = self.root / "skills" / "alpha"
        for name in ("zebra.md", "apple.md", "SKILL.md"):
            write(unit_dir / name)
        write(unit_dir / "sub" / "aaa.md")
        config = base_config(self.root, skills={"enabled": True})

        (unit,) = discovery.discover(config)
        files = discovery.iter_files(unit, config["exclude_globs"])

        self.assertEqual(files, sorted(files))

    def test_single_element_list_for_file_units(self):
        write(self.root / "hooks" / "notify.sh")
        config = base_config(self.root, hooks={"enabled": True})

        (unit,) = discovery.discover(config)

        self.assertEqual(
            discovery.iter_files(unit, config["exclude_globs"]),
            [unit.source_path],
        )

    def test_vanished_unit_returns_empty_list(self):
        write(self.root / "skills" / "alpha" / "SKILL.md")
        config = base_config(self.root, skills={"enabled": True})
        (unit,) = discovery.discover(config)

        for child in (unit.source_path).rglob("*"):
            child.unlink()
        unit.source_path.rmdir()

        self.assertEqual(discovery.iter_files(unit, []), [])

    def test_directories_are_not_returned(self):
        write(self.root / "skills" / "alpha" / "sub" / "f.md")
        config = base_config(self.root, skills={"enabled": True})

        (unit,) = discovery.discover(config)
        files = discovery.iter_files(unit, config["exclude_globs"])

        self.assertTrue(all(p.is_file() for p in files))


# --- plugins ----------------------------------------------------------------
class TestPlugins(TempTreeCase):
    def _plugin_tree(self):
        plugins = self.root / "plugins"
        write(plugins / "installed_plugins.json", "{}")
        write(plugins / "known_marketplaces.json", "{}")
        write(plugins / "blocklist.json", "{}")
        write(plugins / "cache" / "huge" / "payload.json", "{}")
        write(plugins / "marketplaces" / "mkt" / "meta.json", "{}")
        write(plugins / "data" / "notes.md")
        return plugins

    def test_manifests_mode_emits_one_unit_per_top_level_json(self):
        self._plugin_tree()
        config = base_config(self.root, plugins={"enabled": True})

        units = discovery.discover(config)

        self.assertEqual(
            names(units),
            ["blocklist", "installed_plugins", "known_marketplaces"],
        )
        self.assertTrue(all(u.kind == KIND_PLUGIN_MANIFEST for u in units))
        self.assertTrue(all(u.is_file for u in units))

    def test_manifests_mode_never_descends_into_cache(self):
        self._plugin_tree()
        config = base_config(self.root, plugins={"enabled": True})

        units = discovery.discover(config)
        every_file = [f for u in units
                      for f in discovery.iter_files(u, config["exclude_globs"])]

        self.assertFalse(any("cache" in p.parts for p in every_file))
        self.assertFalse(any("marketplaces" in p.parts for p in every_file))

    def test_manifests_mode_default_when_mode_absent(self):
        self._plugin_tree()
        config = base_config(self.root, plugins={"enabled": True})
        del config["sources"]["plugins"]["mode"]

        self.assertEqual(len(discovery.discover(config)), 3)

    def test_full_mode_emits_one_unit_for_the_tree(self):
        plugins = self._plugin_tree()
        config = base_config(self.root, plugins={"enabled": True, "mode": "full"})

        (unit,) = discovery.discover(config)

        self.assertEqual(unit.kind, KIND_PLUGIN_MANIFEST)
        self.assertEqual(unit.name, "plugins")
        self.assertFalse(unit.is_file)
        self.assertEqual(unit.source_path, plugins.resolve())

        files = discovery.iter_files(unit, config["exclude_globs"])
        self.assertTrue(any("cache" in p.parts for p in files))

    def test_missing_plugins_dir_is_skipped(self):
        config = base_config(self.root, plugins={"enabled": True})

        self.assertEqual(discovery.discover(config), [])


# --- settings ---------------------------------------------------------------
class TestSettings(TempTreeCase):
    def test_emits_settings_local_and_claude_md_per_account(self):
        accounts = self.root / "accounts"
        for account in ("personal", "work"):
            write(accounts / account / "settings.json", "{}")
            write(accounts / account / "settings.local.json", "{}")
            write(accounts / account / "CLAUDE.md", "# hi")
        config = base_config(self.root, settings={"enabled": True})

        units = discovery.discover(config)

        self.assertEqual(
            sorted((u.kind, u.name) for u in units),
            [
                (KIND_CLAUDE_MD, "personal/CLAUDE.md"),
                (KIND_CLAUDE_MD, "work/CLAUDE.md"),
                (KIND_SETTINGS, "personal/settings.json"),
                (KIND_SETTINGS, "personal/settings.local.json"),
                (KIND_SETTINGS, "work/settings.json"),
                (KIND_SETTINGS, "work/settings.local.json"),
            ],
        )
        self.assertTrue(all(u.is_file for u in units))

    def test_absent_files_are_omitted(self):
        write(self.root / "accounts" / "personal" / "settings.json", "{}")
        config = base_config(self.root, settings={"enabled": True})

        units = discovery.discover(config)

        self.assertEqual(names(units), ["personal/settings.json"])

    def test_missing_account_dir_is_skipped_not_fatal(self):
        write(self.root / "accounts" / "personal" / "CLAUDE.md", "# hi")
        config = base_config(self.root, settings={"enabled": True})

        units, problems = discovery.discover_with_problems(config)

        self.assertEqual(names(units), ["personal/CLAUDE.md"])
        self.assertEqual(len(problems), 1)
        self.assertIn("work", problems[0])

    def test_settings_source_path_points_at_the_real_file(self):
        target = write(self.root / "accounts" / "personal" / "settings.json", "{}")
        config = base_config(self.root, settings={"enabled": True})

        (unit,) = discovery.discover(config)

        self.assertEqual(unit.source_path, target.resolve())
        self.assertEqual(discovery.iter_files(unit, []), [target.resolve()])


if __name__ == "__main__":
    unittest.main()
