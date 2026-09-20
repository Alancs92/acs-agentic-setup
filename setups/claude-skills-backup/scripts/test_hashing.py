#!/usr/bin/env python3
"""Tests for hashing.py -- the canonical content hash.

The load-bearing property under test is NEGATIVE: the hash must NOT depend on
mtime, ctime, uid, gid, absolute path, or directory iteration order. If any of
those leak in, a `touch` or a OneDrive resync fabricates a snapshot on every
cycle and the entire change-detection design collapses.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import hashing
from backup_types import FileEntry, Unit


class _TreeMixin:
    """Helpers to build throwaway unit trees."""

    def _tmpdir(self) -> Path:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _unit(self, root: Path, name: str = "demo") -> Unit:
        return Unit(kind="skill", name=name, source_path=root, is_file=False)

    def _write(self, root: Path, relpath: str, content: bytes, executable: bool = False) -> Path:
        p = root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        mode = 0o755 if executable else 0o644
        os.chmod(p, mode)
        return p

    def _standard_tree(self, root: Path) -> list[Path]:
        a = self._write(root, "SKILL.md", b"# demo skill\n")
        b = self._write(root, "scripts/run.sh", b"#!/bin/sh\necho hi\n", executable=True)
        c = self._write(root, "references/notes.md", b"notes\n")
        return [a, b, c]

    def _hash_of(self, root: Path, files: list[Path], name: str = "demo") -> str:
        unit = self._unit(root, name)
        return hashing.content_hash(hashing.file_entries(unit, files))


class TestFileEntries(_TreeMixin, unittest.TestCase):
    def test_relpaths_are_posix_and_unit_relative(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        entries = hashing.file_entries(self._unit(root), files)
        self.assertEqual(
            [e.relpath for e in entries],
            ["SKILL.md", "references/notes.md", "scripts/run.sh"],
        )
        for e in entries:
            self.assertNotIn("\\", e.relpath)
            self.assertFalse(Path(e.relpath).is_absolute())

    def test_entries_sorted_by_relpath_regardless_of_input_order(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        forward = hashing.file_entries(self._unit(root), files)
        backward = hashing.file_entries(self._unit(root), list(reversed(files)))
        self.assertEqual(forward, backward)

    def test_sha256_is_lowercase_hex_of_file_bytes(self):
        root = self._tmpdir()
        f = self._write(root, "a.txt", b"hello world")
        entries = hashing.file_entries(self._unit(root), [f])
        self.assertEqual(entries[0].sha256, hashlib.sha256(b"hello world").hexdigest())
        self.assertEqual(entries[0].sha256, entries[0].sha256.lower())

    def test_executable_bit_reflected(self):
        root = self._tmpdir()
        plain = self._write(root, "plain.md", b"x")
        exe = self._write(root, "run.sh", b"x", executable=True)
        entries = {e.relpath: e for e in hashing.file_entries(self._unit(root), [plain, exe])}
        self.assertFalse(entries["plain.md"].executable)
        self.assertTrue(entries["run.sh"].executable)

    def test_group_or_other_execute_bit_counts_as_executable(self):
        root = self._tmpdir()
        f = self._write(root, "g.sh", b"x")
        os.chmod(f, 0o644 | stat.S_IXGRP)
        entries = hashing.file_entries(self._unit(root), [f])
        self.assertTrue(entries[0].executable)

    def test_single_file_unit_relpath_is_the_filename(self):
        root = self._tmpdir()
        f = self._write(root, "settings.json", b"{}\n")
        unit = Unit(kind="settings", name="personal/settings.json",
                    source_path=f, is_file=True)
        entries = hashing.file_entries(unit, [f])
        self.assertEqual([e.relpath for e in entries], ["settings.json"])

    def test_symlinked_file_is_hashed_by_content(self):
        root = self._tmpdir()
        real = self._write(root, "real.md", b"payload\n")
        link = root / "link.md"
        link.symlink_to(real)
        entries = {e.relpath: e for e in hashing.file_entries(self._unit(root), [real, link])}
        self.assertEqual(entries["link.md"].sha256, entries["real.md"].sha256)

    def test_empty_file_list_yields_empty_manifest(self):
        root = self._tmpdir()
        self.assertEqual(hashing.file_entries(self._unit(root), []), [])

    def test_large_file_hashed_correctly_in_chunks(self):
        root = self._tmpdir()
        payload = (b"claude-skills-backup " * 4096) * 32  # ~2.7 MB
        f = self._write(root, "big.bin", payload)
        entries = hashing.file_entries(self._unit(root), [f])
        self.assertEqual(entries[0].sha256, hashlib.sha256(payload).hexdigest())

    def test_reads_in_bounded_chunks_not_whole_file(self):
        """Structural guard: some skills are megabytes; do not slurp."""
        self.assertTrue(hasattr(hashing, "CHUNK_SIZE"))
        self.assertGreater(hashing.CHUNK_SIZE, 0)
        self.assertLessEqual(hashing.CHUNK_SIZE, 1 << 20)

        seen = []
        real_open = open

        def spy_open(*args, **kwargs):
            fh = real_open(*args, **kwargs)
            real_read = fh.read

            def read(n=-1):
                seen.append(n)
                return real_read(n)

            fh.read = read  # type: ignore[method-assign]
            return fh

        root = self._tmpdir()
        f = self._write(root, "big.bin", b"z" * (hashing.CHUNK_SIZE * 3))
        with unittest.mock.patch("builtins.open", spy_open):
            hashing.file_entries(self._unit(root), [f])
        self.assertTrue(seen, "file was never read through open()")
        self.assertTrue(all(n == hashing.CHUNK_SIZE for n in seen),
                        f"expected only bounded reads, got {seen}")


class TestContentHash(_TreeMixin, unittest.TestCase):
    def test_canonical_format_matches_contract_exactly(self):
        entries = [
            FileEntry("SKILL.md", False, "a" * 64),
            FileEntry("run.sh", True, "b" * 64),
        ]
        expected_input = (
            f"SKILL.md\n0\n{'a' * 64}\n"
            f"run.sh\n1\n{'b' * 64}\n"
        ).encode("utf-8")
        self.assertEqual(
            hashing.content_hash(entries),
            hashlib.sha256(expected_input).hexdigest(),
        )

    def test_hash_is_lowercase_hex_64(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        h = self._hash_of(root, files)
        self.assertEqual(len(h), 64)
        self.assertEqual(h, h.lower())
        int(h, 16)  # raises if not hex

    def test_empty_manifest_hashes_empty_string(self):
        self.assertEqual(hashing.content_hash([]), hashlib.sha256(b"").hexdigest())

    def test_content_hash_sorts_unsorted_input(self):
        a = FileEntry("a.md", False, "1" * 64)
        b = FileEntry("b.md", False, "2" * 64)
        self.assertEqual(hashing.content_hash([a, b]), hashing.content_hash([b, a]))


class TestRedactedHashing(_TreeMixin, unittest.TestCase):
    """The hash must describe the bytes that are ACTUALLY STORED.

    store.write_blob redacts each file before it enters the archive. If the
    manifest hashed the raw on-disk bytes instead, the blob's real content would
    hash differently from the content_hash recorded in the index, and
    verify_blob -- which extracts and recomputes -- would report the unit corrupt
    forever. Latent until the first token appears in settings.json, then
    permanent.
    """

    SECRETS = {
        "key_patterns": ["token", "secret", "password", "apikey", "credential", "auth"],
        "value_patterns": ["^sk-ant-", "^ghp_", "^Bearer\\s+"],
    }
    SECRET = "sk-ant-api03-" + "A" * 40

    def _settings_unit(self, root: Path, payload: dict):
        import json
        f = self._write(root, "settings.json",
                        json.dumps(payload, indent=2).encode("utf-8"))
        unit = Unit(kind="settings", name="personal/settings.json",
                    source_path=f, is_file=True)
        return unit, f

    def test_secret_bearing_json_hashes_differently_with_secrets_config(self):
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"env": {"ANTHROPIC_AUTH_TOKEN": self.SECRET}})
        raw = hashing.content_hash(hashing.file_entries(unit, [f]))
        red = hashing.content_hash(
            hashing.file_entries(unit, [f], secrets_config=self.SECRETS))
        self.assertNotEqual(raw, red)

    def test_manifest_sha_matches_the_redacted_bytes(self):
        import redact
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"token": self.SECRET})
        entries = hashing.file_entries(unit, [f], secrets_config=self.SECRETS)
        stored, count = redact.redact_bytes(f.read_bytes(), "settings.json", self.SECRETS)
        self.assertEqual(count, 1)
        self.assertEqual(entries[0].sha256, hashlib.sha256(stored).hexdigest())

    def test_round_trip_store_then_rehash_matches(self):
        """The exact invariant that was violated: hash(file_entries with
        secrets_config) == hash of the tree that write_blob actually archives."""
        import store
        root = self._tmpdir()
        unit, f = self._settings_unit(
            root, {"env": {"ANTHROPIC_AUTH_TOKEN": self.SECRET}, "model": "opus"})

        expected = hashing.content_hash(
            hashing.file_entries(unit, [f], secrets_config=self.SECRETS))

        blob_root = self._tmpdir()
        key, _size = store.write_blob(blob_root, expected, unit, [f], self.SECRETS)

        dest = self._tmpdir()
        store.read_blob(blob_root, key, dest)
        extracted = sorted(p for p in dest.rglob("*") if p.is_file())
        extracted_unit = Unit("settings", unit.name, dest, False)
        actual = hashing.content_hash(hashing.file_entries(extracted_unit, extracted))

        self.assertEqual(actual, expected)
        self.assertTrue(store.verify_blob(blob_root, key, expected),
                        "verify_blob must not report a freshly written blob corrupt")

    def test_extracted_blob_carries_no_secret(self):
        import store
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"token": self.SECRET})
        h = hashing.content_hash(
            hashing.file_entries(unit, [f], secrets_config=self.SECRETS))
        blob_root = self._tmpdir()
        key, _ = store.write_blob(blob_root, h, unit, [f], self.SECRETS)
        dest = self._tmpdir()
        store.read_blob(blob_root, key, dest)
        text = (dest / "settings.json").read_text()
        self.assertNotIn(self.SECRET, text)
        self.assertIn("«REDACTED:", text)

    def test_changing_the_secret_value_still_changes_the_hash(self):
        """Change detection must survive redaction -- the marker embeds a
        sha256 prefix of the original value, so a rotated token is visible."""
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"token": self.SECRET})
        before = hashing.content_hash(
            hashing.file_entries(unit, [f], secrets_config=self.SECRETS))
        import json
        f.write_bytes(json.dumps({"token": "sk-ant-api03-" + "B" * 40},
                                 indent=2).encode("utf-8"))
        after = hashing.content_hash(
            hashing.file_entries(unit, [f], secrets_config=self.SECRETS))
        self.assertNotEqual(after, before)

    def test_rewriting_the_same_secret_does_not_change_the_hash(self):
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"token": self.SECRET})
        before = hashing.content_hash(
            hashing.file_entries(unit, [f], secrets_config=self.SECRETS))
        os.utime(f, (1, 1))
        after = hashing.content_hash(
            hashing.file_entries(unit, [f], secrets_config=self.SECRETS))
        self.assertEqual(after, before)

    def test_secret_free_json_hashes_identically_with_and_without_config(self):
        """The 99% case: no secrets must mean no churn, or every plugin
        manifest re-snapshots the first time this ships."""
        import json
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"model": "opus", "theme": "dark"})
        self.assertEqual(
            hashing.content_hash(hashing.file_entries(unit, [f],
                                                      secrets_config=self.SECRETS)),
            hashing.content_hash(hashing.file_entries(unit, [f])),
        )

    def test_non_json_files_are_unaffected_by_secrets_config(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        self._write(root, "notes.md", ("key=" + self.SECRET + "\n").encode("utf-8"))
        files = files + [root / "notes.md"]
        unit = self._unit(root)
        self.assertEqual(
            hashing.content_hash(hashing.file_entries(unit, files,
                                                      secrets_config=self.SECRETS)),
            hashing.content_hash(hashing.file_entries(unit, files)),
        )

    def test_empty_secrets_config_is_not_the_same_as_none(self):
        """An explicit empty dict still takes the redaction path; it simply has
        no patterns, so the bytes come back unchanged."""
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"token": self.SECRET})
        self.assertEqual(
            hashing.content_hash(hashing.file_entries(unit, [f], secrets_config={})),
            hashing.content_hash(hashing.file_entries(unit, [f])),
        )

    def test_default_is_none_and_leaves_behaviour_unchanged(self):
        import inspect
        sig = inspect.signature(hashing.file_entries)
        self.assertEqual(list(sig.parameters), ["unit", "files", "secrets_config"])
        self.assertIsNone(sig.parameters["secrets_config"].default)

    def test_missing_redact_module_raises_rather_than_storing_raw(self):
        """Fail loud. Silently hashing unredacted bytes would ship a token to
        corporate OneDrive."""
        import builtins
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"token": self.SECRET})
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "redact":
                raise ImportError("simulated missing redact module")
            return real_import(name, *args, **kwargs)

        with unittest.mock.patch.dict("sys.modules"):
            import sys
            sys.modules.pop("redact", None)
            with unittest.mock.patch("builtins.__import__", blocked):
                with self.assertRaises(ImportError):
                    hashing.file_entries(unit, [f], secrets_config=self.SECRETS)

    def test_filename_passed_to_redact_is_the_member_name(self):
        """Must equal store._member_name, or JSON detection diverges between
        what is hashed and what is stored."""
        import store
        root = self._tmpdir()
        unit, f = self._settings_unit(root, {"token": self.SECRET})
        seen = []
        import redact
        real = redact.redact_bytes

        def spy(data, filename, cfg):
            seen.append(filename)
            return real(data, filename, cfg)

        with unittest.mock.patch.object(redact, "redact_bytes", spy):
            hashing.file_entries(unit, [f], secrets_config=self.SECRETS)
        self.assertEqual(seen, ["settings.json"])
        self.assertEqual(seen[0], store._member_name(unit, f))

    def test_nested_json_inside_a_skill_tree_uses_relative_name(self):
        import store
        root = self._tmpdir()
        cfgfile = self._write(root, "config/mcp.json",
                              b'{"token": "ghp_' + b"C" * 36 + b'"}')
        unit = self._unit(root)
        entries = hashing.file_entries(unit, [cfgfile], secrets_config=self.SECRETS)
        self.assertEqual(entries[0].relpath, "config/mcp.json")
        self.assertEqual(entries[0].relpath, store._member_name(unit, cfgfile))
        plain = hashing.file_entries(unit, [cfgfile])
        self.assertNotEqual(entries[0].sha256, plain[0].sha256)


class TestHashInvariants(_TreeMixin, unittest.TestCase):
    """The properties the whole design rests on."""

    def test_touching_a_file_does_not_change_the_hash(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        before = self._hash_of(root, files)
        for f in files:
            os.utime(f, (1, 1))
        os.utime(root, (1, 1))
        self.assertEqual(self._hash_of(root, files), before)
        for f in files:
            os.utime(f, (2_000_000_000, 2_000_000_000))
        self.assertEqual(self._hash_of(root, files), before)

    def test_directory_iteration_order_does_not_change_the_hash(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        orders = [
            files,
            list(reversed(files)),
            [files[1], files[2], files[0]],
            [files[2], files[0], files[1]],
        ]
        hashes = {self._hash_of(root, o) for o in orders}
        self.assertEqual(len(hashes), 1)

    def test_absolute_path_does_not_leak_into_the_hash(self):
        root_a = self._tmpdir()
        root_b = self._tmpdir()
        files_a = self._standard_tree(root_a)
        files_b = self._standard_tree(root_b)
        self.assertNotEqual(root_a, root_b)
        self.assertEqual(
            self._hash_of(root_a, files_a, name="one"),
            self._hash_of(root_b, files_b, name="two"),
        )

    def test_unit_name_and_kind_do_not_change_the_hash(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        h1 = hashing.content_hash(
            hashing.file_entries(Unit("skill", "alpha", root, False), files))
        h2 = hashing.content_hash(
            hashing.file_entries(Unit("command", "beta", root, False), files))
        self.assertEqual(h1, h2)

    def test_renaming_a_file_changes_the_hash(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        before = self._hash_of(root, files)
        renamed = root / "references" / "renamed.md"
        files[2].rename(renamed)
        files[2] = renamed
        self.assertNotEqual(self._hash_of(root, files), before)

    def test_moving_a_file_between_dirs_changes_the_hash(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        before = self._hash_of(root, files)
        moved = root / "notes.md"
        files[2].rename(moved)
        files[2] = moved
        self.assertNotEqual(self._hash_of(root, files), before)

    def test_flipping_the_executable_bit_changes_the_hash(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        before = self._hash_of(root, files)
        os.chmod(files[1], 0o644)
        after = self._hash_of(root, files)
        self.assertNotEqual(after, before)
        os.chmod(files[1], 0o755)
        self.assertEqual(self._hash_of(root, files), before)

    def test_changing_content_changes_the_hash(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        before = self._hash_of(root, files)
        files[0].write_bytes(b"# demo skill (edited)\n")
        self.assertNotEqual(self._hash_of(root, files), before)

    def test_adding_and_removing_a_file_changes_the_hash(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        before = self._hash_of(root, files)
        extra = self._write(root, "EXTRA.md", b"extra\n")
        with_extra = self._hash_of(root, files + [extra])
        self.assertNotEqual(with_extra, before)
        self.assertEqual(self._hash_of(root, files), before)

    def test_swapping_content_between_two_files_changes_the_hash(self):
        """Guards against hashing a set of digests without their paths."""
        root = self._tmpdir()
        a = self._write(root, "a.md", b"AAA")
        b = self._write(root, "b.md", b"BBB")
        before = self._hash_of(root, [a, b])
        a.write_bytes(b"BBB")
        b.write_bytes(b"AAA")
        self.assertNotEqual(self._hash_of(root, [a, b]), before)

    def test_repeated_hashing_is_stable(self):
        root = self._tmpdir()
        files = self._standard_tree(root)
        hashes = {self._hash_of(root, files) for _ in range(5)}
        self.assertEqual(len(hashes), 1)


if __name__ == "__main__":
    unittest.main()
