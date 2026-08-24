#!/usr/bin/env python3
"""Tests for store.py -- the content-addressed blob store.

Written before the implementation. The cases that matter are the ones where a
bug is either a security hole or a silent data loss:

  * redaction ordering  -- a secret must never reach the archive; these blobs
                           are written into corporate OneDrive
  * path traversal      -- a crafted archive must not be able to write outside
                           the destination directory
  * reproducibility     -- identical content must produce byte-identical
                           archives, otherwise every run rewrites every blob
  * dedup               -- an existing blob is never rewritten
  * atomicity           -- an interrupted write must leave nothing at the key
  * delete safety       -- a missing blob is a no-op, never an exception

`redact` and `hashing` belong to other modules. This file substitutes
deterministic fakes for them so a change in either cannot turn a store bug
green (or red), but it does so with `mock.patch.object(store, ...)` scoped to
each test and reverted in cleanup.

Deliberately NOT by assigning sys.modules: that only works when this file is
imported first, so the suite would pass alone and fail under `unittest
discover`, and -- far worse -- the fake would still be installed when
test_integration.py later asserts that a REAL secret never reaches a blob,
quietly turning that security test into an assertion about a stub.

The fake `hashing.content_hash` implements the canonical algorithm exactly as
specified in contracts.md; `test_fake_matches_the_real_hashing_module` pins it
against the real implementation so the two cannot silently drift.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# --- fakes for the modules owned by other agents ----------------------------

class _FakeRedact:
    """Minimal stand-in for redact.py.

    Replaces one known secret literal, and can be told to blow up on a given
    filename so the atomicity path can be exercised.
    """

    SECRET = b"sk-ant-SUPERSECRETVALUE"
    PLACEHOLDER = b"<<REDACTED>>"

    def __init__(self) -> None:
        self.fail_on = None      # Optional[str]
        self.seen = []           # List[str]

    def redact_bytes(self, data: bytes, filename: str, secrets_config: dict):
        self.seen.append(filename)
        if self.fail_on is not None and filename.endswith(self.fail_on):
            raise RuntimeError("simulated redaction failure")
        if self.SECRET in data:
            return data.replace(self.SECRET, self.PLACEHOLDER), 1
        return data, 0


class _FakeHashing:
    """Canonical content hash, per contracts.md hashing.py."""

    @staticmethod
    def content_hash(entries) -> str:
        h = hashlib.sha256()
        for e in sorted(entries, key=lambda x: x.relpath):
            h.update(f"{e.relpath}\n{int(e.executable)}\n{e.sha256}\n".encode("utf-8"))
        return h.hexdigest()


FAKE_REDACT = _FakeRedact()

import hashing  # noqa: E402  (the real one, for the drift check below)
import store  # noqa: E402
from backup_types import FileEntry, Unit  # noqa: E402


# --- helpers ----------------------------------------------------------------

def canonical_hash_of_tree(root: Path) -> str:
    entries = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            entries.append(
                FileEntry(
                    relpath=p.relative_to(root).as_posix(),
                    executable=bool(p.stat().st_mode & 0o111),
                    sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                )
            )
    return _FakeHashing.content_hash(entries)


def make_unit(tmp: Path, name: str = "deep-research") -> tuple[Unit, list[Path]]:
    src = tmp / "src" / name
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_bytes(b"# a skill\n")
    (src / "scripts" / "run.sh").write_bytes(b"#!/bin/sh\necho hi\n")
    os.chmod(src / "scripts" / "run.sh", 0o755)
    unit = Unit(kind="skill", name=name, source_path=src, is_file=False)
    files = [src / "SKILL.md", src / "scripts" / "run.sh"]
    return unit, files


def craft_archive(path: Path, member_names: list[str]) -> None:
    """Build a tar.xz containing the given member names verbatim."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:xz") as tar:
        for name in member_names:
            data = b"pwned\n"
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            ti.mtime = 0
            tar.addfile(ti, io.BytesIO(data))


HASH_A = "a" * 64
HASH_B = "3f9a2c" + "0" * 58


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        FAKE_REDACT.fail_on = None
        FAKE_REDACT.seen = []

        # Swap store's collaborators for this test only. addCleanup guarantees
        # the real modules are back before any other test file runs, so the
        # end-to-end security assertions in test_integration.py keep exercising
        # the genuine redaction path.
        for target, fake in (("redact", FAKE_REDACT), ("hashing", _FakeHashing)):
            patcher = mock.patch.object(store, target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "blobstore"
        self.root.mkdir()
        self.addCleanup(self._tmp.cleanup)


# --- blob_key ---------------------------------------------------------------

class TestBlobKey(StoreTestCase):
    def test_sharding(self):
        self.assertEqual(
            store.blob_key(HASH_B),
            f"objects/3f/9a/{HASH_B}.tar.xz",
        )

    def test_always_posix_separators(self):
        key = store.blob_key(HASH_A)
        self.assertNotIn("\\", key)
        self.assertEqual(key.count("/"), 3)
        self.assertTrue(key.startswith("objects/"))
        self.assertTrue(key.endswith(".tar.xz"))

    def test_shards_are_first_two_byte_pairs(self):
        key = store.blob_key(HASH_B)
        parts = key.split("/")
        self.assertEqual(parts[1], HASH_B[0:2])
        self.assertEqual(parts[2], HASH_B[2:4])

    def test_rejects_implausible_hash(self):
        with self.assertRaises(ValueError):
            store.blob_key("ab")


class TestFakeFidelity(unittest.TestCase):
    """The fakes stand in for real modules, so pin them against the real thing.

    Without this, hashing.py could change its canonical algorithm and every
    verify_blob test here would keep passing against a stale fake.
    """

    def test_fake_matches_the_real_hashing_module(self):
        entries = [
            FileEntry(relpath="SKILL.md", executable=False, sha256="a" * 64),
            FileEntry(relpath="scripts/run.sh", executable=True, sha256="b" * 64),
            FileEntry(relpath="nested/deep/x.txt", executable=False, sha256="c" * 64),
        ]
        self.assertEqual(
            _FakeHashing.content_hash(entries),
            hashing.content_hash(entries),
        )

    def test_patches_do_not_leak_out_of_a_store_test(self):
        self.assertIs(store.redact, sys.modules["redact"])
        self.assertIs(store.hashing, sys.modules["hashing"])


# --- write_blob -------------------------------------------------------------

class TestWriteBlob(StoreTestCase):
    def test_writes_at_blob_key_and_reports_size(self):
        unit, files = make_unit(self.tmp)
        key, size = store.write_blob(self.root, HASH_A, unit, files, {})
        self.assertEqual(key, store.blob_key(HASH_A))
        target = self.root / key
        self.assertTrue(target.is_file())
        self.assertEqual(size, target.stat().st_size)
        self.assertGreater(size, 0)

    def test_member_names_are_unit_relative_posix(self):
        unit, files = make_unit(self.tmp)
        key, _ = store.write_blob(self.root, HASH_A, unit, files, {})
        with tarfile.open(self.root / key, "r:xz") as tar:
            names = sorted(tar.getnames())
        self.assertEqual(names, ["SKILL.md", "scripts/run.sh"])

    def test_is_file_unit_uses_bare_filename(self):
        f = self.tmp / "src" / "settings.json"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"{}\n")
        unit = Unit(kind="settings", name="personal/settings.json",
                    source_path=f, is_file=True)
        key, _ = store.write_blob(self.root, HASH_A, unit, [f], {})
        with tarfile.open(self.root / key, "r:xz") as tar:
            self.assertEqual(tar.getnames(), ["settings.json"])

    def test_dedup_does_not_rewrite_existing_blob(self):
        unit, files = make_unit(self.tmp)
        key, _ = store.write_blob(self.root, HASH_A, unit, files, {})
        target = self.root / key

        # Overwrite the stored blob with a sentinel. A second write_blob for the
        # same hash must return without touching it -- that is the dedup path.
        sentinel = b"SENTINEL-DO-NOT-REWRITE"
        target.write_bytes(sentinel)
        FAKE_REDACT.seen = []

        key2, size2 = store.write_blob(self.root, HASH_A, unit, files, {})
        self.assertEqual(key2, key)
        self.assertEqual(target.read_bytes(), sentinel)
        self.assertEqual(size2, len(sentinel))
        # Not a single source byte should have been read or redacted.
        self.assertEqual(FAKE_REDACT.seen, [])

    def test_redaction_happens_before_the_byte_enters_the_archive(self):
        src = self.tmp / "src" / "acct"
        src.mkdir(parents=True)
        secret_file = src / "settings.json"
        secret_file.write_bytes(
            b'{"token": "' + _FakeRedact.SECRET + b'", "theme": "dark"}\n'
        )
        unit = Unit(kind="settings", name="acct/settings.json",
                    source_path=src, is_file=False)

        key, _ = store.write_blob(self.root, HASH_A, unit, [secret_file], {})

        raw = (self.root / key).read_bytes()
        self.assertNotIn(_FakeRedact.SECRET, raw)  # not even in compressed bytes

        with tarfile.open(self.root / key, "r:xz") as tar:
            member = tar.extractfile("settings.json").read()
        self.assertNotIn(_FakeRedact.SECRET, member)
        self.assertIn(_FakeRedact.PLACEHOLDER, member)
        self.assertIn(b'"theme": "dark"', member)

        # The source file itself is never modified.
        self.assertIn(_FakeRedact.SECRET, secret_file.read_bytes())

    def test_redact_receives_the_member_relpath(self):
        unit, files = make_unit(self.tmp)
        store.write_blob(self.root, HASH_A, unit, files, {})
        self.assertEqual(sorted(FAKE_REDACT.seen), ["SKILL.md", "scripts/run.sh"])

    def test_archives_are_reproducible(self):
        unit_a, files_a = make_unit(self.tmp / "one")
        unit_b, files_b = make_unit(self.tmp / "two")
        # Different mtimes on identical content must not change the archive.
        for p in files_b:
            os.utime(p, (1_000_000_000, 1_000_000_000))

        root_a = self.tmp / "store_a"
        root_b = self.tmp / "store_b"
        root_a.mkdir()
        root_b.mkdir()
        key_a, _ = store.write_blob(root_a, HASH_A, unit_a, files_a, {})
        key_b, _ = store.write_blob(root_b, HASH_A, unit_b, files_b, {})

        self.assertEqual(
            (root_a / key_a).read_bytes(),
            (root_b / key_b).read_bytes(),
        )

    def test_archive_carries_no_identity_or_time_metadata(self):
        unit, files = make_unit(self.tmp)
        key, _ = store.write_blob(self.root, HASH_A, unit, files, {})
        with tarfile.open(self.root / key, "r:xz") as tar:
            for ti in tar.getmembers():
                self.assertEqual(ti.mtime, 0, ti.name)
                self.assertEqual(ti.uid, 0, ti.name)
                self.assertEqual(ti.gid, 0, ti.name)
                self.assertEqual(ti.uname, "", ti.name)
                self.assertEqual(ti.gname, "", ti.name)

    def test_executable_bit_is_preserved(self):
        unit, files = make_unit(self.tmp)
        key, _ = store.write_blob(self.root, HASH_A, unit, files, {})
        with tarfile.open(self.root / key, "r:xz") as tar:
            modes = {ti.name: ti.mode for ti in tar.getmembers()}
        self.assertTrue(modes["scripts/run.sh"] & 0o111)
        self.assertFalse(modes["SKILL.md"] & 0o111)

    def test_failure_leaves_nothing_at_the_key(self):
        unit, files = make_unit(self.tmp)
        FAKE_REDACT.fail_on = "run.sh"

        with self.assertRaises(RuntimeError):
            store.write_blob(self.root, HASH_A, unit, files, {})

        target = self.root / store.blob_key(HASH_A)
        self.assertFalse(target.exists())
        # ...and no half-written temp file left lying around either.
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), [])


# --- read_blob --------------------------------------------------------------

class TestReadBlob(StoreTestCase):
    def test_round_trip_reproduces_content_exactly(self):
        unit, files = make_unit(self.tmp)
        key, _ = store.write_blob(self.root, HASH_A, unit, files, {})

        dest = self.tmp / "restored"
        count = store.read_blob(self.root, key, dest)

        self.assertEqual(count, 2)
        self.assertEqual((dest / "SKILL.md").read_bytes(), b"# a skill\n")
        self.assertEqual(
            (dest / "scripts" / "run.sh").read_bytes(), b"#!/bin/sh\necho hi\n"
        )
        self.assertTrue((dest / "scripts" / "run.sh").stat().st_mode & 0o111)
        self.assertFalse((dest / "SKILL.md").stat().st_mode & 0o111)

    def test_creates_destination_if_absent(self):
        unit, files = make_unit(self.tmp)
        key, _ = store.write_blob(self.root, HASH_A, unit, files, {})
        dest = self.tmp / "nope" / "deeper"
        self.assertEqual(store.read_blob(self.root, key, dest), 2)

    def test_refuses_dotdot_member(self):
        key = store.blob_key(HASH_A)
        craft_archive(self.root / key, ["../evil.txt"])
        dest = self.tmp / "dest"
        dest.mkdir()

        with self.assertRaises(ValueError):
            store.read_blob(self.root, key, dest)

        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertEqual(list(dest.iterdir()), [])

    def test_refuses_nested_dotdot_member(self):
        key = store.blob_key(HASH_A)
        craft_archive(self.root / key, ["ok/../../evil.txt"])
        dest = self.tmp / "dest"
        dest.mkdir()
        with self.assertRaises(ValueError):
            store.read_blob(self.root, key, dest)
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertEqual(list(dest.iterdir()), [])

    def test_refuses_absolute_member(self):
        victim = self.tmp / "victim.txt"
        key = store.blob_key(HASH_A)
        craft_archive(self.root / key, [str(victim)])
        dest = self.tmp / "dest"
        dest.mkdir()

        with self.assertRaises(ValueError):
            store.read_blob(self.root, key, dest)

        self.assertFalse(victim.exists())
        self.assertEqual(list(dest.iterdir()), [])

    def test_refuses_symlink_member(self):
        # A symlink is a traversal vector too: extract link then write through it.
        key = store.blob_key(HASH_A)
        (self.root / key).parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(self.root / key, "w:xz") as tar:
            ti = tarfile.TarInfo(name="escape")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            ti.mtime = 0
            tar.addfile(ti)
        dest = self.tmp / "dest"
        dest.mkdir()
        with self.assertRaises(ValueError):
            store.read_blob(self.root, key, dest)
        self.assertEqual(list(dest.iterdir()), [])

    def test_one_bad_member_aborts_the_whole_extraction(self):
        key = store.blob_key(HASH_A)
        craft_archive(self.root / key, ["good.txt", "../evil.txt"])
        dest = self.tmp / "dest"
        dest.mkdir()
        with self.assertRaises(ValueError):
            store.read_blob(self.root, key, dest)
        self.assertFalse((dest / "good.txt").exists())

    def test_missing_blob_raises(self):
        with self.assertRaises(FileNotFoundError):
            store.read_blob(self.root, store.blob_key(HASH_A), self.tmp / "dest")


# --- delete_blob ------------------------------------------------------------

class TestDeleteBlob(StoreTestCase):
    def test_returns_bytes_reclaimed(self):
        unit, files = make_unit(self.tmp)
        key, size = store.write_blob(self.root, HASH_A, unit, files, {})
        self.assertEqual(store.delete_blob(self.root, key), size)
        self.assertFalse((self.root / key).exists())

    def test_missing_blob_returns_zero_and_never_raises(self):
        self.assertEqual(store.delete_blob(self.root, store.blob_key(HASH_A)), 0)
        # Missing root entirely, too.
        self.assertEqual(
            store.delete_blob(self.tmp / "no-such-root", store.blob_key(HASH_A)), 0
        )

    def test_double_delete_is_a_noop(self):
        unit, files = make_unit(self.tmp)
        key, size = store.write_blob(self.root, HASH_A, unit, files, {})
        self.assertEqual(store.delete_blob(self.root, key), size)
        self.assertEqual(store.delete_blob(self.root, key), 0)

    def test_prunes_empty_shard_dirs_but_not_objects(self):
        unit, files = make_unit(self.tmp)
        key, _ = store.write_blob(self.root, HASH_A, unit, files, {})
        store.delete_blob(self.root, key)

        self.assertFalse((self.root / "objects" / HASH_A[0:2] / HASH_A[2:4]).exists())
        self.assertFalse((self.root / "objects" / HASH_A[0:2]).exists())
        self.assertTrue((self.root / "objects").is_dir())

    def test_keeps_shard_dirs_that_still_hold_blobs(self):
        unit, files = make_unit(self.tmp)
        other = HASH_A[0:4] + "b" * 60
        key1, _ = store.write_blob(self.root, HASH_A, unit, files, {})
        key2, _ = store.write_blob(self.root, other, unit, files, {})
        store.delete_blob(self.root, key1)

        self.assertTrue((self.root / key2).is_file())
        self.assertTrue((self.root / "objects" / HASH_A[0:2] / HASH_A[2:4]).is_dir())


# --- verify_blob ------------------------------------------------------------

class TestVerifyBlob(StoreTestCase):
    def test_true_when_hash_matches(self):
        unit, files = make_unit(self.tmp)
        expected = canonical_hash_of_tree(unit.source_path)
        key, _ = store.write_blob(self.root, expected, unit, files, {})
        self.assertTrue(store.verify_blob(self.root, key, expected))

    def test_false_when_hash_differs(self):
        unit, files = make_unit(self.tmp)
        key, _ = store.write_blob(self.root, HASH_A, unit, files, {})
        self.assertFalse(store.verify_blob(self.root, key, HASH_A))

    def test_false_for_missing_blob(self):
        self.assertFalse(
            store.verify_blob(self.root, store.blob_key(HASH_A), HASH_A)
        )

    def test_false_for_corrupt_blob(self):
        key = store.blob_key(HASH_A)
        (self.root / key).parent.mkdir(parents=True, exist_ok=True)
        (self.root / key).write_bytes(b"not an xz stream at all")
        self.assertFalse(store.verify_blob(self.root, key, HASH_A))

    def test_leaves_no_temp_dir_behind(self):
        unit, files = make_unit(self.tmp)
        expected = canonical_hash_of_tree(unit.source_path)
        key, _ = store.write_blob(self.root, expected, unit, files, {})

        scratch = self.tmp / "scratch"
        scratch.mkdir()
        previous = tempfile.tempdir
        tempfile.tempdir = str(scratch)
        try:
            self.assertTrue(store.verify_blob(self.root, key, expected))
        finally:
            tempfile.tempdir = previous
        self.assertEqual(list(scratch.iterdir()), [])

    def test_verifies_after_redaction(self):
        """The stored hash is the hash of what is IN the blob, redacted."""
        src = self.tmp / "src" / "acct"
        src.mkdir(parents=True)
        f = src / "settings.json"
        f.write_bytes(b'{"token": "' + _FakeRedact.SECRET + b'"}\n')
        unit = Unit(kind="settings", name="acct", source_path=src, is_file=False)

        redacted, _ = FAKE_REDACT.redact_bytes(f.read_bytes(), "settings.json", {})
        expected = _FakeHashing.content_hash([
            FileEntry(relpath="settings.json", executable=False,
                      sha256=hashlib.sha256(redacted).hexdigest())
        ])

        key, _ = store.write_blob(self.root, expected, unit, [f], {})
        self.assertTrue(store.verify_blob(self.root, key, expected))


if __name__ == "__main__":
    unittest.main()
